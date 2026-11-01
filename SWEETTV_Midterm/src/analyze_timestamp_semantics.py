"""Stage 2 timestamp-semantics diagnostics.

Answers, from observed data only:
- do both timestamp systems parse, and what ranges do they cover?
- is the zero offset the best of the tested alternatives, and how strong is that?
- what does the March 2020 DST transition do to durations and to alignment?
- how many sessions cross midnight, and how long is the tail?
- how do viewing durations compare with scheduled programme durations?

No timezone correction is applied.  Offsets are diagnostic alternatives only.

Output: reports/timestamp_semantics.json
"""

import json
import os
import sys
import time
import resource
from array import array
from bisect import bisect_left
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "outputs", "_intermediate", "schedule_index.pkl")
OUT = os.path.join(ROOT, "reports", "timestamp_semantics.json")

SAMPLE_EVERY = 50          # deterministic sample of the viewing log
OFFSETS = [-10800, -7200, -3600, -1800, 0, 1800, 3600, 7200, 10800]
DST_JUMP = s.parse_view_timestamp("2020-03-29 03:00:00")  # wall clock jumps 03:00 -> 04:00


def nearest_start(starts, x):
    j = bisect_left(starts, x)
    best = 10 ** 12
    for k in (j - 1, j, j + 1):
        if 0 <= k < len(starts):
            d = abs(starts[k] - x)
            if d < best:
                best = d
    return best


def main():
    t0 = time.time()
    idx = s.load_index(INDEX)
    log = lambda m: print(m, flush=True)

    # ---- full-file viewing pass: ranges, midnight, duration tail, DST -----
    log("[ts] full viewing pass")
    n_rows = 0
    parse_errors = 0
    missing = 0
    min_start = None
    max_stop = None
    cross_midnight = 0
    durations = array('q')
    dst_mismatch = []
    dst_crossing = 0
    dst_crossing_mismatch = 0
    over_2h = over_4h = over_8h = over_24h = over_48h = 0
    channels = set()
    users = set()

    for user, ch_s, start, stop, dur, _n in s.iter_viewing():
        n_rows += 1
        channels.add(int(ch_s))
        users.add(user)
        if min_start is None or start < min_start:
            min_start = start
        if max_stop is None or stop > max_stop:
            max_stop = stop
        d = stop - start
        durations.append(d)
        if s.iso_date(start) != s.iso_date(stop):
            cross_midnight += 1
        if d >= 7200:
            over_2h += 1
        if d >= 14400:
            over_4h += 1
        if d >= 28800:
            over_8h += 1
        if d >= 86400:
            over_24h += 1
        if d >= 172800:
            over_48h += 1
        spans = start < DST_JUMP < stop
        if spans:
            dst_crossing += 1
        if dur != d:
            dst_mismatch.append({
                "start": s.iso_utc(start), "stop": s.iso_utc(stop),
                "duraton": dur, "stop_minus_start": d,
                "delta": dur - d, "spans_dst_jump": spans,
            })
            if spans:
                dst_crossing_mismatch += 1

    log("[ts] sampled offset sweep")
    sample = []
    for user, ch_s, start, stop, dur, n in s.iter_viewing():
        if n % SAMPLE_EVERY == 0:
            sample.append((int(ch_s), start, stop, stop - start))

    # coverage counts by reason for zero overlap
    zero_channel_absent = 0
    zero_no_covering_row = 0

    def offset_metrics(offset):
        cov = start60 = start300 = end60 = 0
        ds = []
        de = []
        single_ratios = []
        for ch, start, stop, _d in sample:
            vs, ve = start + offset, stop + offset
            matches = idx.overlaps(ch, vs, ve)
            if matches:
                cov += 1
                if len(matches) == 1 and matches[0][3] > 0:
                    single_ratios.append(matches[0][5] / matches[0][3])
            c = idx.channel(ch)
            if not c:
                continue
            starts = c["starts"]
            a = nearest_start(starts, vs)
            b = nearest_start(starts, ve)
            ds.append(a)
            de.append(b)
            if a <= 60:
                start60 += 1
            if a <= 300:
                start300 += 1
            if b <= 60:
                end60 += 1
        k = max(1, len(ds))
        n = len(sample)
        ds.sort()
        de.sort()
        sr = sorted(single_ratios)
        return {
            "offset_seconds": offset,
            "coverage_pct": round(100 * cov / n, 3),
            "start_within_60s_pct": round(100 * start60 / k, 3),
            "start_within_300s_pct": round(100 * start300 / k, 3),
            "end_within_60s_pct": round(100 * end60 / k, 3),
            "median_nearest_start_delta_s": ds[len(ds) // 2] if ds else None,
            "median_nearest_end_delta_s": de[len(de) // 2] if de else None,
            "single_overlap_median_ratio": round(sr[len(sr) // 2], 4) if sr else None,
            "single_overlap_share_pct": round(100 * len(sr) / n, 2),
        }

    offsets = [offset_metrics(o) for o in OFFSETS]

    # zero-overlap reasons at offset 0
    for ch, start, stop, _d in sample:
        if not idx.overlaps(ch, start, stop):
            if idx.channel(ch) is None:
                zero_channel_absent += 1
            else:
                zero_no_covering_row += 1

    # pre/post-DST offset check
    pre = [r for r in sample if r[1] < DST_JUMP]
    post = [r for r in sample if r[1] >= DST_JUMP]

    def dst_side_metrics(rows, offset):
        s60 = e60 = 0
        k = 0
        for ch, start, stop, _d in rows:
            c = idx.channel(ch)
            if not c:
                continue
            k += 1
            if nearest_start(c["starts"], start + offset) <= 60:
                s60 += 1
            if nearest_start(c["starts"], stop + offset) <= 60:
                e60 += 1
        return {
            "offset_seconds": offset,
            "start_within_60s_pct": round(100 * s60 / max(k, 1), 3),
            "end_within_60s_pct": round(100 * e60 / max(k, 1), 3),
        }

    dst_side = {
        "before_2020-03-29T03:00": [dst_side_metrics(pre, o) for o in (-3600, 0, 3600)],
        "after_2020-03-29T03:00": [dst_side_metrics(post, o) for o in (-3600, 0, 3600)],
    }

    # time-of-day / minute-of-hour alignment (sample)
    session_minutes = {}
    for ch, start, stop, _d in sample:
        mi = (start % 3600) // 60
        session_minutes[mi] = session_minutes.get(mi, 0) + 1
    program_minutes = {}
    for ch, data in idx.channels.items():
        for st in data["starts"]:
            mi = (st % 3600) // 60
            program_minutes[mi] = program_minutes.get(mi, 0) + 1

    def top_minutes(d):
        total = sum(d.values())
        return [{"minute": k, "pct": round(100 * v / total, 2)}
                for k, v in sorted(d.items(), key=lambda x: -x[1])[:8]]

    # duration comparison for sessions with exactly one schedule overlap
    single_view = []
    single_ratio = []
    view_duration_sample = []
    for ch, start, stop, _d in sample:
        view_duration_sample.append(stop - start)
        m = idx.overlaps(ch, start, stop)
        if len(m) == 1 and m[0][3] > 0:
            single_view.append(stop - start)
            single_ratio.append(m[0][5] / m[0][3])

    schedule_durations = array('q')
    for _c, data in idx.channels.items():
        schedule_durations.extend(int(e - st) for st, e in zip(data["starts"], data["ends"]))

    out = {
        "generated_by": "src/analyze_timestamp_semantics.py",
        "method": "naive wall-clock parsing; no timezone conversion; offsets are diagnostics only",
        "viewing": {
            "rows": n_rows,
            "users": len(users),
            "channels": len(channels),
            "parse_errors": parse_errors,
            "missing_fields": missing,
            "min_start": s.iso_utc(min_start),
            "max_stop": s.iso_utc(max_stop),
            "sampled_rows": len(sample),
            "sample_every": SAMPLE_EVERY,
        },
        "schedule": {
            "training_rows": len(idx),
            "channels": len(idx.channels),
            "min_start": s.iso_utc(min(int(d["starts"][0]) for d in idx.channels.values())),
            "max_end": s.iso_utc(max(int(max(d["ends"])) for d in idx.channels.values())),
        },
        "offset_sweep": offsets,
        "offset_zero_choice": {
            "best_start_within_300s": max(offsets, key=lambda r: r["start_within_300s_pct"])["offset_seconds"],
            "best_end_within_60s": max(offsets, key=lambda r: r["end_within_60s_pct"])["offset_seconds"],
            "best_median_end_delta": min(
                (r for r in offsets if r["median_nearest_end_delta_s"] is not None),
                key=lambda r: r["median_nearest_end_delta_s"])["offset_seconds"],
        },
        "zero_overlap_reasons_at_zero_offset": {
            "sampled_sessions": len(sample),
            "channel_absent_from_training_schedules": zero_channel_absent,
            "channel_present_but_no_covering_row_or_outside_coverage": zero_no_covering_row,
        },
        "dst": {
            "jump_2020-03-29T03:00_wall_clock": s.iso_utc(DST_JUMP),
            "sessions_spanning_jump": dst_crossing,
            "duration_mismatch_rows": len(dst_mismatch),
            "duration_mismatch_rows_spanning_jump": dst_crossing_mismatch,
            "mismatch_delta_counts": _counts([r["delta"] for r in dst_mismatch]),
            "mismatch_examples": dst_mismatch[:5],
            "offset_check_before_after": dst_side,
        },
        "midnight_and_long_sessions": {
            "sessions_crossing_midnight": cross_midnight,
            "sessions_crossing_midnight_pct": round(100 * cross_midnight / n_rows, 3),
            "durations_ge_2h": over_2h,
            "durations_ge_4h": over_4h,
            "durations_ge_8h": over_8h,
            "durations_ge_24h": over_24h,
            "durations_ge_48h": over_48h,
            "max_duration_seconds": max(durations),
            "duration_p50": s.percentile(durations, 0.50),
            "duration_p95": s.percentile(durations, 0.95),
            "duration_p99": s.percentile(durations, 0.99),
        },
        "duration_comparison": {
            "viewing_duration_median_s": s.median(view_duration_sample),
            "schedule_duration_median_s": s.median(schedule_durations),
            "schedule_duration_mean_s": round(s.mean(schedule_durations), 1),
            "single_overlap_sessions": len(single_view),
            "single_overlap_view_duration_median_s": s.median(single_view),
            "single_overlap_ratio_median": round(s.median(single_ratio), 4),
            "single_overlap_ratio_mean": round(s.mean(single_ratio), 4),
            "single_overlap_ratio_ge_0_9_pct": round(
                100 * sum(1 for r in single_ratio if r >= 0.9) / max(len(single_ratio), 1), 2),
            "single_overlap_ratio_lt_0_1_pct": round(
                100 * sum(1 for r in single_ratio if r < 0.1) / max(len(single_ratio), 1), 2),
        },
        "minute_of_hour_alignment": {
            "top_session_start_minutes": top_minutes(session_minutes),
            "top_programme_start_minutes": top_minutes(program_minutes),
        },
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
        "python": sys.version.split()[0],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    log("[ts] wrote %s (%.1fs, %.1f MB)" % (OUT, out["runtime_seconds"], out["peak_rss_mb"]))


def _counts(values):
    out = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


if __name__ == "__main__":
    main()
