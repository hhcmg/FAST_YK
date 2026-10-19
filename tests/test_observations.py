"""Offline observation regressions using gzip and the bundled CRX converter."""

import datetime as dt
import gzip
import io
import shlex
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import download


RINEX = (
    "     3.04           OBSERVATION DATA    M".ljust(60)
    + "RINEX VERSION / TYPE\n"
    + "".ljust(60)
    + "END OF HEADER\n"
).encode("ascii")
CRINEX = (
    "3.0                 COMPACT RINEX FORMAT".ljust(60)
    + "CRINEX VERS   / TYPE\n"
    + "RNX2CRX".ljust(60)
    + "CRINEX PROG / DATE\n"
).encode("ascii") + RINEX
# The converter writes the RINEX header, then exits 1 for this invalid epoch.
BROKEN_CRINEX = CRINEX + b"> 2025 01 01 00 00  0.0000000  0  1\n"
DAY = dt.date(2025, 1, 1)
LONG_CRX = "ABCD00XXX_R_20250010000_01D_30S_MO.crx"
SITE_MAP = {"abcd": "ABCD00XXX"}
MGEX_SOURCES = {
    "mgex": [
        "ftp://primary.invalid/<SITE_LONG>_R_<YYYY><DOY>0000_01D_30S_MO.crx.gz",
        "ftp://mirror.invalid/<SITE_LONG>_R_<YYYY><DOY>0000_01D_30S_MO.crx.gz",
    ]
}
IGMAS_SOURCES = {
    "igmas": ["ftp://primary.invalid/<YYYY>/<DOY>/daily/*.<YY>d.Z"]
}


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / "observations"
        self.out.mkdir()
        self.sites = self.root / "sites.txt"
        self.sites.write_text("abcd\n", encoding="ascii")
        self.stats = download.init_stats()
        # Every subprocess-backed test is offline, including on assertion failure.
        self.no_network = mock.patch.object(
            download, "run_cmd", side_effect=AssertionError("unexpected external command")
        )
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.quiet = redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def mgex(self, rename=False, site_map=None):
        download.handle_mgex_obs(
            download.TYPE_META["mgex"], DAY, MGEX_SOURCES, self.out, "exact",
            rename, self.sites, SITE_MAP if site_map is None else site_map,
            self.stats,
        )

    def igmas(self, day=DAY):
        download.handle_igmas(
            download.TYPE_META["igmas"], day, IGMAS_SOURCES, self.out,
            "exact", False, self.sites, self.stats,
        )

    def write_archive(self, path, payload=CRINEX):
        path.write_bytes(gzip.compress(payload))

    def test_only_complete_rinex_observations_count_as_existing(self):
        site = download.normalize_site("abcd", SITE_MAP)
        invalid_outputs = {
            "abcd0010.25o": b"",
            LONG_CRX: CRINEX,
            LONG_CRX + ".gz": gzip.compress(CRINEX),
            "abcd0010.25d": CRINEX,
            "abcd0010.25d.Z": gzip.compress(CRINEX),
            "abcd0010.25d.gz": gzip.compress(CRINEX),
            LONG_CRX.replace(".crx", ".rnx"): RINEX.splitlines(keepends=True)[0],
        }
        for name, payload in invalid_outputs.items():
            (self.out / name).write_bytes(payload)
        self.assertIsNone(download.existing_obs_output(self.out, site, DAY))
        short = self.out / "abcd0010.25o"
        short.write_bytes(RINEX.replace(b"OBSERVATION DATA", b"NAVIGATION DATA "))
        self.assertIsNone(download.existing_obs_output(self.out, site, DAY))
        short.write_bytes(RINEX)
        self.assertEqual(download.existing_obs_output(self.out, site, DAY), short)
        short.unlink()
        long = self.out / LONG_CRX.replace(".crx", ".rnx")
        long.write_bytes(RINEX)
        self.assertEqual(download.existing_obs_output(self.out, site, DAY), long)

    def test_real_converter_supports_long_and_short_output_names(self):
        for rename in (False, True):
            with self.subTest(rename=rename):
                source = self.out / LONG_CRX
                source.write_bytes(CRINEX)
                expected = self.out / (
                    "abcd0010.25o" if rename else LONG_CRX.replace(".crx", ".rnx")
                )
                result = download.convert_crx_file(
                    source, self.out, rename, short_d_name="abcd0010.25d",
                    delete_input=True,
                )
                self.assertEqual(result, expected)
                self.assertEqual(expected.read_bytes(), RINEX)
                self.assertFalse(source.exists())
                self.assertFalse((self.out / "abcd0010.25d").exists())

    def test_converter_failure_keeps_input_and_does_not_publish_partial_output(self):
        for rename in (False, True):
            with self.subTest(rename=rename):
                source = self.out / LONG_CRX
                source.write_bytes(BROKEN_CRINEX)
                before = {path.name: path.read_bytes() for path in self.out.iterdir()}
                result = download.convert_crx_file(
                    source, self.out, rename, short_d_name="abcd0010.25d",
                    delete_input=True,
                )
                self.assertIsNone(result)
                self.assertEqual(
                    {path.name: path.read_bytes() for path in self.out.iterdir()}, before
                )

    def test_converter_failure_preserves_an_existing_final_file(self):
        source = self.out / LONG_CRX
        source.write_bytes(BROKEN_CRINEX)
        final = self.out / "abcd0010.25o"
        final.write_bytes(RINEX)
        download.convert_crx_file(
            source, self.out, True, short_d_name="abcd0010.25d", delete_input=True
        )
        self.assertEqual(final.read_bytes(), RINEX)
        self.assertEqual(source.read_bytes(), BROKEN_CRINEX)

    def test_mgex_finishes_cached_compressed_observations_without_network(self):
        for rename in (False, True):
            with self.subTest(rename=rename):
                for path in self.out.iterdir():
                    path.unlink()
                self.stats = download.init_stats()
                self.write_archive(self.out / (LONG_CRX + ".gz"))
                with mock.patch.object(download, "download_source") as fetch:
                    self.mgex(rename=rename)
                fetch.assert_not_called()
                expected = self.out / (
                    "abcd0010.25o" if rename else LONG_CRX.replace(".crx", ".rnx")
                )
                self.assertEqual(expected.read_bytes(), RINEX)
                self.assertEqual(self.stats["failed"], 0)
                self.assertEqual(self.stats["expected"], 1)

    def test_mgex_finishes_cached_short_files_without_a_long_station_mapping(self):
        for suffix in ("", ".gz", ".Z"):
            with self.subTest(suffix=suffix):
                for path in self.out.iterdir():
                    path.unlink()
                self.stats = download.init_stats()
                source = self.out / ("abcd0010.25d" + suffix)
                source.write_bytes(gzip.compress(CRINEX) if suffix else CRINEX)
                with mock.patch.object(download, "download_source") as fetch:
                    self.mgex(site_map={})
                fetch.assert_not_called()
                self.assertEqual((self.out / "abcd0010.25o").read_bytes(), RINEX)
                self.assertEqual(self.stats["failed"], 0)

    def test_mgex_keeps_cached_archive_when_gzip_is_unavailable(self):
        archive = self.out / (LONG_CRX + ".gz")
        self.write_archive(archive)
        original = archive.read_bytes()
        with (
            mock.patch.object(download.subprocess, "run", side_effect=FileNotFoundError("gzip")),
            mock.patch.object(download, "download_source") as fetch,
        ):
            self.mgex()
        fetch.assert_not_called()
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(self.stats["failed"], 1)
        self.assertEqual(self.stats["existing"], 0)

    def test_mgex_skips_valid_final_output(self):
        (self.out / "abcd0010.25o").write_bytes(RINEX)
        with mock.patch.object(download, "download_source") as fetch:
            self.mgex()
        fetch.assert_not_called()
        self.assertEqual(self.stats["existing"], 1)

    def test_mgex_also_recovers_cached_s_source_long_names(self):
        source_name = LONG_CRX.replace("_R_", "_S_")
        self.write_archive(self.out / (source_name + ".gz"))
        with mock.patch.object(download, "download_source") as fetch:
            self.mgex()
        fetch.assert_not_called()
        self.assertEqual((self.out / source_name.replace(".crx", ".rnx")).read_bytes(), RINEX)
        self.assertEqual(self.stats["failed"], 0)

    def test_mgex_retries_same_name_mirror_after_conversion_failure(self):
        calls = []

        def wget(cmd, cwd=None, ignore_error=False):
            self.assertEqual(shlex.split(cmd)[0], "wget")
            calls.append(cmd)
            payload = BROKEN_CRINEX if "primary.invalid" in cmd else CRINEX
            self.write_archive(Path(cwd) / (LONG_CRX + ".gz"), payload)
            return True

        with mock.patch.object(download, "run_cmd", side_effect=wget):
            self.mgex(rename=True)
        self.assertEqual(len(calls), 2)
        self.assertIn("mirror.invalid", calls[1])
        self.assertEqual((self.out / "abcd0010.25o").read_bytes(), RINEX)
        self.assertEqual(self.stats["downloaded"], 1)
        self.assertEqual(self.stats["failed"], 0)

    def test_mgex_all_bad_conversions_are_failed_and_inputs_remain(self):
        def wget(cmd, cwd=None, ignore_error=False):
            self.assertEqual(shlex.split(cmd)[0], "wget")
            self.write_archive(Path(cwd) / (LONG_CRX + ".gz"), BROKEN_CRINEX)
            return True

        with mock.patch.object(download, "run_cmd", side_effect=wget):
            self.mgex(rename=True)
        self.assertEqual(self.stats["failed"], 1)
        self.assertEqual(self.stats["existing"], 0)
        self.assertEqual(self.stats["downloaded"], 0)
        self.assertEqual((self.out / LONG_CRX).read_bytes(), BROKEN_CRINEX)
        self.assertFalse((self.out / "abcd0010.25o").exists())

    def test_igmas_processes_current_day_all_stations_but_not_other_days(self):
        self.write_archive(self.out / "abcd0010.25d.Z")
        (self.out / "efgh0010.25d").write_bytes(CRINEX)
        other = self.out / "abcd0020.25d"
        other.write_bytes(BROKEN_CRINEX)
        other_archive = self.out / "efgh0020.25d.Z"
        self.write_archive(other_archive)
        before_archive = other_archive.read_bytes()
        download.process_igmas_files(self.out, DAY)
        self.assertEqual((self.out / "abcd0010.25o").read_bytes(), RINEX)
        self.assertEqual((self.out / "efgh0010.25o").read_bytes(), RINEX)
        self.assertEqual(other.read_bytes(), BROKEN_CRINEX)
        self.assertEqual(other_archive.read_bytes(), before_archive)
        self.assertFalse((self.out / "abcd0020.25o").exists())

    def test_igmas_preserves_failed_compact_input(self):
        source = self.out / "abcd0010.25d"
        source.write_bytes(BROKEN_CRINEX)
        download.process_igmas_files(self.out, DAY)
        self.assertEqual(source.read_bytes(), BROKEN_CRINEX)
        self.assertFalse((self.out / "abcd0010.25o").exists())

    def test_igmas_cached_compressed_station_needs_no_download(self):
        self.write_archive(self.out / "abcd0010.25d.Z")
        self.igmas()
        self.assertEqual((self.out / "abcd0010.25o").read_bytes(), RINEX)
        self.assertEqual(self.stats["failed"], 0)

    def test_igmas_downloaded_bad_compact_survives_staging_cleanup(self):
        def wget(cmd, cwd=None, ignore_error=False):
            self.assertEqual(shlex.split(cmd)[0], "wget")
            self.write_archive(Path(cwd) / "abcd0010.25d.Z", BROKEN_CRINEX)
            return True

        with mock.patch.object(download, "run_cmd", side_effect=wget):
            self.igmas()
        self.assertEqual((self.out / "abcd0010.25d").read_bytes(), BROKEN_CRINEX)
        self.assertFalse((self.out / "abcd0010.25o").exists())
        self.assertEqual(self.stats["failed"], 1)
        self.assertEqual(self.stats["downloaded"], 0)

    def test_igmas_environment_failure_does_not_replace_existing_archive(self):
        archive = self.out / "abcd0010.25d.Z"
        self.write_archive(archive)
        original = archive.read_bytes()

        def wget(cmd, cwd=None, ignore_error=False):
            (Path(cwd) / archive.name).write_bytes(b"unusable new download")
            return True

        with (
            mock.patch.object(download.subprocess, "run", side_effect=FileNotFoundError("gzip")),
            mock.patch.object(download, "run_cmd", side_effect=wget),
        ):
            self.igmas()
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(self.stats["failed"], 1)

    def test_igmas_parallel_exact_downloads_have_isolated_staging(self):
        barrier = threading.Barrier(2)
        directories = []
        lock = threading.Lock()

        def wget(cmd, cwd=None, ignore_error=False):
            self.assertEqual(shlex.split(cmd)[0], "wget")
            stage = Path(cwd)
            with lock:
                directories.append(stage)
            doy = "001" if "/001/" in cmd else "002"
            # Each wildcard response includes a station outside the requested list.
            for station in ("abcd", "efgh"):
                self.write_archive(stage / f"{station}{doy}0.25d.Z")
            # Even a stray file returned by a source must not reach another day.
            (stage / "zzzz0030.25d").write_bytes(CRINEX)
            barrier.wait(timeout=10)
            return True

        with mock.patch.object(download, "run_cmd", side_effect=wget):
            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(self.igmas, day) for day in (DAY, DAY + dt.timedelta(days=1))]
                for future in futures:
                    future.result(timeout=15)
        self.assertEqual(len(set(directories)), 2)
        self.assertNotIn(self.out, directories)
        for doy in ("001", "002"):
            for station in ("abcd", "efgh"):
                self.assertEqual((self.out / f"{station}{doy}0.25o").read_bytes(), RINEX)
        self.assertFalse((self.out / "zzzz0030.25d").exists())
        self.assertEqual(self.stats["downloaded"], 2)
        self.assertEqual(self.stats["failed"], 0)


if __name__ == "__main__":
    unittest.main()
