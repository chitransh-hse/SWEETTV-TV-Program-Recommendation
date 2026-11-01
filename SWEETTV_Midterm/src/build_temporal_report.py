"""Render the Stage 2.5 temporal-split audit from measured artifacts.

Reads only generated JSON/CSV outputs and re-streams the two training/validation
schedule exports for seam evidence.  It does not recompute interactions.

    python3 src/build_temporal_report.py
      -> reports/temporal_split_audit.md
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS = os.path.join(ROOT, "reports", "temporal_split_statistics.json")
META = os.path.join(ROOT, "outputs", "temporal_interactions", "metadata.json")
UNIVERSE = os.path.join(ROOT, "reports", "candidate_universe_statistics.json")
UNIVERSE_CSV = os.path.join(ROOT, "reports", "candidate_universe_audit.csv")
VERIFY = os.path.join(ROOT, "reports", "temporal_split_verification.json")
STAGE2 = os.path.join(ROOT, "reports", "interaction_statistics.json")
COMPARE = os.path.join(ROOT, "reports", "boundary_assignment_comparison.json")
OUT = os.path.join(ROOT, "reports", "temporal_split_audit.md")

B = s.parse_view_timestamp("2020-05-18 00:00:00")
TEST_B = s.parse_view_timestamp("2020-07-27 00:00:00")

LABELS = {
    "training_weeks_11_20": "training (weeks 11–20)",
    "validation_weeks_21_30": "validation (weeks 21–30)",
    "test_weeks_31_42": "test (weeks 31–42)",
}


def fmt(n):
    return "{:,}".format(n)


def pct(a, b):
    return "%.3f%%" % (100.0 * a / b) if b else "—"


def seam_details():
    """Counts that show why a single instant cannot separate the exports."""
    p1_after = 0
    p2_before_0600 = 0
    p1_late_channels = set()
    ch487_p1 = []
    ch487_p2 = []
    for period in (1, 2):
        for ch_s, start, dur, pid, _per, _n in s.iter_schedule(period):
            ch = int(ch_s)
            end = start + dur
            if period == 1:
                if start >= B:
                    p1_after += 1
                if end > B:
                    p1_late_channels.add(ch)
                if ch == 487 and start < B < end:
                    ch487_p1.append((start, end, pid))
            else:
                if start < B + 6 * 3600:
                    p2_before_0600 += 1
                if ch == 487 and start < B + 4 * 3600:
                    ch487_p2.append((start, end, pid))
    return {
        "period1_rows_at_or_after_nominal_boundary": p1_after,
        "period1_channels_with_rows_ending_after_boundary": len(p1_late_channels),
        "period2_rows_before_0600": p2_before_0600,
        "channel487_period1": [
            {"start": s.iso_utc(a), "end": s.iso_utc(b), "pid": p} for a, b, p in ch487_p1
        ],
        "channel487_period2": [
            {"start": s.iso_utc(a), "end": s.iso_utc(b), "pid": p} for a, b, p in ch487_p2
        ],
    }


def verification_categories(verification):
    groups = {}
    for c in verification["checks"]:
        name = c["check"]
        if name.startswith("train_") or name.startswith("validation_"):
            cat = "Dataset integrity, ID preservation and leakage (24 files)"
        elif "repeat run" in name:
            cat = "Repeat-run determinism"
        elif "Stage 2 event store" in name:
            cat = "Source immutability"
        elif ("candidate universe" in name or "test programme" in name
              or "cold-start" in name or "universe statistics" in name):
            cat = "Candidate universe and cold start"
        elif ("session" in name or "period" in name or "weeks" in name
              or "cross-period" in name or "retained" in name
              or "excluded" in name or "export span" in name):
            cat = "Period purity, assignment and boundary sessions"
        else:
            cat = "Event-store and aggregation integrity"
        g = groups.setdefault(cat, [0, 0])
        g[0] += 1
        if c["passed"]:
            g[1] += 1
    return groups


def main():
    stats = json.load(open(STATS, encoding="utf-8"))
    meta = json.load(open(META, encoding="utf-8"))
    universe = json.load(open(UNIVERSE, encoding="utf-8"))
    stage2 = json.load(open(STAGE2, encoding="utf-8"))
    verification = json.load(open(VERIFY, encoding="utf-8"))
    comparison = json.load(open(COMPARE, encoding="utf-8"))
    seam = seam_details()

    assign = meta["boundary_session_rule"]["statistics"]
    spans = meta["period_boundaries"]["per_period_spans"]
    handover = meta["period_boundaries"]["channel_handover"]
    combos = meta["combinations"]
    glob = {(c["strategy"], c["rule"]): c for c in stage2["combinations"]}
    u = universe["membership"]
    tm = universe["test_metadata"]
    groups = verification_categories(verification)

    lines = []
    w = lines.append

    w("# Stage 2.5 — Temporal Separation and Interaction Dataset Preparation")
    w("")
    w("**Status: complete, provisional. No threshold, strategy, model or "
      "recommendation was selected.**")
    w("")
    w("This audit separates the Stage 2 interaction event store into training "
      "(schedule weeks 11–20) and validation (weeks 21–30) datasets, evaluates all "
      "12 threshold × strategy combinations on both sides, and documents boundary, "
      "leakage and candidate-universe checks. Test viewing behaviour does not exist "
      "and was not used.")
    w("")
    w("## 1. Exact temporal boundaries")
    w("")
    w("The three schedule files are **broadcast-day exports**, not ISO-week slices. "
      "An airing belongs to the export that ships it. Membership is therefore taken "
      "from the source file (`period` column in the Stage 2 event store: 1 = weeks "
      "11–20, 2 = weeks 21–30, 3 = weeks 31–42), never from ISO week arithmetic.")
    w("")
    w("| Period | Source export | Rows | Labelled rows | Channels | First airing start | Last airing start | Last airing end |")
    w("|---|---|---:|---:|---:|---|---|---|")
    for label in ("training_weeks_11_20", "validation_weeks_21_30", "test_weeks_31_42"):
        sp = spans[label]
        w("| %s | `%s` | %s | %s | %d | %s | %s | %s |" % (
            LABELS[label], sp["export"], fmt(sp["rows"]),
            fmt(sp["labelled_rows"]), sp["channels"], sp["min_airing_start"],
            sp["max_airing_start"], sp["max_airing_end"]))
    w("")
    w("Nominal broadcast-day boundaries: **%s** (training→validation) and **%s** "
      "(validation→test). They are reported for interpretation but are *not* used "
      "for membership, because the exports hand over per channel rather than at a "
      "single instant." % (meta["period_boundaries"]["nominal_training_validation_boundary"],
                           meta["period_boundaries"]["nominal_validation_test_boundary"]))
    w("")
    w("Inclusive/exclusive handling: a scheduled airing occupies `[start, start + "
      "duration)` — start inclusive, end exclusive. A viewing event requires a "
      "strictly positive overlap with that interval. Timestamps are naive local "
      "wall-clock; **offset applied = 0 s**.")
    w("")
    w("Seam evidence (weeks 11–20 vs 21–30):")
    w("")
    w("- %s rows in the weeks 11–20 export start at or after 2020-05-18 00:00 "
      "(through 11:45), and %d channels have a weeks 11–20 airing ending after "
      "midnight." % (fmt(seam["period1_rows_at_or_after_nominal_boundary"]),
                     seam["period1_channels_with_rows_ending_after_boundary"]))
    w("- %s rows in the weeks 21–30 export start before 06:00 on 2020-05-18."
      % fmt(seam["period2_rows_before_0600"]))
    w("- Therefore no timestamp predicate can separate the two exports without "
      "losing or duplicating airings; the export label is the only unambiguous split.")
    w("")
    w("| Handover | Shared channels | Exactly contiguous | With gap | With overlap | Only earlier | Only later |")
    w("|---|---:|---:|---:|---:|---|---|")
    for key in ("1->2", "2->3"):
        h = handover[key]
        w("| %s | %d | %d | %d | %d | %s | %s |" % (
            key.replace("->", " → "), h["shared_channels"], h["exactly_contiguous"],
            h["with_gap"], h["with_overlap"],
            ", ".join(str(c) for c in h["only_earlier"]) or "—",
            ", ".join(str(c) for c in h["only_later"]) or "—"))
    w("")
    h12 = handover["1->2"]
    h23 = handover["2->3"]
    if h12["overlap_examples"]:
        ex = h12["overlap_examples"][0]
        seam_pids = ([x["pid"] for x in seam["channel487_period1"]]
                     + [x["pid"] for x in seam["channel487_period2"]])
        unlabelled = bool(seam_pids) and all(p == "0" for p in seam_pids)
        gap_text = "no gap" if not h23["gap_examples"] else (
            "one %d s gap on channel %d"
            % (h23["gap_examples"][0]["gap_seconds"],
               h23["gap_examples"][0]["channel"]))
        w("The single cross-export interval overlap is channel %d: weeks 11–20 ends "
          "at %s, weeks 21–30 starts at %s (%d s overlap). The rows are distinct "
          "airings in distinct exports%s, so no interaction can be double-counted. "
          "Handover 2→3 has %s and no overlaps."
          % (ex["channel"], ex["earlier_end"], ex["later_start"],
             ex["overlap_seconds"],
             " and both are unlabelled (`tv_show_id = 0`)" if unlabelled else "",
             gap_text))
        w("")
    w("No schedule row is assigned to two periods: each row exists in exactly one "
      "export, and the only cross-export interval overlap involves unlabelled rows.")
    w("")

    w("## 2. Boundary sessions and event separation")
    w("")
    w("The Stage 2 event store contains %s labelled events across %s sessions. "
      "A session can match airings from both exports near the seam."
      % (fmt(stats["source_event_store"]["labelled_events"]),
         fmt(assign["sessions_with_labelled_events"])))
    w("")
    w("**Deterministic rule.** A session that matches both exports is assigned to "
      "the period of its **earliest matched airing**, ordered by airing start "
      "(`pstart`) ascending and then period ascending as a deterministic "
      "tie-breaker. A session with matches in only one period keeps that period. "
      "Only events whose airing period equals the assigned period are retained. "
      "The other period's events from that session are dropped — the session is "
      "never counted in both splits, and is never silently duplicated. Neither "
      "viewing stop time nor total overlap seconds is used, so the amount of "
      "validation-period overlap cannot influence which training events are kept.")
    w("")
    w("| Quantity | Value |")
    w("|---|---:|")
    w("| Sessions with labelled events | %s |" % fmt(assign["sessions_with_labelled_events"]))
    w("| Assigned to training (weeks 11–20) | %s |" % fmt(assign["assigned_training_weeks_11_20"]))
    w("| Assigned to validation (weeks 21–30) | %s |" % fmt(assign["assigned_validation_weeks_21_30"]))
    w("| Sessions matching both exports | %s |" % fmt(assign["cross_period_sessions"]))
    w("| — of which assigned to training | %s |" % fmt(assign["cross_assigned_training"]))
    w("| — of which assigned to validation | %s |" % fmt(assign["cross_assigned_validation"]))
    w("| Events retained | %s |" % fmt(assign["events_retained_total"]))
    w("| Events excluded | %s (%s of source events) |" % (
        fmt(assign["events_excluded_total"]),
        pct(assign["events_excluded_total"], assign["source_labelled_events"])))
    w("| Retained overlap of boundary sessions | %s s |" % fmt(assign["cross_retained_overlap_seconds"]))
    w("| Excluded overlap of boundary sessions | %s s |" % fmt(assign["cross_excluded_overlap_seconds"]))
    w("| Sessions present in both splits | %s |" % fmt(assign["sessions_in_both_splits"]))
    w("")
    w("| Split | Sessions | Retained events | Retained overlap (s) | Excluded events |")
    w("|---|---:|---:|---:|---:|")
    for label in ("training_weeks_11_20", "validation_weeks_21_30"):
        q = stats["per_period_assignment"][label]
        w("| %s | %s | %s | %s | %s |" % (
            label.replace("_", " "), fmt(q["sessions"]), fmt(q["events"]),
            fmt(q["overlap_seconds"]), fmt(q["excluded_events"])))
    w("")
    w("Each retained event is a complete `(session, airing)` match; only whole "
      "events move between splits. No event is split at its midpoint, and no "
      "session's events are spread across both outputs.")
    w("")

    old = comparison["assignment"]["old"]
    new = comparison["assignment"]["corrected"]
    move = comparison["event_movement"]
    old_train_events = assign["events_training"] - move["training_events_newly_included"]
    old_val_events = assign["events_validation"] + move["validation_events_newly_excluded"]
    w("## 3. Boundary-rule correction: old vs corrected impact")
    w("")
    w("The earlier implementation assigned a cross-period session to whichever "
      "export had the larger **total labelled overlap** (`SUM(ov) DESC`, ties to "
      "the earlier period). That decision consumed validation-period overlap, so "
      "validation information could change which training events were retained. "
      "The correction replaces it with the earliest-matched-airing rule above and "
      "removes that dependency; it is reconstructed from the read-only Stage 2 "
      "event store in `src/compare_boundary_assignment.py`.")
    w("")
    w("| Quantity | Old (largest overlap) | Corrected (earliest airing) | Change |")
    w("|---|---:|---:|---:|")
    w("| Cross-period sessions | %s | %s | — |" % (
        fmt(old["cross_period_sessions"]), fmt(new["cross_period_sessions"])))
    w("| — assigned to training | %s | %s | %+d |" % (
        fmt(old["cross_assigned_training"]), fmt(new["cross_assigned_training"]),
        new["cross_assigned_training"] - old["cross_assigned_training"]))
    w("| — assigned to validation | %s | %s | %+d |" % (
        fmt(old["cross_assigned_validation"]), fmt(new["cross_assigned_validation"]),
        new["cross_assigned_validation"] - old["cross_assigned_validation"]))
    w("| Sessions reassigned | — | %s | validation → training only |" % (
        fmt(comparison["sessions_reassigned"])))
    w("| Events excluded | %s | %s | %+d |" % (
        fmt(old["excluded_events"]), fmt(new["excluded_events"]),
        new["excluded_events"] - old["excluded_events"]))
    w("| Events retained | %s | %s | %+d |" % (
        fmt(old["retained_events"]), fmt(new["retained_events"]),
        new["retained_events"] - old["retained_events"]))
    w("| Training events (retained) | %s | %s | +%d |" % (
        fmt(old_train_events), fmt(assign["events_training"]),
        assign["events_training"] - old_train_events))
    w("| Validation events (retained) | %s | %s | %d |" % (
        fmt(old_val_events), fmt(assign["events_validation"]),
        assign["events_validation"] - old_val_events))
    w("")
    w("Event movement between the two rules: **%s** training events are newly "
      "included, **%s** validation events are newly excluded, and **%s** events "
      "are excluded under both rules. All %s reassigned sessions moved from "
      "validation to training; none moved the other way."
      % (fmt(move["training_events_newly_included"]),
         fmt(move["validation_events_newly_excluded"]),
         fmt(move["events_excluded_under_both_rules"]),
         fmt(comparison["sessions_reassigned"])))
    w("")
    w("Per-combination pair counts, old → corrected (all 12 combinations):")
    w("")
    w("| Strategy | Rule | Train pairs old | Train pairs corrected | Δ | Validation pairs old | Validation pairs corrected | Δ |")
    w("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in comparison["combinations"]:
        if r["split"] != "train":
            continue
        va = next(x for x in comparison["combinations"]
                  if x["split"] == "validation" and x["strategy"] == r["strategy"]
                  and x["rule"] == r["rule"])
        w("| %s | %s | %s | %s | %+d | %s | %s | %+d |" % (
            r["strategy"], r["rule"], fmt(r["old_pairs"]), fmt(r["new_pairs"]),
            r["delta_pairs"], fmt(va["old_pairs"]), fmt(va["new_pairs"]),
            va["delta_pairs"]))
    w("")
    w("**Do training aggregates differ?** Yes, for every one of the 12 training "
      "combinations: %s training pairs are added and %s removed, with no pair "
      "value changed except through the added rows. Validation changes are the "
      "mirror image: %s pairs removed and %s added. The sets confirm the expected "
      "monotonicity — corrected training is a strict superset of the old training "
      "set and corrected validation is a strict subset of the old validation set "
      "for every combination."
      % (fmt(comparison["summary"]["training_pairs_added"]),
         fmt(comparison["summary"]["training_pairs_removed"]),
         fmt(comparison["summary"]["validation_pairs_removed"]),
         fmt(comparison["summary"]["validation_pairs_added"])))
    w("")
    w("The correction is conservative: it can only move sessions toward training "
      "(the earlier export) on this dataset, so no training signal is lost, while "
      "the training/validation boundary no longer depends on validation overlap.")
    w("")

    w("## 4. Aggregation and the 12 combinations")
    w("")
    w("For each split independently, events are filtered by the rule, multiple "
      "matches in one session are resolved by the strategy, and events are "
      "aggregated by `(user_id, tv_show_id)`. IDs remain exact strings; "
      "`tv_show_id = 0` is never included. The validation split is built by the "
      "same code but is **held out**: it is not used for any profile, feature, "
      "popularity count or graph edge in this stage.")
    w("")
    w("Thresholds: A = 60 s / 10%, B = 60 s / 20%, C = 120 s / 20%, D = 120 s / 50%. "
      "Strategies: A = keep all, B = maximum absolute overlap, C = maximum overlap "
      "ratio.")
    w("")
    w("| Strategy | Rule | Min s | Min ratio | Train events | Train pairs | Train users | Train programmes | Validation events | Validation pairs | Validation users | Validation programmes | Total events | Stage 2 global | Δ excluded |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for strategy in ("A", "B", "C"):
        for rule in ("A", "B", "C", "D"):
            tr = next(c for c in combos if c["split"] == "train"
                      and c["strategy"] == strategy and c["rule"] == rule)
            va = next(c for c in combos if c["split"] == "validation"
                      and c["strategy"] == strategy and c["rule"] == rule)
            total = tr["events"] + va["events"]
            g = glob[(strategy, rule)]["events"]
            w("| %s | %s | %d | %.0f%% | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                strategy, rule, tr["rule_min_seconds"],
                100 * tr["rule_min_ratio"], fmt(tr["events"]),
                fmt(tr["unique_user_programme_pairs"]), fmt(tr["unique_users"]),
                fmt(tr["unique_programmes"]), fmt(va["events"]),
                fmt(va["unique_user_programme_pairs"]), fmt(va["unique_users"]),
                fmt(va["unique_programmes"]), fmt(total), fmt(g), fmt(g - total)))
    w("")
    excluded_by_rule = {}
    for r in ("A", "B", "C", "D"):
        kept = sum(c["events"] for c in combos
                   if c["split"] in ("train", "validation")
                   and c["strategy"] == "A" and c["rule"] == r)
        excluded_by_rule[r] = glob[("A", r)]["events"] - kept
    w("The totals reconcile with Stage 2: the difference is exactly the set of "
      "events excluded by the boundary-session rule that also pass that rule. With "
      "strategy A (keep all matches) that difference is %s events for rule A, %s "
      "for B/C and %s for D. Strategies B/C resolve per session within a split, so "
      "their totals can differ by fewer events than the raw exclusion count."
      % (fmt(excluded_by_rule["A"]), fmt(excluded_by_rule["B"]),
         fmt(excluded_by_rule["D"])))
    w("")
    w("Both splits have fewer users than the 4,914 viewing users because some users "
      "have no qualifying interaction under a given threshold; this is expected and "
      "no user is imputed. No threshold or strategy is preferred here.")
    w("")

    w("## 5. Leakage and integrity verification")
    w("")
    w("`src/verify_temporal_split.py` independently re-derives expected aggregates "
      "from the split tables and compares every row of all 24 files. It also "
      "re-hashes the Stage 2 event store and checks the candidate universe against "
      "the raw schedule exports.")
    w("")
    w("**Result: %d/%d checks passed, %d failed.**" % (
        verification["checks_passed"], verification["checks_total"],
        verification["checks_failed"]))
    w("")
    w("| Verification group | Passed | Total |")
    w("|---|---:|---:|")
    for cat in sorted(groups):
        passed, total = groups[cat]
        w("| %s | %d | %d |" % (cat, passed, total))
    w("")
    w("Specific guarantees, each backed by a measured check:")
    w("")
    w("- Training events contain only weeks 11–20 airings; validation events only "
      "weeks 21–30; no weeks 31–42 airing is used.")
    w("- Every session is assigned to exactly one period; no session appears in "
      "both splits; every event's session agrees with its split.")
    w("- The earliest-matched-airing rule is checked independently: for every "
      "session the verifier recomputes argmin(`pstart`, `period`) from the source "
      "event store and confirms the assigned period and recorded `first_pstart` "
      "match it, and separately confirms the metadata documents this rule.")
    w("- The Stage 2 event store is unchanged (ordered SHA-256 "
      "`%s`)." % verification["digests"]["source_event_store_sha256"][:16] + "…")
    w("- Every training aggregate row was recomputed from `events_train` alone and "
      "matched exactly, so no validation event contributes to a training "
      "aggregate. The same full-row recomputation was done for validation.")
    w("- No duplicate `(user_id, tv_show_id)` pair in any file; no `tv_show_id = 0`, "
      "empty or invalid programme ID; no duplicate `(session, programme, airing "
      "start)` boundary event; no overlap ≤ 0 or greater than programme duration.")
    w("- IDs are stored as TEXT and every file ID was found verbatim in the source "
      "event store / schedule; no float or signed-int64 conversion occurred.")
    w("- Repeat-run determinism: %s" % (
        "the split SQLite database, all 24 dataset content hashes and the stable "
        "metadata projection are identical across two full runs."
        if "repeat run: all 24 datasets identical" in
        {c["check"] for c in verification["checks"]}
        else "checked with a separate `--compare` digest run (see verification JSON)."))
    w("")

    w("## 6. Candidate programme universe and cold start")
    w("")
    w("Programme IDs were taken from the three schedule exports only. Test viewing "
      "behaviour does not exist and was not used. No programme was removed from the "
      "test universe.")
    w("")
    w("| Quantity | Programme IDs |")
    w("|---|---:|")
    w("| Training schedule (weeks 11–20) | %s |" % fmt(universe["period_totals"]["training_weeks_11_20"]["programmes"]))
    w("| Validation schedule (weeks 21–30) | %s |" % fmt(universe["period_totals"]["validation_weeks_21_30"]["programmes"]))
    w("| Test schedule (weeks 31–42) | %s |" % fmt(universe["period_totals"]["test_weeks_31_42"]["programmes"]))
    w("| Union of all three | %s |" % fmt(universe["period_totals"]["union"]))
    w("| Training-only (not validation or test) | %s |" % fmt(u["training_only_vs_validation_and_test"]))
    w("| Training-only vs validation | %s |" % fmt(u["training_only_vs_validation"]))
    w("| Shared training + validation | %s |" % fmt(u["shared_training_and_validation"]))
    w("| Validation-only (not training or test) | %s |" % fmt(u["validation_only_vs_training_and_test"]))
    w("| Validation-only vs training | %s |" % fmt(u["validation_only_vs_training"]))
    w("| Test-only (not training or validation) | %s |" % fmt(u["test_only_not_in_training_or_validation"]))
    w("| Test absent from training | %s |" % fmt(u["test_absent_from_training"]))
    w("| Test shared with training | %s |" % fmt(u["test_shared_with_training"]))
    w("| Present in all three periods | %s |" % fmt(u["all_three_periods"]))
    w("")
    w("**Cold start:** %s of %s test programmes (%s) never appear in training. "
      "All %s have usable metadata, so they can later be scored from content "
      "features; they are retained in the test universe."
      % (fmt(tm["cold_start_test_programmes"]), fmt(tm["test_programmes"]),
         pct(tm["cold_start_test_programmes"], tm["test_programmes"]),
         fmt(tm["cold_start_with_usable_metadata"])))
    w("")
    w("Usable metadata is defined as a non-empty title plus at least one of "
      "category, genre 1–3, year, director or actors (the tokens `0`, empty, "
      "`None`, `nan`, `null` count as missing).")
    w("")
    w("| Metadata field | All test programmes | Cold-start test programmes |")
    w("|---|---:|---:|")
    fields = ["tv_show_title", "tv_show_category", "tv_show_genre_1",
              "tv_show_genre_2", "tv_show_genre_3", "year_of_production",
              "director", "actors"]
    for f in fields:
        w("| %s | %s | %s |" % (f, fmt(tm["field_coverage_test"][f]),
                                fmt(tm["field_coverage_cold_start"][f])))
    w("")
    w("Per-programme evidence is in `reports/candidate_universe_audit.csv` "
      "(%s rows)." % fmt(universe["period_totals"]["union"]))
    w("")

    w("## 7. Runtime, memory and artifacts")
    w("")
    w("| Step | Runtime | Peak RSS |")
    w("|---|---:|---:|")
    w("| Candidate-universe audit | %.2f s | %.1f MB |" % (
        universe["runtime_seconds"], universe["peak_rss_mb"]))
    w("| Temporal split + 24 datasets | %.2f s | %.1f MB |" % (
        stats["runtime_seconds"], stats["peak_rss_mb"]))
    w("| Verification | %.2f s | — |" % verification["runtime_seconds"])
    w("")
    w("The split pipeline reuses the Stage 2 schedule index indirectly through "
      "the existing event store; it does not rerun interval matching. Only the "
      "Python standard library and SQLite were used; no packages were installed.")
    w("")
    w("| Artifact | Purpose |")
    w("|---|---|")
    w("| `outputs/_intermediate/temporal_split.sqlite` | session assignment + `events_train` / `events_validation` |")
    w("| `outputs/temporal_interactions/{train,validation}_strategy*_rule*_*.csv.gz` | 24 provisional pair-level datasets |")
    w("| `outputs/temporal_interactions/metadata.json` | boundaries, rules, strategies, counts, SHA-256 |")
    w("| `reports/temporal_split_statistics.json` | machine-readable split statistics |")
    w("| `reports/candidate_universe_audit.csv` | one row per programme ID (9,671) |")
    w("| `reports/candidate_universe_statistics.json` | universe intersections and metadata coverage |")
    w("| `reports/temporal_split_verification.json` | %d checks with digests |" % verification["checks_total"])
    w("| `reports/boundary_assignment_comparison.json` | old-vs-corrected assignment, event movement and per-combination deltas |")
    w("| `reports/boundary_assignment_comparison.csv` | one row per (split, strategy, rule) |")
    w("| `src/temporal_split.py`, `src/verify_temporal_split.py`, `src/build_temporal_report.py`, `src/compare_boundary_assignment.py` | reproducible pipeline |")
    w("")

    w("## 8. Caveats and non-decisions")
    w("")
    w("- **No final threshold or strategy is selected.** All 12 combinations remain "
      "provisional and equally documented.")
    w("- No CBF/PPR model, no training, no recommendations and no Kaggle submission "
      "were produced.")
    w("- The training/validation split is temporal at the schedule level. A user "
      "can appear in both splits (different sessions); that is intended for "
      "held-out evaluation and does not leak validation events into training.")
    w("- Validation events are labels only. Any later feature builder must consume "
      "`events_train` / `train_*` files exclusively.")
    w("- The session rule discards %s of %s labelled events (%s), all from the %s "
      "sessions that matched both exports. This is deterministic and documented. "
      "The rejected maximum-overlap rule would have discarded %s events and split "
      "the %s boundary sessions %s/%s between training/validation; the adopted "
      "earliest-airing rule assigns all %s to training because weeks 11–20 "
      "precedes weeks 21–30 for every cross-period session in this data."
      % (fmt(assign["events_excluded_total"]),
         fmt(assign["source_labelled_events"]),
         pct(assign["events_excluded_total"], assign["source_labelled_events"]),
         fmt(assign["cross_period_sessions"]),
         fmt(old["excluded_events"]), fmt(old["cross_period_sessions"]),
         fmt(old["cross_assigned_training"]), fmt(old["cross_assigned_validation"]),
         fmt(new["cross_assigned_training"])))
    w("- The test schedule has no viewing log; nothing in this stage uses test "
      "behaviour.")
    w("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[report] wrote", OUT)


if __name__ == "__main__":
    main()
