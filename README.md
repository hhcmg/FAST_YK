# FAST_YK

`FAST_YK` is a simplified GNSS downloader built for local workflows under `/data/yk/software/FAST_YK`.

It combines:
- FAST's multi-source URL templates
- the practical directory layout from `/data/yk/tools/download.py`
- optional short-name renaming controlled by a global default or CLI flag

## Main Script

`/data/yk/software/FAST_YK/download.py`

## Source Config

`/data/yk/software/FAST_YK/download_sources.json`

## Default Paths

- Data root: `/data2/data`
- MGEX site list: `global_B1a` next to `download.py`
- iGMAS site list: `global_B1I` next to `download.py`
- Helper binaries: `/data/yk/software/FAST_YK/bin`

root    = 你给数据总目录，脚本补 rinex/YYYY/DOY
product = 你给产品目录，脚本补 YYYY/DOY
exact   = 你给最终目录，脚本什么都不补

# root
--output-dir /data/yk/work_hl/data
# -> /data/yk/work_hl/data/rinex/2025/152

# product
--output-dir /data/yk/work_hl/data/rinex --path-mode product
# -> /data/yk/work_hl/data/rinex/2025/152

# exact
--output-dir /data/yk/work_hl/data/rinex/2025/152 --path-mode exact
# -> /data/yk/work_hl/data/rinex/2025/152

Note: the active defaults are defined in `download.py`. Use `--data-dir` or
`--output-dir` to override the output path for one run.

## Supported Types

- Precise orbit: `igs`, `whu`, `sha`, `cod`, `gfz`
- Precise clock: `igsclk`, `whuclk`, `shaclk`, `codclk`, `gfzclk`
- Broadcast ephemeris: `brdc`, `brdm`
- Weekly SINEX: `snx`
- CAS rapid differential code bias: `cas_dcb_rap`
- WHU final observable-specific bias: `whu_osb_finl`
- Station observation: `mgex`, `igmas`

Aliases:
- `wum` -> `whu`
- `wumclk` -> `whuclk`
- `igsobs` -> `mgex`

## Examples

```bash
python3 /data/yk/software/FAST_YK/download.py whuclk --no-rename --workers 8 2025 98 2025 173
nohup python -u /data/yk/software/FAST_YK/download.py whu --no-rename --workers 8 2025 98 2025 173 > /data/yk/software/FAST_YK/log/download_sp3.txt 2>&1 & # clk

nohup python -u  /data/yk/software/FAST_YK/download.py mgex 2025 149 2025 149 --rename --workers 8 > /data/yk/software/FAST_YK/log/download_mgex.txt 2>&1 & ##IGS测站

nohup python -u  /data/yk/software/FAST_YK/download.py mgex 2025 152 2025 152 --rename --workers 8 --output-dir /data/yk/work_hl/data/rinex --path-mode product > /data/yk/software/FAST_YK/log/download_mgex.txt 2>&1 & #IGS测站，传入 rinex 产品目录

nohup python -u   /data/yk/software/FAST_YK/download.py brdm 2025 97 2025 174 --rename --workers 8 > /data/yk/software/FAST_YK/log/download_brdm.txt 2>&1 &  #广播星历

python /data/yk/software/FAST_YK/download.py snx 2023 6 2023 6 --no-rename
python /data/yk/software/FAST_YK/download.py brdc,snx 2024 200 2024 200
python /data/yk/software/FAST_YK/download.py cas_dcb_rap 2023 128 2023 130 --no-rename
python /data/yk/software/FAST_YK/download.py whu_osb_finl 2025 140 2025 149 --no-rename
nohup python -u  /data/yk/software/FAST_YK/download.py mgex 2024 150 2024 150 --no-rename --igs-site-list /path/to/site.list > /data/yk/software/FAST_YK/log/log3.txt 2>&1 &
```

先运行python /data/yk/software/FAST_YK/download.py brdm 2022 234 2022 234 --no-rename
再运行python /data/yk/software/FAST_YK/download.py brdm 2022 234 2022 234 --rename
会导致文件已存在而无法下载，但重命名成功

## Notes

- `gfz` and `gfzclk` are type labels only: their GBM remote URLs, local `sp3/gbm/<year>` directory, and optional `gbm` short-name prefix are unchanged.
- `whu_osb_finl` downloads final WHU OSB products into `<data-dir>/osb` and keeps the decompressed long filename.
- Before downloading, the script creates/checks every output directory needed for the requested date/type range. If any output directory is not writable, it prints an error and exits before starting `wget`.
- Output path control:
  - `--path-mode root` (default): `--data-dir`/`--output-dir` is the data root. Example: `--output-dir /data/yk/work_hl/data` with `mgex` writes to `/data/yk/work_hl/data/rinex/YYYY/DOY`.
  - `--path-mode product`: the path is already the product directory. Example: `--output-dir /data/yk/work_hl/data/rinex --path-mode product` with `mgex` writes to `/data/yk/work_hl/data/rinex/YYYY/DOY`.
  - `--path-mode exact`: the path is the final download directory. Example: `--output-dir /tmp/mgex_one_day --path-mode exact` writes files directly into `/tmp/mgex_one_day`.
- When renaming is enabled, precise products use short prefixes like `igs`, `wum`, `sha`, `cod`, `gbm`.
- Downloads run with 4 parallel date workers by default. Use `--workers N` to choose the concurrency level, or `--workers 1` for the original serial behavior. Different data types for the same date still run sequentially to avoid output-file conflicts.
- `cas_dcb_rap` downloads from NASA CDDIS HTTPS. Earthdata credentials are read from the `cddis` profile in `.download_credentials.json`; session cookies use `~/.urs_cookies`. With `--rename`, files are named like `CAS22430.BSX`; without renaming, files are normalized to names like `CAS0MGXRAP_20230010000_01D_01D_DCB.BSX`.
- `mgex` uses `GCRE_MGEX_obs`. A local `IGSNetwork.csv` lookup is used to translate 4-char station names into 9-char long MGEX names.
- `igmas` keeps the original wildcard download logic but only downloads once per day when requested stations are missing.
- Observation downloads are complete only when a `.rnx` or `.YYo` file has an observation RINEX header and `END OF HEADER`. Cached `.crx`, `.YYd`, and compressed inputs are processed again rather than counted as finished observations.
- `crx2rnx` writes to a temporary file. Only a successful conversion (including its documented warning status) with a valid observation header is published; failed conversions keep the Compact RINEX input for retry or inspection.
- iGMAS downloads use a separate temporary directory for each date/source and process only that day's files. This also applies to parallel downloads with `--path-mode exact`; all stations in the daily wildcard download are still converted.
- Corrupt archives are removed so the current source or a fallback can be tried again. Decompression failures caused by the local environment retain the archive. Failed download attempts are discarded and retried from scratch on the next source/run; partial downloads are not reused.
- Year/day-of-year arguments are validated before creating output directories or starting downloads. Day 366 is accepted only in leap years.
- The script prints a final summary with expected targets, newly downloaded files, existing files skipped, and failed targets. Each failed target includes its date, day of year, expected filename, and output directory.

## Credentials

Manage all configured usernames and passwords in the local file
`.download_credentials.json`, next to `download.py`. It is ignored by Git.
The three profiles are:

| Profile | Use |
| --- | --- |
| `cddis` | Earthdata login for CDDIS HTTPS downloads |
| `cddis_ftp` | Anonymous FTP/FTPS login parameters for the CDDIS FTP sources |
| `igmas` | iGMAS FTP login |

For a new checkout, create the local file from the empty template and fill in
each profile's `username` and `password`:

```bash
cp download_credentials.example.json .download_credentials.json
chmod 600 .download_credentials.json
```

Existing credentials in this workspace have been migrated; edit
`.download_credentials.json` directly. The old `.cddis_credentials.json` is no
longer read. CDDIS session cookies remain in `~/.urs_cookies`.

Authenticated entries in `download_sources.json` reference a profile instead of
containing login values:

```json
{
  "url": "ftp://server.example/path/to/file.gz",
  "credentials": "igmas"
}
```

Sources without credentials can remain URL strings. New profiles can be added
to the local credential file and referenced in the same way. A missing or
invalid profile causes that source to be skipped with a warning, allowing the
next fallback source to run. Commit only the empty example file; ignoring local
credentials does not remove passwords already present in earlier Git commits.
Commands and authenticated wget diagnostics redact login values. Authenticated
wget diagnostics are printed after that download command finishes.

## Offline regression tests

Run from the project directory:

```bash
python3 -B -m unittest discover -s tests -v
```

Tests use temporary files, simulated download sources, and local `gzip`/`bin/crx2rnx`; they do not contact remote servers.
