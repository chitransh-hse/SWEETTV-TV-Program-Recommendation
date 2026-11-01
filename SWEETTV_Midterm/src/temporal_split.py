"""Stage 2.5: temporally separated training / validation interaction datasets.

Reads the Stage 2 event store (`outputs/_intermediate/overlaps.sqlite`, attached
read-only) and the three schedule exports, then:

  1. derives exact calendar boundaries and per-channel handover facts directly
     from the schedule exports (no ISO week arithmetic, no offset);
  2. assigns every viewing session that has labelled matches to exactly one
     schedule period with a deterministic rule
     (period of the earliest matched airing; ties -> earlier period);
  3. materialises `events_train` (matches to weeks 11-20) and
     `events_validation` (matches to weeks 21-30), dropping only the events of a
     cross-period session that belong to the period it was *not* assigned to;
  4. applies all 12 (strategy, rule) combinations to each split independently
     and writes 24 deterministic gzip datasets keyed by (user_id, tv_show_id).

Nothing is selected, trained, or tuned.  All outputs are provisional.
The Stage 2 event store is never written to.

Outputs
-------
outputs/_intermediate/temporal_split.sqlite
outputs/temporal_interactions/{train,validation}_strategy*_rule*_*.csv.gz
outputs/temporal_interactions/metadata.json
reports/temporal_split_statistics.json
"""

import csv
import gzip
import hashlib
import io
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
REPORT_DIR = os.path.join(ROOT, "reports")

STRATEGIES = {
    "A": ("keepall", "keep every candidate airing (multi-label)"),
    "B": ("maxoverlap", "keep the candidate with the largest absolute overlap"),
    "C": ("maxratio", "keep the candidate with the largest overlap ratio"),
}
PERIOD_LABEL = {1: "training_weeks_11_20", 2: "validation_weeks_21_30",
                3: "test_weeks_31_42"}
SPLIT_PERIOD = {"train": 1, "validation": 2}

# Boundary membership is defined by the source export that contains an airing,
# not by comparing a timestamp to an ISO week.  These nominal instants are the
# broadcast-day starts at which the exports hand over; they are reported for
# interpretation only.
NOMINAL_BOUNDARY_TRAIN_VAL = "2020-05-18 00:00:00"
NOMINAL_BOUNDARY_VAL_TEST = "2020-07-27 00:00:00"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha256(path):
    h = hashlib.sha256()
    with gzip.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def schedule_boundaries():
    """Exact spans and per-channel handover facts from the schedule exports.

    A single streaming pass per export.  An airing belongs to the export it was
    shipped in; this is the only unambiguous period label at the seam because
    the exports are broadcast-day exports with per-channel handover times.
    """
    spans = {}
    channel_starts = {}
    channel_ends = {}
    for period in (1, 2, 3):
        min_start = None
        max_start = None
        max_end = None
        rows = 0
        labelled = 0
        channels = set()
        st = {}
        en = {}
        for ch_s, start, dur, pid, per, _n in s.iter_schedule(period):
            rows += 1
            if pid != "0":
                labelled += 1
            ch = int(ch_s)
            end = start + dur
            channels.add(ch)
            if min_start is None or start < min_start:
                min_start = start
            if max_start is None or start > max_start:
                max_start = start
            if max_end is None or end > max_end:
                max_end = end
            if ch not in st or start < st[ch]:
                st[ch] = start
            if ch not in en or end > en[ch]:
                en[ch] = end
        spans[period] = {
            "export": next(name for per, name, _p in s.SCHEDULE_FILES if per == period),
            "rows": rows,
            "labelled_rows": labelled,
            "channels": len(channels),
            "min_airing_start": s.iso_utc(min_start),
            "max_airing_start": s.iso_utc(max_start),
            "max_airing_end": s.iso_utc(max_end),
        }
        channel_starts[period] = st
        channel_ends[period] = en

    handover = {}
    for earlier, later in ((1, 2), (2, 3)):
        shared = sorted(set(channel_starts[earlier]) & set(channel_starts[later]))
        exact = gaps = overlaps = 0
        gap_examples = []
        overlap_examples = []
        for ch in shared:
            delta = channel_starts[later][ch] - channel_ends[earlier][ch]
            if delta == 0:
                exact += 1
            elif delta > 0:
                gaps += 1
                if len(gap_examples) < 5:
                    gap_examples.append({
                        "channel": ch, "gap_seconds": delta,
                        "earlier_end": s.iso_utc(channel_ends[earlier][ch]),
                        "later_start": s.iso_utc(channel_starts[later][ch]),
                    })
            else:
                overlaps += 1
                if len(overlap_examples) < 5:
                    overlap_examples.append({
                        "channel": ch, "overlap_seconds": -delta,
                        "earlier_end": s.iso_utc(channel_ends[earlier][ch]),
                        "later_start": s.iso_utc(channel_starts[later][ch]),
                    })
        handover["%d->%d" % (earlier, later)] = {
            "shared_channels": len(shared),
            "exactly_contiguous": exact,
            "with_gap": gaps,
            "with_overlap": overlaps,
            "gap_examples": gap_examples,
            "overlap_examples": overlap_examples,
            "only_earlier": sorted(set(channel_starts[earlier]) - set(channel_starts[later])),
            "only_later": sorted(set(channel_starts[later]) - set(channel_starts[earlier])),
        }
    return spans, handover


def build_assignment(con):
    """Assign each session to one period and materialise split event tables."""
    con.executescript("""
        PRAGMA temp_store=FILE;
        PRAGMA cache_size=-131072;
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;

        DROP TABLE IF EXISTS session_assignment;
        DROP TABLE IF EXISTS events_train;
        DROP TABLE IF EXISTS events_validation;

        CREATE TABLE session_assignment AS
        WITH per_session_period AS (
            SELECT view_id, period,
                   SUM(ov) AS ov_sum,
                   COUNT(*) AS n_events,
                   MIN(pstart) AS first_pstart
            FROM src.overlaps
            GROUP BY view_id, period
        ), ranked AS (
            SELECT view_id, period, ov_sum, n_events, first_pstart,
                   SUM(ov_sum) OVER (PARTITION BY view_id) AS total_overlap,
                   SUM(n_events) OVER (PARTITION BY view_id) AS total_events,
                   COUNT(*) OVER (PARTITION BY view_id) AS n_periods,
                   ROW_NUMBER() OVER (
                       PARTITION BY view_id
                       ORDER BY first_pstart ASC, period ASC
                   ) AS rn
            FROM per_session_period
        )
        SELECT view_id,
               period AS assigned_period,
               ov_sum AS assigned_overlap,
               n_events AS assigned_events,
               total_overlap - ov_sum AS excluded_overlap,
               total_events - n_events AS excluded_events,
               n_periods,
               first_pstart
        FROM ranked
        WHERE rn = 1;
    """)

    con.executescript("""
        CREATE TABLE events_train AS
        SELECT o.view_id, o.user, o.channel, o.vstart, o.vend, o.pid, o.pstart,
               o.pdur, o.ov, o.ratio, o.period
        FROM src.overlaps o
        JOIN session_assignment a ON a.view_id = o.view_id
        WHERE a.assigned_period = 1 AND o.period = 1;

        CREATE TABLE events_validation AS
        SELECT o.view_id, o.user, o.channel, o.vstart, o.vend, o.pid, o.pstart,
               o.pdur, o.ov, o.ratio, o.period
        FROM src.overlaps o
        JOIN session_assignment a ON a.view_id = o.view_id
        WHERE a.assigned_period = 2 AND o.period = 2;

        CREATE UNIQUE INDEX ux_assignment_view ON session_assignment(view_id);
        CREATE INDEX ix_train_view ON events_train(view_id);
        CREATE INDEX ix_train_user ON events_train(user);
        CREATE INDEX ix_train_pid ON events_train(pid);
        CREATE INDEX ix_val_view ON events_validation(view_id);
        CREATE INDEX ix_val_user ON events_validation(user);
        CREATE INDEX ix_val_pid ON events_validation(pid);
    """)


def assignment_stats(con, source_events):
    stats = {}
    for row in con.execute("""
        SELECT assigned_period, COUNT(*), SUM(assigned_events),
               SUM(assigned_overlap), SUM(excluded_events), SUM(excluded_overlap)
        FROM session_assignment GROUP BY assigned_period ORDER BY assigned_period
    """):
        period, sessions, events, overlap, ex_events, ex_overlap = row
        stats[period] = {
            "sessions": sessions,
            "events": events,
            "overlap_seconds": overlap,
            "excluded_events": ex_events,
            "excluded_overlap_seconds": ex_overlap,
        }
    cross = con.execute("""
        SELECT COUNT(*), SUM(assigned_period = 1), SUM(assigned_period = 2),
               SUM(assigned_events), SUM(excluded_events),
               SUM(assigned_overlap), SUM(excluded_overlap)
        FROM session_assignment WHERE n_periods = 2
    """).fetchone()
    total_sessions = con.execute("SELECT COUNT(*) FROM session_assignment").fetchone()[0]
    split_events = con.execute(
        "SELECT (SELECT COUNT(*) FROM events_train) + "
        "(SELECT COUNT(*) FROM events_validation)").fetchone()[0]
    both_sessions = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT view_id FROM events_train
            INTERSECT
            SELECT view_id FROM events_validation
        )
    """).fetchone()[0]
    return {
        "sessions_with_labelled_events": total_sessions,
        "assigned_training_weeks_11_20": stats.get(1, {}).get("sessions", 0),
        "assigned_validation_weeks_21_30": stats.get(2, {}).get("sessions", 0),
        "events_training": stats.get(1, {}).get("events", 0),
        "events_validation": stats.get(2, {}).get("events", 0),
        "source_labelled_events": source_events,
        "events_retained_total": split_events,
        "events_excluded_total": source_events - split_events,
        "sessions_in_both_splits": both_sessions,
        "cross_period_sessions": cross[0] or 0,
        "cross_assigned_training": cross[1] or 0,
        "cross_assigned_validation": cross[2] or 0,
        "cross_retained_events": cross[3] or 0,
        "cross_excluded_events": cross[4] or 0,
        "cross_retained_overlap_seconds": cross[5] or 0,
        "cross_excluded_overlap_seconds": cross[6] or 0,
    }, stats


def export_pairs(con, table, path):
    con.execute("DROP TABLE IF EXISTS pairs")
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
    cur = con.execute("""
        SELECT user, pid, n_events, n_sessions, total_overlap_seconds,
               mean_overlap_ratio, max_overlap_ratio, first_view_start, last_view_start
        FROM pairs ORDER BY user, pid
    """)
    n = 0
    with open(path, "wb") as raw:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=6, mtime=0)
        f = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.writer(f)
        w.writerow(["user_id", "tv_show_id", "n_overlap_events", "n_sessions",
                    "total_overlap_seconds", "mean_overlap_ratio", "max_overlap_ratio",
                    "first_view_start", "last_view_start"])
        for u, pid, ne, ns, tov, mr, xr, fs, ls in cur:
            n += 1
            w.writerow([u, pid, ne, ns, tov, "%.6f" % mr, "%.6f" % xr,
                        s.iso_utc(int(fs)), s.iso_utc(int(ls))])
        f.flush()
        f.close()
        raw.flush()
    con.execute("DROP TABLE pairs")
    return n


def median_sql(con, table):
    n = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    if not n:
        return 0.0
    lo = con.execute("SELECT ratio FROM %s ORDER BY ratio LIMIT 1 OFFSET ?"
                     % table, ((n - 1) // 2,)).fetchone()[0]
    hi = con.execute("SELECT ratio FROM %s ORDER BY ratio LIMIT 1 OFFSET ?"
                     % table, (n // 2,)).fetchone()[0]
    return (lo + hi) / 2.0


def evaluate(con, split, strategy, rule, min_seconds, min_ratio):
    t0 = time.time()
    table = "events_train" if split == "train" else "events_validation"
    con.execute("DROP TABLE IF EXISTS cand")
    con.execute("DROP TABLE IF EXISTS sel")
    con.execute("""
        CREATE TEMP TABLE cand AS
        SELECT view_id, user, pid, ov, ratio, vstart, period
        FROM %s
        WHERE ov >= ? AND ov >= ? * pdur
    """ % table, (min_seconds, min_ratio))
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
        "COUNT(DISTINCT user || char(31) || pid) FROM sel").fetchone()
    n_sessions = con.execute("SELECT COUNT(DISTINCT view_id) FROM sel").fetchone()[0]
    n_multi = con.execute(
        "SELECT COUNT(*) FROM (SELECT view_id FROM sel GROUP BY view_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    mean_ratio = con.execute("SELECT AVG(ratio) FROM sel").fetchone()[0] or 0.0
    med_ratio = median_sql(con, "sel")
    total_ov = con.execute("SELECT SUM(ov) FROM sel").fetchone()[0] or 0
    first_view, last_view = con.execute(
        "SELECT MIN(vstart), MAX(vstart) FROM sel").fetchone()

    name = "%s_strategy%s_rule%s_%ds_%dpct.csv.gz" % (
        split, strategy, rule, min_seconds, int(round(min_ratio * 100)))
    path = os.path.join(OUT_DIR, name)
    n_pair_rows = export_pairs(con, "sel", path)

    row = {
        "split": split,
        "period": SPLIT_PERIOD[split],
        "schedule_period": PERIOD_LABEL[SPLIT_PERIOD[split]],
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
        "sessions_with_multiple_matches": n_multi,
        "first_view_start": s.iso_utc(int(first_view)) if first_view is not None else None,
        "last_view_start": s.iso_utc(int(last_view)) if last_view is not None else None,
        "users_with_zero_qualifying_interaction": 4914 - n_users,
        "dataset_file": os.path.relpath(path, ROOT),
        "dataset_bytes": os.path.getsize(path),
        "dataset_sha256": sha256(path),
        "content_sha256": content_sha256(path),
        "runtime_seconds": round(time.time() - t0, 2),
    }
    con.execute("DROP TABLE cand")
    con.execute("DROP TABLE sel")
    return row


def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    for path in (DB, DB + "-journal", DB + "-wal"):
        if os.path.exists(path):
            os.remove(path)

    print("[split] deriving schedule boundaries", flush=True)
    spans, handover = schedule_boundaries()

    con = sqlite3.connect(DB)
    con.execute("ATTACH DATABASE ? AS src",
                ("file:%s?mode=ro" % os.path.abspath(SRC_DB),))
    source_events = con.execute("SELECT COUNT(*) FROM src.overlaps").fetchone()[0]
    source_periods = dict(con.execute(
        "SELECT period, COUNT(*) FROM src.overlaps GROUP BY period").fetchall())
    source_digest = con.execute(
        "SELECT COUNT(*), TOTAL(ov), TOTAL(pdur), SUM(view_id), "
        "SUM(LENGTH(user)), SUM(LENGTH(pid)) FROM src.overlaps").fetchone()

    print("[split] assigning %d sessions and materialising split events"
          % con.execute("SELECT COUNT(DISTINCT view_id) FROM src.overlaps").fetchone()[0],
          flush=True)
    build_assignment(con)
    con.commit()

    assign, split_period_stats = assignment_stats(con, source_events)
    print("[split] assignment:", json.dumps(assign), flush=True)

    rows = []
    for split in ("train", "validation"):
        for rule, min_s, min_r, _desc in s.RULES:
            for strategy in ("A", "B", "C"):
                print("[split] %s strategy %s rule %s" % (split, strategy, rule), flush=True)
                rows.append(evaluate(con, split, strategy, rule, min_s, min_r))

    con.close()

    meta = {
        "status": "PROVISIONAL — no final threshold or strategy selected",
        "stage": "2.5",
        "generated_by": "src/temporal_split.py",
        "source_event_store": "outputs/_intermediate/overlaps.sqlite (read-only)",
        "source_event_store_unchanged": True,
        "split_database": "outputs/_intermediate/temporal_split.sqlite",
        "event_definition": "one (viewing session, schedule airing) pair with positive overlap",
        "rule_definition":
            "keep an airing if overlap_seconds >= min_seconds AND "
            "overlap_seconds >= min_ratio * programme_duration",
        "strategies": {k: {"name": v[0], "definition": v[1]}
                       for k, v in STRATEGIES.items()},
        "rules": {r[0]: {"min_seconds": r[1], "min_ratio": r[2], "description": r[3]}
                  for r in s.RULES},
        "period_membership":
            "An airing belongs to the export file that contains it: weeks 11-20 -> "
            "training, weeks 21-30 -> validation, weeks 31-42 -> test. Membership is "
            "not recomputed from a timestamp.",
        "period_boundaries": {
            "nominal_training_validation_boundary": NOMINAL_BOUNDARY_TRAIN_VAL,
            "nominal_validation_test_boundary": NOMINAL_BOUNDARY_VAL_TEST,
            "handling":
                "The exports are broadcast-day exports with per-channel handover, so "
                "no single instant cleanly separates them. Timestamps are inclusive at "
                "an airing's start and exclusive at start + duration. Period membership "
                "is the source export label, which is unambiguous.",
            "per_period_spans": {
                PERIOD_LABEL[p]: spans[p] for p in (1, 2, 3)
            },
            "channel_handover": handover,
        },
        "boundary_session_rule": {
            "rule": "earliest_matched_airing",
            "description":
                "A session whose labelled matches span both weeks 11-20 and weeks 21-30 "
                "is assigned to the period of its earliest matched airing, ordered by "
                "airing start (pstart) ascending and then period ascending as a "
                "deterministic tie-breaker. Only events whose airing period equals the "
                "assigned period are retained. A session is never counted in both "
                "splits. Neither viewing stop time nor total overlap seconds is used, "
                "so validation-period overlap cannot influence which training events "
                "are kept.",
            "ordering": ["pstart ASC", "period ASC"],
            "excluded_events": assign["events_excluded_total"],
            "statistics": assign,
        },
        "aggregation":
            "counted by (user_id, tv_show_id); IDs are exact strings; tv_show_id = 0 "
            "is never included; n_sessions counts distinct viewing sessions",
        "observation_window": "2020-03-09 00:00:02 .. 2020-07-26 23:59:58 "
                              "(no test-period viewing exists)",
        "offset_applied_seconds": 0,
        "combinations": rows,
        "runtime_seconds": round(time.time() - t0, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1),
        "python": sys.version.split()[0],
    }
    with open(os.path.join(OUT_DIR, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # Machine-readable stats summary for the report builder.
    stats = {
        "generated_by": "src/temporal_split.py",
        "status": "provisional",
        "source_event_store": {
            "path": "outputs/_intermediate/overlaps.sqlite",
            "labelled_events": source_events,
            "events_period_1": source_periods.get(1, 0),
            "events_period_2": source_periods.get(2, 0),
            "aggregate_fingerprint": {
                "events": source_digest[0],
                "total_overlap_seconds": source_digest[1],
                "total_programme_duration_seconds": source_digest[2],
                "sum_view_id": source_digest[3],
                "sum_user_length": source_digest[4],
                "sum_pid_length": source_digest[5],
            },
        },
        "period_boundaries": meta["period_boundaries"],
        "boundary_session_rule": meta["boundary_session_rule"],
        "per_period_assignment": {
            PERIOD_LABEL[p]: split_period_stats[p] for p in (1, 2)
        },
        "combination_summary": [
            {k: r[k] for k in (
                "split", "strategy", "strategy_name", "rule", "rule_min_seconds",
                "rule_min_ratio", "candidate_events", "events",
                "unique_viewing_sessions", "unique_users", "unique_programmes",
                "unique_user_programme_pairs", "mean_overlap_ratio",
                "median_overlap_ratio", "total_overlap_seconds",
                "sessions_with_multiple_matches", "dataset_file",
                "dataset_bytes", "dataset_sha256")}
            for r in rows
        ],
        "combinations": rows,
        "runtime_seconds": meta["runtime_seconds"],
        "peak_rss_mb": meta["peak_rss_mb"],
        "python": sys.version.split()[0],
    }
    with open(os.path.join(REPORT_DIR, "temporal_split_statistics.json"), "w",
              encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print("[split] wrote %d datasets in %.1fs (peak %.1f MB)"
          % (len(rows), stats["runtime_seconds"], stats["peak_rss_mb"]))


if __name__ == "__main__":
    main()
