#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FAST_YK simplified downloader.

Usage:
  python download.py <data_type> <year_begin> <doy_begin> <year_end> <doy_end> [--rename|--no-rename]

Examples:
  python download.py igs 2024 100 2024 100
  python download.py gfzclk 2022 234 2022 234 --rename
  python download.py whu_osb_finl 2025 134 2025 139
  python download.py mgex 2024 150 2024 150 --igs-site-list /path/to/site.list
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parent
BIN_DIR = ROOT_DIR / "bin"
SOURCE_JSON = ROOT_DIR / "download_sources.json"

# Global defaults
DIR_DATA = "/data/yk/software/GREAT-PVT-main/GPSsol/data"
DIR_DATA = "/data2/data"

# IGS测站
IGS_SITE_LIST = str(ROOT_DIR / "global_B1a")

# IGMAS测站
IGMAS_SITE_LIST = str(ROOT_DIR / "global_B1I")

RENAME_FILES = False
DEFAULT_WORKERS = 4
WGET_TIMEOUT = 30
WGET_RETRIES = 5
CDDIS_COOKIE_FILE = Path.home() / ".urs_cookies"
CREDENTIAL_FILE = ROOT_DIR / ".download_credentials.json"
STATS_LOCK = threading.Lock()
SENSITIVE_WGET_OPTIONS = {
    "--user", "--password", "--ftp-user", "--ftp-password",
    "--http-user", "--http-password", "--proxy-user", "--proxy-password",
}

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
    "gfz": {"mode": "precise", "source_key": "gfz", "family": "gbm", "suffix": "sp3"},
    "gfzclk": {"mode": "precise", "source_key": "gfzclk", "family": "gbm", "suffix": "clk"},
    "brdc": {"mode": "broadcast", "source_key": "brdc"},
    "brdm": {"mode": "broadcast", "source_key": "brdm"},
    "snx": {"mode": "snx", "source_key": "snx"},
    "mgex": {"mode": "obs", "source_key": "mgex"},
    "igmas": {"mode": "igmas", "source_key": "igmas"},
    "cas_dcb_rap": {"mode": "cas_dcb", "source_key": "cas_dcb_rap"},
    "whu_osb_finl": {
        "mode": "osb",
        "source_key": "GCRE_WHU_F_osb",
        "label": "whu_osb_finl",
    },
}


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def redact_sensitive_cmd(cmd):
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return "<unparseable command omitted>"

    redacted = []
    hide_next = False
    for token in tokens:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        if token in SENSITIVE_WGET_OPTIONS:
            redacted.append(token)
            hide_next = True
            continue
        option, separator, _ = token.partition("=")
        if separator and option in SENSITIVE_WGET_OPTIONS:
            redacted.append(f"{option}=<redacted>")
            continue
        redacted.append(re.sub(r"((?:ftp|ftps|http|https)://)[^/?#\s]*@", r"\1<redacted>@", token))
    return " ".join(shlex.quote(token) for token in redacted)


def command_sensitive_values(cmd):
    """Collect credentials to also redact wget's own diagnostic output."""
    values = set()
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return values
    hide_next = False
    for token in tokens:
        if hide_next:
            values.add(token)
            hide_next = False
            continue
        if token in SENSITIVE_WGET_OPTIONS:
            hide_next = True
            continue
        option, separator, value = token.partition("=")
        if separator and option in SENSITIVE_WGET_OPTIONS:
            values.add(value)
        if token.startswith(("ftp://", "ftps://", "http://", "https://")):
            authority = token.split("://", 1)[1].split("/", 1)[0]
            if "@" in authority:
                for part in authority.rsplit("@", 1)[0].split(":", 1):
                    values.add(part)
                    values.add(unquote(part))
    # wget can display percent-encoded credentials in a URL.
    values.update(quote(value, safe="") for value in tuple(values))
    values.discard("")
    return values


def run_cmd(cmd, cwd=None, ignore_error=False):
    safe_cmd = redact_sensitive_cmd(cmd)
    print(f"[CMD] {safe_cmd}")
    sensitive_values = command_sensitive_values(cmd)
    output_options = {
        "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "errors": "replace",
    } if sensitive_values else {}
    try:
        completed = subprocess.run(cmd, cwd=cwd, shell=True, text=True, **output_options)
    except (OSError, subprocess.SubprocessError):
        if ignore_error:
            print(f"[WARN] could not execute command: {safe_cmd}")
            return False
        raise RuntimeError(f"Could not execute command: {safe_cmd}") from None
    if sensitive_values:
        output = getattr(completed, "stdout", "") or ""
        pattern = "|".join(re.escape(value) for value in sorted(sensitive_values, key=len, reverse=True))
        output = re.sub(pattern, "<redacted>", output)
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
    if completed.returncode != 0 and not ignore_error:
        raise RuntimeError(f"Command failed: {safe_cmd}")
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
    return {
        "expected": 0,
        "downloaded": 0,
        "existing": 0,
        "failed": 0,
        "failed_targets": [],
    }


def record_result(stats, status, target=None):
    with STATS_LOCK:
        stats["expected"] += 1
        if status not in stats:
            stats[status] = 0
        stats[status] += 1
        if status == "failed" and target is not None:
            stats["failed_targets"].append(target)


def make_target_info(data_type, date_obj, output_path, site=None, note=None):
    target = {
        "data_type": data_type,
        "date": date_obj.isoformat(),
        "doy": date_obj.timetuple().tm_yday,
        "file": Path(output_path).name,
        "output_dir": str(Path(output_path).parent),
    }
    if site:
        target["site"] = site
    if note:
        target["note"] = note
    return target


def expected_download_path(out_dir, resolved_sources, renamed_name=None):
    if renamed_name:
        return Path(out_dir) / renamed_name
    if resolved_sources:
        basename = remote_basename(resolved_sources[0])
        if basename:
            return strip_compression_suffix(Path(out_dir) / basename)
    return Path(out_dir) / "<unknown>"


def print_failed_targets(stats):
    failed_targets = sorted(
        stats["failed_targets"],
        key=lambda item: (
            item["date"],
            item["data_type"],
            item.get("site", ""),
            item["file"],
        ),
    )
    if not failed_targets:
        return

    print("")
    print("Failed Targets")
    for target in failed_targets:
        parts = [
            f"type={target['data_type']}",
            f"date={target['date']}",
            f"doy={target['doy']:03d}",
        ]
        if target.get("site"):
            parts.append(f"site={target['site']}")
        parts.extend(
            [
                f"file={target['file']}",
                f"output_dir={target['output_dir']}",
            ]
        )
        if target.get("note"):
            parts.append(f"note={target['note']}")
        print("- " + " | ".join(parts))


def output_dir_for_layout(data_dir, path_mode, layout, date_obj=None, prefix=None):
    base = Path(data_dir)
    if path_mode == "exact":
        return base

    year = f"{date_obj.year:04d}" if date_obj is not None else None
    doy = f"{date_obj.timetuple().tm_yday:03d}" if date_obj is not None else None

    if layout == "precise":
        parts = []
        if path_mode == "root":
            parts.append("sp3")
        parts.extend([prefix, year])
        return base.joinpath(*parts)
    if layout == "broadcast":
        parts = ["brdc", year] if path_mode == "root" else [year]
        return base.joinpath(*parts)
    if layout == "snx":
        return base / "snx" if path_mode == "root" else base
    if layout == "rinex":
        parts = ["rinex", year, doy] if path_mode == "root" else [year, doy]
        return base.joinpath(*parts)
    if layout == "cas_dcb":
        parts = ["dcb", "cas", year] if path_mode == "root" else [year]
        return base.joinpath(*parts)
    if layout == "osb":
        return base / "osb" if path_mode == "root" else base
    raise ValueError(f"Unsupported output layout: {layout}")


def ensure_writable_dir(path):
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create directory: {exc}"
    if not path.is_dir():
        return False, "path exists but is not a directory"

    probe = path / f".fast_yk_write_test_{os.getpid()}"
    try:
        with open(probe, "w", encoding="utf-8") as file_obj:
            file_obj.write("ok\n")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"directory is not writable: {exc}"
    return True, ""


def output_dirs_for_types(date_obj, requested_types, data_dir, path_mode):
    dirs = []
    for dtype in requested_types:
        meta = TYPE_META[dtype]
        mode = meta["mode"]
        if mode == "precise":
            dirs.append(precise_output_dir(meta, date_obj, data_dir, path_mode))
        elif mode == "broadcast":
            dirs.append(output_dir_for_layout(data_dir, path_mode, "broadcast", date_obj=date_obj))
        elif mode == "snx":
            dirs.append(output_dir_for_layout(data_dir, path_mode, "snx", date_obj=date_obj))
        elif mode in {"obs", "igmas"}:
            dirs.append(output_dir_for_layout(data_dir, path_mode, "rinex", date_obj=date_obj))
        elif mode == "cas_dcb":
            dirs.append(cas_dcb_output_dir(date_obj, data_dir, path_mode))
        elif mode == "osb":
            dirs.append(output_dir_for_layout(data_dir, path_mode, "osb", date_obj=date_obj))
    return dirs


def preflight_output_dirs(jobs, data_dir, path_mode):
    output_dirs = {}
    for date_obj, requested_types in jobs:
        for out_dir in output_dirs_for_types(date_obj, requested_types, data_dir, path_mode):
            output_dirs[str(Path(out_dir))] = Path(out_dir)

    failures = []
    for out_dir in output_dirs.values():
        ok, message = ensure_writable_dir(out_dir)
        if not ok:
            failures.append((out_dir, message))

    if not failures:
        return

    print("[ERROR] Output directory preflight failed. No downloads were started.")
    for out_dir, message in failures:
        print(f"[ERROR] {out_dir}: {message}")
    raise SystemExit(1)


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


def is_cddis_https(download_expr):
    url = extract_url(download_expr)
    if not url:
        return False
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (hostname == "cddis.nasa.gov" or hostname.endswith(".cddis.nasa.gov"))


def load_credentials(profile):
    """Read one account from the shared local file without logging its values."""
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
        print("[WARN] invalid credential profile name; skipping source")
        return None
    try:
        with open(CREDENTIAL_FILE, "r", encoding="utf-8") as file_obj:
            profiles = json.load(file_obj)
    except FileNotFoundError:
        print(f"[WARN] credential file is missing: {CREDENTIAL_FILE}; skipping source requiring '{profile}'")
        return None
    except (json.JSONDecodeError, UnicodeError):
        print(f"[WARN] credential file is not valid UTF-8 JSON: {CREDENTIAL_FILE}; skipping source")
        return None
    except OSError:
        print(f"[WARN] credential file cannot be read: {CREDENTIAL_FILE}; skipping source")
        return None
    if not isinstance(profiles, dict):
        print(f"[WARN] credential file must contain a JSON object: {CREDENTIAL_FILE}; skipping source")
        return None
    credentials = profiles.get(profile)
    if not isinstance(credentials, dict):
        print(f"[WARN] credential profile '{profile}' is missing or invalid in {CREDENTIAL_FILE}; skipping source")
        return None
    username = credentials.get("username")
    password = credentials.get("password")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        print(f"[WARN] credential profile '{profile}' needs nonempty username/password strings; skipping source")
        return None
    return {"username": username.strip(), "password": password}


def cddis_wget_options(download_expr):
    if not is_cddis_https(download_expr):
        return ""
    try:
        CDDIS_COOKIE_FILE.touch(mode=0o600, exist_ok=True)
        CDDIS_COOKIE_FILE.chmod(0o600)
    except OSError:
        pass
    cookie_file = shlex.quote(str(CDDIS_COOKIE_FILE))
    return (
        "--auth-no-challenge "
        f"--load-cookies {cookie_file} "
        f"--save-cookies {cookie_file} "
        "--keep-session-cookies "
    )


def date_from_year_doy(year, doy):
    if not 1 <= year <= 9999:
        raise ValueError(f"Year must be between 1 and 9999: {year}.")
    days_in_year = dt.date(year, 12, 31).timetuple().tm_yday
    if not 1 <= doy <= days_in_year:
        raise ValueError(f"Day of year for {year} must be between 1 and {days_in_year}: {doy}.")
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
    try:
        tokens = shlex.split(download_expr)
    except ValueError:
        print("[WARN] invalid download expression; skipping source")
        return None
    profile = None
    source_tokens = []
    for token in tokens:
        if token.startswith("--credential-profile="):
            if profile is not None:
                print("[WARN] multiple credential profiles for one source; skipping source")
                return None
            profile = token.split("=", 1)[1]
        elif token == "--credential-profile":
            print("[WARN] credential profile must use --credential-profile=name; skipping source")
            return None
        else:
            source_tokens.append(token)
    source_expr = shlex.join(source_tokens)
    if profile is None and is_cddis_https(source_expr):
        profile = "cddis"
    credential_options = ""
    if profile is not None:
        credentials = load_credentials(profile)
        if credentials is None:
            return None
        scheme = urlparse(extract_url(source_expr)).scheme
        if scheme in {"ftp", "ftps"}:
            user_option, password_option = "--ftp-user", "--ftp-password"
        elif scheme in {"http", "https"}:
            user_option, password_option = "--user", "--password"
        else:
            print("[WARN] unsupported protocol for credential profile; skipping source")
            return None
        credential_options = shlex.join([
            user_option, credentials["username"], password_option, credentials["password"],
        ]) + " "
    source_options = cddis_wget_options(source_expr)
    return (
        f"wget -N -nH -c -T {WGET_TIMEOUT} -t {WGET_RETRIES} "
        f"{source_options}{credential_options}{source_expr}"
    )


def decompress_file(path, cwd, delete_input=True):
    path = Path(path)
    if not path.is_absolute() and path.parent == Path("."):
        path = Path(cwd) / path
    if path.suffix not in {".gz", ".Z"}:
        return path if file_exists_and_nonempty(path) else None

    output_path = strip_compression_suffix(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False
        ) as output:
            temporary_path = Path(output.name)
            result = subprocess.run(
                ["gzip", "-dc", "--", str(path)],
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        if result.returncode != 0:
            detail = result.stderr.strip()
            data_errors = (
                "not in gzip format",
                "unexpected end of file",
                "invalid compressed data",
                "corrupt input",
                "trailing garbage ignored",
            )
            if any(message in detail.lower() for message in data_errors):
                path.unlink(missing_ok=True)
                print(f"[WARN] removed corrupt archive {path.name}: {detail}")
            else:
                print(f"[WARN] could not decompress {path.name}; archive retained: {detail}")
            return None
        if not file_exists_and_nonempty(temporary_path):
            path.unlink(missing_ok=True)
            print(f"[WARN] removed archive with empty output: {path.name}")
            return None
        temporary_path.chmod(path.stat().st_mode & 0o777)
        temporary_path.replace(output_path)
        if delete_input:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[WARN] could not remove decompressed archive {path.name}: {exc}")
        return output_path
    except OSError as exc:
        print(f"[WARN] could not decompress {path.name}; archive retained: {exc}")
        return None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[WARN] could not clean temporary file {temporary_path.name}: {exc}")


def candidate_paths(out_dir, basename, renamed_name=None):
    paths = []
    if renamed_name:
        paths.append(Path(out_dir) / renamed_name)
    if basename:
        raw = Path(out_dir) / basename
        paths.append(strip_compression_suffix(raw))
        if raw != paths[-1]:
            paths.append(raw)
    return paths


def first_existing_path(paths):
    for path in paths:
        if file_exists_and_nonempty(path):
            return path
    return None


def preserve_download_archive(path, out_dir):
    """Keep a downloaded archive after an environmental failure without replacing a cache."""
    if path.suffix in {".gz", ".Z"} and file_exists_and_nonempty(path):
        try:
            # Staging shares the output filesystem. Link creation cannot overwrite
            # an existing file, even if another task publishes it concurrently.
            os.link(path, Path(out_dir) / path.name)
        except FileExistsError:
            pass


def download_source(source, out_dir, use_existing=True):
    """Return a complete, decompressed file and its status for one source."""
    out_dir = Path(out_dir)
    basename = remote_basename(source)
    if not basename:
        return None, "failed"
    if use_existing:
        existing = first_existing_path(candidate_paths(out_dir, basename))
        if existing is not None:
            current = decompress_file(existing, cwd=out_dir)
            if current is not None:
                return current, "existing"
            if existing.exists():
                # An environmental error cannot be repaired by replacing the archive.
                return None, "failed"

    try:
        # Failed wget attempts never publish partial files under their final names.
        # A new attempt starts afresh, including when changing to a same-name mirror.
        command = build_wget_cmd(source)
        if command is None:
            return None, "failed"
        with tempfile.TemporaryDirectory(dir=out_dir, prefix=".fast_yk_download_") as staging:
            if not run_cmd(command, cwd=staging, ignore_error=True):
                return None, "failed"
            downloaded = Path(staging) / basename
            if not file_exists_and_nonempty(downloaded):
                return None, "failed"
            current = decompress_file(downloaded, cwd=staging, delete_input=False)
            if current is None:
                preserve_download_archive(downloaded, out_dir)
                return None, "failed"
            final_path = out_dir / current.name
            try:
                current.replace(final_path)
            except OSError:
                preserve_download_archive(downloaded, out_dir)
                raise
            return final_path, "downloaded"
    except OSError as exc:
        print(f"[WARN] could not save download {basename}: {exc}")
    return None, "failed"


def download_with_fallback(resolved_sources, out_dir, renamed_name=None, rename_enabled=False):
    if renamed_name and file_exists_and_nonempty(Path(out_dir) / renamed_name):
        print(f"{renamed_name} exist")
        return "existing"

    for source in resolved_sources:
        current, status = download_source(source, out_dir)
        if current is None:
            continue

        if renamed_name:
            current = maybe_rename(current, Path(out_dir) / renamed_name, rename_enabled)
        if file_exists_and_nonempty(current):
            return status

    return "failed"


def resolve_sources(source_defs, source_key, date_obj, site_info=None):
    resolved = []
    for source in source_defs[source_key]:
        profile = None
        if isinstance(source, str):
            template = source
        elif isinstance(source, dict):
            template = source.get("url")
            profile = source.get("credentials")
            if not isinstance(template, str) or not template.startswith(("ftp://", "ftps://", "http://", "https://")):
                print(f"[WARN] invalid URL template for source type '{source_key}'; skipping source")
                continue
            if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
                print(f"[WARN] invalid credential profile for source type '{source_key}'; skipping source")
                continue
        else:
            print(f"[WARN] invalid source definition for type '{source_key}'; skipping source")
            continue
        filled = fill_template(template, date_obj, site_info=site_info)
        if filled:
            if profile is not None:
                filled = shlex.join([f"--credential-profile={profile}", filled])
            resolved.append(filled)
    return resolved


def precise_target_name(meta, date_obj):
    gps_week, gps_day = gps_week_day_from_date(date_obj)
    prefix = PRODUCT_PREFIXES[meta["family"]]
    return f"{prefix}{gps_week}{gps_day}.{meta['suffix']}"


def precise_output_dir(meta, date_obj, data_dir, path_mode):
    prefix = PRODUCT_PREFIXES[meta["family"]]
    return output_dir_for_layout(data_dir, path_mode, "precise", date_obj=date_obj, prefix=prefix)


def handle_precise(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, stats):
    out_dir = precise_output_dir(meta, date_obj, data_dir, path_mode)
    ensure_dir(out_dir)
    renamed_name = precise_target_name(meta, date_obj) if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    target = make_target_info(
        meta["source_key"],
        date_obj,
        expected_download_path(out_dir, resolved, renamed_name),
    )
    record_result(stats, status, target=target)
    if status == "failed":
        print(
            f"[WARN] failed target: {target['file']} "
            f"({date_obj.isoformat()}, DOY {target['doy']:03d})"
        )


def handle_broadcast(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, stats):
    out_dir = output_dir_for_layout(data_dir, path_mode, "broadcast", date_obj=date_obj)
    ensure_dir(out_dir)
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    short_prefix = BROADCAST_PREFIXES[meta["source_key"]]
    renamed_name = f"{short_prefix}{doy}0.{yy}p" if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    target = make_target_info(
        meta["source_key"],
        date_obj,
        expected_download_path(out_dir, resolved, renamed_name),
    )
    record_result(stats, status, target=target)
    if status == "failed":
        print(
            f"[WARN] failed target: {target['file']} "
            f"({date_obj.isoformat()}, DOY {target['doy']:03d})"
        )


def handle_snx(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, seen_weeks, stats):
    gps_week, _, _, _ = week_start_info(date_obj)
    if gps_week in seen_weeks:
        return
    seen_weeks.add(gps_week)

    out_dir = output_dir_for_layout(data_dir, path_mode, "snx", date_obj=date_obj)
    ensure_dir(out_dir)
    yy = f"{date_obj.year % 100:02d}"
    renamed_name = f"igs{yy}P{gps_week}.snx" if rename_enabled else None
    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir, renamed_name=renamed_name, rename_enabled=rename_enabled)
    target = make_target_info(
        meta["source_key"],
        date_obj,
        expected_download_path(out_dir, resolved, renamed_name),
    )
    record_result(stats, status, target=target)
    if status == "failed":
        print(
            f"[WARN] failed target: {target['file']} "
            f"({date_obj.isoformat()}, DOY {target['doy']:03d})"
        )


def cas_dcb_long_name(date_obj):
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    return f"CAS0MGXRAP_{date_obj.year:04d}{doy}0000_01D_01D_DCB.BSX"


def cas_dcb_short_name(date_obj):
    gps_week, gps_day = gps_week_day_from_date(date_obj)
    return f"CAS{gps_week}{gps_day}.BSX"


def cas_dcb_output_dir(date_obj, data_dir, path_mode):
    return output_dir_for_layout(data_dir, path_mode, "cas_dcb", date_obj=date_obj)


def cas_dcb_candidate_paths(out_dir, basename, final_name, long_name):
    paths = [Path(out_dir) / final_name]
    if final_name != long_name:
        paths.append(Path(out_dir) / long_name)
    paths.extend(candidate_paths(out_dir, basename))
    return paths


def normalize_cas_dcb_file(path, out_dir, date_obj, final_name, rename_enabled):
    current = decompress_file(path, cwd=out_dir)
    if current is None:
        return None

    long_path = Path(out_dir) / cas_dcb_long_name(date_obj)
    if current != long_path and current.name != final_name:
        current = maybe_rename(current, long_path, True)

    if rename_enabled:
        current = maybe_rename(current, Path(out_dir) / final_name, True)
    return current


def handle_cas_dcb(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, stats):
    out_dir = cas_dcb_output_dir(date_obj, data_dir, path_mode)
    ensure_dir(out_dir)

    long_name = cas_dcb_long_name(date_obj)
    final_name = cas_dcb_short_name(date_obj) if rename_enabled else long_name
    if file_exists_and_nonempty(out_dir / final_name):
        print(f"{final_name} exist")
        record_result(stats, "existing")
        return

    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = "failed"
    existing_long = out_dir / long_name
    if file_exists_and_nonempty(existing_long):
        current = normalize_cas_dcb_file(existing_long, out_dir, date_obj, final_name, rename_enabled)
        if current is not None:
            status = "existing"
    for source in resolved:
        if status == "existing":
            break
        current, current_status = download_source(source, out_dir)
        if current is None:
            continue

        current = normalize_cas_dcb_file(current, out_dir, date_obj, final_name, rename_enabled)
        if current is not None and file_exists_and_nonempty(out_dir / final_name):
            status = current_status
            break

    target = make_target_info(meta["source_key"], date_obj, out_dir / final_name)
    record_result(stats, status, target=target)
    if status == "failed":
        print(
            f"[WARN] failed target: {target['file']} "
            f"({date_obj.isoformat()}, DOY {target['doy']:03d})"
        )


def handle_osb(meta, date_obj, source_defs, data_dir, path_mode, stats):
    out_dir = output_dir_for_layout(data_dir, path_mode, "osb", date_obj=date_obj)
    ensure_dir(out_dir)

    resolved = resolve_sources(source_defs, meta["source_key"], date_obj)
    status = download_with_fallback(resolved, out_dir)
    target = make_target_info(
        meta.get("label", meta["source_key"]),
        date_obj,
        expected_download_path(out_dir, resolved),
    )
    record_result(stats, status, target=target)
    if status == "failed":
        print(
            f"[WARN] failed target: {target['file']} "
            f"({date_obj.isoformat()}, DOY {target['doy']:03d})"
        )


def crx2rnx_binary():
    local_bin = BIN_DIR / "crx2rnx"
    if local_bin.exists():
        return local_bin
    return Path("crx2rnx")


def is_rinex_obs_file(path):
    """Recognize a completed observation header, not a Compact RINEX input."""
    path = Path(path)
    if not file_exists_and_nonempty(path):
        return False
    try:
        with path.open("r", encoding="ascii", errors="replace") as file_obj:
            first = file_obj.readline()
            if "RINEX VERSION / TYPE" not in first[60:] or first[20:21] != "O":
                return False
            for index, line in enumerate(file_obj):
                if "END OF HEADER" in line[60:]:
                    return True
                if index >= 10000:
                    break
    except OSError:
        return False
    return False


def convert_crx_file(crx_path, out_dir, rename_enabled, short_d_name=None, delete_input=False):
    """Publish RINEX only after conversion succeeds; retain input on failure."""
    crx_path = Path(crx_path)
    out_dir = Path(out_dir)
    if rename_enabled and short_d_name:
        output_path = out_dir / (short_d_name[:-1] + "o")
    elif crx_path.suffix == ".crx":
        output_path = out_dir / crx_path.with_suffix(".rnx").name
    elif crx_path.suffix.endswith("d"):
        output_path = out_dir / (crx_path.name[:-1] + "o")
    else:
        return None
    if is_rinex_obs_file(output_path):
        return output_path

    command = [str(crx2rnx_binary()), str(crx_path.resolve()), "-"]
    print(f"[CMD] {shlex.join(command)}")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".fast_yk_rinex_", suffix=".tmp", dir=out_dir, delete=False) as output:
            temp_path = Path(output.name)
            result = subprocess.run(command, cwd=out_dir, stdout=output)
        # CRX2RNX documents 2 as a completed conversion with warnings.
        if result.returncode not in (0, 2) or not is_rinex_obs_file(temp_path):
            print(f"[WARN] RINEX conversion failed: {crx_path.name}; input retained")
            return None
        temp_path.chmod(crx_path.stat().st_mode & 0o666)
        temp_path.replace(output_path)
        if delete_input or (rename_enabled and short_d_name):
            try:
                crx_path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[WARN] converted {crx_path.name}, but could not remove input: {exc}")
        return output_path
    except OSError as exc:
        print(f"[WARN] cannot convert {crx_path.name}: {exc}; input retained")
        return None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[WARN] could not clean temporary file {temp_path.name}: {exc}")


def existing_obs_output(out_dir, site_info, date_obj):
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    short_o = Path(out_dir) / f"{site_info['short_lower']}{doy}0.{yy}o"
    if is_rinex_obs_file(short_o):
        return short_o
    if site_info["long_upper"]:
        pattern = f"{site_info['long_upper']}_*_{date_obj.year:04d}{doy}0000_01D_30S_MO.rnx"
        for path in sorted(Path(out_dir).glob(pattern)):
            if is_rinex_obs_file(path):
                return path
    return None


def obs_input_paths(out_dir, site_info, date_obj):
    """Find this station/day's cached inputs that still need processing."""
    out_dir = Path(out_dir)
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    short_d = out_dir / f"{site_info['short_lower']}{doy}0.{yy}d"
    paths = [short_d, Path(str(short_d) + ".gz"), Path(str(short_d) + ".Z")]
    if site_info["long_upper"]:
        pattern = f"{site_info['long_upper']}_*_{date_obj.year:04d}{doy}0000_01D_30S_MO.crx"
        for suffix in ("", ".gz", ".Z"):
            paths.extend(sorted(out_dir.glob(pattern + suffix)))
    return [path for path in paths if file_exists_and_nonempty(path)]


def handle_mgex_obs(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, site_list_file, site_map, stats):
    out_dir = output_dir_for_layout(data_dir, path_mode, "rinex", date_obj=date_obj)
    ensure_dir(out_dir)
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"

    for token in read_sites(site_list_file):
        site_info = normalize_site(token, site_map)
        if not site_info["short_lower"]:
            print(f"[WARN] invalid site token: {token}")
            target = make_target_info(meta["source_key"], date_obj, out_dir / f"{token}.obs",
                                      site=token, note="invalid station token")
            record_result(stats, "failed", target=target)
            continue
        short_d = f"{site_info['short_lower']}{doy}0.{yy}d"
        short_o = short_d[:-1] + "o"
        existing = existing_obs_output(out_dir, site_info, date_obj)
        if existing:
            if rename_enabled:
                maybe_rename(existing, out_dir / short_o, True)
            print(f"{token} exist")
            record_result(stats, "existing")
            continue

        resolved = resolve_sources(source_defs, meta["source_key"], date_obj, site_info=site_info)
        expected = out_dir / short_o
        if resolved and not rename_enabled:
            expected = expected_download_path(out_dir, resolved).with_suffix(".rnx")
        target = make_target_info(meta["source_key"], date_obj, expected, site=token,
                                  note=None if resolved else "station long name is unavailable")
        status = "failed"
        local_decompression_error = False
        for cached in obs_input_paths(out_dir, site_info, date_obj):
            current = decompress_file(cached, cwd=out_dir)
            if current is None and cached.suffix in {".gz", ".Z"} and cached.exists():
                # The decoder retained this archive because it could not prove
                # corruption. Replacing it cannot fix a local environment error.
                local_decompression_error = True
            if current is not None and convert_crx_file(current, out_dir, rename_enabled, short_d):
                status = "existing"
                break

        if status == "failed" and not local_decompression_error:
            for source in resolved:
                # Cached inputs were already attempted. A failed conversion must
                # not hide another mirror with the same remote filename.
                current, current_status = download_source(source, out_dir, use_existing=False)
                if current is None:
                    continue
                if convert_crx_file(current, out_dir, rename_enabled, short_d):
                    status = current_status
                    break

        record_result(stats, status, target=target)
        if status == "failed":
            print(f"[WARN] failed target: {target['file']} "
                  f"(site {token}, {date_obj.isoformat()}, DOY {target['doy']:03d})")


def igmas_day_files(out_dir, date_obj, suffixes):
    """Daily short names only: never inspect another date in an exact directory."""
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    yy = f"{date_obj.year % 100:02d}"
    for suffix in suffixes:
        yield from sorted(Path(out_dir).glob(f"????{doy}0.{yy}{suffix}"))


def process_igmas_files(out_dir, date_obj):
    # Process all stations downloaded for this day, preserving wildcard behavior.
    for archive in igmas_day_files(out_dir, date_obj, ("d.Z", "d.gz")):
        rm_if_empty(archive)
        if file_exists_and_nonempty(archive):
            decompress_file(archive, cwd=out_dir)
    for compact in igmas_day_files(out_dir, date_obj, ("d",)):
        rm_if_empty(compact)
        if file_exists_and_nonempty(compact):
            convert_crx_file(compact, out_dir, False, delete_input=True)


def handle_igmas(meta, date_obj, source_defs, data_dir, path_mode, rename_enabled, site_list_file, stats):
    out_dir = output_dir_for_layout(data_dir, path_mode, "rinex", date_obj=date_obj)
    ensure_dir(out_dir)
    yy = f"{date_obj.year % 100:02d}"
    doy = f"{date_obj.timetuple().tm_yday:03d}"
    sites = list(dict.fromkeys(token[:4].lower() for token in read_sites(site_list_file)))
    targets = {site: out_dir / f"{site}{doy}0.{yy}o" for site in sites}
    existing_sites = {site for site, target in targets.items() if is_rinex_obs_file(target)}

    if len(existing_sites) < len(sites):
        process_igmas_files(out_dir, date_obj)
        existing_sites.update(site for site, target in targets.items() if is_rinex_obs_file(target))

    if len(existing_sites) < len(sites):
        for source in resolve_sources(source_defs, meta["source_key"], date_obj):
            command = build_wget_cmd(source)
            if command is None:
                continue
            # FTP wildcard downloads also use shared names such as .listing.
            # Keep each date/source's downloads and conversion entirely private.
            with tempfile.TemporaryDirectory(prefix=f".fast_yk_igmas_{date_obj.year}{doy}_", dir=out_dir) as staging:
                staging = Path(staging)
                run_cmd(command, cwd=staging, ignore_error=True)
                process_igmas_files(staging, date_obj)
                for path in igmas_day_files(staging, date_obj, ("o", "d", "d.Z", "d.gz")):
                    destination = out_dir / path.name
                    final = out_dir / (strip_compression_suffix(path).name[:-1] + "o")
                    if is_rinex_obs_file(final):
                        continue
                    if path.suffix.endswith("o") and not is_rinex_obs_file(path):
                        continue
                    if not path.suffix.endswith("o") and file_exists_and_nonempty(destination):
                        # Preserve an earlier input when processing the new one
                        # failed; only verified final observations supersede it.
                        continue
                    if file_exists_and_nonempty(path):
                        path.replace(destination)
            if all(is_rinex_obs_file(target) for target in targets.values()):
                break

    for site, target in targets.items():
        if site in existing_sites:
            record_result(stats, "existing")
        elif is_rinex_obs_file(target):
            record_result(stats, "downloaded")
        else:
            target_info = make_target_info(meta["source_key"], date_obj, target, site=site)
            record_result(stats, "failed", target=target_info)
            print(f"[WARN] no iGMAS obs downloaded for site {site} on {date_obj.isoformat()}")


def canonical_type(dtype):
    dtype = dtype.lower().strip()
    return TYPE_ALIASES.get(dtype, dtype)


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(description="FAST_YK simplified downloader")
    parser.add_argument(
        "data_type",
        nargs="?",
        help="comma-separated data types, e.g. igs,gfzclk,brdc,mgex",
    )
    parser.add_argument("year_begin", nargs="?", type=int)
    parser.add_argument("doy_begin", nargs="?", type=int)
    parser.add_argument("year_end", nargs="?", type=int)
    parser.add_argument("doy_end", nargs="?", type=int)
    parser.add_argument(
        "--data-dir",
        "--output-dir",
        dest="data_dir",
        default=DIR_DATA,
        help="output path; interpreted by --path-mode (default: script DIR_DATA as root)",
    )
    parser.add_argument(
        "--path-mode",
        choices=("root", "product", "exact"),
        default="root",
        help=(
            "how to interpret --data-dir/--output-dir: root keeps the built-in layout; "
            "product treats the path as the product directory; exact writes directly there"
        ),
    )
    parser.add_argument("--igs-site-list", default=IGS_SITE_LIST, help="station list for mgex downloads")
    parser.add_argument("--igmas-site-list", default=IGMAS_SITE_LIST, help="station list for igmas downloads")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rename", dest="rename", action="store_true", help="force enable renaming")
    group.add_argument("--no-rename", dest="rename", action="store_false", help="force disable renaming")
    parser.set_defaults(rename=None)
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=DEFAULT_WORKERS,
        help=f"parallel date workers (default: {DEFAULT_WORKERS}; use 1 for serial downloads)",
    )
    parser.add_argument("--list-types", action="store_true", help="show supported data types and exit")
    return parser.parse_args()


def build_date_jobs(start_date, end_date, requested_types):
    jobs = []
    seen_weeks = set()
    mjd = mjd_from_date(start_date)
    mjd_end = mjd_from_date(end_date)

    while mjd <= mjd_end:
        date_obj = date_from_mjd(mjd)
        job_types = []
        for dtype in requested_types:
            if TYPE_META[dtype]["mode"] == "snx":
                gps_week, _, _, _ = week_start_info(date_obj)
                if gps_week in seen_weeks:
                    continue
                seen_weeks.add(gps_week)
            job_types.append(dtype)
        if job_types:
            jobs.append((date_obj, job_types))
        mjd += 1
    return jobs


def run_date_job(date_obj, requested_types, source_defs, args, rename_enabled, site_map, stats):
    type_label = ",".join(requested_types)
    print(f"[TASK] {date_obj.isoformat()} start: {type_label}")

    for dtype in requested_types:
        meta = TYPE_META[dtype]
        if meta["mode"] == "precise":
            handle_precise(meta, date_obj, source_defs, args.data_dir, args.path_mode, rename_enabled, stats)
        elif meta["mode"] == "broadcast":
            handle_broadcast(meta, date_obj, source_defs, args.data_dir, args.path_mode, rename_enabled, stats)
        elif meta["mode"] == "snx":
            handle_snx(meta, date_obj, source_defs, args.data_dir, args.path_mode, rename_enabled, set(), stats)
        elif meta["mode"] == "obs":
            handle_mgex_obs(
                meta,
                date_obj,
                source_defs,
                args.data_dir,
                args.path_mode,
                rename_enabled,
                args.igs_site_list,
                site_map,
                stats,
            )
        elif meta["mode"] == "igmas":
            handle_igmas(
                meta,
                date_obj,
                source_defs,
                args.data_dir,
                args.path_mode,
                rename_enabled,
                args.igmas_site_list,
                stats,
            )
        elif meta["mode"] == "cas_dcb":
            handle_cas_dcb(meta, date_obj, source_defs, args.data_dir, args.path_mode, rename_enabled, stats)
        elif meta["mode"] == "osb":
            handle_osb(meta, date_obj, source_defs, args.data_dir, args.path_mode, stats)

    print(f"[TASK] {date_obj.isoformat()} done: {type_label}")


def run_date_jobs(jobs, workers, source_defs, args, rename_enabled, site_map, stats):
    if workers == 1 or len(jobs) <= 1:
        for date_obj, requested_types in jobs:
            run_date_job(date_obj, requested_types, source_defs, args, rename_enabled, site_map, stats)
        return

    active_workers = min(workers, len(jobs))
    print(f"[INFO] parallel downloads enabled: {active_workers} workers, {len(jobs)} date tasks")
    with ThreadPoolExecutor(max_workers=active_workers, thread_name_prefix="fast-yk") as executor:
        futures = {
            executor.submit(
                run_date_job,
                date_obj,
                requested_types,
                source_defs,
                args,
                rename_enabled,
                site_map,
                stats,
            ): date_obj
            for date_obj, requested_types in jobs
        }
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


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

    requested_types = list(
        dict.fromkeys(canonical_type(item) for item in args.data_type.split(",") if item.strip())
    )
    invalid = [item for item in requested_types if item not in TYPE_META]
    if invalid:
        raise SystemExit(f"Unsupported data type(s): {', '.join(invalid)}")

    try:
        start_date = date_from_year_doy(args.year_begin, args.doy_begin)
        end_date = date_from_year_doy(args.year_end, args.doy_end)
    except ValueError as exc:
        raise SystemExit(f"Invalid date: {exc}") from None
    if end_date < start_date:
        raise SystemExit("End date must be greater than or equal to start date.")

    jobs = build_date_jobs(start_date, end_date, requested_types)
    preflight_output_dirs(jobs, args.data_dir, args.path_mode)
    run_date_jobs(jobs, args.workers, source_defs, args, rename_enabled, site_map, stats)

    print("")
    print("Download Summary")
    print(f"应下文件个数: {stats['expected']}")
    print(f"实际下载个数: {stats['downloaded']}")
    print(f"已存在无需下载个数: {stats['existing']}")
    print(f"下载失败个数: {stats['failed']}")
    print_failed_targets(stats)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
