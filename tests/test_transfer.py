import datetime as dt
import errno
import gzip
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import download


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out_dir = Path(self.directory.name)

    def downloader(self, payloads, results=None):
        attempts = []

        def fake_run(cmd, cwd=None, ignore_error=False):
            index = len(attempts)
            attempts.append(cmd)
            basename = download.remote_basename(cmd)
            (Path(cwd) / basename).write_bytes(payloads[index])
            return results[index] if results is not None else True

        return fake_run, attempts

    def test_corrupt_existing_archive_is_replaced_from_current_source(self):
        archive = self.out_dir / "orbit.sp3.gz"
        archive.write_bytes(b"interrupted download")
        fake_run, attempts = self.downloader([gzip.compress(b"complete orbit")])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            path, status = download.download_source("https://one.invalid/orbit.sp3.gz", self.out_dir)
        self.assertEqual(status, "downloaded")
        self.assertEqual(path, self.out_dir / "orbit.sp3")
        self.assertEqual(path.read_bytes(), b"complete orbit")
        self.assertEqual(len(attempts), 1)
        self.assertFalse(archive.exists())

    def test_bad_download_allows_same_name_mirror(self):
        fake_run, attempts = self.downloader([b"not gzip", gzip.compress(b"mirror orbit")])
        sources = ["https://one.invalid/orbit.sp3.gz", "https://two.invalid/orbit.sp3.gz"]
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            status = download.download_with_fallback(sources, self.out_dir)
        self.assertEqual(status, "downloaded")
        self.assertEqual(len(attempts), 2)
        self.assertIn("two.invalid", attempts[1])
        self.assertEqual((self.out_dir / "orbit.sp3").read_bytes(), b"mirror orbit")

    def test_failed_download_never_publishes_partial_uncompressed_product(self):
        fake_run, attempts = self.downloader([b"partial", b"complete"], [False, True])
        sources = ["https://one.invalid/orbit.sp3", "https://two.invalid/orbit.sp3"]
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            status = download.download_with_fallback(sources, self.out_dir)
        self.assertEqual(status, "downloaded")
        self.assertEqual(len(attempts), 2)
        self.assertEqual((self.out_dir / "orbit.sp3").read_bytes(), b"complete")
        self.assertEqual(list(self.out_dir.glob(".fast_yk_download_*")), [])

    def test_failed_attempt_can_be_retried_on_next_run(self):
        source = "https://one.invalid/orbit.sp3.gz"
        fake_run, attempts = self.downloader([gzip.compress(b"partial")[:12], gzip.compress(b"complete")])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            self.assertEqual(download.download_with_fallback([source], self.out_dir), "failed")
            self.assertEqual(list(self.out_dir.iterdir()), [])
            self.assertEqual(download.download_with_fallback([source], self.out_dir), "downloaded")
        self.assertEqual(len(attempts), 2)
        self.assertEqual((self.out_dir / "orbit.sp3").read_bytes(), b"complete")

    def test_bad_archive_does_not_publish_partial_decompression(self):
        archive = self.out_dir / "observation.crx.gz"
        archive.write_bytes(gzip.compress(b"nonempty partial observation")[:-5])
        self.assertIsNone(download.decompress_file(archive, self.out_dir))
        self.assertEqual(list(self.out_dir.iterdir()), [])

    def test_bad_archive_leaves_existing_output_untouched(self):
        output = self.out_dir / "orbit.sp3"
        output.write_bytes(b"already complete")
        archive = self.out_dir / "orbit.sp3.gz"
        archive.write_bytes(gzip.compress(b"replacement")[:-5])
        self.assertIsNone(download.decompress_file(archive, self.out_dir))
        self.assertEqual(output.read_bytes(), b"already complete")
        self.assertFalse(archive.exists())

    def test_decompress_environment_error_retains_archive(self):
        archive = self.out_dir / "orbit.sp3.gz"
        original = gzip.compress(b"valid orbit")
        archive.write_bytes(original)
        failure = subprocess.CompletedProcess([], 1, stderr="gzip: stdout: No space left on device")
        with mock.patch.object(download.subprocess, "run", return_value=failure):
            self.assertIsNone(download.decompress_file(archive, self.out_dir))
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(list(self.out_dir.iterdir()), [archive])

    def test_missing_gzip_retains_archive_without_network_retry(self):
        archive = self.out_dir / "orbit.sp3.gz"
        original = gzip.compress(b"valid orbit")
        archive.write_bytes(original)
        with (
            mock.patch.object(download.subprocess, "run", side_effect=FileNotFoundError("gzip")),
            mock.patch.object(download, "run_cmd") as network,
        ):
            result = download.download_source("https://one.invalid/orbit.sp3.gz", self.out_dir)
        self.assertEqual(result, (None, "failed"))
        self.assertEqual(archive.read_bytes(), original)
        network.assert_not_called()

    def test_publish_error_retains_compressed_input(self):
        archive = self.out_dir / "orbit.sp3.gz"
        original = gzip.compress(b"valid orbit")
        archive.write_bytes(original)
        with mock.patch.object(Path, "replace", side_effect=OSError(errno.EACCES, "Permission denied")):
            self.assertIsNone(download.decompress_file(archive, self.out_dir))
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(list(self.out_dir.iterdir()), [archive])

    def test_new_archive_is_preserved_when_decompression_environment_fails(self):
        original = gzip.compress(b"valid orbit")
        fake_run, _ = self.downloader([original])
        with (
            mock.patch.object(download, "run_cmd", side_effect=fake_run),
            mock.patch.object(download.subprocess, "run", side_effect=FileNotFoundError("gzip")),
        ):
            result = download.download_source("https://one.invalid/orbit.sp3.gz", self.out_dir)
        self.assertEqual(result, (None, "failed"))
        self.assertEqual((self.out_dir / "orbit.sp3.gz").read_bytes(), original)

    def test_forced_mirror_environment_failure_does_not_replace_existing_archive(self):
        archive = self.out_dir / "observation.crx.gz"
        original = gzip.compress(b"complete cached observation")
        archive.write_bytes(original)
        fake_run, _ = self.downloader([b"invalid mirror response"])
        with (
            mock.patch.object(download, "run_cmd", side_effect=fake_run),
            mock.patch.object(download.subprocess, "run", side_effect=FileNotFoundError("gzip")),
        ):
            result = download.download_source(
                "https://two.invalid/observation.crx.gz", self.out_dir, use_existing=False
            )
        self.assertEqual(result, (None, "failed"))
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(list(self.out_dir.iterdir()), [archive])

    def test_forced_corrupt_mirror_does_not_replace_existing_archive(self):
        archive = self.out_dir / "observation.crx.gz"
        original = gzip.compress(b"complete cached observation")
        archive.write_bytes(original)
        fake_run, _ = self.downloader([b"invalid mirror response"])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            result = download.download_source(
                "https://two.invalid/observation.crx.gz", self.out_dir, use_existing=False
            )
        self.assertEqual(result, (None, "failed"))
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(list(self.out_dir.iterdir()), [archive])

    def test_archive_with_trailing_garbage_is_retried_from_same_source(self):
        archive = self.out_dir / "orbit.sp3.gz"
        archive.write_bytes(gzip.compress(b"old orbit") + b"extra garbage")
        fake_run, attempts = self.downloader([gzip.compress(b"complete orbit")])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            path, status = download.download_source("https://one.invalid/orbit.sp3.gz", self.out_dir)
        self.assertEqual(status, "downloaded")
        self.assertEqual(path.read_bytes(), b"complete orbit")
        self.assertEqual(len(attempts), 1)
        self.assertFalse(archive.exists())

    def test_valid_existing_archive_unpacks_without_network(self):
        archive = self.out_dir / "abcd0010.25d.Z"
        # gzip detects compression by magic; both supported suffixes strip correctly.
        archive.write_bytes(gzip.compress(b"compact observations"))
        with mock.patch.object(download, "run_cmd") as network:
            path, status = download.download_source("https://one.invalid/abcd0010.25d.Z", self.out_dir)
        self.assertEqual(status, "existing")
        self.assertEqual(path.name, "abcd0010.25d")
        self.assertEqual(path.read_bytes(), b"compact observations")
        network.assert_not_called()

    def test_force_download_replaces_unusable_existing_intermediate(self):
        intermediate = self.out_dir / "observation.crx"
        intermediate.write_bytes(b"invalid compact RINEX")
        fake_run, attempts = self.downloader([gzip.compress(b"healthy compact RINEX")])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            path, status = download.download_source(
                "https://two.invalid/observation.crx.gz", self.out_dir, use_existing=False
            )
        self.assertEqual((path, status), (intermediate, "downloaded"))
        self.assertEqual(intermediate.read_bytes(), b"healthy compact RINEX")
        self.assertEqual(len(attempts), 1)

    def test_rename_uses_only_successfully_decompressed_product(self):
        fake_run, _ = self.downloader([gzip.compress(b"complete orbit")])
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            status = download.download_with_fallback(
                ["https://one.invalid/long_product.sp3.gz"], self.out_dir,
                renamed_name="igs23473.sp3", rename_enabled=True,
            )
        self.assertEqual(status, "downloaded")
        self.assertEqual((self.out_dir / "igs23473.sp3").read_bytes(), b"complete orbit")
        self.assertEqual(len(list(self.out_dir.iterdir())), 1)

    def test_cas_download_uses_mirror_and_normalizes_successful_output(self):
        fake_run, attempts = self.downloader([b"bad gzip", gzip.compress(b"complete DCB")])
        date = dt.date(2025, 1, 1)
        stats = download.init_stats()
        source_defs = {"cas_dcb_rap": ["https://one.invalid/dcb.bsx.gz", "https://two.invalid/dcb.bsx.gz"]}
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            download.handle_cas_dcb(
                download.TYPE_META["cas_dcb_rap"], date, source_defs,
                self.out_dir, "exact", False, stats,
            )
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(len(attempts), 2)
        self.assertEqual((self.out_dir / download.cas_dcb_long_name(date)).read_bytes(), b"complete DCB")

    def test_cas_existing_bad_archive_redownloads_and_renames(self):
        (self.out_dir / "dcb.bsx.gz").write_bytes(b"bad gzip")
        fake_run, _ = self.downloader([gzip.compress(b"complete DCB")])
        date = dt.date(2025, 1, 1)
        stats = download.init_stats()
        with mock.patch.object(download, "run_cmd", side_effect=fake_run):
            download.handle_cas_dcb(
                download.TYPE_META["cas_dcb_rap"], date,
                {"cas_dcb_rap": ["https://one.invalid/dcb.bsx.gz"]},
                self.out_dir, "exact", True, stats,
            )
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual((self.out_dir / download.cas_dcb_short_name(date)).read_bytes(), b"complete DCB")


if __name__ == "__main__":
    unittest.main()
