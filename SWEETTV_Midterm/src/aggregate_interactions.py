"""Stage 2 threshold sensitivity + multi-match strategy comparison.

Reads the (session, labelled airing) event store produced by
`match_interactions.py` and, for every (strategy, rule) combination:

  1. filters candidate airings with the rule's minimum overlap and coverage,
  2. resolves multiple candidates per session with the strategy,
  3. aggregates surviving events into (user, programme) interactions,
  4. writes a provisional pair-level dataset and summary statistics.

No combination is declared final.  Thresholds and strategies are compared, not
selected.

Outputs
-------
outputs/provisional_interactions/*.csv.gz    12 provisional pair-level datasets
outputs/provisional_interactions/metadata.json
reports/threshold_sensitivity.csv
reports/matching_strategy_comparison.csv
reports/interaction_statistics.json
"""

import csv
import gzip
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
import resource

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "outputs", "_intermediate", "overlaps.sqlite")
OUT_DIR = os.path.join(ROOT, "outputs", "provisional_interactions")
REPORT_DIR = os.path.join(ROOT, "reports")

STRATEGIES = {
    "A": ("keepall", "keep every candidate airing (multi-label)"),
    "B": ("maxoverlap", "keep the candidate with the largest absolute overlap"),
    "C": ("maxratio", "keep the candidate with the largest overlap ratio"),
}

RULE_KEY = {"A": "ruleA", "B": "ruleB", "C": "ruleC", "D": "ruleD"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_distribution(con, table, column):
    vals = [r[0] for r in con.execute(
        "SELECT COUNT(*) FROM %s GROUP BY %s" % (table, column))]
    return s.distribution(vals)


def median_sql(con, table):
    n = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    if not n:
        return 0.0
    lo = con.execute("SELECT ratio FROM %s ORDER BY ratio LIMIT 1 OFFSET ?"
                     % table, ((n - 1) // 2,)).fetchone()[0]
    hi = con.execute("SELECT ratio FROM %s ORDER BY ratio LIMIT 1 OFFSET ?"
                     % table, (n // 2,)).fetchone()[0]
    return (lo + hi) / 2.0


def export_pairs(con, table, path):
    con.execute("""
        CREATE TEMP TABLE pairs AS
        SELECT user, pid,
               COUNT(*) AS n_events,
               COUNT(DISTINCT view_id) AS n_sessions,
               SUM(ov) AS total_overlap_seconds,
               AVG(ratio) AS mean_overlap_ratio,
               MAX(ratio) AS max_overlap_ratio,
               MIN(vstart) AS first_view_start,
               MAX(vstart) AS last_view_start
        FROM %s
        GROUP BY user, pid
    """ % table)
    n = con.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    cur = con.execute("""
        SELECT user, pid, n_events, n_sessions, total_overlap_seconds,
               mean_overlap_ratio, max_overlap_ratio, first_view_start, last_view_start
        FROM pairs ORDER BY user, pid
    """)
    with open(path, "wb") as raw:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0)
        f = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.writer(f)
        w.writerow(["user_id", "tv_show_id", "n_overlap_events", "n_sessions",
                    "total_overlap_seconds", "mean_overlap_ratio", "max_overlap_ratio",
                    "first_view_start", "last_view_start"])
        for row in cur:
            u, pid, ne, ns, tov, mr, xr, fs, ls = row
            w.writerow([u, pid, ne, ns, tov, "%.6f" % mr, "%.6f" % xr,
                        s.iso_utc(int(fs)), s.iso_utc(int(ls))])
        f.flush()
        f.close()
        raw.flush()
    con.execute("DROP TABLE pairs")
    return n


def evaluate(con, strategy, rule, min_seconds, min_ratio):
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS cand")
    con.execute("DROP TABLE IF EXISTS sel")
    con.execute("""
        CREATE TEMP TABLE cand AS
        SELECT view_id, user, pid, ov, ratio, vstart, period
        FROM overlaps
        WHERE ov >= ? AND ov >= ? * pdur
    """, (min_seconds, min_ratio))
    n_cand = con.execute("SELECT COUNT(*) FROM cand").fetchone()[0]

    if strategy == "A":
        con.execute("CREATE TEMP TABLE sel AS SELECT * FROM cand")
    else:
        order = "ov DESC, ratio DESC, pid ASC" if strategy == "B" \
            else "ratio DESC, ov DESC, pid ASC"
        con.execute("""
            CREATE TEMP TABLE sel AS
            SELECT view_id, user, pid, ov, ratio, vstart, period FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY view_id ORDER BY %s
                ) AS rn FROM cand
            ) WHERE rn = 1
        """ % order)

    n_events, n_users, n_pids, n_pairs = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT user), COUNT(DISTINCT pid), "
        "COUNT(DISTINCT user || '\x1f' || pid) FROM sel").fetchone()
    n_sessions = con.execute("SELECT COUNT(DISTINCT view_id) FROM sel").fetchone()[0]
    n_multi = con.execute(
        "SELECT COUNT(*) FROM (SELECT view_id FROM sel GROUP BY view_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    mean_ratio = con.execute("SELECT AVG(ratio) FROM sel").fetchone()[0] or 0.0
    med_ratio = median_sql(con, "sel")
    total_ov = con.execute("SELECT SUM(ov) FROM sel").fetchone()[0] or 0
    by_period = dict(con.execute(
        "SELECT period, COUNT(*) FROM sel GROUP BY period").fetchall())

    name = "strategy%s_%s__%s_%ds_%dpct" % (
        strategy, STRATEGIES[strategy][0], RULE_KEY[rule],
        min_seconds, int(round(min_ratio * 100)))
    path = os.path.join(OUT_DIR, name + ".csv.gz")
    n_pair_rows = export_pairs(con, "sel", path)

    row = {
        "strategy": strategy,
        "strategy_name": STRATEGIES[strategy][0],
        "rule": rule,
        "rule_min_seconds": min_seconds,
        "rule_min_ratio": min_ratio,
        "candidate_events": n_cand,
        "events": n_events,
        "unique_viewing_sessions": n_sessions,
        "unique_users": n_users,
        "unique_programmes": n_pids,
        "unique_user_programme_pairs": n_pairs,
        "pair_rows": n_pair_rows,
        "mean_overlap_ratio": round(mean_ratio, 6),
        "median_overlap_ratio": round(med_ratio, 6),
        "total_overlap_seconds": total_ov,
        "events_period_1": by_period.get(1, 0),
        "events_period_2": by_period.get(2, 0),
        "sessions_with_multiple_matches": n_multi,
        "pct_of_all_viewing_records_with_multiple_matches":
            round(100.0 * n_multi / 5821987, 4),
        "pct_of_matched_records_with_multiple_matches":
            round(100.0 * n_multi / n_sessions, 4) if n_sessions else 0.0,
        "pct_of_all_viewing_records_matched":
            round(100.0 * n_sessions / 5821987, 4),
        "users_with_zero_qualifying_interaction": 4914 - n_users,
        "programmes_with_zero_qualifying_interaction": 8233 - n_pids,
        "events_per_user": count_distribution(con, "sel", "user"),
        "events_per_programme": count_distribution(con, "sel", "pid"),
        "dataset_file": os.path.relpath(path, ROOT),
        "dataset_bytes": os.path.getsize(path),
        "dataset_sha256": sha256(path),
        "runtime_seconds": round(time.time() - t0, 2),
    }
    con.execute("DROP TABLE cand")
    con.execute("DROP TABLE sel")
    return row


def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-131072")

    rows = []
    for strategy in ("A", "B", "C"):
        for rule, min_s, min_r, desc in s.RULES:
            print("[agg] strategy %s, %s" % (strategy, rule), flush=True)
            rows.append(evaluate(con, strategy, rule, min_s, min_r))
    con.close()

    # threshold_sensitivity.csv: one row per (strategy, rule)
    keys = ["strategy", "rule", "rule_min_seconds", "rule_min_ratio",
            "events", "unique_viewing_sessions", "unique_users",
            "unique_programmes", "unique_user_programme_pairs",
            "mean_overlap_ratio", "median_overlap_ratio",
            "users_with_zero_qualifying_interaction",
            "programmes_with_zero_qualifying_interaction",
            "pct_of_matched_records_with_multiple_matches",
            "pct_of_all_viewing_records_matched",
            "events_period_1", "events_period_2"]
    with open(os.path.join(REPORT_DIR, "threshold_sensitivity.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in keys})

    # matching_strategy_comparison.csv: same grid, strategy-oriented columns
    keys2 = ["rule", "strategy", "strategy_name", "events",
             "unique_viewing_sessions", "unique_users", "unique_programmes",
             "unique_user_programme_pairs",
             "events_per_user_mean", "events_per_user_median",
             "events_per_user_p90", "events_per_programme_mean",
             "events_per_programme_median", "events_per_programme_p90",
             "sessions_with_multiple_matches",
             "pct_of_all_viewing_records_with_multiple_matches",
             "mean_overlap_ratio", "median_overlap_ratio"]
    with open(os.path.join(REPORT_DIR, "matching_strategy_comparison.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys2)
        w.writeheader()
        for r in rows:
            out = {k: r[k] for k in keys2 if k in r}
            out["events_per_user_mean"] = r["events_per_user"]["mean"]
            out["events_per_user_median"] = r["events_per_user"]["median"]
            out["events_per_user_p90"] = r["events_per_user"]["p90"]
            out["events_per_programme_mean"] = r["events_per_programme"]["mean"]
            out["events_per_programme_median"] = r["events_per_programme"]["median"]
            out["events_per_programme_p90"] = r["events_per_programme"]["p90"]
            w.writerow(out)

    meta = {
        "status": "PROVISIONAL — no final threshold or strategy selected",
        "generated_by": "src/aggregate_interactions.py",
        "event_definition": "one (viewing session, schedule airing) pair with positive overlap",
        "rule_definition": "keep an airing if overlap_seconds >= min_seconds AND overlap_seconds >= min_ratio * programme_duration",
        "strategies": {k: {"name": v[0], "definition": v[1]} for k, v in STRATEGIES.items()},
        "rules": {r[0]: {"min_seconds": r[1], "min_ratio": r[2], "description": r[3]}
                  for r in s.RULES},
        "aggregation": "counted by (user_id, tv_show_id); n_sessions counts distinct viewing sessions",
        "observation_window": "2020-03-09 00:00:02 .. 2020-07-26 23:59:58 (no test-period viewing)",
        "offset_applied_seconds": 0,
        "combinations": rows,
    }
    with open(os.path.join(OUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    stats = {
        "generated_by": "src/aggregate_interactions.py",
        "status": "provisional",
        "viewing_universe": {"sessions": 5821987, "users": 4914,
                             "labelled_programmes_in_training_schedules": 8233},
        "combinations": rows,
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
    }
    with open(os.path.join(REPORT_DIR, "interaction_statistics.json"), "w",
              encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print("[agg] wrote %d provisional datasets in %.1fs (peak %.1f MB)"
          % (len(rows), stats["runtime_seconds"], stats["peak_rss_mb"]))


if __name__ == "__main__":
    main()
