"""Build the compact per-channel schedule index used by Stage 2.

Reads the weeks 11-20 and 21-30 exports (the training/validation schedules that
overlap the viewing log) and writes:

  outputs/_intermediate/schedule_index.pkl   compact per-channel arrays
  reports/schedule_index_stats.json          integrity + seam diagnostics

The test export (weeks 31-42) is *read once* only to check the 21-30 -> 31-42
handover; its rows are never indexed for interactions because the log has no
viewing in that period.
"""

import json
import os
import sys
import time
import resource
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "outputs", "_intermediate")
REPORT_DIR = os.path.join(ROOT, "reports")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


def channel_extents(index):
    """min start / max end per channel, used for seam checks."""
    ext = {}
    for ch, data in index.channels.items():
        ext[ch] = (int(data["starts"][0]), int(max(data["ends"])))
    return ext


def main():
    t0 = time.time()
    log = lambda m: print(m, flush=True)

    log("[index] streaming weeks 11-20 + 21-30")
    index, stats = s.build_schedule_index((1, 2))
    stats["channels"] = stats["channels"]
    ext_train = channel_extents(index)

    # Re-open the test export only for handover checks (no rows retained).
    log("[index] checking 21-30 -> 31-42 handover (test export, not indexed)")
    test_min = {}
    test_max = {}
    test_rows = 0
    test_channels = set()
    test_labelled = 0
    for ch_s, start, dur, pid_s, _per, _n in s.iter_schedule(3):
        test_rows += 1
        ch = int(ch_s)
        test_channels.add(ch)
        if pid_s != "0":
            test_labelled += 1
        if ch not in test_min or start < test_min[ch]:
            test_min[ch] = start
        end = start + max(dur, 0)
        if ch not in test_max or end > test_max[ch]:
            test_max[ch] = end

    gap_after_train = 0
    overlap_test = 0
    missing_in_test = 0
    for ch, (_s, e) in ext_train.items():
        if ch not in test_min:
            missing_in_test += 1
            continue
        if test_min[ch] > e:
            gap_after_train += 1
        elif test_min[ch] < e:
            overlap_test += 1

    # Seam diagnostics between the two training exports: count cross-file
    # interval overlaps (not just identical start slots).
    log("[index] measuring 11-20 <-> 21-30 cross-file interval overlaps")
    seam_overlaps = []
    for ch, data in index.channels.items():
        per = data["periods"]
        starts = data["starts"]
        ends = data["ends"]
        rows = [(int(starts[i]), int(ends[i]), int(per[i]),
                 index.pids[data["pids"][i]]) for i in range(len(starts))]
        first = [r for r in rows if r[2] == 1]
        second = [r for r in rows if r[2] == 2]
        j = 0
        for a in first:
            while j < len(second) and second[j][1] <= a[0]:
                j += 1
            k = j
            while k < len(second) and second[k][0] < a[1]:
                if min(a[1], second[k][1]) > max(a[0], second[k][0]):
                    seam_overlaps.append({
                        "channel": ch,
                        "first": [s.iso_utc(a[0]), s.iso_utc(a[1])],
                        "second": [s.iso_utc(second[k][0]), s.iso_utc(second[k][1])],
                        "first_pid": a[3], "second_pid": second[k][3],
                    })
                k += 1

    pkl = os.path.join(OUT_DIR, "schedule_index.pkl")
    s.save_index(index, pkl)

    out = {
        "generated_by": "src/build_schedule_index.py",
        "method": "stdlib csv streaming; compact array per channel; no pandas",
        "training_rows_indexed": len(index),
        "training_channels_indexed": len(index.channels),
        "programme_ids_indexed": len(index.pids) - 1,
        "labelled_rows_indexed": stats["labelled_rows"],
        "unlabelled_rows_indexed": stats["unlabelled_rows"],
        "nonpositive_duration_rows_skipped": stats["nonpositive_duration"],
        "invalid_timestamps": stats["invalid_timestamps"],
        "train_time_range": [s.iso_utc(stats["min_start"]), s.iso_utc(stats["max_end"])],
        "internal_overlap_pairs": stats["overlap_pairs_within_channel"],
        "channels_with_internal_overlap": stats["channels_with_internal_overlap"],
        "gap_occurrences_within_channel": stats["channels_with_gap"],
        "cross_export_interval_overlaps": len(seam_overlaps),
        "cross_export_overlap_examples": seam_overlaps[:10],
        "test_export": {
            "rows": test_rows,
            "channels": len(test_channels),
            "labelled_rows": test_labelled,
            "min_start": s.iso_utc(min(test_min.values())),
            "max_end": s.iso_utc(max(test_max.values())),
            "channels_missing_from_test": missing_in_test,
        },
        "handover_21_30_to_31_42": {
            "channels_with_gap": gap_after_train,
            "channels_whose_test_rows_start_before_train_ends": overlap_test,
        },
        "index_file": os.path.relpath(pkl, ROOT),
        "index_bytes": os.path.getsize(pkl),
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
        "python": sys.version.split()[0],
    }
    with open(os.path.join(REPORT_DIR, "schedule_index_stats.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    log("[index] rows=%d channels=%d pids=%d index=%.1f MB overlaps=%d"
        % (len(index), len(index.channels), len(index.pids) - 1,
           out["index_bytes"] / 1048576, len(seam_overlaps)))
    log("[index] runtime %.1fs peak RSS %.1f MB"
        % (out["runtime_seconds"], out["peak_rss_mb"]))


if __name__ == "__main__":
    main()
