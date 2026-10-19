"""Offline authentication regressions; never open the user's credential or cookie files."""

import datetime as dt
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import download


class CredentialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.credentials = self.root / ".download_credentials.json"
        self.cookies = self.root / ".urs_cookies"
        for name, value in (
            ("CREDENTIAL_FILE", self.credentials),
            ("CDDIS_COOKIE_FILE", self.cookies),
            ("CDDIS_CREDENTIAL_FILE", self.root / ".cddis_credentials.json"),
        ):
            # The legacy constant is patched too, so regressions cannot read a
            # real account even if the old loader is accidentally reintroduced.
            patcher = mock.patch.object(download, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output = io.StringIO()
        quiet = redirect_stdout(self.output)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def write_credentials(self, profiles=None):
        if profiles is None:
            profiles = {
                name: {"username": f"test-{name}-user", "password": f"test-{name}-password"}
                for name in ("cddis", "cddis_ftp", "igmas")
            }
        self.credentials.write_text(json.dumps(profiles), encoding="utf-8")
        return profiles

    def assert_option(self, tokens, option, value):
        self.assertIn(option, tokens)
        self.assertEqual(tokens[tokens.index(option) + 1], value)

    def test_missing_file_skips_authenticated_source_with_a_warning(self):
        self.assertIsNone(download.load_credentials("igmas"))
        self.assertIsNone(download.build_wget_cmd(
            "--credential-profile=igmas ftp://private.invalid/orbit.sp3"
        ))
        self.assertIn("[WARN]", self.output.getvalue())

    def test_malformed_json_does_not_reveal_credential_values(self):
        marker = "secret-that-must-not-be-logged"
        self.credentials.write_text('{"igmas": {"password": "' + marker, encoding="utf-8")
        self.assertIsNone(download.load_credentials("igmas"))
        self.assertIn("[WARN]", self.output.getvalue())
        self.assertNotIn(marker, self.output.getvalue())

    def test_unreadable_file_is_a_safe_source_failure(self):
        self.credentials.mkdir()
        self.assertIsNone(download.load_credentials("igmas"))
        self.assertIn("[WARN]", self.output.getvalue())

    def test_invalid_utf8_is_a_safe_source_failure(self):
        self.credentials.write_bytes(b'{"igmas": "secret-\xff"}')
        self.assertIsNone(download.load_credentials("igmas"))
        self.assertNotIn("secret-", self.output.getvalue())

    def test_invalid_structures_and_empty_fields_do_not_leak_values(self):
        marker = "private-structure-value"
        cases = [
            None,
            [marker],
            marker,
            {"other": {"username": marker, "password": marker}},
            {"igmas": marker},
            {"igmas": [marker]},
            {"igmas": {"username": "", "password": marker}},
            {"igmas": {"username": "  ", "password": marker}},
            {"igmas": {"username": marker, "password": ""}},
            {"igmas": {"username": 12345, "password": marker}},
            {"igmas": {"username": marker, "password": [marker]}},
        ]
        for index, data in enumerate(cases):
            with self.subTest(case=index):
                self.credentials.write_text(json.dumps(data), encoding="utf-8")
                self.assertIsNone(download.load_credentials("igmas"))
                self.assertNotIn(marker, self.output.getvalue())

    def test_profile_selection_keeps_accounts_separate(self):
        profiles = self.write_credentials()
        for name, expected in profiles.items():
            with self.subTest(profile=name):
                self.assertEqual(download.load_credentials(name), expected)
        self.assertEqual(self.output.getvalue(), "")

    def test_legacy_cddis_file_is_not_a_second_credential_source(self):
        legacy = self.root / ".cddis_credentials.json"
        legacy.write_text(json.dumps({
            "username": "legacy-user", "password": "legacy-password"
        }), encoding="utf-8")
        with mock.patch.object(download, "ROOT_DIR", self.root):
            self.assertIsNone(download.build_wget_cmd(
                "https://cddis.nasa.gov/archive/orbit.sp3"
            ))
        self.assertNotIn("legacy-user", self.output.getvalue())
        self.assertNotIn("legacy-password", self.output.getvalue())

    def test_three_profiles_use_protocol_appropriate_wget_options(self):
        profiles = self.write_credentials()
        cases = (
            ("cddis", "https://cddis.nasa.gov/archive/orbit.sp3", "--user", "--password"),
            ("cddis_ftp", "ftps://gdc.cddis.eosdis.nasa.gov/orbit.sp3", "--ftp-user", "--ftp-password"),
            ("igmas", "ftp://private.invalid/orbit.sp3", "--ftp-user", "--ftp-password"),
            ("igmas", "http://private.invalid/orbit.sp3", "--user", "--password"),
        )
        for profile, url, user_option, password_option in cases:
            with self.subTest(profile=profile, url=url):
                tokens = shlex.split(download.build_wget_cmd(f"--credential-profile={profile} {url}"))
                self.assert_option(tokens, user_option, profiles[profile]["username"])
                self.assert_option(tokens, password_option, profiles[profile]["password"])
                self.assertIn(url, tokens)
                self.assertFalse(any(token.startswith("--credential-profile") for token in tokens))

    def test_cddis_https_string_source_uses_unified_credentials_and_cookies(self):
        profiles = self.write_credentials()
        tokens = shlex.split(download.build_wget_cmd("https://cddis.nasa.gov/archive/orbit.sp3"))
        self.assert_option(tokens, "--user", profiles["cddis"]["username"])
        self.assert_option(tokens, "--password", profiles["cddis"]["password"])
        self.assert_option(tokens, "--load-cookies", str(self.cookies))
        self.assert_option(tokens, "--save-cookies", str(self.cookies))
        self.assertIn("--keep-session-cookies", tokens)
        self.assertEqual(stat.S_IMODE(self.cookies.stat().st_mode), 0o600)

    def test_ftp_source_does_not_use_earthdata_cookies(self):
        self.write_credentials()
        command = download.build_wget_cmd("--credential-profile=igmas ftp://private.invalid/orbit.sp3")
        self.assertNotIn("cookies", command)
        self.assertFalse(self.cookies.exists())

    def test_anonymous_source_does_not_need_credentials(self):
        url = "https://public.invalid/orbit.sp3"
        tokens = shlex.split(download.build_wget_cmd(url))
        self.assertIn(url, tokens)
        for option in ("--user", "--password", "--ftp-user", "--ftp-password"):
            self.assertNotIn(option, tokens)
        self.assertEqual(self.output.getvalue(), "")
        self.assertFalse(self.cookies.exists())

    def test_source_objects_preserve_template_expansion_and_remote_names(self):
        sources = {"mgex": [
            {"url": "ftp://private.invalid/<YYYY>/<DOY>/<SITE><DOY>0.<YY>d.Z", "credentials": "igmas"},
            "https://public.invalid/<YYYY>/<DOY>/<SITE><DOY>0.<YY>d.Z",
        ]}
        site = {"short_lower": "abcd", "short_upper": "ABCD", "long_upper": "ABCD00XXX"}
        resolved = download.resolve_sources(sources, "mgex", dt.date(2025, 1, 2), site)
        self.assertEqual(len(resolved), 2)
        self.assertIn("--credential-profile=igmas", shlex.split(resolved[0]))
        self.assertEqual(download.extract_url(resolved[0]), "ftp://private.invalid/2025/002/abcd0020.25d.Z")
        self.assertEqual(download.extract_url(resolved[1]), "https://public.invalid/2025/002/abcd0020.25d.Z")
        for source in resolved:
            self.assertEqual(download.remote_basename(source), "abcd0020.25d.Z")
        self.assertFalse(self.credentials.exists())

    def test_missing_credentials_allow_fallback_to_public_mirror(self):
        sources = download.resolve_sources({"igs": [
            {"url": "ftp://private.invalid/orbit.sp3", "credentials": "igmas"},
            "https://public.invalid/orbit.sp3",
        ]}, "igs", dt.date(2025, 1, 1))
        attempts = []

        def fake_run(command, cwd=None, ignore_error=False):
            self.assertIsInstance(command, str)
            attempts.append(command)
            self.assertIn("public.invalid", command)
            (Path(cwd) / "orbit.sp3").write_bytes(b"complete public product")
            return True

        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            status = download.download_with_fallback(sources, self.root)
        self.assertEqual(status, "downloaded")
        self.assertEqual(len(attempts), 1)
        self.assertEqual((self.root / "orbit.sp3").read_bytes(), b"complete public product")

    def test_shell_metacharacters_in_credentials_are_literal_arguments(self):
        username = "user ' \" $HOME $(touch injected-user) `touch injected-backtick`"
        password = " pass ' \" ; $(touch injected-password) & | < > \\ end "
        self.write_credentials({"igmas": {"username": username, "password": password}})
        executable = self.root / "wget"
        capture = self.root / "arguments.json"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['FAST_YK_TEST_CAPTURE'], 'w') as output:\n"
            "    json.dump(sys.argv[1:], output)\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        command = download.build_wget_cmd("--credential-profile=igmas ftp://private.invalid/orbit.sp3")
        result = subprocess.run(
            command, shell=True, cwd=self.root, capture_output=True, text=True, timeout=10,
            env={**os.environ, "PATH": str(self.root), "FAST_YK_TEST_CAPTURE": str(capture)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(capture.read_text(encoding="utf-8"))
        self.assert_option(arguments, "--ftp-user", username)
        self.assert_option(arguments, "--ftp-password", password)
        self.assertEqual(list(self.root.glob("injected-*")), [])

    def test_logs_and_command_failures_redact_usernames_and_passwords(self):
        values = {"username": "private-user-marker", "password": "private-password-marker"}
        self.write_credentials({"igmas": values})
        command = download.build_wget_cmd("--credential-profile=igmas ftp://private.invalid/orbit.sp3")
        process = subprocess.CompletedProcess(
            command, 1, stdout="Logging in as private-user-marker; password private-password-marker\n"
        )
        with mock.patch.object(download.subprocess, "run", return_value=process):
            with self.assertRaises(RuntimeError) as failure:
                download.run_cmd(command)
        visible = self.output.getvalue() + str(failure.exception)
        for value in values.values():
            self.assertNotIn(value, visible)
        self.assertIn("private.invalid", visible)

    def test_spawn_errors_do_not_reveal_command_credentials(self):
        self.write_credentials()
        command = download.build_wget_cmd("--credential-profile=igmas ftp://private.invalid/orbit.sp3")
        with mock.patch.object(download.subprocess, "run", side_effect=OSError(f"Cannot run {command}")):
            with self.assertRaises((OSError, RuntimeError)) as failure:
                download.run_cmd(command)
        visible = self.output.getvalue() + str(failure.exception)
        self.assertNotIn("test-igmas-user", visible)
        self.assertNotIn("test-igmas-password", visible)

    def test_redaction_handles_both_equals_and_separate_wget_options(self):
        for option in ("--user", "--password", "--ftp-user", "--ftp-password"):
            for separator in ("=", " "):
                with self.subTest(option=option, separator=separator):
                    command = f"wget {option}{separator}'sensitive-value with space' https://public.invalid/a"
                    self.assertNotIn("sensitive-value", download.redact_sensitive_cmd(command))


class PublicCredentialConfigurationTests(unittest.TestCase):
    def test_source_configuration_contains_references_instead_of_credentials(self):
        # Only the public source catalogue and empty example are inspected here.
        text = (download.ROOT_DIR / "download_sources.json").read_text(encoding="utf-8")
        for option in ("--user", "--password", "--ftp-user", "--ftp-password"):
            self.assertNotIn(option, text)
        profiles = set()
        for entries in json.loads(text).values():
            for entry in entries:
                if isinstance(entry, dict):
                    self.assertIsInstance(entry["url"], str)
                    profiles.add(entry["credentials"])
                    self.assertNotIn("username", entry)
                    self.assertNotIn("password", entry)
                else:
                    self.assertIsInstance(entry, str)
        self.assertEqual(profiles, {"cddis", "cddis_ftp", "igmas"})

    def test_public_example_has_no_credentials(self):
        example = json.loads((download.ROOT_DIR / "download_credentials.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example, {
            name: {"username": "", "password": ""}
            for name in ("cddis", "cddis_ftp", "igmas")
        })


if __name__ == "__main__":
    unittest.main()
