#!/usr/bin/env python3
"""Stage 1 audit for the SWEET.TV TV Program Recommender competition.

Streams every CSV (stdlib csv) and writes machine-readable statistics to
reports/dataset_statistics.json. No file is ever opened for writing except
the JSON output. Original competition files are treated as read-only.
"""

import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time
import resource
from array import array
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
OUT = os.path.join(PROJECT, "reports", "dataset_statistics.json")

VIEWING = "dataset11-30.csv"
SCHEDULE_FILES = [
    ("weeks_11_20", "export_arh_11-20-final.csv"),
    ("weeks_21_30", "export_arh_21-30-final.csv"),
    ("weeks_31_42", "export_arh_31-42-final.csv"),
]
SUBMISSION = "submission.csv"

VIEWING_COLS = ["user_id", "vsetv_id", "start_time", "stop_time", "duraton"]
SCHEDULE_COLS = [
    "channel_id", "channel_title", "start_time", "duration", "tv_show_title",
    "tv_show_id", "tv_show_category", "tv_show_genre_1", "tv_show_genre_2",
    "tv_show_genre_3", "year_of_production", "director", "actors",
]
SENTINELS = {"", "na", "n/a", "null", "none", "nan", "-"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def sha256_and_flags(path, chunk=8 * 1024 * 1024):
    """Hash file and detect BOM / non-ASCII bytes in one streaming pass."""
    h = hashlib.sha256()
    non_ascii = 0
    first = b""
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            if len(first) < 4:
                first += block[: 4 - len(first)]
            h.update(block)
            if block.isascii() is False:
                non_ascii += sum(1 for b in block if b > 127)
    return {
        "sha256": h.hexdigest(),
        "bom": first.startswith(b"\xef\xbb\xbf"),
        "non_ascii_bytes": non_ascii,
        "ascii_only": non_ascii == 0,
    }


def detect_encoding(path):
    """Full-file UTF-8 validation using an incremental decoder (chunk-safe)."""
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")()
    with open(path, "rb") as f:
        while True:
            block = f.read(8 * 1024 * 1024)
            if not block:
                break
            try:
                decoder.decode(block)
            except UnicodeDecodeError:
                return "not-utf8"
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "not-utf8"
    return "utf-8"


def detect_delimiter(path, encoding):
    with open(path, "r", encoding=encoding, errors="strict", newline="") as f:
        sample = f.read(64 * 1024)
    counts = {",": sample.count(","), ";": sample.count(";"), "\t": sample.count("\t"), "|": sample.count("|")}
    delim = max(counts, key=counts.get)
    return delim, counts


def h64(text):
    return int.from_bytes(
        hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).digest(), "big"
    )


def pct(part, whole):
    return round(100.0 * part / whole, 4) if whole else None


def quantiles(sorted_values, qs):
    n = len(sorted_values)
    out = {}
    for q in qs:
        if n == 0:
            out[str(q)] = None
            continue
        idx = int(round(q * (n - 1)))
        out[str(q)] = sorted_values[idx]
    return out


# --------------------------------------------------------------------------
# Viewing log
# --------------------------------------------------------------------------
def audit_viewing(path):
    log(f"[viewing] streaming {os.path.basename(path)}")
    info = {
        "file": os.path.basename(path),
        "size_bytes": os.path.getsize(path),
        "mtime_epoch": int(os.path.getmtime(path)),
        "delimiter": None,
        "delimiter_counts_sample": None,
        "encoding": None,
        "bom": None,
        "ascii_only": None,
        "sha256": None,
        "header_raw": None,
        "header": None,
        "header_matches_expected": None,
        "data_rows": 0,
        "malformed_field_count": 0,
        "malformed_field_count_examples": [],
        "missing_user_id": 0,
        "missing_vsetv_id": 0,
        "missing_start_time": 0,
        "missing_stop_time": 0,
        "missing_duraton": 0,
        "non_numeric_user_id": 0,
        "non_numeric_vsetv_id": 0,
        "non_numeric_duraton": 0,
        "invalid_start_time": 0,
        "invalid_stop_time": 0,
        "stop_before_start": 0,
        "stop_equals_start": 0,
        "duraton_negative": 0,
        "duraton_zero": 0,
        "duraton_mismatch": 0,
        "duraton_match_exact": 0,
        "abs_diff_le_1s": 0,
        "abs_diff_le_5s": 0,
        "abs_diff_le_60s": 0,
        "abs_diff_gt_60s": 0,
        "sum_abs_diff_seconds": 0,
        "unique_users": None,
        "unique_channels": None,
        "unique_start_dates": None,
        "unique_user_channel_pairs_hashed": None,
        "duplicate_full_rows_hashed": 0,
        "duplicate_user_channel_start_hashed": 0,
        "duraton_mismatch_examples": [],
        "duraton_mismatch_delta_counts": {},
        "duraton_mismatch_start_dates": {},
        "min_start_time": None,
        "max_start_time": None,
        "min_stop_time": None,
        "max_stop_time": None,
        "duration_seconds": None,
        "channel_row_counts": None,
    }
    sha = sha256_and_flags(path)
    info.update({k: sha[k] for k in ("sha256", "bom", "non_ascii_bytes", "ascii_only")})
    info["encoding"] = detect_encoding(path)
    delim, counts = detect_delimiter(path, "utf-8")
    info["delimiter"] = delim
    info["delimiter_counts_sample"] = counts

    users = set()
    channels = set()
    dates = set()
    channel_rows = {}
    seen_full = set()
    seen_uc = set()
    durations = array("q")

    min_start = max_start = min_stop = max_stop = None
    with open(path, "r", encoding="utf-8", newline="") as f:
        info["header_raw"] = f.readline().rstrip("\r\n")
        f.seek(0)
        reader = csv.reader(f, delimiter=delim)
        header = next(reader)
        info["header"] = header
        info["header_matches_expected"] = header == VIEWING_COLS

        for lineno, row in enumerate(reader, start=2):
            if len(row) != len(VIEWING_COLS):
                info["malformed_field_count"] += 1
                if len(info["malformed_field_count_examples"]) < 5:
                    info["malformed_field_count_examples"].append({"line": lineno, "fields": len(row)})
                continue
            info["data_rows"] += 1
            user, chan, start, stop, dur = row

            if user.strip() == "":
                info["missing_user_id"] += 1
            else:
                users.add(user)
                try:
                    int(user)
                except ValueError:
                    info["non_numeric_user_id"] += 1
            if chan.strip() == "":
                info["missing_vsetv_id"] += 1
            else:
                channels.add(chan)
                channel_rows[chan] = channel_rows.get(chan, 0) + 1
                try:
                    int(chan)
                except ValueError:
                    info["non_numeric_vsetv_id"] += 1
            if start.strip() == "":
                info["missing_start_time"] += 1
            if stop.strip() == "":
                info["missing_stop_time"] += 1
            if dur.strip() == "":
                info["missing_duraton"] += 1

            dt_start = dt_stop = None
            if start.strip():
                try:
                    dt_start = datetime.fromisoformat(start.strip())
                except ValueError:
                    info["invalid_start_time"] += 1
            if stop.strip():
                try:
                    dt_stop = datetime.fromisoformat(stop.strip())
                except ValueError:
                    info["invalid_stop_time"] += 1

            if dt_start is not None:
                if min_start is None or dt_start < min_start:
                    min_start = dt_start
                if max_start is None or dt_start > max_start:
                    max_start = dt_start
                dates.add(dt_start.date().isoformat())
            if dt_stop is not None:
                if min_stop is None or dt_stop < min_stop:
                    min_stop = dt_stop
                if max_stop is None or dt_stop > max_stop:
                    max_stop = dt_stop

            diff = None
            if dt_start is not None and dt_stop is not None:
                diff = int((dt_stop - dt_start).total_seconds())
                if diff < 0:
                    info["stop_before_start"] += 1
                elif diff == 0:
                    info["stop_equals_start"] += 1

            dval = None
            if dur.strip():
                try:
                    dval = int(dur)
                except ValueError:
                    info["non_numeric_duraton"] += 1
            if dval is not None:
                durations.append(dval)
                if dval < 0:
                    info["duraton_negative"] += 1
                elif dval == 0:
                    info["duraton_zero"] += 1
                if diff is not None:
                    delta = abs(dval - diff)
                    info["sum_abs_diff_seconds"] += delta
                    if delta == 0:
                        info["duraton_match_exact"] += 1
                    else:
                        info["duraton_mismatch"] += 1
                        k = str(dval - diff)
                        info["duraton_mismatch_delta_counts"][k] = (
                            info["duraton_mismatch_delta_counts"].get(k, 0) + 1
                        )
                        if dt_start is not None:
                            dkey = dt_start.date().isoformat()
                            info["duraton_mismatch_start_dates"][dkey] = (
                                info["duraton_mismatch_start_dates"].get(dkey, 0) + 1
                            )
                        if len(info["duraton_mismatch_examples"]) < 5:
                            info["duraton_mismatch_examples"].append(
                                {
                                    "line": lineno,
                                    "user_id": user,
                                    "vsetv_id": chan,
                                    "start_time": start,
                                    "stop_time": stop,
                                    "duraton": dval,
                                    "stop_minus_start_seconds": diff,
                                    "abs_difference_seconds": delta,
                                }
                            )
                    if delta <= 1:
                        info["abs_diff_le_1s"] += 1
                    if delta <= 5:
                        info["abs_diff_le_5s"] += 1
                    if delta <= 60:
                        info["abs_diff_le_60s"] += 1
                    else:
                        info["abs_diff_gt_60s"] += 1

            if user and chan and start:
                seen_uc.add(h64(f"{user}|{chan}"))
            # Duplicate detection on the full raw row (exact string content).
            rowh = h64("|".join(row))
            if rowh in seen_full:
                info["duplicate_full_rows_hashed"] += 1
            else:
                seen_full.add(rowh)

    info["unique_users"] = len(users)
    info["unique_channels"] = len(channels)
    info["unique_start_dates"] = len(dates)
    if users:
        info["user_id_max_digits"] = max(len(u) for u in users)
        info["user_id_numeric_max"] = max(int(u) for u in users)
        info["user_ids_exceeding_int64"] = sum(1 for u in users if int(u) > 2**63 - 1)
    info["unique_user_channel_pairs_hashed"] = len(seen_uc)
    info["duplicate_user_channel_start_hashed"] = None  # filled by second pass in main()
    info["channel_row_counts"] = channel_rows
    info["min_start_time"] = min_start.isoformat() if min_start else None
    info["max_start_time"] = max_start.isoformat() if max_start else None
    info["min_stop_time"] = min_stop.isoformat() if min_stop else None
    info["max_stop_time"] = max_stop.isoformat() if max_stop else None

    durations = array("q", sorted(durations))
    n = len(durations)
    if n:
        info["duration_seconds"] = {
            "count": n,
            "min": int(durations[0]),
            "max": int(durations[-1]),
            "mean": round(sum(durations) / n, 4),
            "median": statistics.median(durations),
            "quantiles": quantiles(durations, [0.01, 0.25, 0.75, 0.95, 0.99]),
        }
        del durations
    else:
        info["duration_seconds"] = {"count": 0}
    info["unique_users_list"] = users  # kept in memory for cross-file checks; stripped later
    return info


# --------------------------------------------------------------------------
# Schedule files
# --------------------------------------------------------------------------
META_FIELDS = ["tv_show_title", "tv_show_category", "tv_show_genre_1",
               "tv_show_genre_2", "tv_show_genre_3", "year_of_production",
               "director", "actors"]


def audit_schedule(path, key):
    log(f"[schedule:{key}] streaming {os.path.basename(path)}")
    info = {
        "file": os.path.basename(path),
        "size_bytes": os.path.getsize(path),
        "mtime_epoch": int(os.path.getmtime(path)),
        "delimiter": None,
        "delimiter_counts_sample": None,
        "encoding": None,
        "bom": None,
        "ascii_only": None,
        "sha256": None,
        "header": None,
        "header_matches_expected": None,
        "data_rows": 0,
        "malformed_field_count": 0,
        "malformed_field_count_examples": [],
        "unique_channels": None,
        "unique_programme_ids": None,
        "rows_tv_show_id_zero": 0,
        "rows_tv_show_id_missing": 0,
        "rows_tv_show_id_non_numeric": 0,
        "unlabelled_rows": 0,
        "unlabelled_pct": None,
        "missing_titles": 0,
        "missing_categories": 0,
        "missing_genres_any": 0,
        "missing_genres_all": 0,
        "missing_years": 0,
        "years_non_4digit": 0,
        "years_examples": [],
        "missing_directors": 0,
        "missing_actors": 0,
        "missing_channel_ids": 0,
        "missing_channel_titles": 0,
        "invalid_start_times": 0,
        "duration_non_numeric": 0,
        "duration_non_positive": 0,
        "duration_stats": None,
        "duplicate_full_rows_hashed": 0,
        "duplicate_channel_start_hashed": 0,
        "min_start_time": None,
        "max_start_time": None,
        "channel_ids": None,
        "programme_ids": None,
        "metadata_conflicts_within_file": 0,
        "metadata_conflict_fields_within_file": {f: 0 for f in META_FIELDS},
        "metadata": None,
        "labelled_rows": 0,
        "labelled_missing_titles": 0,
        "labelled_missing_categories": 0,
        "labelled_missing_genres_all": 0,
        "labelled_with_any_genre": 0,
        "labelled_missing_years": 0,
        "labelled_missing_directors": 0,
        "labelled_missing_actors": 0,
        "programme_id_max": None,
        "programme_ids_exceeding_int32": None,
        "channel_id_max": None,
        "total_duration_seconds": 0,
        "labelled_duration_seconds": 0,
        "channels_with_multiple_titles": 0,
    }
    channel_titles = {}
    sha = sha256_and_flags(path)
    info.update({k: sha[k] for k in ("sha256", "bom", "non_ascii_bytes", "ascii_only")})
    info["encoding"] = detect_encoding(path)
    delim, counts = detect_delimiter(path, "utf-8")
    info["delimiter"] = delim
    info["delimiter_counts_sample"] = counts

    channels = set()
    pids = set()
    metadata = {}
    durations = array("q")
    seen_rows = set()
    seen_keys = set()
    min_start = max_start = None
    year_re = re.compile(r"^\d{4}$")

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=delim)
        header = next(reader)
        info["header"] = header
        info["header_matches_expected"] = header == SCHEDULE_COLS
        for lineno, row in enumerate(reader, start=2):
            if len(row) != len(SCHEDULE_COLS):
                info["malformed_field_count"] += 1
                if len(info["malformed_field_count_examples"]) < 5:
                    info["malformed_field_count_examples"].append({"line": lineno, "fields": len(row)})
                continue
            info["data_rows"] += 1
            (channel_id, channel_title, start_time, duration, show_title,
             show_id, category, g1, g2, g3, year, director, actors) = [x.strip() for x in row]

            if channel_id == "":
                info["missing_channel_ids"] += 1
            else:
                channels.add(channel_id)
                channel_titles.setdefault(channel_id, set()).add(channel_title)
            if channel_title == "":
                info["missing_channel_titles"] += 1

            dt = None
            if start_time == "":
                info["invalid_start_times"] += 1
            else:
                try:
                    dt = datetime.strptime(start_time, "%d.%m.%Y %H:%M:%S")
                except ValueError:
                    info["invalid_start_times"] += 1
            if dt is not None:
                if min_start is None or dt < min_start:
                    min_start = dt
                if max_start is None or dt > max_start:
                    max_start = dt

            dur_val = None
            if duration == "":
                info["duration_non_numeric"] += 1
            else:
                try:
                    dur_val = int(duration)
                except ValueError:
                    info["duration_non_numeric"] += 1
            if dur_val is not None:
                durations.append(dur_val)
                info["total_duration_seconds"] += dur_val
                if dur_val <= 0:
                    info["duration_non_positive"] += 1

            pid = None
            if show_id == "":
                info["rows_tv_show_id_missing"] += 1
                info["unlabelled_rows"] += 1
            else:
                try:
                    pid = int(show_id)
                except ValueError:
                    info["rows_tv_show_id_non_numeric"] += 1
                    info["unlabelled_rows"] += 1
                else:
                    if pid == 0:
                        info["rows_tv_show_id_zero"] += 1
                        info["unlabelled_rows"] += 1
                    else:
                        pids.add(pid)

            if show_title in SENTINELS:
                info["missing_titles"] += 1
            if category in SENTINELS:
                info["missing_categories"] += 1
            genres = [g1, g2, g3]
            if any(g in SENTINELS for g in genres):
                info["missing_genres_any"] += 1
            if all(g in SENTINELS for g in genres):
                info["missing_genres_all"] += 1
            if year in SENTINELS:
                info["missing_years"] += 1
            elif not year_re.match(year):
                info["years_non_4digit"] += 1
                if len(info["years_examples"]) < 5:
                    info["years_examples"].append(year)
            if director in SENTINELS:
                info["missing_directors"] += 1
            if actors in SENTINELS:
                info["missing_actors"] += 1

            if pid is not None and pid > 0:
                info["labelled_rows"] += 1
                if dur_val is not None:
                    info["labelled_duration_seconds"] += dur_val
                if show_title in SENTINELS:
                    info["labelled_missing_titles"] += 1
                if category in SENTINELS:
                    info["labelled_missing_categories"] += 1
                if all(g in SENTINELS for g in genres):
                    info["labelled_missing_genres_all"] += 1
                else:
                    info["labelled_with_any_genre"] += 1
                if year in SENTINELS:
                    info["labelled_missing_years"] += 1
                if director in SENTINELS:
                    info["labelled_missing_directors"] += 1
                if actors in SENTINELS:
                    info["labelled_missing_actors"] += 1
                meta = (show_title, category, g1, g2, g3, year, director, actors)
                prev = metadata.get(pid)
                if prev is None:
                    metadata[pid] = meta
                elif prev != meta:
                    info["metadata_conflicts_within_file"] += 1
                    for i, fname in enumerate(META_FIELDS):
                        if prev[i] != meta[i]:
                            info["metadata_conflict_fields_within_file"][fname] += 1

            rowh = h64("|".join(row))
            if rowh in seen_rows:
                info["duplicate_full_rows_hashed"] += 1
            else:
                seen_rows.add(rowh)
            if channel_id and start_time:
                keyh = h64(f"{channel_id}|{start_time}")
                if keyh in seen_keys:
                    info["duplicate_channel_start_hashed"] += 1
                else:
                    seen_keys.add(keyh)

    info["unique_channels"] = len(channels)
    info["unique_programme_ids"] = len(pids)
    info["unlabelled_pct"] = pct(info["unlabelled_rows"], info["data_rows"])
    info["labelled_duration_pct"] = pct(info["labelled_duration_seconds"], info["total_duration_seconds"])
    info["channels_with_multiple_titles"] = sum(1 for s in channel_titles.values() if len(s) > 1)
    if pids:
        info["programme_id_max"] = max(pids)
        info["programme_ids_exceeding_int32"] = sum(1 for p in pids if p > 2**31 - 1)
    if channels:
        nums = [int(c) for c in channels if c.lstrip("-").isdigit()]
        info["channel_id_max"] = max(nums) if nums else None
    info["channel_ids"] = channels
    info["programme_ids"] = pids
    info["metadata"] = metadata
    info["slot_keys"] = seen_keys
    info["min_start_time"] = min_start.isoformat() if min_start else None
    info["max_start_time"] = max_start.isoformat() if max_start else None

    durations = array("q", sorted(durations))
    if durations:
        info["duration_stats"] = {
            "count": len(durations),
            "min": int(durations[0]),
            "max": int(durations[-1]),
            "mean": round(sum(durations) / len(durations), 4),
            "median": statistics.median(durations),
        }
        del durations
    else:
        info["duration_stats"] = {"count": 0}
    return info


# --------------------------------------------------------------------------
# Submission template
# --------------------------------------------------------------------------
def audit_submission(path):
    log(f"[submission] reading {os.path.basename(path)}")
    info = {
        "file": os.path.basename(path),
        "size_bytes": os.path.getsize(path),
        "sha256": None,
        "encoding": None,
        "bom": None,
        "delimiter": None,
        "header_present": None,
        "header_raw": None,
        "columns": None,
        "data_rows": 0,
        "unique_users": 0,
        "duplicate_user_rows": 0,
        "rows_with_five_tokens": 0,
        "rows_with_other_token_count": 0,
        "token_count_distribution": {},
        "non_integer_tokens": 0,
        "all_zero_rows": 0,
        "rows_with_nonzero_tokens": 0,
        "distinct_nonzero_programme_ids": 0,
        "token_min": None,
        "token_max": None,
        "user_id_min_length": None,
        "user_id_max_length": None,
        "example_rows": [],
        "users": None,
    }
    sha = sha256_and_flags(path)
    info.update({k: sha[k] for k in ("sha256", "bom")})
    info["encoding"] = detect_encoding(path)
    delim, _ = detect_delimiter(path, "utf-8")
    info["delimiter"] = delim

    users = set()
    tokens_seen = set()
    nonzero = set()
    tmin = tmax = None
    ulens = set()
    raw_header = None
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw_header = f.readline().rstrip("\r\n")
        f.seek(0)
        reader = csv.reader(f, delimiter=delim)
        header = next(reader)
    info["header_raw"] = raw_header
    info["columns"] = header
    info["header_present"] = header == ["user_id", "tv_show_id"]
    info["delimiter"] = "," if "," in raw_header else delim

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=",")
        next(reader)
        for row in reader:
            if len(row) < 2:
                continue
            info["data_rows"] += 1
            user = row[0].strip()
            field = ",".join(row[1:]).strip()
            ulens.add(len(user))
            if user in users:
                info["duplicate_user_rows"] += 1
            else:
                users.add(user)
            toks = field.split()
            info["token_count_distribution"][str(len(toks))] = (
                info["token_count_distribution"].get(str(len(toks)), 0) + 1
            )
            if len(toks) == 5:
                info["rows_with_five_tokens"] += 1
            else:
                info["rows_with_other_token_count"] += 1
            all_zero = True
            for t in toks:
                tokens_seen.add(t)
                try:
                    v = int(t)
                except ValueError:
                    info["non_integer_tokens"] += 1
                    all_zero = False
                    continue
                if tmin is None or v < tmin:
                    tmin = v
                if tmax is None or v > tmax:
                    tmax = v
                if v != 0:
                    all_zero = False
                    nonzero.add(v)
            if all_zero:
                info["all_zero_rows"] += 1
            else:
                info["rows_with_nonzero_tokens"] += 1
            if len(info["example_rows"]) < 5:
                info["example_rows"].append([user, field])

    info["unique_users"] = len(users)
    info["distinct_nonzero_programme_ids"] = len(nonzero)
    info["token_min"] = tmin
    info["token_max"] = tmax
    info["distinct_token_values"] = len(tokens_seen)
    info["user_id_min_length"] = min(ulens) if ulens else None
    info["user_id_max_length"] = max(ulens) if ulens else None
    info["users"] = users
    info["nonzero_ids"] = nonzero
    return info


# --------------------------------------------------------------------------
# Timestamp alignment test (empirical, no arbitrary offset applied)
# --------------------------------------------------------------------------
def audit_timestamp_alignment(viewing_path, schedule_paths, sample_every=10):
    """Measure agreement between viewing start times and schedule slot starts.

    For each candidate clock offset, count viewing sessions that begin within
    +/-120 s of a programme start on the same channel. The offset with the
    highest agreement is the best empirical estimate; 0 means the two files
    already share a clock. No offset is applied to any output data.
    """
    import bisect
    from datetime import timezone

    def epoch(dt):
        return int(dt.replace(tzinfo=timezone.utc).timestamp())

    starts = {}
    smin = smax = None
    for fn in schedule_paths:
        with open(fn, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if len(row) != len(SCHEDULE_COLS):
                    continue
                ch = row[0].strip()
                if not ch.lstrip("-").isdigit():
                    continue
                try:
                    dt = datetime.strptime(row[2].strip(), "%d.%m.%Y %H:%M:%S")
                except ValueError:
                    continue
                e = epoch(dt)
                starts.setdefault(int(ch), []).append(e)
                if smin is None or e < smin:
                    smin = e
                if smax is None or e > smax:
                    smax = e
    for ch in starts:
        starts[ch] = sorted(set(starts[ch]))

    offsets = [-10800, -7200, -3600, -1800, 0, 1800, 3600, 7200, 10800]
    stats = {o: {"match_120": 0, "match_300": 0, "sum_delta": 0, "n": 0} for o in offsets}
    sampled = 0

    with open(viewing_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for i, row in enumerate(reader):
            if i % sample_every:
                continue
            if len(row) != len(VIEWING_COLS):
                continue
            ch = row[1].strip()
            if not ch.lstrip("-").isdigit():
                continue
            ch = int(ch)
            if ch not in starts:
                continue
            try:
                e = epoch(datetime.fromisoformat(row[2].strip()))
            except ValueError:
                continue
            if smin is not None and (e < smin or e > smax + 86400):
                continue
            lst = starts[ch]
            sampled += 1
            for off in offsets:
                target = e - off
                j = bisect.bisect_left(lst, target)
                best = None
                if j < len(lst):
                    best = abs(lst[j] - target)
                if j > 0:
                    d = abs(lst[j - 1] - target)
                    best = d if best is None else min(best, d)
                if best is None:
                    continue
                s = stats[off]
                s["n"] += 1
                s["sum_delta"] += best
                if best <= 120:
                    s["match_120"] += 1
                if best <= 300:
                    s["match_300"] += 1

    out = {"method": "nearest schedule programme start on the same channel; "
                      "viewing rows sampled every %d" % sample_every,
           "sample_rows": sampled,
           "offsets": {}}
    best_off, best_pct = None, -1.0
    for off in offsets:
        s = stats[off]
        n = s["n"] or 1
        p = round(100.0 * s["match_120"] / n, 4)
        out["offsets"][str(off)] = {
            "match_within_120s": s["match_120"],
            "match_within_120s_pct": p,
            "match_within_300s_pct": round(100.0 * s["match_300"] / n, 4),
            "mean_nearest_start_delta_s": round(s["sum_delta"] / n, 2),
        }
        if p > best_pct:
            best_pct, best_off = p, off
    out["best_offset_seconds"] = best_off
    out["best_match_within_120s_pct"] = best_pct
    ranked = sorted(
        ((v["match_within_120s_pct"], int(k)) for k, v in out["offsets"].items()), reverse=True
    )
    margin = round(ranked[0][0] - ranked[1][0], 4) if len(ranked) > 1 else None
    out["margin_over_second_best_pp"] = margin
    out["interpretation"] = (
        "Best agreement at offset 0 s, but the margin is small (%.2f pp); this is weak "
        "supporting evidence only, not proof that the clocks are identical." % margin
        if best_off == 0
        else "Best agreement at %d s offset; NOT applied, needs confirmation." % best_off
    )
    return out


def main():
    t0 = time.time()
    result = {"generated_by": "src/audit_stage1.py", "viewing_log": {}, "schedules": {}, "submission": {}}

    v = audit_viewing(os.path.join(DATA, VIEWING))
    # Duplicate count on (user_id, vsetv_id, start_time) needs a second pass
    # because the first pass only kept distinct hashes.
    log("[viewing] second pass: duplicate (user, channel, start) keys")
    seen = set()
    dupes = 0
    with open(os.path.join(DATA, VIEWING), "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=v["delimiter"])
        next(reader)
        for row in reader:
            if len(row) != len(VIEWING_COLS):
                continue
            user, chan, start = row[0], row[1], row[2]
            k = h64(f"{user}|{chan}|{start}")
            if k in seen:
                dupes += 1
            else:
                seen.add(k)
    del seen
    v["duplicate_user_channel_start_hashed"] = dupes

    users_all = v.pop("unique_users_list")

    result["viewing_log"] = v
    for key, fname in SCHEDULE_FILES:
        result["schedules"][key] = audit_schedule(os.path.join(DATA, fname), key)

    s = audit_submission(os.path.join(DATA, SUBMISSION))
    sub_users = s.pop("users")
    sub_nonzero = s.pop("nonzero_ids")
    result["submission"] = s

    # ---------------- cross-file compatibility ----------------
    log("[cross] comparing channels, IDs, periods, submission")
    viewing_channels = set(v["channel_row_counts"].keys())
    schedule_channels = set()
    schedule_pids = set()
    for key in result["schedules"]:
        sch = result["schedules"][key]
        schedule_channels |= sch["channel_ids"]
        schedule_pids |= sch["programme_ids"]

    viewing_channels_int = {int(c) for c in viewing_channels if c.lstrip("-").isdigit()}
    schedule_channels_int = {int(c) for c in schedule_channels if c.lstrip("-").isdigit()}
    matched = viewing_channels_int & schedule_channels_int
    absent = viewing_channels_int - schedule_channels_int
    rows_on_absent = sum(
        n for c, n in v["channel_row_counts"].items() if c.lstrip("-").isdigit() and int(c) in absent
    )
    rows_on_matched = sum(
        n for c, n in v["channel_row_counts"].items() if c.lstrip("-").isdigit() and int(c) in matched
    )

    periods = list(result["schedules"].keys())
    pair_overlaps = {}
    for i in range(len(periods)):
        for j in range(i + 1, len(periods)):
            a, b = periods[i], periods[j]
            pa = result["schedules"][a]["programme_ids"]
            pb = result["schedules"][b]["programme_ids"]
            pair_overlaps[f"{a}__{b}"] = {
                "intersection": len(pa & pb),
                "only_in_first": len(pa - pb),
                "only_in_second": len(pb - pa),
                "jaccard": round(len(pa & pb) / len(pa | pb), 4) if (pa | pb) else None,
            }

    new_ids = {}
    p_11 = result["schedules"]["weeks_11_20"]["programme_ids"]
    p_21 = result["schedules"]["weeks_21_30"]["programme_ids"]
    p_31 = result["schedules"]["weeks_31_42"]["programme_ids"]
    new_ids["weeks_21_30_vs_weeks_11_20"] = len(p_21 - p_11)
    new_ids["weeks_31_42_vs_weeks_11_20"] = len(p_31 - p_11)
    new_ids["weeks_31_42_vs_weeks_11_20_and_21_30"] = len(p_31 - (p_11 | p_21))
    new_ids["weeks_31_42_never_seen_before"] = len(p_31 - (p_11 | p_21))
    new_ids["in_all_three_periods"] = len(p_11 & p_21 & p_31)
    new_ids["in_any_period"] = len(p_11 | p_21 | p_31)

    # Schedule slots (channel_id + start_time) repeated across consecutive files,
    # i.e. the boundary overlap between exported periods.
    boundary_overlap = {}
    for i in range(len(periods) - 1):
        a, b = periods[i], periods[i + 1]
        ka = result["schedules"][a]["slot_keys"]
        kb = result["schedules"][b]["slot_keys"]
        boundary_overlap[f"{a}__{b}"] = {
            "shared_channel_start_slots": len(ka & kb),
            "first_file_slots": len(ka),
            "second_file_slots": len(kb),
        }

    # Metadata consistency across periods
    meta_report = {}
    for i in range(len(periods)):
        for j in range(i + 1, len(periods)):
            a, b = periods[i], periods[j]
            ma = result["schedules"][a]["metadata"]
            mb = result["schedules"][b]["metadata"]
            common = set(ma) & set(mb)
            fields = {f: 0 for f in META_FIELDS}
            examples = []
            for pid in common:
                if ma[pid] != mb[pid]:
                    for idx, f in enumerate(META_FIELDS):
                        if ma[pid][idx] != mb[pid][idx]:
                            fields[f] += 1
                    if len(examples) < 5:
                        diffs = [
                            {"field": f, "first": ma[pid][idx], "second": mb[pid][idx]}
                            for idx, f in enumerate(META_FIELDS)
                            if ma[pid][idx] != mb[pid][idx]
                        ]
                        examples.append({"tv_show_id": pid, "differences": diffs})
            meta_report[f"{a}__{b}"] = {
                "common_programme_ids": len(common),
                "ids_with_any_difference": sum(
                    1 for pid in common if ma[pid] != mb[pid]
                ),
                "field_difference_counts": fields,
                "examples": examples,
            }

    # Programme IDs that appear in schedules and in the submission template
    period_channel_ints = {
        k: {
            int(c)
            for c in result["schedules"][k]["channel_ids"]
            if c.lstrip("-").isdigit()
        }
        for k in periods
    }
    result["cross_file"] = {
        "viewing_unique_channels": len(viewing_channels_int),
        "schedule_unique_channels": len(schedule_channels_int),
        "schedule_channels_present_in_viewing": len(matched),
        "schedule_channels_absent_from_viewing": len(schedule_channels_int - viewing_channels_int),
        "viewing_channels_present_in_schedule": len(matched),
        "viewing_channels_absent_from_schedule": len(absent),
        "viewing_channels_absent_from_schedule_detail": {
            str(c): v["channel_row_counts"].get(str(c), 0) for c in sorted(absent)
        },
        "viewing_rows_on_channels_absent_from_schedule": rows_on_absent,
        "viewing_rows_on_channels_present_in_schedule": rows_on_matched,
        "viewing_row_pct_on_matched_channels": pct(rows_on_matched, rows_on_matched + rows_on_absent),
        "schedule_channels_not_in_viewing": sorted(schedule_channels_int - viewing_channels_int),
        "viewing_channels_not_in_schedule": sorted(absent),
        "period_channel_coverage": {
            k: {
                "schedule_channels": len(period_channel_ints[k]),
                "viewing_channels_present": len(
                    period_channel_ints[k] & viewing_channels_int
                ),
                "viewing_channels_absent": sorted(
                    viewing_channels_int - period_channel_ints[k]
                ),
                "viewing_rows_on_absent_channels": sum(
                    v["channel_row_counts"].get(str(cc), 0)
                    for cc in (viewing_channels_int - period_channel_ints[k])
                ),
            }
            for k in periods
        },
        "schedule_period_boundary_overlap": boundary_overlap,
        "schedule_programme_ids_total": len(schedule_pids),
        "schedule_id_period_overlaps": pair_overlaps,
        "new_programme_ids": new_ids,
        "metadata_consistency_across_periods": meta_report,
        "timestamp_formats": {
            "viewing": "YYYY-MM-DD HH:MM:SS (e.g. 2020-03-09 00:00:02)",
            "schedule": "DD.MM.YYYY HH:MM:SS (e.g. 09.03.2020 06:00:00)",
            "compatible_directly": False,
        },
        "timestamp_ranges": {
            "viewing_start_min": v["min_start_time"],
            "viewing_start_max": v["max_start_time"],
            "viewing_stop_min": v["min_stop_time"],
            "viewing_stop_max": v["max_stop_time"],
            **{
                f"{k}_schedule_min": result["schedules"][k]["min_start_time"]
                for k in periods
            },
            **{
                f"{k}_schedule_max": result["schedules"][k]["max_start_time"]
                for k in periods
            },
        },
        "submission_users_in_viewing_log": len(sub_users & users_all),
        "submission_users_total": len(sub_users),
        "submission_users_missing_from_viewing_log": len(sub_users - users_all),
        "submission_user_overlap_pct": pct(len(sub_users & users_all), len(sub_users)),
        "submission_nonzero_ids_in_schedule": len(sub_nonzero & schedule_pids),
        "submission_nonzero_ids_not_in_schedule": len(sub_nonzero - schedule_pids),
        "submission_nonzero_ids_distinct": len(sub_nonzero),
    }

    log("[cross] timestamp alignment test (sampled)")
    result["cross_file"]["timestamp_alignment"] = audit_timestamp_alignment(
        os.path.join(DATA, VIEWING),
        [os.path.join(DATA, f) for k, f in SCHEDULE_FILES if k in ("weeks_11_20", "weeks_21_30")],
    )

    # Strip sets (not JSON serialisable) after cross-file work.
    for key in result["schedules"]:
        result["schedules"][key].pop("channel_ids", None)
        result["schedules"][key].pop("programme_ids", None)
        result["schedules"][key].pop("metadata", None)
        result["schedules"][key].pop("slot_keys", None)
    v.pop("channel_row_counts", None)

    result["audit_run"] = {
        "started_at_epoch": int(t0),
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1),
        "python": sys.version.split()[0],
        "method": "stdlib csv streaming; no full-file pandas load; no files modified",
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    log(f"[done] wrote {OUT}")


if __name__ == "__main__":
    main()
