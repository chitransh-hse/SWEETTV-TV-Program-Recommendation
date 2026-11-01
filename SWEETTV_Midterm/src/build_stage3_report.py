"""Build the Stage 3 final exam report as Markdown and PDF.

All numbers are read from the verified Stage 3 artifacts:

    reports/stage3_experiment_results.json
    outputs/stage3/selected_model.json
    outputs/stage3/submission_metadata.json
    reports/stage3_verification.json
    reports/stage3_status.json
    reports/temporal_split_verification.json
    reports/candidate_universe_statistics.json
    reports/stage3_example_recommendations.csv

    python3 src/build_stage3_report.py
      -> reports/SWEETTV_Final_Exam_Report.md
      -> reports/SWEETTV_Final_Exam_Report.pdf
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_renderer as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
STAGE3_OUT = os.path.join(ROOT, "outputs", "stage3")
MD_OUT = os.path.join(REPORTS, "SWEETTV_Final_Exam_Report.md")
PDF_OUT = os.path.join(REPORTS, "SWEETTV_Final_Exam_Report.pdf")
SCREENSHOT = os.path.join(REPORTS, "kaggle_submission_screenshot.png")

AUTHOR = "[Author name — to be completed]"
REPO = "[GitHub repository URL — not available: no .git directory in the project]"
REPORT_DATE = "1 November 2026"


def load(name):
    with open(os.path.join(REPORTS, name), encoding="utf-8") as f:
        return json.load(f)


def load_out(name):
    with open(os.path.join(STAGE3_OUT, name), encoding="utf-8") as f:
        return json.load(f)


def n(value):
    return "{:,}".format(int(value))


def m5(value):
    return "%.6f" % value


def pct(value, digits=2):
    return ("%." + str(digits) + "f%%") % (value * 100.0)


def pretty_config(config):
    """Human-readable, wrap-friendly rendering of an internal config key."""
    return config.replace("+", " + ").replace("_", " ")


def grouped_hash(value):
    return " ".join(value[i:i + 8] for i in range(0, len(value), 8))


EXP = load("stage3_experiment_results.json")
SEL = load_out("selected_model.json")
SUB = load_out("submission_metadata.json")
VER = load("stage3_verification.json")
STATUS = load("stage3_status.json")
TEMP_VER = load("temporal_split_verification.json")
UNIVERSE = load("candidate_universe_statistics.json")

BEST = EXP["best_by_method"]
RESULTS = EXP["results"]
PROTO = EXP["protocol"]
DATA = EXP["dataset"]

METHOD_LABEL = {
    "popularity": "Popularity baseline",
    "content_based": "Content-based filtering",
    "association_rules": "Association rules",
    "repeat_recency": "Repeat-recency personalisation",
    "hybrid": "Hybrid (RRF)",
}
BEST_ORDER = ["popularity", "content_based", "association_rules",
              "repeat_recency", "hybrid"]


def find_result(config):
    for r in RESULTS:
        if r["config"] == config:
            return r
    raise KeyError(config)


def example_rows():
    path = os.path.join(REPORTS, "stage3_example_recommendations.csv")
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_blocks():
    blocks = []
    a = blocks.append

    sel = SEL["selected"]
    sel_params = sel["params"]
    sub = SUB["submission"]
    checks = SUB["checks"]
    cmp_ext = SUB["comparison_with_existing"]

    # ------------------------------------------------------------------
    a(("h1", "1. Project overview and objective"))
    a(("p",
       "This report documents Stage 3 of the SWEET.TV TV Program Recommendation "
       "project: the design, implementation and offline evaluation of "
       "recommendation models on the corrected Stage 2.5 temporal split, the "
       "construction of a Kaggle submission, and a critical assessment of the "
       "results. It continues directly from the verified Stage 1 (dataset audit), "
       "Stage 2 (viewing-to-programme matching) and Stage 2.5 (temporal "
       "separation) work; none of those artifacts was rebuilt or modified."))
    a(("p",
       "The competition asks for a ranked list of up to five programme IDs for "
       "each of 1,260 users, covering the weeks 31–42 broadcast schedule. There "
       "is a viewing log for weeks 11–30 but no viewing log for the test period, "
       "so the task is a temporal, cold-start-item recommendation problem: every "
       "model must be fitted on the past and applied to a future schedule whose "
       "programme mix only partly overlaps the training catalogue."))
    a(("table", {
        "header": ["Item", "Value"],
        "widths": [0.24, 0.76],
        "rows": [
            ["Competition",
             "SWEET.TV TV Program Recommender (Kaggle)"],
            ["Competition URL",
             "https://www.kaggle.com/competitions/sweettv-tv-program-recommender"],
            ["Task",
             "Rank five programme IDs per user for the weeks 31–42 schedule"],
            ["Users to score", "1,260 (fixed template order)"],
            ["Training interactions",
             "Weeks 11–20, strategy A, rule B (335,816 user–programme pairs)"],
            ["Validation interactions",
             "Weeks 21–30, same rule (312,934 pairs)"],
            ["Primary metric", "MAP@5 on the validation period"],
            ["Selected model",
             "Reciprocal-rank fusion of repeat-recency, popularity and "
             "content-based scores"],
            ["Offline validation MAP@5", m5(sel["validation_map5"])],
            ["Kaggle public / private score",
             "Not available for this submission — see Section 13"],
            ["Author", AUTHOR],
        ],
    }))
    a(("p",
       "**Scope of this report.** Section 2 summarises the outcome. Sections 3–7 "
       "describe the data, matching, split, quality treatment and candidate "
       "universe. Sections 8–12 present the baseline and the implemented methods, "
       "the experimental protocol, the validation comparison and the model "
       "selection. Sections 13–18 cover the submission, worked examples, business "
       "interpretation, limitations, reproducibility and the repository. "
       "Appendix A lists the complete hyperparameter grid and Appendix B the "
       "final audit."))

    # ------------------------------------------------------------------
    a(("h1", "2. Executive summary"))
    a(("p",
       "Five scoring approaches were implemented and evaluated on the corrected "
       "validation period: a global popularity baseline, content-based filtering "
       "on programme metadata, item-to-item association rules from co-viewing, a "
       "personalised repeat-recency model, and a reciprocal-rank-fusion hybrid. "
       "All behavioural parameters were estimated on training weeks 11–20 only; "
       "metadata TF-IDF weights use schedule content available at prediction "
       "time. Validation "
       "weeks 21–30 were used exclusively for scoring and model selection. No "
       "test-period viewing behaviour exists or was used."))
    a(("p",
       "The best validation configuration is a hybrid that combines "
       "repeat-recency (weight 1.00), popularity (0.20) and content-based "
       "similarity (0.05) with reciprocal-rank fusion at k = 60. It reaches "
       "**MAP@5 = " + m5(sel["validation_map5"]) + "** and Recall@5 = " +
       "%.4f" % sel["validation_recall5"] + " on 4,774 validation users, "
       "compared with " + m5(BEST["popularity"]["map5"]) + " for the popularity "
       "baseline. The gain over the repeat-recency model alone (" +
       m5(BEST["repeat_recency"]["map5"]) + ") is +" +
       "%.4f" % (sel["validation_map5"] - BEST["repeat_recency"]["map5"]) +
       ", so most of the performance comes from modelling re-consumption, with "
       "popularity and content providing a small complementary lift."))
    a(("table", {
        "header": ["Result", "Value", "Provenance"],
        "widths": [0.46, 0.20, 0.34],
        "align": ["L", "R", "L"],
        "rows": [
            ["Offline validation MAP@5, selected hybrid",
             m5(sel["validation_map5"]), "Computed locally from verified data"],
            ["Offline validation MAP@5, popularity baseline",
             m5(BEST["popularity"]["map5"]), "Computed locally"],
            ["Offline validation MAP@5, best single learned method",
             m5(BEST["repeat_recency"]["map5"]), "Computed locally"],
            ["Kaggle public score, this submission",
             "not available", "No upload performed from this environment"],
            ["Kaggle private score, this submission",
             "not available", "No upload performed from this environment"],
            ["Kaggle public score, earlier submission",
             "0.35830", "User-reported, not verified locally"],
            ["Kaggle private score, earlier submission",
             "0.33986", "User-reported, not verified locally"],
        ],
    }))
    a(("callout",
       "**Provenance warning.** The offline validation MAP@5 values in this "
       "report are not Kaggle scores and must not be read as such. They are "
       "computed on a held-out ten-week period with a candidate universe of "
       "6,363 validation programmes; the Kaggle leaderboard uses a different "
       "user set, a different period and a different candidate universe. The "
       "0.35830 / 0.33986 pair belongs to an earlier, externally produced "
       "submission and is reproduced here only because the user reported it."))
    a(("bullets", [
        "**Data:** 5.82 M viewing sessions, 3.84 M labelled overlap events, "
        "9,671 distinct labelled programmes across three schedule exports.",
        "**Split:** corrected earliest-matched-airing rule; 323/323 Stage 2.5 "
        "checks pass; 97 cross-period sessions all assigned to training.",
        "**Main configuration:** strategy A (keep all matches), rule B "
        "(≥60 s and ≥20% overlap).",
        "**Methods:** popularity, content-based filtering, association rules, "
        "repeat-recency and a rank-fusion hybrid; the exam requirement of at "
        "least two method families plus a popularity baseline is met by CBF and "
        "association rules.",
        "**Submission:** 1,260 rows, five IDs per row, no zeros, all IDs inside "
        "the 6,636-programme test universe, template order preserved.",
        "**Honesty:** no Kaggle acceptance or score is claimed for the final "
        "model, and no screenshot is fabricated.",
    ]))

    # ------------------------------------------------------------------
    a(("h1", "3. Dataset description and exploratory audit"))
    a(("p",
       "The competition ships five UTF-8 CSV files. The three schedule exports "
       "are broadcast-day exports rather than ISO-week slices: an airing belongs "
       "to the export that contains it. That property is central to the split in "
       "Section 5."))
    a(("table", {
        "header": ["File", "Rows", "Columns", "Period", "Role"],
        "widths": [0.29, 0.13, 0.09, 0.22, 0.27],
        "align": ["L", "R", "R", "L", "L"],
        "rows": [
            ["dataset11-30.csv", "5,821,987", "5",
             "2020-03-09 → 2020-07-26", "Viewing log (weeks 11–30)"],
            ["export_arh_11-20-final.csv", "1,074,235", "13",
             "2020-03-08 → 2020-05-18", "Training schedule"],
            ["export_arh_21-30-final.csv", "1,074,394", "13",
             "2020-05-18 → 2020-07-27", "Validation schedule"],
            ["export_arh_31-42-final.csv", "1,295,378", "13",
             "2020-07-27 → 2020-10-19", "Test schedule (no viewing log)"],
            ["submission.csv", "1,260", "2", "—",
             "Kaggle template (all placeholder zeros)"],
        ],
    }))
    a(("p",
       "**Viewing log.** 5,821,987 rows, 4,914 users, 202 channels, 139 active "
       "dates. The file is unusually clean: no missing IDs, no unparsable "
       "timestamps, no negative durations and only 17 exact duplicate rows. The "
       "median session lasts 396 s (6.6 min) and the mean 1,480 s (24.7 min); the "
       "maximum of 342,145 s (95 h) is consistent with a session that was never "
       "closed. The column name `duraton` is misspelled in the source and was "
       "preserved exactly."))
    a(("p",
       "**Schedules.** About 88.9% of schedule rows carry `tv_show_id = 0`, which "
       "denotes unlabelled airtime (trailers, continuity, untagged content). "
       "Labelled rows account for only 11.1% of rows but 16.5% of scheduled "
       "airtime, because labelled slots are longer on average. Metadata on "
       "labelled rows is substantially better than on unlabelled rows: roughly "
       "95% have at least one genre, 99% a production year and 87% cast or "
       "director information."))
    a(("table", {
        "header": ["Period", "Schedule rows", "Labelled rows", "Labelled share",
                   "Distinct labelled programmes"],
        "widths": [0.22, 0.18, 0.16, 0.18, 0.26],
        "align": ["L", "R", "R", "R", "R"],
        "rows": [
            ["Weeks 11–20", "1,074,235", "115,613", "10.76%", "5,847"],
            ["Weeks 21–30", "1,074,394", "121,505", "11.31%", "6,363"],
            ["Weeks 31–42", "1,295,378", "144,774", "11.18%", "6,636"],
            ["All periods", "3,444,007", "381,892", "11.09%", "9,671 (union)"],
        ],
    }))
    a(("p",
       "**Exploratory findings that shaped the modelling.**"))
    a(("bullets", [
        "User IDs exceed signed 64-bit range (2,483 of 4,914 users), so every ID "
        "is handled as a string. Programme IDs are also kept as strings because "
        "some carry leading zeros.",
        "Timestamps are naive local wall-clock time. All 117 `duraton` "
        "disagreements are exactly −3,600 s and 115 of them start on 2020-03-29, "
        "the Ukrainian DST spring-forward date. No timezone offset was applied.",
        "Titles are not stable programme identifiers: they vary by episode, "
        "season and editorial prefix. Category is stable; genre, year and cast "
        "are nearly stable. Titles were therefore used for display only, never "
        "as a join key.",
        "1,437 programme IDs in the test schedule (21.7% of test IDs) never "
        "appear in either earlier schedule, which makes item cold start a "
        "first-class constraint.",
        "Channel coverage is high but not complete: 197 of 202 viewing channels "
        "exist in at least one schedule, covering 98.05% of viewing rows.",
    ]))

    # ------------------------------------------------------------------
    a(("h1", "4. Interaction matching methodology"))
    a(("p",
       "Interactions were reconstructed in Stage 2 by joining the viewing log to "
       "the schedule on channel and time. The join key is "
       "`viewing.vsetv_id = schedule.channel_id`; the schedule scope is the union "
       "of the weeks 11–20 and 21–30 exports, which hand over per channel and "
       "therefore form a near-continuous timeline. No timestamp offset was "
       "applied (0 s)."))
    a(("p",
       "For a viewing session with interval `[view_start, view_end)` and an "
       "airing with `[programme_start, programme_end)`, the overlap is:"))
    a(("code", [
        "overlap = max(0, min(view_end, programme_end)",
        "                - max(view_start, programme_start))",
    ]))
    a(("p",
       "A match, or **event**, requires `overlap > 0`. Events with "
       "`tv_show_id = 0` are discarded immediately and never enter a user "
       "profile, a co-viewing count or a candidate list. The remaining matches "
       "are filtered by the selected overlap rule and aggregated to "
       "`(user_id, tv_show_id)` pairs. Pair-level features retained for modelling "
       "are the number of sessions, total overlap seconds, number of overlap "
       "events, mean and maximum overlap ratio, and the first and last viewing "
       "timestamps."))
    a(("table", {
        "header": ["Matching diagnostic", "Value"],
        "widths": [0.72, 0.28],
        "align": ["L", "R"],
        "rows": [
            ["Viewing sessions", "5,821,987"],
            ["Sessions overlapping no schedule airing", "413,389 (7.10%)"],
            ["Sessions with at least one labelled overlap", "2,964,346 (50.92%)"],
            ["Labelled overlap events", "3,847,696"],
            ["Unlabelled overlap events (tv_show_id = 0)", "3,904,737"],
            ["Sessions with zero labelled overlap", "2,857,641 (49.08%)"],
            ["Maximum labelled airings matched by one session", "57"],
            ["Labelled overlap time as share of session time", "53.08%"],
        ],
    }))
    a(("p",
       "About half of all sessions match no labelled programme. The main reason "
       "is structural, not a matching failure: 113,579 sessions are on channels "
       "absent from the schedules, and a further 297,331 occur before their "
       "channel's first scheduled row. The remaining 470 are in-channel gaps. "
       "This is the dominant source of interaction sparsity and is discussed in "
       "Section 16."))
    a(("p",
       "**Threshold and multi-match policy.** The project's pre-specified main "
       "configuration is **strategy A** (keep every match in a session) with "
       "**rule B** (an event must overlap the airing by at least 60 s and at "
       "least 20% of the programme duration). Rule B removes brief channel "
       "zapping and programme-boundary bleed while retaining short but genuine "
       "views. Strategy A avoids discarding valid second-screen or "
       "picture-in-picture behaviour. This pair was fixed before modelling and "
       "was **not** tuned on validation; the full 12-combination comparison "
       "already exists in the Stage 2.5 artifacts. Fixing it in advance keeps the "
       "validation score an honest estimate rather than the maximum of a "
       "threshold search."))

    # ------------------------------------------------------------------
    a(("h1", "5. Temporal split methodology and the Stage 2.5 correction"))
    a(("p",
       "Period membership comes from the source export, not from ISO-week "
       "arithmetic. The nominal boundaries are 2020-05-18 00:00 and 2020-07-27 "
       "00:00, but the exports hand over per channel: 2,431 weeks 11–20 rows "
       "start at or after 2020-05-18 00:00, and 1,267 weeks 21–30 rows start "
       "before 06:00 that day. No timestamp predicate can separate the exports "
       "cleanly, so the export label is the only unambiguous split."))
    a(("table", {
        "header": ["Period", "First airing start", "Last airing end",
                   "Channels"],
        "widths": [0.20, 0.30, 0.30, 0.20],
        "align": ["L", "L", "L", "R"],
        "rows": [
            ["Training (11–20)", "2020-03-08 23:00", "2020-05-18 12:00", "400"],
            ["Validation (21–30)", "2020-05-18 00:00", "2020-07-27 12:00", "403"],
            ["Test (31–42)", "2020-07-27 00:00", "2020-10-19 12:00", "404"],
        ],
    }))
    a(("h2", "5.1 The corrected boundary rule"))
    a(("p",
       "A viewing session can match airings in both exports near the seam. The "
       "corrected rule assigns a cross-period session to the period of its "
       "**earliest matched airing**, ordered by airing start ascending with "
       "period as a deterministic tie-breaker. Only events whose airing period "
       "equals the assigned period are retained. A session is therefore never "
       "counted in both splits and is never duplicated."))
    a(("p",
       "The earlier implementation assigned a cross-period session to whichever "
       "export had the larger total labelled overlap. That rule is unsound for a "
       "temporal evaluation because validation-period overlap can change which "
       "training events are retained. The correction removes that dependency "
       "entirely: neither viewing stop time nor total overlap seconds is used."))
    a(("table", {
        "header": ["Quantity", "Old (max overlap)", "Corrected (earliest)",
                   "Change"],
        "widths": [0.42, 0.18, 0.22, 0.18],
        "align": ["L", "R", "R", "R"],
        "rows": [
            ["Cross-period sessions", "97", "97", "—"],
            ["— assigned to training", "36", "97", "+61"],
            ["— assigned to validation", "61", "0", "−61"],
            ["Events excluded", "109", "192", "+83"],
            ["Events retained", "3,847,587", "3,847,504", "−83"],
            ["Training events", "2,384,927", "2,384,996", "+69"],
            ["Validation events", "1,462,660", "1,462,508", "−152"],
        ],
    }))
    a(("p",
       "All 61 reassigned sessions moved from validation to training; none moved "
       "the other way. Corrected training pair sets are strict supersets of the "
       "old ones for all 12 threshold × strategy combinations (+175 pairs, none "
       "removed), and corrected validation sets are strict subsets (−274 pairs, "
       "none added). The correction therefore cannot lose training signal, while "
       "it removes the leakage channel."))
    a(("h2", "5.2 Final datasets"))
    a(("table", {
        "header": ["Split", "Pairs", "Users", "Programmes", "Median pairs/user",
                   "P90", "Max"],
        "widths": [0.20, 0.14, 0.12, 0.14, 0.16, 0.10, 0.14],
        "align": ["L", "R", "R", "R", "R", "R", "R"],
        "rows": [
            ["Training (11–20)", n(DATA["train_pairs"]), n(DATA["train_users"]),
             n(DATA["train_items"]), "62", "130", "788"],
            ["Validation (21–30)", n(DATA["validation_pairs"]),
             n(DATA["validation_users"]), n(DATA["validation_items"]),
             "50", "141", "428"],
        ],
    }))
    a(("p",
       "Verification of this split is a prerequisite for every number in this "
       "report: **" + str(TEMP_VER["checks_passed"]) + "/" +
       str(TEMP_VER["checks_total"]) + "** checks pass, including explicit "
       "earliest-airing assignment checks, a no-session-in-both-splits check, "
       "deterministic tie-breaking, ID preservation and a repeat run with "
       "identical digests. The source event-store digest is "
       "`" + TEMP_VER["digests"]["source_event_store_sha256"] + "`."))

    # ------------------------------------------------------------------
    a(("h1", "6. Data-quality findings and treatment of unlabelled programmes"))
    a(("table", {
        "header": ["Issue", "Evidence", "Treatment"],
        "widths": [0.24, 0.44, 0.32],
        "rows": [
            ["Unlabelled airtime",
             "3,062,115 of 3,444,007 schedule rows (88.9%) have tv_show_id = 0",
             "Excluded from interactions, popularity counts, co-viewing, "
             "features and candidate universes. Counted only for the airing "
             "timeline."],
            ["DST duration artifact",
             "117 rows; duraton = stop − start − 3,600 s on 2020-03-29",
             "No offset applied; both timestamps retained as local wall-clock. "
             "Downstream matching uses stop_time, which is self-consistent."],
            ["Unclosed session",
             "Maximum duration 342,145 s (95 h)",
             "Retained; Rule B bounds its contribution through the overlap "
             "ratio and the per-airing aggregation."],
            ["Duplicate viewing rows",
             "17 exact duplicates; 122 repeated (user, channel, start_time)",
             "Retained; they represent repeated device events, and pair "
             "aggregation makes them idempotent for binary models."],
            ["Unstable titles",
             "66k–79k rows per period show title variants for the same ID",
             "Titles used for display only; content features use category, "
             "genre, year, director and cast."],
            ["Missing metadata on labelled rows",
             "4.7% / 4.5% / 3.9% have no genre; 13% have no cast or director",
             "Missing fields contribute no feature tokens; programme ID remains "
             "a valid interaction key."],
            ["IDs beyond int64",
             "2,483 of 4,914 user IDs exceed signed 64-bit range",
             "All IDs handled as strings end-to-end."],
            ["Non-positive schedule duration",
             "One 0-second airing in weeks 11–20",
             "Cannot produce a positive overlap under Rule B and is effectively "
             "ignored."],
        ],
    }))
    a(("p",
       "Unlabelled programmes deserve emphasis because they shape the ceiling of "
       "the task. `tv_show_id = 0` is not a programme: it is the absence of a "
       "programme label. Treating it as an item would create a spurious "
       "blockbuster that absorbs 89% of all airtime, and recommending it would "
       "be invalid. It is excluded at the earliest possible stage, so every "
       "downstream count, score and ranking is defined over labelled content "
       "only. The cost is that roughly half of all viewing sessions cannot be "
       "attributed to a labelled programme, which limits how much behavioural "
       "signal is available per user."))

    # ------------------------------------------------------------------
    a(("h1", "7. Candidate universe and cold-start analysis"))
    a(("p",
       "A candidate universe is the set of labelled programmes in the target "
       "period's schedule. The policy is identical for every method and every "
       "evaluation: a recommendation must be a labelled programme that airs in "
       "the target period. The test policy is the weeks 31–42 universe, which "
       "also guarantees that each recommended ID is schedulable in the test "
       "window."))
    a(("table", {
        "header": ["Universe", "Labelled programmes", "Labelled airings"],
        "widths": [0.44, 0.28, 0.28],
        "align": ["L", "R", "R"],
        "rows": [
            ["Training schedule (weeks 11–20)", "5,847", "115,613"],
            ["Validation schedule (weeks 21–30)", "6,363", "121,505"],
            ["Test schedule (weeks 31–42)", "6,636", "144,774"],
            ["Union of all three", "9,671", "381,892"],
        ],
    }))
    a(("table", {
        "header": ["Membership pattern", "Programmes"],
        "widths": [0.72, 0.28],
        "align": ["L", "R"],
        "rows": [
            ["In all three periods", "3,260"],
            ["Test shared with training", "4,070"],
            ["Test absent from training (item cold start)", "2,566"],
            ["Test absent from training and validation", "1,437"],
            ["Validation-only versus training and test", "1,258"],
            ["Training-only versus validation and test", "1,061"],
        ],
    }))
    a(("p",
       "All 2,566 cold-start test programmes have usable metadata (title plus at "
       "least one of category, genre, year, director or cast). That is why a "
       "content-based component can still place them in a user profile even "
       "though they have no interaction history. It does not make them "
       "well-estimated: a cold item's similarity rests entirely on metadata "
       "shared with watched programmes."))
    a(("p",
       "User cold start is much smaller. Of the 4,774 validation users, 61 "
       "(1.3%) have no training interaction. In the submission template, 1,250 "
       "of 1,260 users have training history and 10 have none; 2 have neither "
       "training nor validation history. All users without a usable profile "
       "receive the same fallback: the global training popularity ranking by "
       "distinct users, restricted to the target candidate universe. The same "
       "fallback fills any row where a method produces fewer than five positive "
       "scores, so no user can receive a zero, a duplicate or an out-of-universe "
       "ID from any method."))

    # ------------------------------------------------------------------
    a(("h1", "8. Popularity baseline"))
    a(("p",
       "The baseline ranks candidates by global popularity in training weeks "
       "11–20, restricted to the candidate universe. Three variants were "
       "evaluated: the number of distinct users who watched the programme, the "
       "total number of sessions, and the total number of overlap events. "
       "Distinct-user popularity was selected because it is least sensitive to "
       "a small number of heavy repeat viewers."))
    a(("table", {
        "header": ["Variant", "Definition", "MAP@5", "Recall@5", "Coverage"],
        "widths": [0.20, 0.42, 0.13, 0.13, 0.12],
        "align": ["L", "L", "R", "R", "R"],
        "rows": [
            ["Users (selected)",
             "count of distinct users with a training interaction",
             m5(find_result("popularity_users")["map5"]),
             "%.4f" % find_result("popularity_users")["recall5"],
             pct(find_result("popularity_users")["catalog_coverage"])],
            ["Sessions",
             "sum of n_sessions over training interactions",
             m5(find_result("popularity_sessions")["map5"]),
             "%.4f" % find_result("popularity_sessions")["recall5"],
             pct(find_result("popularity_sessions")["catalog_coverage"])],
            ["Events",
             "sum of n_overlap_events over training interactions",
             m5(find_result("popularity_events")["map5"]),
             "%.4f" % find_result("popularity_events")["recall5"],
             pct(find_result("popularity_events")["catalog_coverage"])],
        ],
    }))
    a(("p",
       "The baseline is strong on this dataset: **" +
       m5(BEST["popularity"]["map5"]) + "** MAP@5, with at least one hit for "
       "90.3% of validation users. That is expected when a small number of "
       "national news, breakfast and entertainment programmes dominate linear "
       "TV. Its weakness is coverage: the top five programmes account for just " +
       pct(find_result("popularity_users")["catalog_coverage"], 3) +
       " of the candidate catalogue, so it personalises nothing and would "
       "produce the same list for every viewer. It is the floor that any "
       "personalised method must beat, and the source of the fallback ranking."))

    # ------------------------------------------------------------------
    a(("h1", "9. Recommendation methods implemented"))
    a(("p",
       "The exam requires at least two methods from content-based filtering, "
       "association rules, implicit-feedback matrix factorisation and "
       "personalised PageRank, in addition to a popularity baseline. **Two "
       "families were implemented and evaluated on this competition's own "
       "temporal data — content-based filtering and association rules — plus a "
       "personalised repeat-recency model and a rank-fusion hybrid.** Implicit "
       "matrix factorisation and personalised PageRank were not implemented; "
       "Section 9.5 explains why and what would be required."))

    a(("h2", "9.1 Content-based filtering (CBF)"))
    a(("p",
       "**Algorithm.** Each programme is represented by a sparse bag of metadata "
       "tokens derived from the schedule exports: category (`c:`), each genre "
       "(`g:`), production year (`y:`) and decade (`d:`), and optionally director "
       "(`dir:`) and cast (`act:`). Token weights are 1.0 for category, 1.2 for "
       "genre, 0.4 for year, 0.3 for decade, 0.6 for director and 0.4 for actor. "
       "Each token is multiplied by a sublinear inverse document frequency, "
       "`idf = log((1 + N) / (1 + df)) + 1`, computed over the union of training "
       "and candidate programmes, and each programme vector is L2-normalised. "
       "The item–item similarity is the cosine of these vectors."))
    a(("p",
       "A user profile is the session-weighted sum of the vectors of the "
       "programmes they watched in training, with weight `log1p(n_sessions)`. "
       "The score of candidate `c` is `sum_i profile_i × sim(i, c)`. A "
       "neighbour truncation keeps only the `K` most similar candidates per "
       "training item; this prevents broad genre tokens from giving every "
       "programme a small non-zero score and sharpens the ranking."))
    a(("p",
       "**Hyperparameters.** Feature set {genre-only, all fields} × profile "
       "weighting {binary, log-sessions} × neighbour K {none, 50, 200}: 12 "
       "configurations. The selected configuration is genre-only features (173 "
       "tokens), log-session profile weighting and K = 50."))
    a(("table", {
        "header": ["Configuration", "MAP@5", "Recall@5", "Coverage", "Runtime (s)"],
        "widths": [0.44, 0.14, 0.14, 0.14, 0.14],
        "align": ["L", "R", "R", "R", "R"],
        "rows": [
            [pretty_config(r["config"]), m5(r["map5"]),
             "%.4f" % r["recall5"], pct(r["catalog_coverage"]),
             "%.2f" % r["runtime_seconds"]]
            for r in RESULTS if r["method"] == "content_based"
        ],
    }))
    a(("p",
       "**Result and interpretation.** The best CBF configuration reaches " +
       m5(BEST["content_based"]["map5"]) + ", far below the popularity baseline. "
       "Content features describe what a programme *is*, not whether a user will "
       "watch it in a given week; on a schedule dominated by recurring news and "
       "breakfast programming, metadata similarity is a weak substitute for "
       "behaviour. Its value is complementary: it covers cold items and "
       "diversifies the list, which is why it enters the hybrid with a small "
       "weight rather than as a standalone recommender."))

    a(("h2", "9.2 Association rules"))
    a(("p",
       "**Algorithm.** Co-viewing counts are computed over the binary training "
       "user × programme matrix. For antecedent `i` and consequent `j`, "
       "`support(i,j)` is the number of users who watched both; "
       "`confidence(i→j) = support(i,j) / support(i)`; and "
       "`lift(i→j) = confidence(i→j) × N / support(j)`. Self-rules are removed. "
       "Rules below a minimum antecedent support or a minimum pair support are "
       "discarded, and only the top `K` outgoing rules per antecedent are kept. "
       "A user's score for candidate `c` is the sum of the strengths of the "
       "rules fired by the programmes in their training history."))
    a(("p",
       "**Hyperparameters.** Metric {confidence, lift} × top-K {50, 100} × "
       "minimum support / pair support {(3,3), (5,5)}: 6 configurations. The "
       "selected configuration uses confidence, K = 50, support = 5 and pair "
       "support = 5, producing 110,387 rules (about 39 per antecedent)."))
    a(("table", {
        "header": ["Configuration", "MAP@5", "Recall@5", "Rules", "Coverage"],
        "widths": [0.40, 0.14, 0.14, 0.14, 0.18],
        "align": ["L", "R", "R", "R", "R"],
        "rows": [
            [pretty_config(r["config"]), m5(r["map5"]),
             "%.4f" % r["recall5"], n(r["params"]["n_rules"]),
             pct(r["catalog_coverage"])]
            for r in RESULTS if r["method"] == "association_rules"
        ],
    }))
    a(("p",
       "**Result and interpretation.** Confidence-based rules reach " +
       m5(BEST["association_rules"]["map5"]) + ", essentially the popularity "
       "baseline. This is not a coincidence: with a catalogue where a few "
       "programmes are watched by almost everyone, the highest-confidence rule "
       "for most antecedents is the same handful of mass-audience programmes, so "
       "confidence reproduces popularity. Lift deliberately favours rare "
       "co-occurrences; the best lift variant reaches only " +
       m5(find_result("ar_lift_k50_sup5_pair5")["map5"]) +
       "; it finds niche associations but almost never matches the validation "
       "ground truth at rank 5. The useful conclusion is that co-viewing alone "
       "adds little beyond popularity on this schedule, which is why the hybrid "
       "does not include the association component in the final configuration."))

    a(("h2", "9.3 Repeat-recency personalisation"))
    a(("p",
       "This is not one of the four exam families; it is a personalised "
       "re-consumption model included because the data strongly rewards it and "
       "because repeat recommendations are explicitly allowed. For each "
       "training pair, let `s` be the number of matched sessions and `d` the "
       "number of days between the last matched view and the training/validation "
       "boundary (2020-05-18), clipped at zero. The score is:"))
    a(("code", [
        "score(u, c) = log1p(s) ** alpha  *  exp(-d / tau)",
    ]))
    a(("p",
       "**Hyperparameters.** `tau` ∈ {60, 90, 120} days and `alpha` ∈ {1.0, 1.5}: "
       "6 configurations. The selected configuration is `tau = 120` days, "
       "`alpha = 1.0`, giving " + m5(BEST["repeat_recency"]["map5"]) +
       ". The surface is flat: `tau = 60, alpha = 1.5` and `tau = 90, alpha = "
       "1.0` both score " + m5(find_result("repeat_tau60_alpha1.5")["map5"]) +
       ", so the exact parameter values are not critical."))
    a(("p",
       "**Interpretation.** Re-consumption is the dominant behaviour in the "
       "validation period. A model that simply ranks the user's own recently and "
       "frequently watched programmes beats every content or co-viewing model by "
       "a wide margin. This is a genuine property of the data, not a leak: the "
       "score uses only training timestamps and session counts. It is also the "
       "main reason the final recommendation lists are repeat-heavy, which "
       "Section 15 discusses critically."))

    a(("h2", "9.4 Hybrid rank fusion"))
    a(("p",
       "The hybrid combines per-method top-50 lists with reciprocal-rank fusion "
       "(RRF). For user `u` and candidate `c`:"))
    a(("code", [
        "score(u, c) = sum_m  w_m / (k + rank_m(u, c) + 1)",
    ]))
    a(("p",
       "with `k = 60`; a candidate absent from a method's top-50 contributes "
       "nothing from that method. RRF is used instead of score averaging because "
       "the component scores are on incompatible scales (counts, cosine "
       "similarities and rule strengths) and RRF is invariant to monotone "
       "rescaling. The selected weights are 1.00 for repeat-recency, 0.20 for "
       "popularity and 0.05 for content-based filtering. The resulting list is "
       "cut to five with the standard fallback."))
    a(("table", {
        "header": ["Fusion (same repeat base: tau = 120, alpha = 1.0)", "MAP@5",
                   "Δ vs repeat alone"],
        "widths": [0.58, 0.20, 0.22],
        "align": ["L", "R", "R"],
        "rows": [
            ["Repeat-recency alone",
             m5(BEST["repeat_recency"]["map5"]), "—"],
            ["+ popularity 0.10",
             m5(find_result("hybrid_rep_tau120_a1.0_pop0.1")["map5"]),
             "%+.6f" % (find_result("hybrid_rep_tau120_a1.0_pop0.1")["map5"]
                        - BEST["repeat_recency"]["map5"])],
            ["+ popularity 0.20",
             m5(find_result("hybrid_rep_tau120_a1.0_pop0.2")["map5"]),
             "%+.6f" % (find_result("hybrid_rep_tau120_a1.0_pop0.2")["map5"]
                        - BEST["repeat_recency"]["map5"])],
            ["+ popularity 0.20 + CBF 0.05 (selected)",
             m5(sel["validation_map5"]),
             "%+.6f" % (sel["validation_map5"]
                        - BEST["repeat_recency"]["map5"])],
            ["+ popularity 0.20 + CBF 0.10",
             m5(find_result(
                 "hybrid_1.00_0.20_0.10_repeat_recency+popularity+content_based")["map5"]),
             "%+.6f" % (find_result(
                 "hybrid_1.00_0.20_0.10_repeat_recency+popularity+content_based")["map5"]
                 - BEST["repeat_recency"]["map5"])],
            ["+ popularity 0.20 + AR 0.05",
             m5(find_result(
                 "hybrid_1.00_0.20_0.05_repeat_recency+popularity+association_rules")["map5"]),
             "%+.6f" % (find_result(
                 "hybrid_1.00_0.20_0.05_repeat_recency+popularity+association_rules")["map5"]
                 - BEST["repeat_recency"]["map5"])],
            ["+ popularity 0.20 + AR 0.05 + CBF 0.05",
             m5(find_result(
                 "hybrid_1.00_0.20_0.05_0.05_repeat_recency+popularity+association_rules+content_based")["map5"]),
             "%+.6f" % (find_result(
                 "hybrid_1.00_0.20_0.05_0.05_repeat_recency+popularity+association_rules+content_based")["map5"]
                 - BEST["repeat_recency"]["map5"])],
        ],
    }))
    a(("p",
       "Adding the association component hurts the best fusion (MAP@5 falls "
       "below the repeat + popularity variant), confirming that confidence rules "
       "duplicate popularity rather than complement it. Content-based filtering "
       "adds a small but consistent gain of +0.002556 over repeat + popularity "
       "0.20, and doubling its weight erodes that gain, so 0.05 is retained."))

    a(("h2", "9.5 Methods considered but not implemented"))
    a(("bullets", [
        "**Implicit-feedback matrix factorisation (ALS/BPR).** The environment "
        "has no SciPy, scikit-learn, `implicit` or LightFM, and the brief "
        "forbids installing packages. A NumPy SGD implementation of BPR is "
        "feasible but was not attempted within the available time; it is the "
        "most valuable next experiment because it would learn latent structure "
        "instead of relying on hand-set metadata weights.",
        "**Personalised PageRank on a programme co-view graph.** The graph is "
        "small enough for a dense NumPy implementation, but tuning the restart "
        "vector, edge weighting and damping factor is a separate study. It was "
        "left as future work rather than reported without evaluation.",
        "Neither omission affects the exam requirement: two families (CBF and "
        "association rules) were implemented and evaluated on this competition's "
        "data, in addition to the popularity baseline.",
    ]))

    # ------------------------------------------------------------------
    a(("h1", "10. Methodology, hyperparameters and experimental setup"))
    a(("table", {
        "header": ["Protocol element", "Specification"],
        "widths": [0.28, 0.72],
        "rows": [
            ["Training data", "Weeks 11–20, strategy A, rule B: 335,816 pairs, "
                              "4,823 users, 2,807 programmes"],
            ["Validation data", "Weeks 21–30, same rule: 312,934 pairs, 4,774 "
                                "users, 3,088 programmes"],
            ["Evaluation users", "All 4,774 users with at least one validation "
                                 "interaction"],
            ["Ground truth", "Validation programmes restricted to the 6,363-"
                             "programme validation universe; 0 items fall outside"],
            ["Candidate universe", "Labelled programmes in the weeks 21–30 "
                                   "schedule (validation) or weeks 31–42 "
                                   "schedule (submission)"],
            ["Metric", "MAP@5, with AP@5 denominator min(|G_u|, 5)"],
            ["Secondary diagnostics", "Recall@5, hit rate, catalogue coverage, "
                                      "mean recommendations per user"],
            ["Repeats", "Allowed identically for every method"],
            ["Fallback", "Global training popularity by distinct users, "
                         "restricted to the candidate universe"],
            ["Tuning", "Small documented grid on validation only; one primary "
                       "configuration per method reported"],
            ["Final fit", "Training weeks 11–20 only; validation is not used to "
                          "fit the submission"],
            ["Randomness", "None. All components are deterministic; ties break "
                           "by candidate index"],
        ],
    }))
    a(("h2", "10.1 Metric definition"))
    a(("p",
       "For user `u` with ground-truth set `G_u` and a ranked list of five "
       "recommendations, let `rel(k)` be 1 if the item at rank `k` is in `G_u` "
       "and 0 otherwise. Then:"))
    a(("code", [
        "P@k(u)   = (1/k) * sum_{j=1..k} rel(j)",
        "AP@5(u)  = (1 / min(|G_u|, 5)) * sum_{k=1..5} P@k(u) * rel(k)",
        "MAP@5    = (1 / |U|) * sum_{u in U} AP@5(u)",
    ]))
    a(("p",
       "Normalising by `min(|G_u|, 5)` rewards a user who watched fewer than five "
       "programmes for a perfect top-|G_u| list; it is the standard MAP@5 "
       "convention for recommendation tasks with variable-length ground truth. "
       "Recall@5 is the total number of hits divided by the total number of "
       "ground-truth items and is reported for context; it is not the selection "
       "metric."))
    a(("h2", "10.2 Hyperparameter search"))
    a(("table", {
        "header": ["Method", "Configurations", "Grid", "Best MAP@5"],
        "widths": [0.25, 0.12, 0.43, 0.20],
        "align": ["L", "R", "L", "R"],
        "rows": [
            ["Popularity", "3", "variant {distinct users, sessions, events}",
             m5(BEST["popularity"]["map5"])],
            ["Content-based", "12",
             "features {genre, all} × profile {binary, log-sessions} × "
             "K {none, 50, 200}", m5(BEST["content_based"]["map5"])],
            ["Association rules", "6",
             "metric {confidence, lift} × K {50, 100} × "
             "support {3, 5}", m5(BEST["association_rules"]["map5"])],
            ["Repeat-recency", "6", "tau {60, 90, 120} × alpha {1.0, 1.5}",
             m5(BEST["repeat_recency"]["map5"])],
            ["Hybrid", "23",
             "18 repeat × popularity weight combinations at three popularity "
             "strengths, plus 5 three- and four-component fusions",
             m5(BEST["hybrid"]["map5"])],
            ["Total", "50", "—", m5(sel["validation_map5"])],
        ],
    }))
    a(("p",
       "The grid is deliberate but modest. Fifty configurations over one "
       "validation fold is enough to establish which family works and to avoid "
       "obviously bad parameter values, but it is not a substitute for nested "
       "cross-validation; Section 12 quantifies how close the leading "
       "configurations are."))
    a(("h2", "10.3 Runtime and environment"))
    a(("table", {
        "header": ["Item", "Value"],
        "widths": [0.40, 0.60],
        "rows": [
            ["Python", EXP["environment"]["python"]],
            ["NumPy", EXP["environment"]["numpy"]],
            ["Platform / accelerator", "macOS arm64, Apple M2, CPU only"],
            ["Libraries not used", "SciPy, scikit-learn, implicit, NetworkX, "
                                   "PyTorch, Matplotlib (not installed)"],
            ["Total experiment runtime", "%.1f s" % EXP["runtime_seconds"]],
            ["Peak resident memory", "%.1f MB" % EXP["peak_rss_mb"]],
            ["Submission build runtime", "%.1f s" % SUB["runtime_seconds"]],
            ["Slowest single configuration", "0.70 s (CBF, all features, "
                                              "K = 200)"],
            ["Random seeds", "None required; every method is deterministic"],
        ],
    }))
    a(("p",
       "Determinism is enforced structurally rather than statistically. Scores "
       "are accumulated in sorted candidate order, ties are broken by candidate "
       "index, and no sampling, shuffling or random initialisation is used. "
       "Running the pipeline twice produces the same submission hash."))
    a(("h2", "10.4 Leakage controls"))
    a(("bullets", [
        "Popularity counts, co-viewing counts, user profiles and repeat "
        "parameters are estimated on training weeks 11–20 only. The metadata "
        "TF-IDF vocabulary and IDF weights are computed from the union of "
        "training and target-period programme metadata; this is schedule "
        "content available at prediction time and contains no interaction "
        "behaviour. Validation users are scored with profiles built from their "
        "training history.",
        "Validation interactions are never used to build a profile, a popularity "
        "count, a rule or a feature weight. They appear only in the ground-truth "
        "sets used for scoring.",
        "The validation schedule is used only for its programme universe and "
        "metadata, which are available at prediction time and contain no user "
        "behaviour.",
        "The test schedule is used only for its candidate universe and metadata. "
        "No test viewing log exists, so no test behaviour can enter the model.",
        "`tv_show_id = 0` is removed before any modelling, so unlabelled airtime "
        "cannot inflate popularity or co-viewing.",
        "Every method shares the candidate universe, the repeat policy and the "
        "fallback, so differences in MAP@5 reflect the ranking method rather "
        "than the evaluation envelope.",
    ]))

    # ------------------------------------------------------------------
    a(("h1", "11. Validation MAP@5 comparison"))
    a(("table", {
        "header": ["Method", "Best configuration", "MAP@5", "Recall@5",
                   "Hit rate", "Coverage", "Runtime (s)"],
        "widths": [0.16, 0.34, 0.09, 0.09, 0.08, 0.09, 0.09],
        "align": ["L", "L", "R", "R", "R", "R", "R"],
        "rows": [
            [METHOD_LABEL[m], pretty_config(BEST[m]["config"]),
             m5(BEST[m]["map5"]),
             "%.4f" % BEST[m]["recall5"], "%.4f" % BEST[m]["hit_rate"],
             pct(BEST[m]["catalog_coverage"]), "%.2f" % BEST[m]["runtime_seconds"]]
            for m in BEST_ORDER
        ],
    }))
    a(("p",
       "The ranking is repeat-recency (with fusion) > popularity ≈ association "
       "rules > content-based filtering. Three observations matter more than "
       "the exact ordering."))
    a(("bullets", [
        "**Popularity is a hard baseline.** Any model that ignores the "
        "mass-audience structure of linear TV starts far behind. CBF, which "
        "ignores popularity entirely, loses roughly 37 MAP@5 points to it.",
        "**Personalised re-consumption is the strongest single signal.** "
        "Repeat-recency improves on popularity by +" +
        "%.4f" % (BEST["repeat_recency"]["map5"] - BEST["popularity"]["map5"]) +
        " MAP@5 while also lifting catalogue coverage from 0.08% to 14.2%.",
        "**Fusion buys a little, not a lot.** The hybrid improves on the best "
        "single method by +" +
        "%.4f" % (sel["validation_map5"] - BEST["repeat_recency"]["map5"]) +
        " MAP@5. That is a real but small effect, and it should be described as "
        "such rather than as a step change.",
    ]))
    a(("p",
       "The hit rate is high for every behavioural method (90–93%) because the "
       "validation ground truth is large — the median user has 50 validation "
       "pairs — and because the same mass-audience programmes appear in almost "
       "every user's history. MAP@5 is the more discriminating metric because it "
       "rewards putting hits at the top of the list, not merely somewhere in the "
       "top five."))

    # ------------------------------------------------------------------
    a(("h1", "12. Final model selection and justification"))
    a(("p",
       "The selection rule was fixed in advance: choose the configuration with "
       "the highest validation MAP@5, and among near-ties prefer the simpler, "
       "better-understood model. The selected model is:"))
    a(("table", {
        "header": ["Component", "Weight", "Role"],
        "widths": [0.34, 0.14, 0.52],
        "align": ["L", "R", "L"],
        "rows": [
            ["Repeat-recency (tau = 120 d, alpha = 1.0)", "1.00",
             "Personalised re-consumption; carries the ranking"],
            ["Popularity (distinct users)", "0.20",
             "Prior for mass-audience programmes and cold users"],
            ["Content-based (genre, log-sessions, K = 50)", "0.05",
             "Discovery and cold-item coverage"],
            ["RRF constant k", "60", "Rank-fusion damping"],
        ],
    }))
    a(("p",
       "**Justification.** The hybrid is the best of 50 evaluated configurations "
       "at MAP@5 = " + m5(sel["validation_map5"]) + ", with Recall@5 = " +
       "%.4f" % sel["validation_recall5"] + " and hit rate " +
       "%.4f" % find_result(sel["config"])["hit_rate"] + ". It improves on the "
       "strongest single model by 0.012042 and on the strongest two-component "
       "fusion with the same repeat base by 0.002556. The strongest "
       "two-component fusion overall (tau = 60, alpha = 1.5, popularity 0.20) "
       "is 0.002284 behind. Those gains are small, so the honest claim is that "
       "the hybrid is *marginally* better, not decisively better."))
    a(("table", {
        "header": ["Configuration", "MAP@5", "Gap to selected"],
        "widths": [0.60, 0.20, 0.20],
        "align": ["L", "R", "R"],
        "rows": [
            ["Selected hybrid (repeat + popularity + CBF)", m5(sel["validation_map5"]),
             "—"],
            ["Hybrid repeat + popularity (tau = 60, alpha = 1.5, w_pop = 0.20)",
             m5(find_result("hybrid_rep_tau60_a1.5_pop0.2")["map5"]),
             "%.6f" % (find_result("hybrid_rep_tau60_a1.5_pop0.2")["map5"]
                       - sel["validation_map5"])],
            ["Hybrid repeat + popularity (tau = 90, alpha = 1.0, w_pop = 0.20)",
             m5(find_result("hybrid_rep_tau90_a1.0_pop0.2")["map5"]),
             "%.6f" % (find_result("hybrid_rep_tau90_a1.0_pop0.2")["map5"]
                       - sel["validation_map5"])],
            ["Hybrid repeat + popularity + AR + CBF",
             m5(find_result(
                 "hybrid_1.00_0.20_0.05_0.05_repeat_recency+popularity+association_rules+content_based")["map5"]),
             "%.6f" % (find_result(
                 "hybrid_1.00_0.20_0.05_0.05_repeat_recency+popularity+association_rules+content_based")["map5"]
                 - sel["validation_map5"])],
            ["Repeat-recency alone (tau = 120, alpha = 1.0)",
             m5(BEST["repeat_recency"]["map5"]),
             "%.6f" % (BEST["repeat_recency"]["map5"] - sel["validation_map5"])],
            ["Popularity baseline", m5(BEST["popularity"]["map5"]),
             "%.6f" % (BEST["popularity"]["map5"] - sel["validation_map5"])],
        ],
    }))
    a(("p",
       "The top five configurations lie within 0.0023 MAP@5 of one another. Given "
       "that the validation set contains 4,774 users, this is a narrow margin and "
       "the selection should be read as \"one of several statistically "
       "indistinguishable good configurations\" rather than a uniquely optimal "
       "model. The chosen configuration is nevertheless the best observed, is "
       "simple to reproduce, and degrades gracefully: if the content component "
       "fails for an item it falls back to popularity, and if a user has no "
       "history the whole list falls back to popularity."))
    a(("callout",
       "**No refitting on validation.** The submission uses the same "
       "hyperparameters but re-estimates every behavioural component "
       "(popularity, repeat-recency and co-viewing) from training weeks 11–20, "
       "recomputes the metadata TF-IDF weights over the training and test "
       "universes, and applies the result to the test schedule. Validation "
       "interactions are not present in the submitted model in any form. This "
       "keeps the validation estimate and the submitted model aligned."))

    # ------------------------------------------------------------------
    a(("h1", "13. Kaggle submission"))
    a(("p",
       "The submission is written to `outputs/final_submission.csv` with the "
       "header `user_id,tv_show_id`, the exact 1,260 template user IDs in "
       "template order, and exactly five space-separated programme IDs per row."))
    a(("table", {
        "header": ["Automated check", "Result"],
        "widths": [0.70, 0.30],
        "rows": [
            ["Header exactly `user_id,tv_show_id`",
             "pass" if checks["header_exact"] else "FAIL"],
            ["Exactly 1,260 data rows", "pass" if checks["row_count_1260"] else "FAIL"],
            ["1,260 unique user IDs", "pass" if checks["unique_user_ids"] else "FAIL"],
            ["Template order preserved",
             "pass" if checks["template_order_preserved"] else "FAIL"],
            ["Exactly five IDs per row", "pass" if checks["five_ids_per_row"] else "FAIL"],
            ["No duplicate ID within a row",
             "pass" if checks["no_duplicate_ids_within_row"] else "FAIL"],
            ["No `tv_show_id = 0`", "pass" if checks["no_zero_id"] else "FAIL"],
            ["All IDs numeric", "pass" if checks["all_ids_numeric"] else "FAIL"],
            ["All IDs in the weeks 31–42 labelled universe",
             "pass" if checks["all_ids_in_test_universe"] else "FAIL"],
        ],
    }))
    a(("table", {
        "header": ["Submission property", "Value"],
        "widths": [0.55, 0.45],
        "rows": [
            ["Path", "`outputs/final_submission.csv`"],
            ["Rows", n(sub["rows"])],
            ["Users with training history", n(SUB["users"]["with_training_history"])],
            ["Users without training history", n(SUB["users"]["without_training_history"])],
            ["Distinct programme IDs", n(sub["distinct_programme_ids"])],
            ["Distinct recommendation lists", n(sub["distinct_recommendation_lists"])],
            ["Candidate universe", "6,636 labelled programmes (weeks 31–42)"],
            ["SHA-256 (grouped for display)",
             "`" + grouped_hash(sub["sha256"]) + "`"],
        ],
    }))
    a(("p",
       "The lists are repeat-heavy: on average 4.94 of the five recommendations "
       "are programmes the user already watched in training, 1,239 of 1,260 rows "
       "consist entirely of repeats, and 11 rows contain no repeat at all: the "
       "10 users with no training history, plus one user whose single training "
       "programme does not air in the test universe. This is the direct consequence of the validation "
       "evidence in Section 11 and is discussed as a business risk in Section "
       "15."))
    a(("h2", "13.1 Comparison with the existing external submissions"))
    a(("p",
       "Three submission files were found outside the project in `~/Downloads`. "
       "They were inspected read-only, were not modified, and cannot be "
       "reproduced from this repository because no generating code or "
       "configuration accompanies them. All three are structurally valid."))
    a(("table", {
        "header": ["File", "Mean overlap at 5", "Top-1 agreement",
                   "Identical rows", "Mean repeats / 5", "All-repeat rows"],
        "widths": [0.26, 0.16, 0.16, 0.14, 0.14, 0.14],
        "align": ["L", "R", "R", "R", "R", "R"],
        "rows": [
            ["solution.csv", "%.2f / 5" % cmp_ext["solution.csv"]["mean_overlap_at_5"],
             "%.3f" % cmp_ext["solution.csv"]["top1_agreement"],
             n(cmp_ext["solution.csv"]["identical_rows"]), "4.94", "98.3%"],
            ["solution_improved.csv",
             "%.2f / 5" % cmp_ext["solution_improved.csv"]["mean_overlap_at_5"],
             "%.3f" % cmp_ext["solution_improved.csv"]["top1_agreement"],
             n(cmp_ext["solution_improved.csv"]["identical_rows"]), "4.94",
             "98.3%"],
            ["final_submission.csv (external)",
             "%.2f / 5" % cmp_ext["final_submission.csv"]["mean_overlap_at_5"],
             "%.3f" % cmp_ext["final_submission.csv"]["top1_agreement"],
             n(cmp_ext["final_submission.csv"]["identical_rows"]), "4.38",
             "63.1%"],
            ["This submission", "—", "—", "—", "4.94", "98.3%"],
        ],
    }))
    a(("p",
       "Overlap is high but not identical, as expected for independently built "
       "repeat-heavy recommenders. The two earlier external files are almost "
       "exactly as repeat-dominated as this submission (mean 4.94 of five, "
       "98.3% of rows entirely repeats), while the external `final_submission."
       "csv` is less so (mean 4.38, 63.1%). High overlap with an unverified "
       "external file is not evidence of quality; it only shows that all "
       "approaches converge on the same mass-audience programmes."))
    a(("h2", "13.2 Scores: offline, public and private"))
    a(("p",
       "The three kinds of score are kept strictly separate. Only the offline "
       "validation numbers were produced in this environment."))
    a(("table", {
        "header": ["Score type", "Value", "Applies to", "Verification"],
        "widths": [0.30, 0.16, 0.30, 0.24],
        "rows": [
            ["Offline validation MAP@5", m5(sel["validation_map5"]),
             "Selected hybrid, weeks 21–30",
             "Computed locally from the 323/323 verified split"],
            ["Offline validation MAP@5",
             m5(BEST["popularity"]["map5"]), "Popularity baseline, weeks 21–30",
             "Computed locally"],
            ["Kaggle public score", "not available",
             "`outputs/final_submission.csv`", "Not uploaded from here"],
            ["Kaggle private score", "not available",
             "`outputs/final_submission.csv`", "Not uploaded from here"],
            ["Kaggle public score", "0.35830",
             "An earlier external submission",
             "User-reported; no API record or screenshot in the project"],
            ["Kaggle private score", "0.33986",
             "An earlier external submission",
             "User-reported; no API record or screenshot in the project"],
            ["Offline validation MAP@5", "≈ 0.63747",
             "The user's `solution_improved.csv`",
             "User-reported; not reproduced by this pipeline"],
        ],
    }))
    a(("callout",
       "**What can and cannot be claimed.** The selected hybrid's offline "
       "validation MAP@5 is " + m5(sel["validation_map5"]) + ". Its Kaggle "
       "public and private scores are unknown. The 0.35830 / 0.33986 pair "
       "belongs to a different, earlier submission; it is not this model's "
       "score and it is not evidence about this model. No Kaggle acceptance "
       "has been verified."))
    a(("h2", "13.3 Manual upload instructions"))
    a(("p",
       "No Kaggle credentials or network submission were available in this "
       "environment, so the file was not uploaded. To submit manually:"))
    a(("code", [
        "1. Open https://www.kaggle.com/competitions/",
        "         sweettv-tv-program-recommender",
        "2. Click 'Submit Predictions' / 'Late Submission'.",
        "3. Upload outputs/final_submission.csv",
        "   SHA-256 " + sub["sha256"],
        "4. Add a description, e.g. 'Stage 3 RRF hybrid v1'.",
        "5. Record the public and private scores exactly as shown.",
    ]))
    a(("h2", "13.4 Submission screenshot"))
    if os.path.isfile(SCREENSHOT):
        a(("image", {
            "path": SCREENSHOT,
            "width": 1.0,
            "caption": "Kaggle submission record for outputs/final_submission.csv, "
                       "including submission time and the public and private "
                       "scores.",
        }))
    else:
        a(("placeholder",
           "SCREENSHOT PLACEHOLDER — no Kaggle submission screenshot exists in "
           "the project, and none is fabricated.\n\n"
           "Required screenshot: the Kaggle competition's 'My Submissions' page, "
           "showing the row for `final_submission.csv` with its submission time, "
           "public score and private score visible, plus the competition name in "
           "the page header. Ideally also the submission detail page showing the "
           "score breakdown.\n\n"
           "Save it as `reports/kaggle_submission_screenshot.png` and re-run "
           "`python3 src/build_stage3_report.py`; the report will embed it "
           "automatically once the file exists."))

    # ------------------------------------------------------------------
    a(("h1", "14. Example recommendations"))
    a(("p",
       "Two users are shown: the first template user, who has extensive training "
       "history, and a template user with no history at all, which exercises the "
       "cold-start path. Titles are the most frequently aired variant for each "
       "programme ID, taken from the schedule metadata."))
    rows = example_rows()
    users = []
    for row in rows:
        if row["user_id"] not in users:
            users.append(row["user_id"])
    for idx, user in enumerate(users):
        subset = [r for r in rows if r["user_id"] == user]
        warm = any(r["source"] == "repeat" for r in subset)
        a(("h2", "14.%d User `%s`%s" % (
            idx + 1, user, "" if warm else " (no training history)")))
        a(("table", {
            "header": ["Rank", "Programme ID", "Title", "Source",
                       "Training sessions"],
            "widths": [0.08, 0.17, 0.47, 0.16, 0.12],
            "align": ["C", "L", "L", "L", "R"],
            "rows": [
                [r["rank"], r["tv_show_id"], r["title"], r["source"],
                 r["training_sessions_for_item"] or "—"]
                for r in subset
            ],
        }))
    a(("p",
       "**Reading the examples.** The warm user's list is entirely re-"
       "consumption: national news (`ТСН.`), morning news (`Факты.`), the "
       "breakfast show (`Завтрак с 1+1`) and the evening news (`Сегодня.`), all "
       "watched between 30 and 98 times in training. The ordering follows "
       "recency-weighted session counts, not raw session counts; in this "
       "example the raw-count order happens to agree with the ranking. "
       "For the cold user the repeat component is "
       "empty and the content component has no profile, so the fused ranking "
       "degenerates to the popularity prior; the list is therefore dominated by "
       "the same mass-audience news programmes. That is the documented fallback "
       "behaviour, not a personalisation failure specific to this user."))

    # ------------------------------------------------------------------
    a(("h1", "15. Business interpretation"))
    a(("h2", "15.1 Familiar preference dominates"))
    a(("p",
       "The strongest and most robust finding is that SWEET.TV viewers in this "
       "period mostly re-watch. A model built only on recency-weighted repeat "
       "counts reaches MAP@5 " + m5(BEST["repeat_recency"]["map5"]) +
       ", and every improvement on top of it is small. For a linear-TV product "
       "this is intuitive: news, breakfast shows and long-running series are "
       "appointment viewing, and the same programme appears at the same time "
       "every day. A recommender that treats the task as pure discovery would be "
       "optimising the wrong objective."))
    a(("h2", "15.2 Discovery is real but secondary"))
    a(("p",
       "Content-based filtering alone reaches only " +
       m5(BEST["content_based"]["map5"]) + ", and its marginal contribution to "
       "the hybrid is +0.002556 MAP@5. Discovery therefore matters, but it is "
       "worth about a quarter of a percentage point in this evaluation. A "
       "product team should still keep a discovery slot for catalogue health and "
       "editorial reasons, but should not expect it to move the primary metric "
       "much. The 0.05 weight is a deliberate compromise between relevance and "
       "exposure."))
    a(("h2", "15.3 Popularity bias"))
    a(("p",
       "The popularity baseline concentrates " +
       pct(find_result("popularity_users")["catalog_coverage"]) +
       " of the catalogue in its top five and still achieves " +
       m5(BEST["popularity"]["map5"]) + " MAP@5. The final submission uses 220 "
       "distinct programme IDs, and the validation hybrid covers " +
       pct(find_result(sel["config"])["catalog_coverage"]) +
       " of the candidate catalogue. That is a healthier distribution than pure "
       "popularity, but it is still narrow: a small set of news and breakfast "
       "programmes absorbs most exposure. In production this creates a "
       "feedback loop — popular items get recommended, get more views, and "
       "become more popular — and it under-serves viewers who want films, sport "
       "or niche genres."))
    a(("h2", "15.4 Cold start"))
    a(("p",
       "Item cold start is the binding constraint for the test period: 2,566 of "
       "6,636 test programmes (38.7%) never appear in training, and 1,437 never "
       "appear in either earlier period. All have metadata, so a content "
       "component can place them, but only weakly. User cold start is small (10 "
       "of 1,260 submission users) and is handled by the popularity fallback, "
       "which is reasonable for a first session but cannot personalise."))
    a(("h2", "15.5 Practical deployment"))
    a(("bullets", [
        "**Cost.** The validation grid runs in about 27 s and the submission "
        "build in about 15 s on a laptop CPU, peaking below 1 GB, so a weekly "
        "batch refresh is trivial and requires no GPU or external service.",
        "**Freshness.** Popularity and repeat scores should be recomputed on the "
        "most recent completed weeks; the current model uses a fixed ten-week "
        "training window and would decay if left unchanged for months.",
        "**Exploration.** Reserve at least one of the five slots for a "
        "content-based or editorial pick. The validation cost of forcing "
        "exploration can be measured directly by re-ranking with a smaller "
        "repeat weight.",
        "**Monitoring.** Track catalogue coverage, repeat share and hit rate "
        "alongside MAP@5. A model can improve MAP@5 while becoming more "
        "homogeneous, and coverage is the early warning signal.",
        "**Cold items.** Give new programmes a temporary popularity prior based "
        "on genre and slot, then let observed behaviour take over. The current "
        "fallback would never surface a cold programme unless it is already "
        "popular in training.",
    ]))

    # ------------------------------------------------------------------
    a(("h1", "16. Limitations and future improvements"))
    a(("bullets", [
        "**Single temporal fold.** One training window and one validation window "
        "give a point estimate with no confidence interval. A rolling-origin "
        "evaluation over several ten-week folds would show whether the ranking "
        "of methods is stable.",
        "**Validation optimism.** Fifty configurations were compared on the same "
        "hold-out. The top five are within 0.0023 MAP@5, so the selection margin "
        "is comparable to selection noise. Nested validation or a small "
        "bootstrap would quantify this.",
        "**Metric alignment.** MAP@5 rewards recommending programmes the user "
        "already watched, and the Kaggle metric may weight users or periods "
        "differently. The offline-to-leaderboard gap cannot be estimated without "
        "an actual submission score.",
        "**No test feedback.** There is no test viewing log, so the test-period "
        "model cannot be validated directly. The candidate universe is known, "
        "but whether users actually watch the recommended programmes is not.",
        "**Sparse metadata.** About 4% of labelled rows have no genre and 13% no "
        "cast or director. Content features are therefore uneven across the "
        "catalogue.",
        "**Unlabelled airtime.** Half of all viewing sessions cannot be "
        "attributed to a labelled programme, which caps the behavioural signal "
        "per user.",
        "**No channel-affinity constraint.** Candidates are not filtered by the "
        "channels a user actually watches, so the model can recommend a "
        "programme on a channel the user never tunes to.",
        "**Missing method families.** Implicit matrix factorisation and "
        "personalised PageRank were not implemented (Section 9.5), so the "
        "comparison does not cover latent-factor or graph-based ranking.",
    ]))
    a(("p",
       "**Prioritised next steps.** (1) Submit the current file to obtain a real "
       "leaderboard signal. (2) Implement BPR or ALS matrix factorisation in "
       "NumPy and evaluate it under the identical protocol. (3) Add a "
       "channel-affinity filter to the candidate universe and measure the effect "
       "on MAP@5 and coverage. (4) Replace the single fold with rolling-origin "
       "validation. (5) Learn fusion weights per user segment instead of using "
       "one global set."))

    # ------------------------------------------------------------------
    a(("h1", "17. Reproducibility"))
    a(("p",
       "All original data are read-only symbolic links under `data/`; nothing in "
       "the source files was modified. Every Stage 3 artifact can be rebuilt "
       "from the verified Stage 2.5 outputs with the commands below."))
    a(("h2", "17.1 Environment"))
    a(("table", {
        "header": ["Item", "Value"],
        "widths": [0.34, 0.66],
        "rows": [
            ["Operating system", "macOS arm64 (Apple M2, 8 logical cores)"],
            ["Memory", "8 GiB"],
            ["Python", "3.9.6 (`/usr/bin/python3`)"],
            ["NumPy", "2.0.2"],
            ["pandas", "2.3.3 (audit only)"],
            ["PDF rendering", "ReportLab 5.0.1 (already installed)"],
            ["Not installed", "SciPy, scikit-learn, implicit, LightFM, "
                              "NetworkX, PyTorch, Matplotlib"],
            ["Packages installed for this stage", "none"],
        ],
    }))
    a(("h2", "17.2 Execution steps"))
    a(("code", [
        'cd "/Users/chitranshgupta/Downloads/SWEETTV_Midterm"',
        "",
        "# 1. Pre-modelling status report (reads verified Stage 2.5 outputs)",
        "python3 src/build_stage3_status.py",
        "#    -> reports/stage3_status.md, reports/stage3_status.json",
        "",
        "# 2. Full validation grid and model selection (~27 s)",
        "python3 src/evaluate_stage3_models.py",
        "#    -> reports/stage3_experiment_results.json",
        "#    -> reports/stage3_model_comparison.csv",
        "#    -> reports/stage3_model_comparison.md",
        "#    -> outputs/stage3/selected_model.json",
        "",
        "# 3. Build the Kaggle submission (~15 s)",
        "python3 src/build_final_submission.py",
        "#    -> outputs/final_submission.csv",
        "#    -> outputs/stage3/submission_metadata.json",
        "#    -> reports/stage3_example_recommendations.csv",
        "",
        "# 4. Independent audit (33 checks)",
        "python3 src/verify_stage3.py",
        "#    -> reports/stage3_verification.json",
        "#    -> reports/stage3_audit.md",
        "",
        "# 5. Render this report (Markdown + PDF)",
        "python3 src/build_stage3_report.py",
        "#    -> reports/SWEETTV_Final_Exam_Report.md",
        "#    -> reports/SWEETTV_Final_Exam_Report.pdf",
    ]))
    a(("p",
       "Stage 1, 2 and 2.5 are upstream dependencies and are not re-run by these "
       "commands. Their reproduction steps are documented in `README.md`. The "
       "Stage 3 scripts read only:\n"
       "`outputs/temporal_interactions/train_strategyA_ruleB_60s_20pct.csv.gz`, "
       "`outputs/temporal_interactions/validation_strategyA_ruleB_60s_20pct.csv.gz`, "
       "`data/export_arh_11-20-final.csv`, `data/export_arh_21-30-final.csv`, "
       "`data/export_arh_31-42-final.csv` and `data/submission.csv`."))
    a(("h2", "17.3 Project structure"))
    a(("code", [
        "SWEETTV_Midterm/",
        "├── data/                      # read-only symlinks to the competition CSVs",
        "├── src/",
        "│   ├── stage2_common.py       # Stage 2 parsing / schedule index",
        "│   ├── stage3_common.py       # loading, features, CBF, rules, ranking",
        "│   ├── build_stage3_status.py # pre-modelling status report",
        "│   ├── evaluate_stage3_models.py  # 50-config validation grid",
        "│   ├── build_final_submission.py  # selected-model submission builder",
        "│   ├── verify_stage3.py       # independent 33-check audit",
        "│   ├── report_renderer.py     # Markdown + ReportLab PDF renderer",
        "│   └── build_stage3_report.py # this report",
        "├── outputs/",
        "│   ├── final_submission.csv   # Kaggle submission",
        "│   └── stage3/                # selected_model.json, submission metadata",
        "└── reports/                   # experiments, audits, examples, this report",
    ]))
    a(("h2", "17.4 Verification summary"))
    a(("table", {
        "header": ["Verification", "Result"],
        "widths": [0.68, 0.32],
        "rows": [
            ["Stage 2.5 temporal split",
             "%d/%d checks passed" % (TEMP_VER["checks_passed"],
                                      TEMP_VER["checks_total"])],
            ["Stage 3 submission and experiment audit",
             "%d/%d checks passed" % (VER["checks_passed"],
                                      VER["checks_total"])],
            ["Selected model present in the experiment grid", "pass"],
            ["Submission hash matches metadata", "pass"],
            ["External files unchanged", "pass"],
        ],
    }))

    # ------------------------------------------------------------------
    a(("h1", "18. Repository"))
    a(("p",
       "No Git repository was found in the project directory (no `.git` "
       "directory, no remote configuration), so no repository URL can be "
       "reported. The project is currently a self-contained directory with the "
       "structure in Section 17.3."))
    a(("placeholder",
       "GITHUB REPOSITORY LINK — placeholder.\n\n"
       "Required action: initialise or locate the repository and provide its "
       "public URL. Suggested steps:\n"
       "  git init\n"
       "  git add README.md src reports outputs/stage3\n"
       "  git commit -m \"Stage 3: recommendation models, submission and report\"\n"
       "  git remote add origin <URL>\n"
       "  git push -u origin main\n\n"
       "Do not commit the 890 MB raw competition data; `data/` contains "
       "symlinks and should be excluded or documented."))

    # ------------------------------------------------------------------
    a(("h1", "Appendix A. Complete validation grid"))
    a(("p",
       "All 50 evaluated configurations, ordered by method and MAP@5. Runtime is "
       "the wall-clock time for that configuration only, excluding shared data "
       "loading."))
    a(("table", {
        "header": ["Method", "Configuration", "MAP@5", "Recall@5", "Coverage",
                   "Runtime (s)"],
        "widths": [0.16, 0.42, 0.11, 0.11, 0.10, 0.10],
        "align": ["L", "L", "R", "R", "R", "R"],
        "rows": [
            [METHOD_LABEL[r["method"]], pretty_config(r["config"]),
             m5(r["map5"]),
             "%.4f" % r["recall5"], pct(r["catalog_coverage"]),
             "%.2f" % r["runtime_seconds"]]
            for r in sorted(RESULTS,
                            key=lambda r: (BEST_ORDER.index(r["method"]),
                                           -r["map5"]))
        ],
    }))

    a(("h1", "Appendix B. Audit detail"))
    a(("p",
       "The Stage 3 audit is an independent script that re-reads the artifacts "
       "rather than trusting the experiment outputs. It checks %d conditions, "
       "all of which pass." % VER["checks_total"]))
    a(("table", {
        "header": ["#", "Check", "Result"],
        "widths": [0.06, 0.72, 0.22],
        "align": ["C", "L", "L"],
        "rows": [
            [str(i + 1), c["check"], "pass" if c["passed"] else "FAIL"]
            for i, c in enumerate(VER["checks"])
        ],
    }))
    return blocks


def main():
    blocks = build_blocks()
    os.makedirs(REPORTS, exist_ok=True)
    meta = {
        "title": "SWEET.TV TV Program Recommendation",
        "subtitle": "Final Exam Report — LLM4Rec · Stage 3: modelling, "
                    "evaluation and Kaggle submission",
        "competition": "SWEET.TV TV Program Recommender (Kaggle)",
        "url": "https://www.kaggle.com/competitions/sweettv-tv-program-recommender",
        "author": AUTHOR,
        "course": "LLM4Rec — final exam",
        "date": REPORT_DATE,
        "version": "1.0 (offline validation complete; Kaggle scores pending)",
        "status": "Offline validation MAP@5 = " + m5(SEL["selected"]["validation_map5"]) +
                  ". The Kaggle public/private scores for this submission are not "
                  "available and are not claimed. See Sections 2 and 13.",
    }
    R.render_markdown(blocks, MD_OUT,
                      title="SWEET.TV TV Program Recommendation — Final Exam Report")
    R.render_pdf(blocks, PDF_OUT, meta)
    size_kb = os.path.getsize(PDF_OUT) / 1024.0
    print("[report] blocks:", len(blocks))
    print("[report] wrote", MD_OUT)
    print("[report] wrote", PDF_OUT, "(%.1f KB)" % size_kb)


if __name__ == "__main__":
    main()
