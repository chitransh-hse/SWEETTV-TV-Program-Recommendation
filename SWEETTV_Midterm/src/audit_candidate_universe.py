"""Stage 2.5 candidate-universe audit.

Streams the three schedule exports and records, for every non-zero programme ID:

- which schedule periods it appears in and how many rows it has in each;
- which metadata fields are populated (union across all periods);
- whether it is a test-period cold-start item (in test but not in training);
- whether its metadata is usable (title plus at least one descriptive field).

Outputs
-------
reports/candidate_universe_audit.csv        one row per programme ID
reports/candidate_universe_statistics.json  set intersections + metadata coverage

Only the Python standard library is used.  IDs stay strings throughout.
Nothing in this script selects, removes, or re-ranks programmes.
"""

import collections
import csv
import json
import os
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(ROOT, "reports")
OUT_CSV = os.path.join(REPORT_DIR, "candidate_universe_audit.csv")
OUT_JSON = os.path.join(REPORT_DIR, "candidate_universe_statistics.json")

FIELDS = [
    "tv_show_title", "tv_show_category", "tv_show_genre_1", "tv_show_genre_2",
    "tv_show_genre_3", "year_of_production", "director", "actors",
]

MISSING_TOKENS = {"", "0", "none", "nan", "null"}


def _has(value):
    return value.strip().lower() not in MISSING_TOKENS


def _usable(fields):
    # A programme is usable if it has a title and at least one descriptive
    # field besides the title.  This is a feature-availability check only; it
    # does not judge metadata quality.
    return "tv_show_title" in fields and len(fields) >= 2


def main():
    t0 = time.time()
    period_pids = {1: set(), 2: set(), 3: set()}
    period_rows = {1: collections.Counter(), 2: collections.Counter(), 3: collections.Counter()}
    meta_any = collections.defaultdict(set)
    meta_test = collections.defaultdict(set)

    for period in (1, 2, 3):
        path = os.path.join(
            s.DATA, next(p for per, _name, p in s.SCHEDULE_FILES if per == period)
        )
        with s.open_csv(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row["tv_show_id"]
                if pid == "0":
                    continue
                period_pids[period].add(pid)
                period_rows[period][pid] += 1
                for field in FIELDS:
                    if _has(row[field]):
                        meta_any[pid].add(field)
                        if period == 3:
                            meta_test[pid].add(field)
        print("[universe] period %d: %d labelled programmes" % (period, len(period_pids[period])),
              flush=True)

    train, val, test = period_pids[1], period_pids[2], period_pids[3]
    universe = train | val | test

    summary = {
        "generated_by": "src/audit_candidate_universe.py",
        "method": "stdlib csv streaming; programme IDs preserved as exact strings",
        "metadata_usable_definition":
            "non-empty tv_show_title plus at least one of category, genre_1..3, "
            "year_of_production, director, actors (values '0', empty, None, nan, null "
            "are treated as missing)",
        "period_totals": {
            "training_weeks_11_20": {"programmes": len(train), "rows": sum(period_rows[1].values())},
            "validation_weeks_21_30": {"programmes": len(val), "rows": sum(period_rows[2].values())},
            "test_weeks_31_42": {"programmes": len(test), "rows": sum(period_rows[3].values())},
            "union": len(universe),
        },
        "membership": {
            "training_only_vs_validation_and_test": len(train - (val | test)),
            "training_only_vs_validation": len(train - val),
            "shared_training_and_validation": len(train & val),
            "validation_only_vs_training_and_test": len(val - (train | test)),
            "validation_only_vs_training": len(val - train),
            "test_only_not_in_training_or_validation": len(test - (train | val)),
            "test_absent_from_training": len(test - train),
            "test_shared_with_training": len(test & train),
            "test_shared_with_validation": len(test & val),
            "all_three_periods": len(train & val & test),
            "training_and_test_but_not_validation": len((train & test) - val),
            "validation_and_test_but_not_training": len((val & test) - train),
            "training_and_validation_but_not_test": len((train & val) - test),
        },
        "test_metadata": {
            "test_programmes": len(test),
            "test_programmes_with_usable_metadata": sum(1 for p in test if _usable(meta_test[p])),
            "test_programmes_without_usable_metadata": sum(1 for p in test if not _usable(meta_test[p])),
            "cold_start_test_programmes": len(test - train),
            "cold_start_with_usable_metadata": sum(1 for p in test - train if _usable(meta_test[p])),
            "cold_start_without_usable_metadata": sum(1 for p in test - train if not _usable(meta_test[p])),
            "field_coverage_test": {f: sum(1 for p in test if f in meta_test[p]) for f in FIELDS},
            "field_coverage_cold_start": {
                f: sum(1 for p in test - train if f in meta_test[p]) for f in FIELDS
            },
        },
        "notes": [
            "Cold-start test programmes were retained; nothing was removed from the test universe.",
            "Test viewing behaviour does not exist and was not used.",
            "Programme IDs are strings; no ID was converted to float or signed 64-bit integer.",
        ],
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
        "python": sys.version.split()[0],
    }

    ordered = sorted(universe, key=lambda p: (int(p), p))
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "tv_show_id", "in_training", "in_validation", "in_test",
            "training_rows", "validation_rows", "test_rows", "test_cold_start",
            "metadata_fields", "metadata_field_count", "usable_metadata",
        ])
        for pid in ordered:
            fields = meta_any[pid]
            w.writerow([
                pid,
                int(pid in train), int(pid in val), int(pid in test),
                period_rows[1].get(pid, 0), period_rows[2].get(pid, 0),
                period_rows[3].get(pid, 0),
                int(pid in test and pid not in train),
                "|".join(sorted(fields)),
                len(fields),
                int(_usable(fields)),
            ])

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("[universe] wrote %d programme IDs, %.1fs, peak %.1f MB"
          % (len(ordered), summary["runtime_seconds"], summary["peak_rss_mb"]))


if __name__ == "__main__":
    main()
