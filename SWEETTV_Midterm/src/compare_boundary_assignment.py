"""Compare the rejected maximum-overlap boundary rule with the corrected
earliest-matched-airing rule.

The old rule assigned a cross-period session to the period with the larger total
labelled overlap (ties -> earlier period).  It is reconstructed here from the
read-only Stage 2 event store; the corrected assignment is read from
`temporal_split.sqlite`.  For every split and every (strategy, rule) combination
the old aggregates are recomputed and compared row-by-row with the current
gzip datasets.

Outputs
-------
reports/boundary_assignment_comparison.json
reports/boundary_assignment_comparison.csv

This script does not modify the split database, the datasets or the Stage 2
event store.
"""

import csv
import gzip
import json
import os
import resource
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DB = os.path.join(ROOT, "outputs", "_intermediate", "overlaps.sqlite")
DB = os.path.join(ROOT, "outputs", "_intermediate", "temporal_split.sqlite")
OUT_DIR = os.path.join(ROOT, "outputs", "temporal_interactions")
META = os.path.join(OUT_DIR, "metadata.json")
REPORT_DIR = os.path.join(ROOT, "reports")
OUT_JSON = os.path.join(REPORT_DIR, "boundary_assignment_comparison.json")
OUT_CSV = os.path.join(REPORT_DIR, "boundary_assignment_comparison.csv")


def build_old_assignment(con):
    """Reconstruct the previous rule: SUM(ov) DESC, then period ASC."""
    con.executescript("""
        DROP TABLE IF EXISTS old_assignment;
        CREATE TEMP TABLE old_assignment AS
        WITH per_session_period AS (
            SELECT view_id, period,
                   SUM(ov) AS ov_sum,
                   COUNT(*) AS n_events,
                   MIN(pstart) AS first_pstart
            FROM src.overlaps
            GROUP BY view_id, period
        ), ranked AS (
            SELECT view_id, period, ov_sum, n_events, first_pstart,
                   COUNT(*) OVER (PARTITION BY view_id) AS n_periods,
                   ROW_NUMBER() OVER (
                       PARTITION BY view_id
                       ORDER BY ov_sum DESC, period ASC
                   ) AS rn
            FROM per_session_period
        )
        SELECT view_id, period AS assigned_period, ov_sum AS assigned_overlap,
               n_events AS assigned_events, n_periods, first_pstart
        FROM ranked WHERE rn = 1;
    """)


def assignment_summary(con, table):
    row = con.execute("""
        SELECT COUNT(*), SUM(assigned_period = 1), SUM(assigned_period = 2),
               SUM(assigned_events), SUM(assigned_overlap),
               SUM(n_periods = 2), SUM(n_periods = 2 AND assigned_period = 1),
               SUM(n_periods = 2 AND assigned_period = 2)
        FROM %s
    """ % table).fetchone()
    excluded = con.execute("""
        SELECT COUNT(*), COALESCE(SUM(o.ov), 0)
        FROM src.overlaps o JOIN %s a ON a.view_id = o.view_id
        WHERE o.period <> a.assigned_period
    """ % table).fetchone()
    return {
        "sessions": row[0],
        "assigned_training": row[1],
        "assigned_validation": row[2],
        "retained_events": row[3],
        "retained_overlap_seconds": row[4],
        "cross_period_sessions": row[5],
        "cross_assigned_training": row[6],
        "cross_assigned_validation": row[7],
        "excluded_events": excluded[0],
        "excluded_overlap_seconds": excluded[1],
    }


def old_aggregates(con, split_period, strategy, min_seconds, min_ratio):
    con.execute("DROP TABLE IF EXISTS ocand")
    con.execute("DROP TABLE IF EXISTS osel")
    con.execute("""
        CREATE TEMP TABLE ocand AS
        SELECT o.view_id, o.user, o.pid, o.ov, o.ratio, o.vstart
        FROM src.overlaps o JOIN old_assignment a ON a.view_id = o.view_id
        WHERE a.assigned_period = ? AND o.period = ?
          AND o.ov >= ? AND o.ov >= ? * o.pdur
    """, (split_period, split_period, min_seconds, min_ratio))
    if strategy == "A":
        con.execute("CREATE TEMP TABLE osel AS SELECT * FROM ocand")
    else:
        order = "ov DESC, ratio DESC, pid ASC" if strategy == "B" \
            else "ratio DESC, ov DESC, pid ASC"
        con.execute("""
            CREATE TEMP TABLE osel AS
            SELECT view_id, user, pid, ov, ratio, vstart FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY view_id ORDER BY %s) AS rn FROM ocand
            ) WHERE rn = 1
        """ % order)
    n_events = con.execute("SELECT COUNT(*) FROM osel").fetchone()[0]
    out = {}
    for u, pid, ne, ns, tov, mr, xr, fs, ls in con.execute("""
        SELECT user, pid, COUNT(*), COUNT(DISTINCT view_id), SUM(ov),
               AVG(ratio), MAX(ratio), MIN(vstart), MAX(vstart)
        FROM osel GROUP BY user, pid
    """):
        out[(u, pid)] = (str(ne), str(ns), str(tov), "%.6f" % mr,
                         "%.6f" % xr, s.iso_utc(int(fs)), s.iso_utc(int(ls)))
    con.execute("DROP TABLE ocand")
    con.execute("DROP TABLE osel")
    return out, n_events


def read_pairs(path):
    out = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            out[(row[0], row[1])] = tuple(row[2:])
    return out


def main():
    t0 = time.time()
    meta = json.load(open(META, encoding="utf-8"))
    con = sqlite3.connect(DB)
    con.execute("ATTACH DATABASE ? AS src",
                ("file:%s?mode=ro" % os.path.abspath(SRC_DB),))
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-131072")

    build_old_assignment(con)
    old_assign = assignment_summary(con, "old_assignment")
    new_assign = assignment_summary(con, "session_assignment")

    moved = con.execute("""
        SELECT COUNT(*), SUM(o.assigned_period = 1 AND n.assigned_period = 2),
               SUM(o.assigned_period = 2 AND n.assigned_period = 1)
        FROM old_assignment o JOIN session_assignment n USING (view_id)
        WHERE o.assigned_period <> n.assigned_period
    """).fetchone()
    event_moves = con.execute("""
        SELECT
          SUM(o.period = 1 AND old.assigned_period = 2 AND new.assigned_period = 1),
          SUM(o.period = 2 AND old.assigned_period = 2 AND new.assigned_period = 1),
          SUM(o.period <> old.assigned_period AND o.period <> new.assigned_period)
        FROM src.overlaps o
        JOIN old_assignment old ON old.view_id = o.view_id
        JOIN session_assignment new ON new.view_id = o.view_id
    """).fetchone()

    rows = []
    for split, split_period in (("train", 1), ("validation", 2)):
        for rule, min_s, min_r, _desc in s.RULES:
            for strategy in ("A", "B", "C"):
                combo = next(c for c in meta["combinations"]
                             if c["split"] == split and c["strategy"] == strategy
                             and c["rule"] == rule)
                path = os.path.join(ROOT, combo["dataset_file"])
                old, old_events = old_aggregates(con, split_period, strategy,
                                                 min_s, min_r)
                new = read_pairs(path)
                old_keys, new_keys = set(old), set(new)
                added = new_keys - old_keys
                removed = old_keys - new_keys
                changed = sum(1 for k in old_keys & new_keys if old[k] != new[k])
                row = {
                    "split": split,
                    "strategy": strategy,
                    "rule": rule,
                    "rule_min_seconds": min_s,
                    "rule_min_ratio": min_r,
                    "old_events": old_events,
                    "new_events": combo["events"],
                    "delta_events": combo["events"] - old_events,
                    "old_pairs": len(old),
                    "new_pairs": len(new),
                    "delta_pairs": len(new) - len(old),
                    "pairs_added": len(added),
                    "pairs_removed": len(removed),
                    "pairs_changed": changed,
                    "pairs_unchanged": len(old_keys & new_keys) - changed,
                    "aggregate_differs": bool(added or removed or changed),
                }
                rows.append(row)
                print("[cmp] %s %s/%s: events %+d, pairs %+d "
                      "(+%d/-%d/~%d)"
                      % (split, strategy, rule, row["delta_events"],
                         row["delta_pairs"], len(added), len(removed), changed),
                      flush=True)

    summary = {
        "training_combinations_changed": sum(
            1 for r in rows if r["split"] == "train" and r["aggregate_differs"]),
        "validation_combinations_changed": sum(
            1 for r in rows if r["split"] == "validation" and r["aggregate_differs"]),
        "training_pairs_added": sum(r["pairs_added"] for r in rows
                                    if r["split"] == "train"),
        "training_pairs_removed": sum(r["pairs_removed"] for r in rows
                                      if r["split"] == "train"),
        "validation_pairs_added": sum(r["pairs_added"] for r in rows
                                      if r["split"] == "validation"),
        "validation_pairs_removed": sum(r["pairs_removed"] for r in rows
                                        if r["split"] == "validation"),
    }

    result = {
        "generated_by": "src/compare_boundary_assignment.py",
        "status": "provisional",
        "old_rule": {
            "name": "largest_total_labelled_overlap",
            "ordering": ["SUM(ov) DESC", "period ASC"],
            "leakage_concern":
                "Validation-period overlap contributed to the decision of which "
                "training events were retained.",
        },
        "corrected_rule": {
            "name": "earliest_matched_airing",
            "ordering": ["pstart ASC", "period ASC"],
            "leakage_resolution":
                "The decision uses only the earliest matched airing start; no "
                "validation overlap amount or viewing stop time is used.",
        },
        "assignment": {"old": old_assign, "corrected": new_assign},
        "sessions_reassigned": moved[0],
        "sessions_moved_validation_to_training": moved[2],
        "sessions_moved_training_to_validation": moved[1],
        "event_movement": {
            "training_events_newly_included": event_moves[0],
            "validation_events_newly_excluded": event_moves[1],
            "events_excluded_under_both_rules": event_moves[2],
            "net_training_event_change": new_assign["retained_events"]
            - new_assign["excluded_events"]
            - (old_assign["retained_events"] - old_assign["excluded_events"]),
        },
        "combinations": rows,
        "summary": summary,
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                             / 1048576, 1),
    }
    # The net training change is simply the difference in event totals.
    result["event_movement"]["net_training_event_change"] = (
        sum(r["delta_events"] for r in rows if r["split"] == "train"
            and r["strategy"] == "A" and r["rule"] == "A"))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        fields = list(rows[0].keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("[cmp] old assignment:", json.dumps(old_assign))
    print("[cmp] new assignment:", json.dumps(new_assign))
    print("[cmp] wrote %s and %s in %.1fs (peak %.1f MB)"
          % (OUT_JSON, OUT_CSV, result["runtime_seconds"], result["peak_rss_mb"]))


if __name__ == "__main__":
    main()
