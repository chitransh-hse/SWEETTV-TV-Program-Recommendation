"""Coverage diagnostics that complement the event store.

Computes, in one streaming pass:
- histogram of scheduled airings overlapped per viewing session;
- viewing rows per schedule period whose channel is absent from that period;
- reasons for zero overlap, broken down by channel and date;
- sessions that overlap no labelled programme.

Output: reports/coverage_diagnostics.json
"""

import json
import os
import sys
import time
import resource
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "outputs", "_intermediate", "schedule_index.pkl")
STAGE1 = os.path.join(ROOT, "reports", "dataset_statistics.json")
OUT = os.path.join(ROOT, "reports", "coverage_diagnostics.json")


def main():
    t0 = time.time()
    idx = s.load_index(INDEX)
    stage1 = json.load(open(STAGE1, encoding="utf-8"))
    absent_by_period = {
        1: set(stage1["cross_file"]["period_channel_coverage"]["weeks_11_20"]["viewing_channels_absent"]),
        2: set(stage1["cross_file"]["period_channel_coverage"]["weeks_21_30"]["viewing_channels_absent"]),
    }
    sched_channels = {}
    # Channels present in each training period = indexed channels minus those
    # absent from that period's export.
    all_channels = set(idx.channels)
    sched_channels[1] = all_channels - absent_by_period[1]
    sched_channels[2] = all_channels - absent_by_period[2]

    hist_labelled = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    hist_any = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    period_rows = {1: 0, 2: 0, 3: 0}
    period_rows_channel_absent = {1: 0, 2: 0, 3: 0}
    period_sessions = {1: 0, 2: 0, 3: 0}
    period_sessions_channel_absent = {1: 0, 2: 0, 3: 0}
    zero_by_channel = {}
    zero_by_date = {}
    zero_by_hour = {}
    sessions = 0
    sessions_zero_labelled = 0
    sessions_zero_any = 0

    for user, ch_s, start, stop, dur, _n in s.iter_viewing():
        sessions += 1
        ch = int(ch_s)
        matches = idx.overlaps(ch, start, stop)
        n_any = len(matches)
        n_lab = sum(1 for m in matches if m[0] != "0")
        hist_any[min(n_any, 4)] += 1
        hist_labelled[min(n_lab, 4)] += 1

        wk = s.week_of(start)
        period = 1 if 11 <= wk <= 20 else (2 if 21 <= wk <= 30 else 3)
        period_rows[period] += 1
        period_sessions[period] += 1
        if period in sched_channels and ch not in sched_channels[period]:
            period_rows_channel_absent[period] += 1
            period_sessions_channel_absent[period] += 1

        if n_lab == 0:
            sessions_zero_labelled += 1
        if n_any == 0:
            sessions_zero_any += 1
            zero_by_channel[ch] = zero_by_channel.get(ch, 0) + 1
            d = s.iso_date(start)
            zero_by_date[d] = zero_by_date.get(d, 0) + 1
            h = (start % 86400) // 3600
            zero_by_hour[h] = zero_by_hour.get(h, 0) + 1

    top_zero_channels = sorted(zero_by_channel.items(), key=lambda x: -x[1])[:15]
    top_zero_dates = sorted(zero_by_date.items(), key=lambda x: -x[1])[:10]
    out = {
        "generated_by": "src/analyze_coverage.py",
        "sessions": sessions,
        "sessions_with_zero_any_overlap": sessions_zero_any,
        "sessions_with_zero_labelled_overlap": sessions_zero_labelled,
        "sessions_with_zero_labelled_overlap_pct": round(100 * sessions_zero_labelled / sessions, 3),
        "histogram_scheduled_airings_per_session": {
            "0": hist_any[0], "1": hist_any[1], "2": hist_any[2],
            "3": hist_any[3], "4_plus": hist_any[4]},
        "histogram_labelled_airings_per_session": {
            "0": hist_labelled[0], "1": hist_labelled[1], "2": hist_labelled[2],
            "3": hist_labelled[3], "4_plus": hist_labelled[4]},
        "by_schedule_period": {
            str(p): {
                "week_range": {1: "11-20", 2: "21-30", 3: "31-42"}[p],
                "viewing_sessions": period_sessions[p],
                "sessions_on_channels_absent_from_period": period_sessions_channel_absent[p],
                "sessions_on_channels_absent_from_period_pct":
                    round(100 * period_sessions_channel_absent[p] / max(period_sessions[p], 1), 3),
                "viewing_rows": period_rows[p],
            } for p in (1, 2, 3)
        },
        "zero_overlap_top_channels": [
            {"channel": c, "sessions": n, "channel_in_union": c in all_channels,
             "first_schedule_start": s.iso_utc(int(idx.channels[c]["starts"][0]))
             if c in all_channels else None}
            for c, n in top_zero_channels],
        "zero_overlap_top_start_dates": [{"date": d, "sessions": n} for d, n in top_zero_dates],
        "zero_overlap_by_start_hour": {str(h): zero_by_hour[h] for h in sorted(zero_by_hour)},
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("[coverage] %.1fs, peak %.1f MB" % (out["runtime_seconds"], out["peak_rss_mb"]))


if __name__ == "__main__":
    main()
