#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FAST_YK simplified downloader.

Usage:
  python download.py <data_type> <year_begin> <doy_begin> <year_end> <doy_end> [--rename|--no-rename]

Examples:
  python download.py igs 2024 100 2024 100
  python download.py gbmclk 2022 234 2022 234 --rename
  python download.py mgex 2024 150 2024 150 --igs-site-list /path/to/site.list
"""

import argparse
import csv
import datetime as dt
import json
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parent
BIN_DIR = ROOT_DIR / "bin"
SOURCE_JSON = ROOT_DIR / "download_sources.json"

# Global defaults
DIR_DATA = "/data/yk/work_hl/data"
DIR_DATA = "/data2/data"
IGS_SITE_LIST = "/data/yk/software/FAST_YK/global_B1I"
IGMAS_SITE_LIST = "/data/yk/software/FAST_YK/global_B1I"
RENAME_FILES = False
WGET_TIMEOUT = 30
WGET_RETRIES = 5

PRODUCT_PREFIXES = {
    "igs": "igs",
    "whu": "wum",
    "sha": "sha",
    "cod": "cod",
    "gbm": "gbm",
}

BROADCAST_PREFIXES = {
    "brdc": "brdc",
    "brdm": "brdm",
}

TYPE_ALIASES = {
    "wum": "whu",
    "wumclk": "whuclk",
    "gfz": "gbm",
    "gfzclk": "gbmclk",
    "igsobs": "mgex",
}

TYPE_META = {
    "igs": {"mode": "precise", "source_key": "igs", "family": "igs", "suffix": "sp3"},
    "igsclk": {"mode": "precise", "source_key": "igsclk", "family": "igs", "suffix": "clk"},
    "whu": {"mode": "precise", "source_key": "whu", "family": "whu", "suffix": "sp3"},
    "whuclk": {"mode": "precise", "source_key": "whuclk", "family": "whu", "suffix": "clk"},
    "sha": {"mode": "precise", "source_key": "sha", "family": "sha", "suffix": "sp3"},
    "shaclk": {"mode": "precise", "source_key": "shaclk", "family": "sha", "suffix": "clk"},
    "cod": {"mode": "precise", "source_key": "cod", "family": "cod", "suffix": "sp3"},
    "codclk": {"mode": "precise", "source_key": "codclk", "family": "cod", "suffix": "clk"},
    "gbm": {"mode": "precise", "source_key": "gbm", "family": "gbm", "suffix": "sp3"},
    "gbmclk": {"mode": "precise", "source_key": "gbmclk", "family": "gbm", "suffix": "clk"},
    "brdc": {"mode": "broadcast", "source_key": "brdc"},
    "brdm": {"mode": "broadcast", "source_key": "brdm"},
    "snx": {"mode": "snx", "source_key": "snx"},
    "mgex": {"mode": "obs", "source_key": "mgex"},
    "igmas": {"mode": "igmas", "source_key": "igmas"},
}


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def run_cmd(cmd, cwd=None, ignore_error=False):
    print(f"[CMD] {cmd}")
    completed = subprocess.run(cmd, cwd=cwd, shell=True, text=True)
    if completed.returncode != 0 and not ignore_error:
        raise RuntimeError(f"Command failed: {cmd}")
    return completed.returncode == 0


def file_exists_and_nonempty(path):
    return path.exists() and path.is_file() and path.stat().st_size > 0


def rm_if_empty(path):
    try:
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


def init_stats():
    return {"expected": 0, "downloaded": 0, "existing": 0, "failed": 0}


def record_result(stats, status):
    stats["expected"] += 1
    if status not in stats:
        stats[status] = 0
    stats[status] += 1


def maybe_rename(src, dst, enabled):
    if not enabled or src == dst or not src.exists():
        return src
    dst.unlink(missing_ok=True)
    src.rename(dst)
    return dst


def strip_compression_suffix(path):
    path = Path(path)
    if path.suffix in {".gz", ".Z"}:
        return path.with_suffix("")
    return path


def extract_url(download_expr):
    for token in reversed(shlex.split(download_expr)):
        if token.startswith(("ftp://", "ftps://", "http://", "https://")):
            return token
    return ""


def remote_basename(download_expr):
    url = extract_url(download_expr)
    if not url:
        return ""
    return Path(urlparse(url).path).name


def date_from_year_doy(year, doy):
    return dt.date(year, 1, 1) + dt.timedelta(days=doy - 1)


def mjd_from_date(date_obj):
    mjd_epoch = dt.date(1858, 11, 17)
    return (date_obj - mjd_epoch).days


def date_from_mjd(mjd):
    mjd_epoch = dt.date(1858, 11, 17)
    return mjd_epoch + dt.timedelta(days=mjd)


def gps_week_day_from_date(date_obj):
    gps_epoch = dt.date(1980, 1, 6)
    delta = (date_obj - gps_epoch).days
    week = delta // 7
    day = delta % 7
    return week, day


def week_start_info(date_obj):
    gps_week, gps_day = gps_week_day_from_date(date_obj)
    week_start = date_obj - dt.timedelta(days=gps_day)
    return gps_week, gps_day, week_start.year, week_start.timetuple().tm_yday


def load_sources():
    with open(SOURCE_JSON, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def load_site_map():
    site_map = {}
    csv_file = BIN_DIR / "IGSNetwork.csv"
    if not csv_file.exists():
        return site_map

    with open(csv_file, "r", encoding="utf-8") as file_obj:
        reader = csv.DictReader(file_obj)
        for row in reader:
            station_name = row["#StationName"].strip().upper()
            short_name = station_name[:4].lower()
            site_map.setdefault(short_name, station_name)
    return site_map


def read_sites(site_list_file):
    site_path = Path(site_list_file)
    if not site_path.exists():
        raise FileNotFoundError(f"Site list not found: {site_list_file}")
    return site_path.read_text(encoding="utf-8").split()


def normalize_site(site_token, site_map):
    token = site_token.strip()
    if len(token) == 4:
        short_name = token.lower()
        long_name = site_map.get(short_name, "")
        return {
            "input": token,
            "short_lower": short_name,
            "short_upper": short_name.upper(),
            "long_upper": long_name,
        }
    if len(token) == 9:
        return {
            "input": token,
            "short_lower": token[:4].lower(),
            "short_upper": token[:4].upper(),
            "long_upper": token.upper(),
        }
    return {
        "input": token,
        "short_lower": "",
        "short_upper": "",
        "long_upper": "",
    }


def fill_template(template, date_obj, site_info=None):
    gps_week, gps_day, week0year, week0doy = week_start_info(date_obj)
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    values = {
        "<YEAR>": f"{date_obj.year:04d}",
        "<YYYY>": f"{date_obj.year:04d}",
        "<YY>": f"{date_obj.year % 100:02d}",
        "<DOY>": doy,
        "<GPSW>": str(gps_week),
        "<GPSWD>": f"{gps_week}{gps_day}",
        "<WEEK0YEAR>": f"{week0year:04d}",
        "<WEEK0DOY>": f"{week0doy:03d}",
    }
    filled = template
    for key, value in values.items():
        filled = filled.replace(key, value)

    if site_info is not None:
        filled = filled.replace("<SITE>", site_info["short_lower"])
        filled = filled.replace("<SITE_SHORT>", site_info["short_upper"])
        if "<SITE_LONG>" in filled:
            if not site_info["long_upper"]:
                return None
            filled = filled.replace("<SITE_LONG>", site_info["long_upper"])

    return filled


def build_wget_cmd(download_expr):
    return (
        f"wget -N -nH -c -T {WGET_TIMEOUT} -t {WGET_RETRIES} "
        f"{download_expr}"
    )


def decompress_file(path, cwd):
    path = Path(path)
    if path.suffix not in {".gz", ".Z"}:
        return path
    run_cmd(f"gzip -d -f {shlex.quote(path.name)}", cwd=cwd, ignore_error=True)
    return strip_compression_suffix(path)


def candidate_paths(out_dir, basename, renamed_name=None):
    paths = []
    if renamed_name:
        paths.append(Path(out_dir) / renamed_name)
    if basename:
        raw = Path(out_dir) / basename
        paths.append(raw)
        paths.append(strip_compression_suffix(raw))
    return paths


def first_existing_path(paths):
    for path in paths:
        if file_exists_and_nonempty(path):
            return path
    return None


def download_with_fallback(resolved_sources, out_dir, renamed_name=None, rename_enabled=False):
    if renamed_name and file_exists_and_nonempty(Path(out_dir) / renamed_name):
        print(f"{renamed_name} exist")
        return "existing"

    for source in resolved_sources:
        basename = remote_basename(source)
        current = first_existing_path(candidate_paths(out_dir, basename, renamed_name))
        status = "existing"
        if current is None:
            run_cmd(build_wget_cmd(source), cwd=out_dir, ignore_error=True)
            current = first_existing_path(candidate_paths(out_dir, basename, renamed_name))
            status = "downloaded"
        if current is None:
            continue

        current = decompress_file(current, cwd=out_dir)
        if renamed_name:
            current = maybe_rename(current, Path(out_dir) / renamed_name, rename_enabled)
        rm_if_empty(current)

        if file_exists_and_nonempty(current):
            return status

    return "failed"


def resolve_sources(source_defs, source_key, date_obj, site_info=None):
    resolved = []
    for template in source_defs[source_key]:
        filled = fill_template(template, date_obj, site_info=site_info)
        if filled:
            resolved.append(filled)
    return resolved


def precise_target_name(meta, date_obj):
    gps_week, gps_day = gps_week_day_from_date(date_obj)
    prefix = PRODUCT_PREFIXES[meta["family"]]
    return f"{prefix}{gps_week}{gps_day}.{meta['suffix']}"


def precise_output_dir(meta, date_obj, data_dir):
    prefix = PRODUCT_PREFIXES[meta["family"]]
    return Path(data_dir) / "sp3" / prefix / f"{date_obj.year:04d}"


def handle_precise(meta, date_obj, source_defs, data_dir, rename_enabled, stats):
    out_dir = precise_output_dir(meta, date_obj, data_dir)
    ensure_dir(out_dir)
    renamed_name = precise_target_name(meta, date_obj) if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    record_result(stats, status)
    if status == "failed":
        print(f"[WARN] no file downloaded for {meta['source_key']} {date_obj.isoformat()}")


def handle_broadcast(meta, date_obj, source_defs, data_dir, rename_enabled, stats):
    out_dir = Path(data_dir) / "brdc" / f"{date_obj.year:04d}"
    ensure_dir(out_dir)
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    short_prefix = BROADCAST_PREFIXES[meta["source_key"]]
    renamed_name = f"{short_prefix}{doy}0.{yy}p" if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    record_result(stats, status)
    if status == "failed":
        print(f"[WARN] no file downloaded for {meta['source_key']} {date_obj.isoformat()}")


def handle_snx(meta, date_obj, source_defs, data_dir, rename_enabled, seen_weeks, stats):
    gps_week, _, _, _ = week_start_info(date_obj)
    if gps_week in seen_weeks:
        return
    seen_weeks.add(gps_week)

    out_dir = Path(data_dir) / "snx"
    ensure_dir(out_dir)
    yy = f"{date_obj.year % 100:02d}"
    renamed_name = f"igs{yy}P{gps_week}.snx" if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    record_result(stats, status)
    if status == "failed":
        print(f"[WARN] no file downloaded for {meta['source_key']} {date_obj.isoformat()}")


def crx2rnx_binary():
    local_bin = BIN_DIR / "crx2rnx"
    if local_bin.exists():
        return local_bin
    return Path("crx2rnx")


def convert_crx_file(crx_path, out_dir, rename_enabled, short_d_name=None):
    converter = crx2rnx_binary()
    input_path = crx_path
    if rename_enabled and short_d_name:
        input_path = maybe_rename(crx_path, Path(out_dir) / short_d_name, True)
    run_cmd(f"{shlex.quote(str(converter))} {shlex.quote(input_path.name)}", cwd=out_dir, ignore_error=True)
    if rename_enabled and short_d_name:
        input_path.unlink(missing_ok=True)


def existing_obs_output(out_dir, site_info, date_obj):
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    short_o = Path(out_dir) / f"{site_info['short_lower']}{doy}0.{yy}o"
    if file_exists_and_nonempty(short_o):
        return short_o
    short_d = Path(out_dir) / f"{site_info['short_lower']}{doy}0.{yy}d"
    if file_exists_and_nonempty(short_d):
        return short_d
    if file_exists_and_nonempty(short_d.with_suffix(short_d.suffix + ".gz")):
        return short_d.with_suffix(short_d.suffix + ".gz")
    if file_exists_and_nonempty(short_d.with_suffix(short_d.suffix + ".Z")):
        return short_d.with_suffix(short_d.suffix + ".Z")
    if site_info["long_upper"]:
        pattern = f"{site_info['long_upper']}_*{date_obj.year:04d}{doy}0000_01D_30S_MO*"
        for path in Path(out_dir).glob(pattern):
            if file_exists_and_nonempty(path):
                return path
    return None


def handle_mgex_obs(meta, date_obj, source_defs, data_dir, rename_enabled, site_list_file, site_map, stats):
    out_dir = Path(data_dir) / "rinex" / f"{date_obj.year:04d}" / f"{date_obj.timetuple().tm_yday:03d}"
    ensure_dir(out_dir)

    for token in read_sites(site_list_file):
        site_info = normalize_site(token, site_map)
        if not site_info["short_lower"]:
            print(f"[WARN] invalid site token: {token}")
            record_result(stats, "failed")
            continue
        if existing_obs_output(out_dir, site_info, date_obj):
            print(f"{token} exist")
            record_result(stats, "existing")
            continue

        resolved = resolve_sources(source_defs, meta["source_key"], date_obj, site_info=site_info)
        status = "failed"
        for source in resolved:
            basename = remote_basename(source)
            current = first_existing_path(candidate_paths(out_dir, basename))
            current_status = "existing"
            if current is None:
                run_cmd(build_wget_cmd(source), cwd=out_dir, ignore_error=True)
                current = first_existing_path(candidate_paths(out_dir, basename))
                current_status = "downloaded"
            if current is None:
                continue

            current = decompress_file(current, cwd=out_dir)
            rm_if_empty(current)
            if not file_exists_and_nonempty(current):
                continue

            if current.suffix == ".crx":
                doy = f"{date_obj.timetuple().tm_yday:03d}"
                yy = f"{date_obj.year % 100:02d}"
                short_d = f"{site_info['short_lower']}{doy}0.{yy}d"
                convert_crx_file(current, out_dir, rename_enabled, short_d_name=short_d)
            if existing_obs_output(out_dir, site_info, date_obj):
                status = current_status
            else:
                status = "failed"
            break

        record_result(stats, status)
        if status == "failed":
            print(f"[WARN] no MGEX obs downloaded for site {token} on {date_obj.isoformat()}")


def handle_igmas(meta, date_obj, source_defs, data_dir, rename_enabled, site_list_file, stats):
    out_dir = Path(data_dir) / "rinex" / f"{date_obj.year:04d}" / f"{date_obj.timetuple().tm_yday:03d}"
    ensure_dir(out_dir)
    yy = f"{date_obj.year % 100:02d}"
    doy = f"{date_obj.timetuple().tm_yday:03d}"

    sites = read_sites(site_list_file)
    missing_sites = []
    for token in sites:
        site4 = token[:4].lower()
        target = out_dir / f"{site4}{doy}0.{yy}o"
        if file_exists_and_nonempty(target):
            record_result(stats, "existing")
        else:
            missing_sites.append(site4)

    if missing_sites:
        resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
        for source in resolved:
            run_cmd(build_wget_cmd(source), cwd=out_dir, ignore_error=True)

        for z_file in out_dir.glob(f"*.{yy}d.Z"):
            rm_if_empty(z_file)
            if file_exists_and_nonempty(z_file):
                decompress_file(z_file, cwd=out_dir)

        for d_file in list(out_dir.glob(f"*.{yy}d")):
            rm_if_empty(d_file)
            if file_exists_and_nonempty(d_file):
                run_cmd(f"{shlex.quote(str(crx2rnx_binary()))} {shlex.quote(d_file.name)}", cwd=out_dir, ignore_error=True)
                d_file.unlink(missing_ok=True)

    for site4 in missing_sites:
        target = out_dir / f"{site4}{doy}0.{yy}o"
        if file_exists_and_nonempty(target):
            record_result(stats, "downloaded")
        else:
            record_result(stats, "failed")
            print(f"[WARN] no iGMAS obs downloaded for site {site4} on {date_obj.isoformat()}")


def canonical_type(dtype):
    dtype = dtype.lower().strip()
    return TYPE_ALIASES.get(dtype, dtype)


def parse_args():
    parser = argparse.ArgumentParser(description="FAST_YK simplified downloader")
    parser.add_argument("data_type", nargs="?", help="comma-separated data types, e.g. igs,gbmclk,brdc,mgex")
    parser.add_argument("year_begin", nargs="?", type=int)
    parser.add_argument("doy_begin", nargs="?", type=int)
    parser.add_argument("year_end", nargs="?", type=int)
    parser.add_argument("doy_end", nargs="?", type=int)
    parser.add_argument("--data-dir", default=DIR_DATA, help="output root directory")
    parser.add_argument("--igs-site-list", default=IGS_SITE_LIST, help="station list for mgex downloads")
    parser.add_argument("--igmas-site-list", default=IGMAS_SITE_LIST, help="station list for igmas downloads")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rename", dest="rename", action="store_true", help="force enable renaming")
    group.add_argument("--no-rename", dest="rename", action="store_false", help="force disable renaming")
    parser.set_defaults(rename=None)
    parser.add_argument("--list-types", action="store_true", help="show supported data types and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.list_types:
        print(", ".join(sorted(TYPE_META)))
        return

    required = [args.data_type, args.year_begin, args.doy_begin, args.year_end, args.doy_end]
    if any(item is None for item in required):
        raise SystemExit("data_type year_begin doy_begin year_end doy_end are required unless --list-types is used.")

    rename_enabled = RENAME_FILES if args.rename is None else args.rename
    source_defs = load_sources()
    site_map = load_site_map()
    stats = init_stats()

    requested_types = [canonical_type(item) for item in args.data_type.split(",") if item.strip()]
    invalid = [item for item in requested_types if item not in TYPE_META]
    if invalid:
        raise SystemExit(f"Unsupported data type(s): {', '.join(invalid)}")

    start_date = date_from_year_doy(args.year_begin, args.doy_begin)
    end_date = date_from_year_doy(args.year_end, args.doy_end)
    if end_date < start_date:
        raise SystemExit("End date must be greater than or equal to start date.")

    seen_weeks = set()
    mjd = mjd_from_date(start_date)
    mjd_end = mjd_from_date(end_date)
    while mjd <= mjd_end:
        date_obj = date_from_mjd(mjd)
        for dtype in requested_types:
            meta = TYPE_META[dtype]
            if meta["mode"] == "precise":
                handle_precise(meta, date_obj, source_defs, args.data_dir, rename_enabled, stats)
            elif meta["mode"] == "broadcast":
                handle_broadcast(meta, date_obj, source_defs, args.data_dir, rename_enabled, stats)
            elif meta["mode"] == "snx":
                handle_snx(meta, date_obj, source_defs, args.data_dir, rename_enabled, seen_weeks, stats)
            elif meta["mode"] == "obs":
                handle_mgex_obs(meta, date_obj, source_defs, args.data_dir, rename_enabled, args.igs_site_list, site_map, stats)
            elif meta["mode"] == "igmas":
                handle_igmas(meta, date_obj, source_defs, args.data_dir, rename_enabled, args.igmas_site_list, stats)
        mjd += 1

    print("")
    print("Download Summary")
    print(f"应下文件个数: {stats['expected']}")
    print(f"实际下载个数: {stats['downloaded']}")
    print(f"已存在无需下载个数: {stats['existing']}")
    print(f"下载失败个数: {stats['failed']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
