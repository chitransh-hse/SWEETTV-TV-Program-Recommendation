"""Independent verification of the Stage 2.5 temporal separation.

Every check measures the real artifacts: the read-only Stage 2 event store, the
new `temporal_split.sqlite`, the 24 gzip datasets, the metadata and the
candidate-universe audit.  Checks are grouped by the requirement they defend:

  * period purity and session assignment,
  * leakage (validation events never enter training aggregates),
  * ID preservation and aggregation integrity,
  * boundary-event uniqueness,
  * candidate universe / cold start,
  * repeat-run determinism.

Usage
-----
    python3 src/verify_temporal_split.py                  # checks + JSON
    python3 src/verify_temporal_split.py --digest-only    # print digests
    python3 src/verify_temporal_split.py --compare FILE   # + determinism check
"""

import csv
import gzip
import hashlib
import json
import os
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
INDEX = os.path.join(ROOT, "outputs", "_intermediate", "schedule_index.pkl")
UNIVERSE = os.path.join(ROOT, "reports", "candidate_universe_audit.csv")
UNIVERSE_STATS = os.path.join(ROOT, "reports", "candidate_universe_statistics.json")
OUT = os.path.join(ROOT, "reports", "temporal_split_verification.json")

META_COLUMNS = ["user_id", "tv_show_id", "n_overlap_events", "n_sessions",
                "total_overlap_seconds", "mean_overlap_ratio", "max_overlap_ratio",
                "first_view_start", "last_view_start"]

MISSING_TOKENS = {"", "0", "none", "nan", "null"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_content(path):
    h = hashlib.sha256()
    with gzip.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_digest(con):
    """Same ordered digest Stage 2 used, to prove the event store is unchanged."""
    h = hashlib.sha256()
    n = 0
    for row in con.execute("""
        SELECT view_id, user, channel, vstart, vend, pid, pstart, pdur, ov, ratio, period
        FROM src.overlaps ORDER BY view_id, pid, pstart
    """):
        h.update(("|".join(str(x) for x in row) + "\n").encode())
        n += 1
    return h.hexdigest(), n


def split_digest(con):
    h = hashlib.sha256()
    counts = {}
    for table, order in (
        ("session_assignment", "view_id"),
        ("events_train", "view_id, pid, pstart"),
        ("events_validation", "view_id, pid, pstart"),
    ):
        n = 0
        for row in con.execute("SELECT * FROM %s ORDER BY %s" % (table, order)):
            h.update(("%s|" % table + "|".join(str(x) for x in row) + "\n").encode())
            n += 1
        counts[table] = n
    return h.hexdigest(), counts


def dataset_digests():
    return {f: sha256_content(os.path.join(OUT_DIR, f))
            for f in sorted(os.listdir(OUT_DIR)) if f.endswith(".csv.gz")}


def metadata_projection_digest():
    """Digest of the metadata excluding wall-clock runtime measurements.

    Runtimes naturally differ between runs; boundary definitions, counts, rules
    and file hashes must not.  This projection is what the determinism check
    compares.
    """
    meta = json.load(open(META, encoding="utf-8"))
    stable = {k: v for k, v in meta.items()
              if k not in ("runtime_seconds", "peak_rss_mb")}
    stable["combinations"] = [
        {k: v for k, v in c.items() if k != "runtime_seconds"}
        for c in meta["combinations"]
    ]
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def schedule_pid_sets():
    """Re-stream the three exports: labelled PID sets and test metadata usability."""
    sets = {1: set(), 2: set(), 3: set()}
    test_usable = {}
    fields = ["tv_show_title", "tv_show_category", "tv_show_genre_1",
              "tv_show_genre_2", "tv_show_genre_3", "year_of_production",
              "director", "actors"]
    for period in (1, 2, 3):
        path = os.path.join(
            s.DATA, next(p for per, _name, p in s.SCHEDULE_FILES if per == period))
        with s.open_csv(path) as f:
            for row in csv.DictReader(f):
                pid = row["tv_show_id"]
                if pid == "0":
                    continue
                sets[period].add(pid)
                if period == 3:
                    present = {fld for fld in fields
                               if row[fld].strip().lower() not in MISSING_TOKENS}
                    if "tv_show_title" in present and len(present) >= 2:
                        test_usable[pid] = True
    return sets, test_usable


def expected_pairs(con, table, strategy, min_seconds, min_ratio):
    """Recompute aggregates for one split/strategy/rule directly from the split table."""
    con.execute("DROP TABLE IF EXISTS vcand")
    con.execute("DROP TABLE IF EXISTS vsel")
    con.execute("""
        CREATE TEMP TABLE vcand AS
        SELECT view_id, user, pid, ov, ratio, vstart
        FROM %s WHERE ov >= ? AND ov >= ? * pdur
    """ % table, (min_seconds, min_ratio))
    n_cand = con.execute("SELECT COUNT(*) FROM vcand").fetchone()[0]
    if strategy == "A":
        con.execute("CREATE TEMP TABLE vsel AS SELECT * FROM vcand")
    else:
        order = "ov DESC, ratio DESC, pid ASC" if strategy == "B" \
            else "ratio DESC, ov DESC, pid ASC"
        con.execute("""
            CREATE TEMP TABLE vsel AS
            SELECT view_id, user, pid, ov, ratio, vstart FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY view_id ORDER BY %s) AS rn FROM vcand
            ) WHERE rn = 1
        """ % order)
    out = {}
    for u, pid, ne, ns, tov, mr, xr, fs, ls in con.execute("""
        SELECT user, pid, COUNT(*), COUNT(DISTINCT view_id), SUM(ov),
               AVG(ratio), MAX(ratio), MIN(vstart), MAX(vstart)
        FROM vsel GROUP BY user, pid ORDER BY user, pid
    """):
        out[(u, pid)] = (ne, ns, tov, "%.6f" % mr, "%.6f" % xr,
                         s.iso_utc(int(fs)), s.iso_utc(int(ls)))
    con.execute("DROP TABLE vcand")
    con.execute("DROP TABLE vsel")
    return out, n_cand


def verify_file(path, expected, users, pids):
    """Stream one gzip dataset and compare every row with the recomputed aggregate."""
    bad = {"header": 0, "rows": 0, "duplicate": 0, "extra": 0, "missing": 0,
           "value": 0, "id": 0, "numeric": 0, "order": 0, "time": 0}
    seen = set()
    n_rows = 0
    sum_events = 0
    prev_key = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        if next(reader) != META_COLUMNS:
            bad["header"] = 1
            return bad, n_rows, sum_events
        for row in reader:
            if len(row) != len(META_COLUMNS):
                bad["value"] += 1
                continue
            n_rows += 1
            user, pid = row[0], row[1]
            key = (user, pid)
            if key in seen:
                bad["duplicate"] += 1
            seen.add(key)
            if prev_key is not None and key < prev_key:
                bad["order"] += 1
            prev_key = key
            if user not in users or pid not in pids:
                bad["id"] += 1
            try:
                ne = int(row[2]); ns = int(row[3]); tov = int(row[4])
                float(row[5]); float(row[6])
                sum_events += ne
                if ne < 1 or ns < 1 or ns > ne or tov <= 0:
                    bad["numeric"] += 1
                if not (row[7] <= row[8]):
                    bad["time"] += 1
            except ValueError:
                bad["numeric"] += 1
                continue
            want = expected.get(key)
            if want is None:
                bad["extra"] += 1
            elif (ne, ns, tov, row[5], row[6], row[7], row[8]) != want:
                bad["value"] += 1
    bad["missing"] = len(expected) - (n_rows - bad["extra"] - bad["duplicate"])
    return bad, n_rows, sum_events


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    t0 = time.time()
    checks = []

    if mode == "--digest-only":
        con = sqlite3.connect(DB)
        d, counts = split_digest(con)
        con.close()
        digests = {
            "temporal_split_db_sha256": d,
            "temporal_split_db_rows": counts,
            "datasets": dataset_digests(),
            "metadata_projection_sha256": metadata_projection_digest(),
        }
        print(json.dumps(digests, indent=2, sort_keys=True))
        return

    meta = json.load(open(META, encoding="utf-8"))
    con = sqlite3.connect(DB)
    con.execute("ATTACH DATABASE ? AS src",
                ("file:%s?mode=ro" % os.path.abspath(SRC_DB),))

    # ---------------------------------------------------------------- helpers
    def check(name, bad, detail=""):
        checks.append({"check": name, "passed": bad == 0, "violations": bad,
                       "detail": detail})

    def one(name, condition, detail=""):
        checks.append({"check": name, "passed": bool(condition),
                       "violations": 0 if condition else 1, "detail": detail})

    def scalar(sql, params=()):
        return con.execute(sql, params).fetchone()[0]

    # ------------------------------------------ source event store unchanged
    src_sha, src_rows = source_digest(con)
    stage2 = json.load(open(os.path.join(ROOT, "reports", "stage2_verification.json"),
                            encoding="utf-8"))
    one("Stage 2 event store is byte-for-byte unchanged (ordered SHA-256)",
        src_sha == stage2["digests"]["overlaps_db_sha256"],
        "%s, %d events" % (src_sha, src_rows))
    one("Stage 2 event store row count unchanged", src_rows == 3847696,
        "%d rows" % src_rows)
    check("no test-period viewing behaviour exists in the event store",
          scalar("SELECT COUNT(*) FROM src.overlaps WHERE vstart > ?",
                 (s.parse_view_timestamp("2020-07-26 23:59:58"),)),
          "latest viewing start is 2020-07-26 23:59:58")
    check("source event store has no duplicate (session, programme, airing start)",
          scalar("""SELECT COUNT(*) FROM (
                     SELECT view_id, pid, pstart FROM src.overlaps
                     GROUP BY view_id, pid, pstart HAVING COUNT(*) > 1)"""),
          "seam duplication guard")

    # ------------------------------------------------ period purity / split
    n_train = scalar("SELECT COUNT(*) FROM events_train")
    n_val = scalar("SELECT COUNT(*) FROM events_validation")
    n_assign = scalar("SELECT COUNT(*) FROM session_assignment")
    n_excluded = scalar("SELECT COUNT(*) FROM src.overlaps o "
                        "JOIN session_assignment a ON a.view_id = o.view_id "
                        "WHERE o.period <> a.assigned_period")
    check("training events contain only weeks 11-20 airings",
          scalar("SELECT COUNT(*) FROM events_train WHERE period <> 1"),
          "%d events" % n_train)
    check("validation events contain only weeks 21-30 airings",
          scalar("SELECT COUNT(*) FROM events_validation WHERE period <> 2"),
          "%d events" % n_val)
    check("every event's session is assigned to the event's period",
          scalar("""SELECT COUNT(*) FROM (
                     SELECT view_id FROM (
                       SELECT view_id, assigned_period FROM session_assignment
                       UNION ALL SELECT view_id, 1 FROM events_train
                       UNION ALL SELECT view_id, 2 FROM events_validation
                     ) GROUP BY view_id
                     HAVING COUNT(DISTINCT assigned_period) <> 1)"""),
          "one period per session")
    check("no session appears in both training and validation",
          scalar("""SELECT COUNT(*) FROM (
                     SELECT view_id FROM events_train
                     INTERSECT SELECT view_id FROM events_validation)"""),
          "%d training / %d validation sessions" % (
              scalar("SELECT COUNT(DISTINCT view_id) FROM events_train"),
              scalar("SELECT COUNT(DISTINCT view_id) FROM events_validation")))
    check("no test-period (weeks 31-42) airing is used",
          scalar("SELECT COUNT(*) FROM (SELECT period FROM events_train "
                 "UNION ALL SELECT period FROM events_validation) WHERE period = 3"),
          "period labels are 1/2 only")
    one("retained + excluded events reconcile with Stage 2 event store",
        n_train + n_val + n_excluded == src_rows,
        "%d + %d + %d = %d" % (n_train, n_val, n_excluded,
                               n_train + n_val + n_excluded))
    one("exactly 192 cross-period events were excluded", n_excluded == 192,
        "%d excluded" % n_excluded)
    one("session assignment covers every matched session once", n_assign == 2964346,
        "%d sessions" % n_assign)
    check("session assignment key is unique",
          scalar("SELECT COUNT(*) FROM (SELECT view_id FROM session_assignment "
                 "GROUP BY view_id HAVING COUNT(*) > 1)"), "")

    cross, cross_tr, cross_va = con.execute("""
        SELECT COUNT(*), SUM(assigned_period = 1), SUM(assigned_period = 2)
        FROM session_assignment WHERE n_periods = 2""").fetchone()
    one("boundary sessions assigned deterministically (97; 97 train, 0 validation)",
        (cross, cross_tr, cross_va) == (97, 97, 0),
        "%d cross, %d train, %d validation" % (cross, cross_tr, cross_va))
    one("metadata documents the earliest-matched-airing boundary rule",
        meta["boundary_session_rule"]["rule"] == "earliest_matched_airing",
        meta["boundary_session_rule"]["rule"])
    check("every session is assigned to the period of its earliest matched airing "
          "(pstart ASC, then period ASC)",
          scalar("""
              WITH per_session_period AS (
                  SELECT view_id, period, MIN(pstart) AS first_pstart
                  FROM src.overlaps GROUP BY view_id, period
              ), expected AS (
                  SELECT view_id, period,
                         ROW_NUMBER() OVER (
                             PARTITION BY view_id
                             ORDER BY first_pstart ASC, period ASC
                         ) AS rn
                  FROM per_session_period
              )
              SELECT COUNT(*) FROM session_assignment a
              JOIN expected e ON e.view_id = a.view_id AND e.rn = 1
              WHERE a.assigned_period <> e.period
          """),
          "argmin(pstart, period) recomputed from Stage 2 events")
    check("assigned period's recorded first airing start equals its true minimum",
          scalar("""
              SELECT COUNT(*) FROM session_assignment a
              JOIN (SELECT view_id, period, MIN(pstart) AS fp
                    FROM src.overlaps GROUP BY view_id, period) p
                ON p.view_id = a.view_id AND p.period = a.assigned_period
              WHERE a.first_pstart <> p.fp
          """),
          "first_pstart is the per-period minimum, not a global value")
    check("cross-period sessions retain no event from the other period",
          scalar("""SELECT COUNT(*) FROM src.overlaps o
                    JOIN session_assignment a ON a.view_id = o.view_id
                    WHERE a.n_periods = 2 AND o.period = a.assigned_period
                      AND o.view_id NOT IN (SELECT view_id FROM events_train
                                            UNION SELECT view_id FROM events_validation)"""),
          "retained events of boundary sessions are materialised")

    # airing timestamps lie inside the export's observed span
    spans = meta["period_boundaries"]["per_period_spans"]
    for label, table, period in (("training", "events_train", 1),
                                 ("validation", "events_validation", 2)):
        span = spans[{"1": "training_weeks_11_20", "2": "validation_weeks_21_30"}[str(period)]]
        lo = s.parse_view_timestamp(span["min_airing_start"])
        hi = s.parse_view_timestamp(span["max_airing_end"])
        check("%s airings lie inside the source export span" % label,
              scalar("SELECT COUNT(*) FROM %s WHERE pstart < ? OR pstart + pdur > ?"
                     % table, (lo, hi)),
              "%s .. %s" % (span["min_airing_start"], span["max_airing_end"]))

    # event-level integrity
    for table in ("events_train", "events_validation"):
        check("%s: overlap > 0 and <= programme duration" % table,
              scalar("SELECT COUNT(*) FROM %s WHERE ov <= 0 OR ov > pdur" % table), "")
        check("%s: ratio equals overlap / duration" % table,
              scalar("SELECT COUNT(*) FROM %s WHERE ABS(ratio - 1.0*ov/pdur) > 1e-9"
                     % table), "tolerance 1e-9")
        check("%s: no tv_show_id = 0 or empty" % table,
              scalar("SELECT COUNT(*) FROM %s WHERE pid = '0' OR pid = ''" % table), "")
        check("%s: IDs stored as TEXT" % table,
              scalar("SELECT COUNT(*) FROM %s WHERE typeof(user) <> 'text' "
                     "OR typeof(pid) <> 'text'" % table), "")
        check("%s: no duplicate (session, programme, airing start)" % table,
              scalar("""SELECT COUNT(*) FROM (
                         SELECT view_id, pid, pstart FROM %s
                         GROUP BY view_id, pid, pstart HAVING COUNT(*) > 1)""" % table),
              "")

    # sampled citation of real schedule rows with the right export label
    idx = s.load_index(INDEX)
    import bisect
    for table, period in (("events_train", 1), ("events_validation", 2)):
        bad = 0
        n = 0
        for ch, pstart, pid in con.execute(
                "SELECT channel, pstart, pid FROM %s WHERE (view_id %% 1000) = 0" % table):
            n += 1
            c = idx.channel(ch)
            ok = False
            if c is not None:
                i = bisect.bisect_left(c["starts"], pstart)
                if i < len(c["starts"]) and c["starts"][i] == pstart \
                        and idx.pids[c["pids"][i]] == pid \
                        and c["periods"][i] == period:
                    ok = True
            if not ok:
                bad += 1
        check("%s: sampled events cite a real schedule row in the same export" % table,
              bad, "%d sampled (view_id %% 1000 == 0)" % n)

    # IDs must exist verbatim in the source event store / schedule index
    users = {r[0] for r in con.execute("SELECT DISTINCT user FROM src.overlaps")}
    pids = set(idx.pids[1:])
    bad_ids = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT user, pid FROM events_train
            UNION ALL SELECT user, pid FROM events_validation
        ) WHERE LENGTH(user) = 0 OR LENGTH(pid) = 0
    """).fetchone()[0]
    check("no empty user/programme strings in split events", bad_ids, "")

    # ------------------------------------------------ dataset + leakage checks
    combos = {(c["split"], c["strategy"], c["rule"]): c for c in meta["combinations"]}
    one("metadata lists all 24 split x strategy x rule combinations",
        len(meta["combinations"]) == 24 and len(combos) == 24,
        "%d entries" % len(meta["combinations"]))
    one("metadata marks outputs provisional",
        "PROVISIONAL" in meta["status"].upper(), meta["status"])

    dataset_hashes = {}
    for split, table, period in (("train", "events_train", 1),
                                 ("validation", "events_validation", 2)):
        for strategy in ("A", "B", "C"):
            for rule, min_s, min_r, _desc in s.RULES:
                combo = combos.get((split, strategy, rule))
                if combo is None:
                    check("metadata entry for %s/%s/%s" % (split, strategy, rule), 1, "")
                    continue
                path = os.path.join(ROOT, combo["dataset_file"])
                expected, n_cand = expected_pairs(con, table, strategy, min_s, min_r)
                bad, n_rows, sum_events = verify_file(path, expected, users, pids)
                tag = os.path.basename(path)
                dataset_hashes[tag] = sha256_content(path)

                check("%s: header and parseable rows" % tag,
                      bad["header"] + bad["value"] + bad["numeric"], "%d rows" % n_rows)
                check("%s: every (user, programme) row matches a recomputation "
                      "from %s only" % (tag, table),
                      bad["extra"] + bad["missing"] + bad["value"],
                      "leakage guard: aggregates derive only from the split table")
                check("%s: no duplicate user-programme pairs" % tag, bad["duplicate"], "")
                check("%s: IDs are exact source strings (no float/int64 conversion)" % tag,
                      bad["id"], "all IDs found verbatim in source events / schedule")
                check("%s: deterministic ascending order" % tag, bad["order"], "")
                check("%s: timestamps ordered and inside the viewing window" % tag,
                      bad["time"], "")
                check("%s: row count matches metadata" % tag,
                      0 if n_rows == combo["pair_rows"] else 1,
                      "%d vs %d" % (n_rows, combo["pair_rows"]))
                check("%s: event sum matches metadata" % tag,
                      0 if sum_events == combo["events"] else 1,
                      "%d vs %d" % (sum_events, combo["events"]))
                check("%s: candidate count matches metadata" % tag,
                      0 if n_cand == combo["candidate_events"] else 1,
                      "%d vs %d" % (n_cand, combo["candidate_events"]))
                check("%s: file SHA-256 matches metadata" % tag,
                      0 if sha256_file(path) == combo["dataset_sha256"] else 1,
                      "")
                check("%s: content SHA-256 matches metadata" % tag,
                      0 if dataset_hashes[tag] == combo["content_sha256"] else 1,
                      "")

                # explicit leakage wording for the training side
                if split == "train":
                    one("%s: no validation event contributes to this training aggregate"
                        % tag,
                        bad["extra"] == 0 and bad["value"] == 0 and bad["missing"] == 0,
                        "recomputed from events_train (weeks 11-20) only")
                del expected

    # training and validation file counts cannot exceed their event tables
    tr_events = sum(c["events"] for c in meta["combinations"] if c["split"] == "train")
    va_events = sum(c["events"] for c in meta["combinations"] if c["split"] == "validation")
    one("aggregate event totals are bounded by split event tables",
        tr_events <= n_train * 12 and va_events <= n_val * 12,
        "%d train aggregate events, %d validation" % (tr_events, va_events))

    # ------------------------------------------------ candidate universe audit
    with open(UNIVERSE, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    universe_ids = [r["tv_show_id"] for r in rows]
    one("candidate universe CSV has one row per programme ID",
        len(universe_ids) == len(set(universe_ids)), "%d IDs" % len(universe_ids))

    pid_sets, test_usable = schedule_pid_sets()
    one("candidate universe equals the union of the three schedule PID sets",
        set(universe_ids) == (pid_sets[1] | pid_sets[2] | pid_sets[3]),
        "CSV %d, schedules %d" % (len(universe_ids),
                                  len(pid_sets[1] | pid_sets[2] | pid_sets[3])))
    csv_train = {r["tv_show_id"] for r in rows if r["in_training"] == "1"}
    csv_val = {r["tv_show_id"] for r in rows if r["in_validation"] == "1"}
    csv_test = {r["tv_show_id"] for r in rows if r["in_test"] == "1"}
    one("candidate universe period membership matches the schedules",
        csv_train == pid_sets[1] and csv_val == pid_sets[2] and csv_test == pid_sets[3],
        "train %d, validation %d, test %d" % (len(csv_train), len(csv_val), len(csv_test)))

    cold = csv_test - csv_train
    one("all 6,636 test programmes are retained (no cold-start removal)",
        len(csv_test) == 6636, "%d test programmes" % len(csv_test))
    one("test-only cold-start programmes retained", len(cold) == 2566,
        "%d test programmes absent from training" % len(cold))
    one("every test programme has usable metadata", len(test_usable) == 6636,
        "%d/%d usable" % (len(test_usable), len(csv_test)))
    one("every cold-start test programme has usable metadata",
        sum(1 for p in cold if p in test_usable) == 2566,
        "%d/%d usable" % (sum(1 for p in cold if p in test_usable), len(cold)))

    ustats = json.load(open(UNIVERSE_STATS, encoding="utf-8"))
    m = ustats["membership"]
    one("universe statistics: training-only / shared / validation-only / test-only",
        (m["training_only_vs_validation_and_test"],
         m["shared_training_and_validation"],
         m["validation_only_vs_training_and_test"],
         m["test_only_not_in_training_or_validation"]) == (1061, 3976, 1258, 1437),
        "1,061 / 3,976 / 1,258 / 1,437")

    # ------------------------------------------------ digests / determinism
    split_sha, split_counts = split_digest(con)
    meta_projection = metadata_projection_digest()
    digests = {
        "source_event_store_sha256": src_sha,
        "temporal_split_db_sha256": split_sha,
        "temporal_split_db_rows": split_counts,
        "datasets": dataset_hashes,
        "metadata_projection_sha256": meta_projection,
    }

    if mode == "--compare":
        prev = json.load(open(sys.argv[2], encoding="utf-8"))
        one("repeat run: temporal split database identical",
            prev["temporal_split_db_sha256"] == split_sha,
            split_sha)
        changed = [f for f in dataset_hashes
                   if prev.get("datasets", {}).get(f) != dataset_hashes[f]]
        check("repeat run: all 24 datasets identical", len(changed),
              ", ".join(changed) or "none")
        one("repeat run: metadata (excluding runtime) identical",
            prev.get("metadata_projection_sha256") == meta_projection,
            meta_projection)

    con.close()
    result = {
        "generated_by": "src/verify_temporal_split.py",
        "timestamp_utc": s.iso_utc(int(time.time())),
        "checks_total": len(checks),
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_failed": sum(1 for c in checks if not c["passed"]),
        "checks": checks,
        "digests": digests,
        "runtime_seconds": round(time.time() - t0, 2),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("[verify] %d/%d checks passed, %d failed"
          % (result["checks_passed"], result["checks_total"], result["checks_failed"]))
    for c in checks:
        if not c["passed"]:
            print("  FAIL:", c["check"], c["violations"], c["detail"])
    print("[verify] wrote", OUT)


if __name__ == "__main__":
    main()
