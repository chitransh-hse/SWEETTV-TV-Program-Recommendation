"""Stage 2 interval matcher: viewing sessions -> scheduled programmes.

For every viewing row it finds every schedule row on the same channel whose
interval overlaps, using the compact per-channel index.  It writes

  outputs/_intermediate/overlaps.sqlite   one row per (session, labelled airing)
  reports/match_diagnostics.json          aggregate join statistics

An overlap event is a (viewing session, schedule row) pair.  Unlabelled airings
(tv_show_id = 0) are counted for diagnostics but never written as interactions.
No threshold and no multi-match resolution is applied here; that is done in
`aggregate_interactions.py`, so every threshold/strategy combination is evaluated
from the same event store.

The join uses the union of the weeks 11-20 and 21-30 exports.  Those exports
hand over per channel without repeating slots (one unlabelled cross-export
overlap pair was found), so a session cannot be matched twice to the same slot.
"""

import json
import os
import sqlite3
import sys
import time
import resource

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "outputs", "_intermediate", "schedule_index.pkl")
DB = os.path.join(ROOT, "outputs", "_intermediate", "overlaps.sqlite")
OUT = os.path.join(ROOT, "reports", "match_diagnostics.json")

SEAM = s.parse_view_timestamp("2020-05-18 00:00:00")  # 11-20 / 21-30 export seam


def main():
    t0 = time.time()
    idx = s.load_index(INDEX)

    for path in (DB, DB + "-journal", DB + "-wal"):
        if os.path.exists(path):
            os.remove(path)

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=FILE")
    con.execute("""
        CREATE TABLE overlaps (
            view_id INTEGER NOT NULL,
            user TEXT NOT NULL,
            channel INTEGER NOT NULL,
            vstart INTEGER NOT NULL,
            vend INTEGER NOT NULL,
            pid TEXT NOT NULL,
            pstart INTEGER NOT NULL,
            pdur INTEGER NOT NULL,
            ov INTEGER NOT NULL,
            ratio REAL NOT NULL,
            period INTEGER NOT NULL
        )
    """)

    stats = {
        "sessions": 0,
        "users": 0,
        "labelled_events": 0,
        "unlabelled_events": 0,
        "sessions_0_any": 0,
        "sessions_1_any": 0,
        "sessions_2plus_any": 0,
        "sessions_0_labelled": 0,
        "sessions_1_labelled": 0,
        "sessions_2plus_labelled": 0,
        "sessions_only_unlabelled": 0,
        "sessions_labelled_and_unlabelled": 0,
        "sessions_only_labelled": 0,
        "zero_no_channel": 0,
        "zero_before_coverage": 0,
        "zero_after_coverage": 0,
        "zero_gap_inside_coverage": 0,
        "cross_seam_sessions": 0,
        "cross_period_match_sessions": 0,
        "session_seconds": 0,
        "labelled_overlap_seconds": 0,
        "unlabelled_overlap_seconds": 0,
        "session_seconds_without_labelled": 0,
        "session_seconds_without_any": 0,
        "max_labelled_matches_in_session": 0,
        "long_sessions_ge_4h": 0,
        "long_sessions_ge_4h_with_labelled": 0,
        "events_with_ov_gt_pdur": 0,
        "invalid_ov": 0,
        "ratio_out_of_range": 0,
    }
    users = set()

    ins = ("INSERT INTO overlaps (view_id,user,channel,vstart,vend,pid,pstart,pdur,ov,ratio,period) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    batch = []
    log_every = 500000

    for user, ch_s, start, stop, dur, view_id in s.iter_viewing():
        stats["sessions"] += 1
        users.add(user)
        ch = int(ch_s)
        matches = idx.overlaps(ch, start, stop)
        stats["session_seconds"] += stop - start

        if not matches:
            stats["sessions_0_any"] += 1
            stats["session_seconds_without_any"] += stop - start
            c = idx.channel(ch)
            if c is None:
                stats["zero_no_channel"] += 1
            else:
                first = int(c["starts"][0])
                last = int(max(c["ends"]))
                if stop <= first:
                    stats["zero_before_coverage"] += 1
                elif start >= last:
                    stats["zero_after_coverage"] += 1
                else:
                    stats["zero_gap_inside_coverage"] += 1
        else:
            stats["sessions_1_any" if len(matches) == 1 else "sessions_2plus_any"] += 1

        n_lab = 0
        n_unlab = 0
        periods_seen = set()
        lab_seconds = 0
        unlab_seconds = 0
        for pid, ps, pe, pdur, period, ov in matches:
            if pid == "0":
                n_unlab += 1
                unlab_seconds += ov
                stats["unlabelled_events"] += 1
            else:
                n_lab += 1
                periods_seen.add(period)
                lab_seconds += ov
                stats["labelled_events"] += 1
                if ov <= 0:
                    stats["invalid_ov"] += 1
                if ov > pdur:
                    stats["events_with_ov_gt_pdur"] += 1
                ratio = s.overlap_ratio(ov, pdur)
                if not (0.0 <= ratio <= 1.0):
                    stats["ratio_out_of_range"] += 1
                batch.append((view_id, user, ch, start, stop, pid, ps, pdur, ov, ratio, period))
            stats["labelled_overlap_seconds" if pid != "0" else "unlabelled_overlap_seconds"] += ov

        if n_lab == 0:
            stats["sessions_0_labelled"] += 1
            stats["session_seconds_without_labelled"] += stop - start
        elif n_lab == 1:
            stats["sessions_1_labelled"] += 1
        else:
            stats["sessions_2plus_labelled"] += 1
        if n_lab > stats["max_labelled_matches_in_session"]:
            stats["max_labelled_matches_in_session"] = n_lab
        if n_lab and n_unlab:
            stats["sessions_labelled_and_unlabelled"] += 1
        elif n_unlab:
            stats["sessions_only_unlabelled"] += 1
        elif n_lab:
            stats["sessions_only_labelled"] += 1
        if len(periods_seen) > 1:
            stats["cross_period_match_sessions"] += 1
        if start < SEAM < stop:
            stats["cross_seam_sessions"] += 1
        if stop - start >= 14400:
            stats["long_sessions_ge_4h"] += 1
            if n_lab:
                stats["long_sessions_ge_4h_with_labelled"] += 1

        if len(batch) >= 20000:
            con.executemany(ins, batch)
            batch = []
            if stats["sessions"] % log_every == 0:
                print("[match] %d sessions, %d labelled events"
                      % (stats["sessions"], stats["labelled_events"]), flush=True)

    if batch:
        con.executemany(ins, batch)
    con.commit()

    print("[match] building indexes", flush=True)
    con.execute("CREATE INDEX ix_overlaps_view ON overlaps(view_id)")
    con.execute("CREATE INDEX ix_overlaps_pid ON overlaps(pid)")
    con.commit()

    stats["users"] = len(users)
    stats["distinct_users_with_labelled_overlap"] = con.execute(
        "SELECT COUNT(DISTINCT user) FROM overlaps").fetchone()[0]
    stats["distinct_pids_with_labelled_overlap"] = con.execute(
        "SELECT COUNT(DISTINCT pid) FROM overlaps").fetchone()[0]
    stats["distinct_sessions_with_labelled_overlap"] = con.execute(
        "SELECT COUNT(DISTINCT view_id) FROM overlaps").fetchone()[0]
    stats["labelled_overlap_seconds_pct_of_session_seconds"] = round(
        100 * stats["labelled_overlap_seconds"] / stats["session_seconds"], 3)
    stats["unlabelled_overlap_seconds_pct_of_session_seconds"] = round(
        100 * stats["unlabelled_overlap_seconds"] / stats["session_seconds"], 3)
    stats["sessions_without_labelled_pct"] = round(
        100 * stats["sessions_0_labelled"] / stats["sessions"], 3)
    stats["session_seconds_without_labelled_pct"] = round(
        100 * stats["session_seconds_without_labelled"] / stats["session_seconds"], 3)
    stats["sessions_without_any_pct"] = round(
        100 * stats["sessions_0_any"] / stats["sessions"], 3)
    stats["sessions_2plus_labelled_pct"] = round(
        100 * stats["sessions_2plus_labelled"] / stats["sessions"], 3)
    stats["sessions_only_unlabelled_pct"] = round(
        100 * stats["sessions_only_unlabelled"] / stats["sessions"], 3)
    stats["events_ov_gt_pdur"] = stats.pop("events_with_ov_gt_pdur")
    stats["db_bytes"] = os.path.getsize(DB)
    stats["runtime_seconds"] = round(time.time() - t0, 2)
    stats["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576, 1)
    stats["python"] = sys.version.split()[0]
    stats["method"] = ("stdlib csv streaming + per-channel array index + disk-backed "
                       "SQLite event store; no pandas; no threshold applied")
    stats["join_rule"] = ("viewing.vsetv_id = schedule.channel_id and "
                          "max(0, min(view_end, programme_end) - max(view_start, programme_start)) > 0")
    stats["schedule_scope"] = "union of weeks 11-20 and 21-30 exports (training + validation)"
    stats["offset_applied_seconds"] = 0

    con.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print("[match] sessions=%d labelled_events=%d unlabelled_events=%d"
          % (stats["sessions"], stats["labelled_events"], stats["unlabelled_events"]))
    print("[match] %.1fs, peak %.1f MB, db %.1f MB"
          % (stats["runtime_seconds"], stats["peak_rss_mb"], stats["db_bytes"] / 1048576))


if __name__ == "__main__":
    main()
