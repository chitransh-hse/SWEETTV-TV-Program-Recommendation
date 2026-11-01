"""Independent verification of the Stage 2 interaction pipeline.

Checks are run against the actual artifacts (SQLite event store, provisional
datasets, metadata), not against in-memory assumptions.  Every check reports a
real measured count.

Usage:
    python3 src/verify_stage2.py                 # run checks, write JSON
    python3 src/verify_stage2.py --digest-only   # print deterministic digests
    python3 src/verify_stage2.py --compare FILE  # compare digests with FILE
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "outputs", "_intermediate", "overlaps.sqlite")
OUT_DIR = os.path.join(ROOT, "outputs", "provisional_interactions")
META = os.path.join(OUT_DIR, "metadata.json")
INDEX = os.path.join(ROOT, "outputs", "_intermediate", "schedule_index.pkl")
OUT = os.path.join(ROOT, "reports", "stage2_verification.json")

MAX_VIEW_START = s.parse_view_timestamp("2020-07-26 23:59:58")


def content_sha256(path):
    h = hashlib.sha256()
    with gzip.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def db_digest(con):
    h = hashlib.sha256()
    cur = con.execute("""
        SELECT view_id, user, channel, vstart, vend, pid, pstart, pdur, ov, ratio, period
        FROM overlaps ORDER BY view_id, pid, pstart
    """)
    n = 0
    for row in cur:
        h.update(("|".join(str(x) for x in row) + "\n").encode())
        n += 1
    return h.hexdigest(), n


def viewing_universe():
    users = set()
    n = 0
    min_st = None
    max_st = None
    for user, ch, start, stop, dur, _n in s.iter_viewing():
        users.add(user)
        n += 1
        if min_st is None or start < min_st:
            min_st = start
        if max_st is None or start > max_st:
            max_st = start
    return users, n, min_st, max_st


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    t0 = time.time()
    idx = s.load_index(INDEX)
    pid_set = set(idx.pids[1:])

    con = sqlite3.connect(DB)
    checks = []
    digests = {}

    if mode == "--digest-only":
        d, n = db_digest(con)
        digests["overlaps_db_rows"] = n
        digests["overlaps_db_sha256"] = d
        files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".csv.gz"))
        digests["datasets"] = {f: content_sha256(os.path.join(OUT_DIR, f)) for f in files}
        print(json.dumps(digests, indent=2))
        return

    users, n_view, min_st, max_st = viewing_universe()

    def check(name, bad, detail):
        checks.append({"check": name, "passed": bad == 0, "violations": bad,
                       "detail": detail})

    # ---- event store checks ---------------------------------------------
    n_events = con.execute("SELECT COUNT(*) FROM overlaps").fetchone()[0]
    bad = con.execute("SELECT COUNT(*) FROM overlaps WHERE pid = '0' OR pid = ''").fetchone()[0]
    check("no tv_show_id = 0 in event store", bad, "labelled events only")

    bad = con.execute("SELECT COUNT(*) FROM overlaps WHERE ov <= 0 OR ov > pdur").fetchone()[0]
    check("overlap > 0 and <= programme duration", bad,
          "%d events; min/max overlap checked" % n_events)

    bad = con.execute(
        "SELECT COUNT(*) FROM overlaps WHERE ABS(ratio - (1.0*ov/pdur)) > 1e-9").fetchone()[0]
    check("ratio equals overlap/programme_duration", bad, "tolerance 1e-9")

    bad = con.execute("SELECT COUNT(*) FROM overlaps WHERE vstart >= vend").fetchone()[0]
    check("viewing start < stop", bad, "all events")

    bad = con.execute("SELECT COUNT(*) FROM overlaps WHERE period NOT IN (1,2)").fetchone()[0]
    check("no test-period (weeks 31-42) schedule rows used", bad, "period in (1,2)")

    bad = con.execute("SELECT COUNT(*) FROM overlaps WHERE vstart > ?", (MAX_VIEW_START,)).fetchone()[0]
    check("no future viewing (start <= 2020-07-26 23:59:58)", bad,
          "observation window only")

    d_users = {r[0] for r in con.execute("SELECT DISTINCT user FROM overlaps")}
    bad = len(d_users - users)
    check("every event user_id exists verbatim in viewing log", bad,
          "%d distinct users in events" % len(d_users))

    d_pids = {r[0] for r in con.execute("SELECT DISTINCT pid FROM overlaps")}
    bad = len(d_pids - pid_set)
    check("every event programme ID exists verbatim in training schedules", bad,
          "%d distinct programmes in events" % len(d_pids))

    # sample: the cited airing really exists in the schedule index
    sample_bad = 0
    sample_n = 0
    for ch, pstart, pid in con.execute(
            "SELECT channel, pstart, pid FROM overlaps WHERE (view_id % 20) = 0"):
        sample_n += 1
        c = idx.channel(ch)
        ok = False
        if c is not None:
            import bisect
            i = bisect.bisect_left(c["starts"], pstart)
            if i < len(c["starts"]) and c["starts"][i] == pstart and idx.pids[c["pids"][i]] == pid:
                ok = True
        if not ok:
            sample_bad += 1
    check("sampled events cite a real (channel, start, programme) schedule row",
          sample_bad, "%d sampled (view_id %% 20 == 0)" % sample_n)

    # ---- provisional dataset checks -------------------------------------
    meta = json.load(open(META, encoding="utf-8"))
    for combo in meta["combinations"]:
        path = os.path.join(ROOT, combo["dataset_file"])
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            header = next(r)
            expect = ["user_id", "tv_show_id", "n_overlap_events", "n_sessions",
                      "total_overlap_seconds", "mean_overlap_ratio", "max_overlap_ratio",
                      "first_view_start", "last_view_start"]
            if header != expect:
                check("header %s" % os.path.basename(path), 1, str(header))
                continue
            pairs = set()
            dup = 0
            bad_pid = bad_user = bad_time = bad_num = 0
            n_rows = 0
            sum_events = 0
            for row in r:
                n_rows += 1
                user, pid = row[0], row[1]
                key = (user, pid)
                if key in pairs:
                    dup += 1
                pairs.add(key)
                if pid == "0" or pid not in pid_set:
                    bad_pid += 1
                if user not in users:
                    bad_user += 1
                if row[7] > row[8] or row[8] > "2020-07-26 23:59:58":
                    bad_time += 1
                try:
                    ne = int(row[2]); ns = int(row[3]); tov = int(row[4])
                    mr = float(row[5]); xr = float(row[6])
                    if ne < 1 or ns < 1 or tov <= 0 or not (0 <= mr <= 1) or not (0 <= xr <= 1) \
                            or ns > ne:
                        bad_num += 1
                    sum_events += ne
                except ValueError:
                    bad_num += 1
        tag = os.path.basename(path)
        check("dataset %s: unique (user, programme)" % tag, dup, "%d rows" % n_rows)
        check("dataset %s: valid non-zero programme IDs" % tag, bad_pid, "")
        check("dataset %s: user IDs preserved" % tag, bad_user, "")
        check("dataset %s: timestamps inside window and ordered" % tag, bad_time, "")
        check("dataset %s: numeric sanity" % tag, bad_num, "")
        check("dataset %s: rows match metadata" % tag,
              0 if n_rows == combo["pair_rows"] else 1,
              "%d vs %d" % (n_rows, combo["pair_rows"]))
        check("dataset %s: event sum matches metadata" % tag,
              0 if sum_events == combo["events"] else 1,
              "%d vs %d" % (sum_events, combo["events"]))

    check("event store count matches match_diagnostics.json",
          0 if n_events == json.load(open(os.path.join(ROOT, "reports", "match_diagnostics.json")))[
              "labelled_events"] else 1,
          "%d events" % n_events)

    # ---- digests for the repeat-run comparison ---------------------------
    d, n = db_digest(con)
    digests["overlaps_db_rows"] = n
    digests["overlaps_db_sha256"] = d
    files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".csv.gz"))
    digests["datasets"] = {f: content_sha256(os.path.join(OUT_DIR, f)) for f in files}
    con.close()

    if mode == "--compare":
        prev = json.load(open(sys.argv[2], encoding="utf-8"))
        same_db = prev["overlaps_db_sha256"] == digests["overlaps_db_sha256"]
        changed = [f for f in digests["datasets"]
                   if prev.get("datasets", {}).get(f) != digests["datasets"][f]]
        check("repeat run: event store identical", 0 if same_db else 1, digests["overlaps_db_sha256"])
        check("repeat run: all 12 datasets identical", len(changed), ", ".join(changed) or "none")

    result = {
        "generated_by": "src/verify_stage2.py",
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
