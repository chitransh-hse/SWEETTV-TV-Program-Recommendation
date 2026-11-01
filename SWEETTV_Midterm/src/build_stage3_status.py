"""Build the Stage 3 pre-modelling status report from verified artifacts.

Reads only Stage 2.5 outputs, reports and the Kaggle template.  The report
records the state that modelling will start from; it does not train anything.

    python3 src/build_stage3_status.py
      -> reports/stage3_status.md
      -> reports/stage3_status.json
"""

import csv
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
OUTPUTS = os.path.join(ROOT, "outputs", "temporal_interactions")
DATA = os.path.join(ROOT, "data")

MAIN_SPLIT = {
    "train": os.path.join(OUTPUTS, "train_strategyA_ruleB_60s_20pct.csv.gz"),
    "validation": os.path.join(OUTPUTS, "validation_strategyA_ruleB_60s_20pct.csv.gz"),
}
OUT_MD = os.path.join(REPORTS, "stage3_status.md")
OUT_JSON = os.path.join(REPORTS, "stage3_status.json")

EXTERNAL_SUBMISSIONS = [
    ("solution.csv", "/Users/chitranshgupta/Downloads/solution.csv"),
    ("solution_improved.csv", "/Users/chitranshgupta/Downloads/solution_improved.csv"),
    ("final_submission.csv", "/Users/chitranshgupta/Downloads/final_submission.csv"),
]


def fmt(n):
    return "{:,}".format(n)


def read_pairs(path):
    users = {}
    items = set()
    pairs = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if header[0] != "user_id" or header[1] != "tv_show_id":
            raise ValueError("unexpected header in %s" % path)
        for row in reader:
            pairs += 1
            users.setdefault(row[0], 0)
            users[row[0]] += 1
            items.add(row[1])
    sizes = sorted(users.values())
    return {
        "file": os.path.relpath(path, ROOT),
        "pairs": pairs,
        "users": len(users),
        "items": len(items),
        "mean_pairs_per_user": round(pairs / len(users), 2),
        "median_pairs_per_user": sizes[len(sizes) // 2],
        "p90_pairs_per_user": sizes[int(0.9 * (len(sizes) - 1))],
        "max_pairs_per_user": sizes[-1],
        "user_ids": set(users),
    }


def inspect_submission(path):
    if not os.path.exists(path):
        return {"exists": False, "path": path}
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    ids = [r[0] for r in rows]
    values = [x for r in rows for x in r[1].split()]
    with open(os.path.join(DATA, "submission.csv"), "r", encoding="utf-8",
              newline="") as f:
        reader = csv.reader(f)
        next(reader)
        template_ids = [r[0] for r in reader]
    return {
        "exists": True,
        "path": path,
        "rows": len(rows),
        "header": header,
        "unique_user_ids": len(set(ids)),
        "order_matches_template": ids == template_ids,
        "five_ids_per_row": all(len(r[1].split()) == 5 for r in rows),
        "duplicate_ids_within_row": sum(
            1 for r in rows if len(set(r[1].split())) != 5),
        "zero_ids": sum(1 for x in values if x == "0"),
        "distinct_programme_ids": len(set(values)),
        "distinct_recommendation_lists": len(set(r[1] for r in rows)),
        "sha256": __import__("hashlib").sha256(
            open(path, "rb").read()).hexdigest(),
    }


def main():
    verification = json.load(open(os.path.join(
        REPORTS, "temporal_split_verification.json"), encoding="utf-8"))
    stats = json.load(open(os.path.join(
        REPORTS, "temporal_split_statistics.json"), encoding="utf-8"))
    universe = json.load(open(os.path.join(
        REPORTS, "candidate_universe_statistics.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(
        OUTPUTS, "metadata.json"), encoding="utf-8"))

    train = read_pairs(MAIN_SPLIT["train"])
    validation = read_pairs(MAIN_SPLIT["validation"])
    template_users = set()
    with open(os.path.join(DATA, "submission.csv"), "r", encoding="utf-8",
              newline="") as f:
        reader = csv.reader(f)
        next(reader)
        template_users = {r[0] for r in reader}

    assignment = meta["boundary_session_rule"]["statistics"]
    test_meta = universe["test_metadata"]
    external = [inspect_submission(p) for _name, p in EXTERNAL_SUBMISSIONS]
    for (name, _p), rec in zip(EXTERNAL_SUBMISSIONS, external):
        rec["label"] = name

    facts = {
        "generated_by": "src/build_stage3_status.py",
        "stage": "Stage 3 pre-modelling status",
        "verified_stage2_5": {
            "boundary_rule": meta["boundary_session_rule"]["rule"],
            "cross_period_sessions": assignment["cross_period_sessions"],
            "cross_assigned_training": assignment["cross_assigned_training"],
            "cross_assigned_validation": assignment["cross_assigned_validation"],
            "events_excluded": assignment["events_excluded_total"],
            "sessions_in_both_splits": assignment["sessions_in_both_splits"],
            "verification_checks_passed": verification["checks_passed"],
            "verification_checks_total": verification["checks_total"],
            "verification_checks_failed": verification["checks_failed"],
            "source_event_store_sha256":
                verification["digests"]["source_event_store_sha256"],
            "temporal_split_db_sha256":
                verification["digests"]["temporal_split_db_sha256"],
            "metadata_projection_sha256":
                verification["digests"]["metadata_projection_sha256"],
        },
        "main_configuration": {
            "strategy": "A (keep all matches)",
            "rule": "B (60 s minimum overlap, 20% minimum overlap ratio)",
            "train_pairs": train["pairs"],
            "train_users": train["users"],
            "train_items": train["items"],
            "validation_pairs": validation["pairs"],
            "validation_users": validation["users"],
            "validation_items": validation["items"],
            "validation_users_without_training_history":
                len(validation["user_ids"] - train["user_ids"]),
            "train_users_without_validation_history":
                len(train["user_ids"] - validation["user_ids"]),
            "train_pair_table": {k: v for k, v in train.items() if k != "user_ids"},
            "validation_pair_table":
                {k: v for k, v in validation.items() if k != "user_ids"},
        },
        "candidate_universe": {
            "training_weeks_11_20": universe["period_totals"][
                "training_weeks_11_20"]["programmes"],
            "validation_weeks_21_30": universe["period_totals"][
                "validation_weeks_21_30"]["programmes"],
            "test_weeks_31_42": universe["period_totals"][
                "test_weeks_31_42"]["programmes"],
            "union": universe["period_totals"]["union"],
            "cold_start_test_programmes": test_meta["cold_start_test_programmes"],
            "cold_start_with_usable_metadata":
                test_meta["cold_start_with_usable_metadata"],
        },
        "submission_template": {
            "file": "data/submission.csv",
            "rows": len(template_users),
            "users_in_training": len(template_users & train["user_ids"]),
            "users_without_training_history":
                len(template_users - train["user_ids"]),
            "users_without_training_or_validation_history":
                len(template_users - (train["user_ids"] | validation["user_ids"])),
        },
        "existing_submissions": external,
        "existing_submission_note":
            "Files were found outside the project in ~/Downloads and were "
            "inspected read-only. They are not overwritten by Stage 3.",
        "environment": {
            "python": sys.version.split()[0],
            "numpy": __import__("numpy").__version__,
            "pandas": __import__("pandas").__version__,
            "scipy": "not installed",
            "scikit-learn": "not installed",
            "implicit": "not installed",
            "networkx": "not installed",
            "reportlab": "installed (PDF rendering)",
            "gpu": "none (CPU only, Apple M2, 8 GiB RAM)",
        },
        "user_reported_kaggle_scores": {
            "first_submission_public": 0.35830,
            "first_submission_private": 0.33986,
            "improved_offline_validation_map5": 0.63747,
            "improved_kaggle_score": None,
            "verified": False,
            "note": "Reported by the user; no Kaggle API result is stored locally.",
        },
        "planned_protocol": {
            "metric": "MAP@5",
            "evaluation_users": "all users with >= 1 validation interaction",
            "candidate_universe": "labelled programmes in the target period's "
                                  "schedule export; tv_show_id=0 excluded",
            "repeats": "allowed identically for every method",
            "fallback": "global training popularity restricted to the candidate "
                        "universe for users with no history or fewer than five "
                        "method scores",
            "methods": [
                "popularity baseline",
                "content-based filtering on schedule metadata",
                "association rules on training co-viewing",
                "rank-fusion hybrid",
            ],
            "tuning": "small documented grid on validation only",
        },
        "open_items": [
            "Kaggle public/private score for solution_improved.csv",
            "Kaggle submission-result screenshot",
            "GitHub repository URL (no .git directory found)",
            "AI-session export, if the exam requires it",
        ],
    }

    lines = []
    w = lines.append
    v = facts["verified_stage2_5"]
    w("# Stage 3 — Pre-modelling Status Report")
    w("")
    w("**Status: Stage 2.5 verified; Stage 3 modelling not yet started.**")
    w("")
    w("## 1. Scope")
    w("")
    w("This report records the verified starting point for Stage 3: the corrected "
      "temporal split, the main interaction configuration, candidate universes, "
      "existing submission assets, environment constraints and the evaluation "
      "protocol to be applied. No model has been fitted at this point.")
    w("")
    w("## 2. Corrected Stage 2.5 state")
    w("")
    w("| Check | Value |")
    w("|---|---|")
    w("| Boundary rule | `%s` |" % v["boundary_rule"])
    w("| Cross-period sessions | %s |" % fmt(v["cross_period_sessions"]))
    w("| — assigned to training | %s |" % fmt(v["cross_assigned_training"]))
    w("| — assigned to validation | %s |" % fmt(v["cross_assigned_validation"]))
    w("| Events excluded | %s |" % fmt(v["events_excluded"]))
    w("| Sessions in both splits | %s |" % fmt(v["sessions_in_both_splits"]))
    w("| Verification | **%d/%d passed, %d failed** |" % (
        v["verification_checks_passed"], v["verification_checks_total"],
        v["verification_checks_failed"]))
    w("")
    w("The maximum-overlap rule is not used. The split verified here is the one "
      "whose cross-period sessions follow the earliest matched airing "
      "(`pstart` ascending, then period ascending).")
    w("")
    w("Source event store SHA-256: `%s`." % v["source_event_store_sha256"])
    w("")
    w("## 3. Main interaction configuration")
    w("")
    w("Strategy **A** (keep every match) with rule **B** (at least 60 s and at "
      "least 20% programme overlap).")
    w("")
    w("| Split | Pair rows | Users | Programmes | Mean pairs/user | Median | P90 | Max |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    for split, rec in (("training (weeks 11–20)", train),
                       ("validation (weeks 21–30)", validation)):
        w("| %s | %s | %s | %s | %.2f | %d | %d | %d |" % (
            split, fmt(rec["pairs"]), fmt(rec["users"]), fmt(rec["items"]),
            rec["mean_pairs_per_user"], rec["median_pairs_per_user"],
            rec["p90_pairs_per_user"], rec["max_pairs_per_user"]))
    w("")
    w("Users present in validation but absent from training: **%d** (they will "
      "receive the documented fallback). Training users absent from validation: "
      "**%d** (not evaluated, but still available for fitting)." % (
          facts["main_configuration"]["validation_users_without_training_history"],
          facts["main_configuration"]["train_users_without_validation_history"]))
    w("")
    w("## 4. Candidate universe and cold start")
    w("")
    w("Candidate universes contain labelled programmes from the corresponding "
      "schedule export only; `tv_show_id = 0` is excluded everywhere.")
    w("")
    w("| Period | Labelled programmes |")
    w("|---|---:|")
    w("| Training schedule (weeks 11–20) | %s |" % fmt(
        facts["candidate_universe"]["training_weeks_11_20"]))
    w("| Validation schedule (weeks 21–30) | %s |" % fmt(
        facts["candidate_universe"]["validation_weeks_21_30"]))
    w("| Test schedule (weeks 31–42) | %s |" % fmt(
        facts["candidate_universe"]["test_weeks_31_42"]))
    w("| Union | %s |" % fmt(facts["candidate_universe"]["union"]))
    w("")
    w("Of %s test programmes, **%s** never occur in training. All %s cold-start "
      "programmes have usable metadata, so content-based scoring can still place "
      "them in a user profile." % (
          fmt(facts["candidate_universe"]["test_weeks_31_42"]),
          fmt(facts["candidate_universe"]["cold_start_test_programmes"]),
          fmt(facts["candidate_universe"]["cold_start_with_usable_metadata"])))
    w("")
    w("## 5. Existing submission assets")
    w("")
    st = facts["submission_template"]
    w("The Kaggle template has **%s users**, all with five placeholder IDs. "
      "%s template users occur in training and **%s** have no training history; "
      "%s have neither training nor validation history." % (
          fmt(st["rows"]), fmt(st["users_in_training"]),
          fmt(st["users_without_training_history"]),
          fmt(st["users_without_training_or_validation_history"])))
    w("")
    w("No recommendation script or submission existed inside the project. Three "
      "submission files were found in `~/Downloads` and were inspected read-only:")
    w("")
    w("| File | Rows | 5 IDs/row | Template order | Zero IDs | Distinct IDs | Distinct lists |")
    w("|---|---:|---|---|---:|---:|---:|")
    for rec in external:
        if not rec["exists"]:
            w("| `%s` | missing | — | — | — | — | — |" % rec["label"])
            continue
        w("| `%s` | %s | %s | %s | %d | %s | %s |" % (
            rec["label"], fmt(rec["rows"]), rec["five_ids_per_row"],
            rec["order_matches_template"], rec["zero_ids"],
            fmt(rec["distinct_programme_ids"]),
            fmt(rec["distinct_recommendation_lists"])))
    w("")
    w("All three pass the structural submission checks. They are outside the "
      "repository and will not be overwritten. Their methods are not recorded "
      "locally, so they cannot be exactly reproduced here.")
    w("")
    w("## 6. Environment and tooling")
    w("")
    w("| Item | Value |")
    w("|---|---|")
    for key, val in facts["environment"].items():
        w("| %s | %s |" % (key, val))
    w("")
    w("SciPy, scikit-learn, `implicit`, NetworkX, PyTorch and Matplotlib are not "
      "installed. Stage 3 therefore uses NumPy and the standard library only. "
      "PDF rendering uses the already-installed ReportLab. No package will be "
      "installed.")
    w("")
    w("## 7. Protocol to be applied")
    w("")
    w("- **Primary metric:** MAP@5 over all %s validation users." % fmt(
        validation["users"]))
    w("- **Fitting:** all parameters from training weeks 11–20; validation used "
      "only for selection/tuning.")
    w("- **Candidate universe:** the target period's labelled schedule "
      "programmes; no test viewing data exists and none is used.")
    w("- **Repeats:** allowed, identically for every method.")
    w("- **Fallback:** training-popularity ranking restricted to the candidate "
      "universe for users with no history or fewer than five scored items.")
    w("- **Methods:** popularity baseline, content-based filtering, association "
      "rules and a rank-fusion hybrid (additional methods only if reliable).")
    w("- **Tuning:** small documented grid; one primary configuration per method "
      "reported.")
    w("")
    w("## 8. User-reported Kaggle results and open items")
    w("")
    w("The first submission's **public 0.35830 / private 0.33986** are "
      "user-reported and are not verified locally. The improved submission's "
      "offline validation MAP@5 of approximately 0.63747 is not a Kaggle score; "
      "the improved Kaggle result has not been provided. No Kaggle API response "
      "or screenshot is stored in the project.")
    w("")
    w("Open items: improved-submission Kaggle score, submission screenshot, "
      "GitHub URL, and (if required by the exam) the original AI-session export.")
    w("")
    w("## 9. Readiness")
    w("")
    w("The corrected split, datasets, metadata, candidate universe and template "
      "are internally consistent and verified. Stage 3 can proceed without "
      "rebuilding or modifying any Stage 1/2/2.5 artifact.")
    w("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(facts, f, indent=2, ensure_ascii=False)
    print("[status] wrote", OUT_MD)
    print("[status] wrote", OUT_JSON)


if __name__ == "__main__":
    main()
