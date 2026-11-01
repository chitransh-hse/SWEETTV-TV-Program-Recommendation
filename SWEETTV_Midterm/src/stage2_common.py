"""Shared primitives for Stage 2: timestamp parsing, schedule indexing,
interval matching, overlap calculation, threshold filtering, match resolution
and interaction aggregation.

Design constraints (Stage 1 findings):
- 5.8M viewing rows, 3.4M schedule rows, 8 GiB RAM.
- IDs are kept as strings: user IDs exceed signed int64, programme IDs must not
  become zero-padded or float-valued.
- Timestamps on both sides are naive local wall-clock.  No timezone offset is
  applied anywhere in this module.
- Nothing here reads a whole CSV into a pandas DataFrame.

Period model
------------
Schedule exports are broadcast-week exports: weeks 11-20, 21-30, 31-42.  Within
one channel, consecutive exports hand over at the broadcast-day boundary and do
not repeat the same (channel, start_time) slot (Stage 1 found 0 shared slots).
Therefore the *training* join uses the union of the weeks 11-20 and 21-30
exports, which is a near-continuous per-channel timeline.  Using the union
instead of a calendar-week assignment is required at the seam: a session at
2020-05-18 02:00 belongs to ISO week 21, but its schedule row was exported in
the 11-20 file.  The union avoids both a lost match and a duplicated match.
"""

import csv
import os
from array import array
from bisect import bisect_left
from datetime import date

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

VIEWING_FILE = "dataset11-30.csv"
SCHEDULE_FILES = [
    (1, "weeks_11_20", "export_arh_11-20-final.csv"),
    (2, "weeks_21_30", "export_arh_21-30-final.csv"),
    (3, "weeks_31_42", "export_arh_31-42-final.csv"),
]

VIEW_COLUMNS = ["user_id", "vsetv_id", "start_time", "stop_time", "duraton"]
SCHEDULE_COLUMNS = [
    "channel_id", "channel_title", "start_time", "duration", "tv_show_title",
    "tv_show_id", "tv_show_category", "tv_show_genre_1", "tv_show_genre_2",
    "tv_show_genre_3", "year_of_production", "director", "actors",
]

EPOCH_ORD = date(1970, 1, 1).toordinal()


# --------------------------------------------------------------------------
# Timestamp parsing
# --------------------------------------------------------------------------

def epoch_from_parts(y, mo, d, h, mi, s):
    """Seconds since 1970-01-01 for a naive wall-clock datetime.

    Pure calendar arithmetic: no timezone database, no DST, no local time.
    Raises ValueError on an impossible date/time.
    """
    return (date(y, mo, d).toordinal() - EPOCH_ORD) * 86400 + h * 3600 + mi * 60 + s


def parse_view_timestamp(text):
    """Parse `YYYY-MM-DD HH:MM:SS` (viewing log) to epoch seconds (int).

    Strict: the caller decides whether a ValueError is a data error.
    """
    if len(text) != 19 or text[4] != "-" or text[7] != "-" or text[10] != " " \
            or text[13] != ":" or text[16] != ":":
        raise ValueError("bad viewing timestamp: %r" % (text,))
    return epoch_from_parts(
        int(text[0:4]), int(text[5:7]), int(text[8:10]),
        int(text[11:13]), int(text[14:16]), int(text[17:19]),
    )


def parse_schedule_timestamp(text):
    """Parse `DD.MM.YYYY HH:MM:SS` (schedule) to epoch seconds (int)."""
    if len(text) != 19 or text[2] != "." or text[5] != "." or text[10] != " " \
            or text[13] != ":" or text[16] != ":":
        raise ValueError("bad schedule timestamp: %r" % (text,))
    return epoch_from_parts(
        int(text[6:10]), int(text[3:5]), int(text[0:2]),
        int(text[11:13]), int(text[14:16]), int(text[17:19]),
    )


def iso_utc(ts):
    """Format epoch seconds as `YYYY-MM-DD HH:MM:SS` (wall-clock, no offset)."""
    days, rem = divmod(int(ts), 86400)
    d = date.fromordinal(EPOCH_ORD + days)
    h, rem = divmod(rem, 3600)
    mi, s = divmod(rem, 60)
    return "%04d-%02d-%02d %02d:%02d:%02d" % (d.year, d.month, d.day, h, mi, s)


def iso_date(ts):
    return iso_utc(ts)[:10]


def week_of(ts):
    """ISO week number of a wall-clock timestamp (used only for reporting)."""
    days, _ = divmod(int(ts), 86400)
    return date.fromordinal(EPOCH_ORD + days).isocalendar()[1]


# --------------------------------------------------------------------------
# CSV iteration
# --------------------------------------------------------------------------

def open_csv(path):
    # utf-8-sig strips a BOM if present; all shipped files are UTF-8.
    return open(path, encoding="utf-8-sig", newline="")


def iter_viewing(path=None):
    """Yield (user_id, channel_str, start_epoch, stop_epoch, duraton_int, line_no).

    `user_id` and channel are yielded as the exact strings from the file.
    `line_no` is the 1-based data-row ordinal (header excluded), stable across
    runs and used as the session key.
    """
    path = path or os.path.join(DATA, VIEWING_FILE)
    with open_csv(path) as f:
        reader = csv.reader(f)
        header = next(reader)
        if header != VIEW_COLUMNS:
            raise ValueError("unexpected viewing header: %r" % (header,))
        for n, row in enumerate(reader, 1):
            if len(row) != 5:
                raise ValueError("row %d has %d fields" % (n, len(row)))
            yield (
                row[0], row[1],
                parse_view_timestamp(row[2]), parse_view_timestamp(row[3]),
                int(row[4]), n,
            )


def iter_schedule(period, path=None):
    """Yield schedule rows as a dict-like tuple.

    Returns (channel_str, start_epoch, duration, tv_show_id, period, row_no).
    Metadata columns are not needed for matching and are not retained.
    """
    path = path or os.path.join(
        DATA, next(p for per, name, p in SCHEDULE_FILES if per == period)
    )
    with open_csv(path) as f:
        reader = csv.reader(f)
        header = next(reader)
        if header != SCHEDULE_COLUMNS:
            raise ValueError("unexpected schedule header: %r" % (header,))
        for n, row in enumerate(reader, 1):
            if len(row) != 13:
                raise ValueError("row %d has %d fields" % (n, len(row)))
            yield (
                row[0], parse_schedule_timestamp(row[2]), int(row[3]),
                row[5], period, n,
            )


# --------------------------------------------------------------------------
# Schedule index
# --------------------------------------------------------------------------

class ScheduleIndex:
    """Per-channel sorted interval arrays plus a programme-ID table.

    Memory: ~20 bytes per schedule row in `array` objects (about 45 MB for the
    two training exports), plus the programme-ID strings.
    """

    def __init__(self, pids, channels):
        self.pids = pids              # list[str]; index 0 is the literal "0"
        self.channels = channels      # dict[int, dict[str, array]]

    def __len__(self):
        return sum(len(c["starts"]) for c in self.channels.values())

    def channel(self, channel_int):
        return self.channels.get(channel_int)

    def overlaps(self, channel_int, vstart, vend):
        """Return [(programme_id, pstart, pend, pdur, period, overlap), ...].

        Zero-length and negative overlaps are never returned.  Correct even if
        a channel's schedule rows overlap each other (prefix-max pruning).
        """
        ch = self.channels.get(channel_int)
        if not ch:
            return []
        starts = ch["starts"]
        ends = ch["ends"]
        pmax = ch["pmax"]
        codes = ch["pids"]
        periods = ch["periods"]
        i = bisect_left(starts, vend) - 1
        out = []
        while i >= 0 and pmax[i] > vstart:
            ps = starts[i]
            pe = ends[i]
            if pe > vstart and ps < vend:
                ov = (pe if pe < vend else vend) - (ps if ps > vstart else vstart)
                if ov > 0:
                    out.append((
                        self.pids[codes[i]], ps, pe, pe - ps, periods[i], ov,
                    ))
            i -= 1
        out.reverse()
        return out


def build_schedule_index(periods=(1, 2)):
    """Stream the requested schedule exports into a compact per-channel index."""
    pids = ["0"]
    pid_code = {"0": 0}
    raw = {}
    stats = {
        "rows_read": 0, "invalid_timestamps": 0, "nonpositive_duration": 0,
        "labelled_rows": 0, "unlabelled_rows": 0, "channels": set(),
        "min_start": None, "max_end": None,
    }
    for period in periods:
        for ch_s, start, dur, pid_s, per, _n in iter_schedule(period):
            stats["rows_read"] += 1
            ch = int(ch_s)
            if pid_s not in pid_code:
                pid_code[pid_s] = len(pids)
                pids.append(pid_s)
            code = pid_code[pid_s]
            if code == 0:
                stats["unlabelled_rows"] += 1
            else:
                stats["labelled_rows"] += 1
            if dur <= 0:
                stats["nonpositive_duration"] += 1
                continue
            end = start + dur
            stats["channels"].add(ch)
            if stats["min_start"] is None or start < stats["min_start"]:
                stats["min_start"] = start
            if stats["max_end"] is None or end > stats["max_end"]:
                stats["max_end"] = end
            raw.setdefault(ch, []).append((start, end, code, per))

    channels = {}
    overlap_pairs = 0
    channels_with_overlap = 0
    channels_with_gap = 0
    for ch, rows in raw.items():
        rows.sort()
        starts = array("q", [r[0] for r in rows])
        ends = array("q", [r[1] for r in rows])
        codes = array("i", [r[2] for r in rows])
        pers = array("b", [r[3] for r in rows])
        pmax = array("q", [0]) * len(rows)
        run = 0
        for i, e in enumerate(ends):
            if e > run:
                run = e
            pmax[i] = run
        # integrity: overlapping rows inside one channel, and gaps
        local_overlaps = 0
        max_end = ends[0]
        for i in range(1, len(rows)):
            if starts[i] < max_end:
                local_overlaps += 1
            elif starts[i] > max_end:
                channels_with_gap += 1
            if ends[i] > max_end:
                max_end = ends[i]
        if local_overlaps:
            channels_with_overlap += 1
            overlap_pairs += local_overlaps
        channels[ch] = {
            "starts": starts, "ends": ends, "pids": codes,
            "periods": pers, "pmax": pmax,
        }
    stats["channels"] = len(stats["channels"])
    stats["overlap_pairs_within_channel"] = overlap_pairs
    stats["channels_with_internal_overlap"] = channels_with_overlap
    stats["channels_with_gap"] = channels_with_gap
    stats["pid_count"] = len(pids) - 1
    return ScheduleIndex(pids, channels), stats


def save_index(index, path):
    import pickle
    with open(path, "wb") as f:
        pickle.dump({"pids": index.pids, "channels": index.channels}, f,
                    protocol=pickle.HIGHEST_PROTOCOL)


def load_index(path):
    import pickle
    with open(path, "rb") as f:
        obj = pickle.load(f)
    return ScheduleIndex(obj["pids"], obj["channels"])


# --------------------------------------------------------------------------
# Overlap / matching primitives
# --------------------------------------------------------------------------

def overlap_seconds(vstart, vend, pstart, pend):
    """max(0, min(vend, pend) - max(vstart, pstart))."""
    lo = vstart if vstart > pstart else pstart
    hi = vend if vend < pend else pend
    return hi - lo if hi > lo else 0


def overlap_ratio(ov, programme_duration):
    if programme_duration <= 0:
        return 0.0
    return ov / programme_duration


RULES = [
    ("A", 60, 0.10, "at least 60 s and 10% programme coverage"),
    ("B", 60, 0.20, "at least 60 s and 20% programme coverage"),
    ("C", 120, 0.20, "at least 120 s and 20% programme coverage"),
    ("D", 120, 0.50, "at least 120 s and 50% programme coverage"),
]


def passes_rule(ov, pdur, min_seconds, min_ratio):
    """Threshold filter shared by every strategy (strictly >= on both tests)."""
    return ov >= min_seconds and pdur > 0 and ov >= min_ratio * pdur


def resolve_matches(candidates, strategy):
    """Resolve the labelled candidates of one viewing session.

    `candidates` is a list of dicts with keys overlap and ratio, already
    filtered by a threshold rule.  Returns the list of kept candidates.

    A: keep every candidate (multi-label).
    B: keep the single candidate with the largest absolute overlap
       (ties broken by larger ratio, then by programme ID, deterministically).
    C: keep the single candidate with the largest overlap ratio
       (ties broken by larger overlap, then by programme ID).
    """
    if not candidates:
        return []
    if strategy == "A":
        return sorted(candidates, key=lambda c: c["pid"])
    if strategy == "B":
        key = lambda c: (c["overlap"], c["ratio"])
    elif strategy == "C":
        key = lambda c: (c["ratio"], c["overlap"])
    else:
        raise ValueError("unknown strategy %r" % (strategy,))
    best = max(candidates, key=key)
    tied = [c for c in candidates if key(c) == key(best)]
    return [min(tied, key=lambda c: c["pid"])]


# --------------------------------------------------------------------------
# Small stats helpers
# --------------------------------------------------------------------------

def mean(values):
    return sum(values) / len(values) if values else 0.0


def median(values):
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def percentile(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def distribution(values):
    """Compact summary of a list of counts (users, programmes, ...)."""
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p90": 0.0,
                "p99": 0.0, "max": 0, "min": 0}
    return {
        "n": len(values),
        "mean": round(mean(values), 4),
        "median": median(values),
        "p90": round(percentile(values, 0.90), 2),
        "p99": round(percentile(values, 0.99), 2),
        "max": max(values),
        "min": min(values),
    }
