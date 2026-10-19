import datetime as dt
import unittest
from unittest import mock

import download


class DateValidationTests(unittest.TestCase):
    def test_valid_year_boundaries_and_leap_years(self):
        cases = [
            (1, 1, dt.date(1, 1, 1)),
            (9999, 365, dt.date(9999, 12, 31)),
            (2025, 365, dt.date(2025, 12, 31)),
            (2024, 60, dt.date(2024, 2, 29)),
            (2024, 366, dt.date(2024, 12, 31)),
            (1900, 365, dt.date(1900, 12, 31)),
            (2000, 366, dt.date(2000, 12, 31)),
        ]
        for year, doy, expected in cases:
            with self.subTest(year=year, doy=doy):
                self.assertEqual(download.date_from_year_doy(year, doy), expected)

    def test_invalid_year_does_not_roll_over(self):
        for year in (-1, 0, 10000):
            with self.subTest(year=year):
                with self.assertRaisesRegex(ValueError, "Year must be between 1 and 9999"):
                    download.date_from_year_doy(year, 1)

    def test_invalid_doy_does_not_roll_over(self):
        for year, doy, maximum in [
            (2025, -1, 365),
            (2025, 0, 365),
            (2025, 366, 365),
            (2024, 367, 366),
            (1900, 366, 365),
            (9999, 366, 365),
        ]:
            with self.subTest(year=year, doy=doy):
                with self.assertRaisesRegex(ValueError, f"between 1 and {maximum}"):
                    download.date_from_year_doy(year, doy)

    def test_cli_rejects_invalid_dates_before_output_or_download(self):
        for date_args in [
            ["2025", "0", "2025", "1"],
            ["2025", "1", "2025", "366"],
            ["0", "1", "2025", "1"],
            ["2025", "1", "10000", "1"],
        ]:
            with self.subTest(date_args=date_args):
                with (
                    mock.patch("sys.argv", ["download.py", "igs", *date_args]),
                    mock.patch.object(download, "load_sources", return_value={}),
                    mock.patch.object(download, "load_site_map", return_value={}),
                    mock.patch.object(download, "preflight_output_dirs") as output_dirs,
                    mock.patch.object(download, "run_date_jobs") as run_jobs,
                ):
                    with self.assertRaisesRegex(SystemExit, "Invalid date:"):
                        download.main()
                    output_dirs.assert_not_called()
                    run_jobs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
