"""Independent Stage 3 audit: submission structure, metric traceability and
artifact immutability.

    python3 src/verify_stage3.py
      -> reports/stage3_verification.json
      -> reports/stage3_audit.md
"""

import csv
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
STAGE3_OUT = os.path.join(ROOT, "outputs", "stage3")
SUBMISSION = os.path.join(ROOT, "outputs", "final_submission.csv")
TEMPLATE = os.path.join(DATA, "submission.csv")

OUT_JSON = os.path.join(REPORTS, "stage3_verification.json")
OUT_MD = os.path.join(REPORTS, "stage3_audit.md")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    checks = []

    def check(name, passed, detail=""):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    experiment = json.load(open(os.path.join(
        REPORTS, "stage3_experiment_results.json"), encoding="utf-8"))
    selected = json.load(open(os.path.join(
        STAGE3_OUT, "selected_model.json"), encoding="utf-8"))
    sub_meta = json.load(open(os.path.join(
        STAGE3_OUT, "submission_metadata.json"), encoding="utf-8"))
    status = json.load(open(os.path.join(
        REPORTS, "stage3_status.json"), encoding="utf-8"))
    temporal = json.load(open(os.path.join(
        REPORTS, "temporal_split_verification.json"), encoding="utf-8"))
    temporal_stats = json.load(open(os.path.join(
        REPORTS, "temporal_split_statistics.json"), encoding="utf-8"))

    # ---------------------------------------------------------------- data
    check("Stage 2.5 verification still 323/323",
          temporal["checks_passed"] == temporal["checks_total"] == 323,
          "%d/%d" % (temporal["checks_passed"], temporal["checks_total"]))
    check("source event store digest unchanged",
          temporal["digests"]["source_event_store_sha256"]
          == status["verified_stage2_5"]["source_event_store_sha256"],
          temporal["digests"]["source_event_store_sha256"][:16])
    check("temporal split DB digest unchanged",
          temporal["digests"]["temporal_split_db_sha256"]
          == status["verified_stage2_5"]["temporal_split_db_sha256"],
          temporal["digests"]["temporal_split_db_sha256"][:16])
    check("metadata projection digest unchanged",
          temporal["digests"]["metadata_projection_sha256"]
          == status["verified_stage2_5"]["metadata_projection_sha256"],
          temporal["digests"]["metadata_projection_sha256"][:16])
    check("main training data hash matches experiment",
          sha256(os.path.join(ROOT, experiment["protocol"]["train"]))
          == experiment["inputs_sha256"]["train_pairs"])
    check("main validation data hash matches experiment",
          sha256(os.path.join(ROOT, experiment["protocol"]["validation"]))
          == experiment["inputs_sha256"]["validation_pairs"])
    check("no test-period viewing log is used",
          not os.path.exists(os.path.join(DATA, "dataset31-42.csv")),
          "only schedule 31-42 is read for the candidate universe")
    check("validation used for scoring/selection only",
          experiment["protocol"]["validation_used_for_training"] is False)

    # ------------------------------------------------------ model traceability
    selected_rec = next(
        (r for r in experiment["results"]
         if r["method"] == selected["selected"]["method"]
         and r["config"] == selected["selected"]["config"]), None)
    check("selected model exists in experiment grid", selected_rec is not None)
    if selected_rec is not None:
        check("selected MAP@5 matches experiment",
              abs(selected_rec["map5"] - selected["selected"]["validation_map5"])
              < 1e-9,
              "%.6f" % selected_rec["map5"])
    check("experiment includes a popularity baseline",
          any(r["method"] == "popularity" for r in experiment["results"]))
    check("experiment includes content-based filtering",
          any(r["method"] == "content_based" for r in experiment["results"]))
    check("experiment includes association rules",
          any(r["method"] == "association_rules" for r in experiment["results"]))
    check("experiment includes a hybrid",
          any(r["method"] == "hybrid" for r in experiment["results"]))
    check("all reported metrics are finite",
          all(0.0 <= r["map5"] <= 1.0 for r in experiment["results"]))

    # ------------------------------------------------------------- submission
    with open(TEMPLATE, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        template_users = [r[0] for r in reader]
    with open(SUBMISSION, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    ids = [r[0] for r in rows]
    values = [x for r in rows for x in r[1].split()]
    candidate_pids = set()
    with open(os.path.join(DATA, "export_arh_31-42-final.csv"), "r",
              encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["tv_show_id"] != "0":
                candidate_pids.add(row["tv_show_id"])

    check("submission header exact", header == ["user_id", "tv_show_id"])
    check("submission has exactly 1260 rows", len(rows) == 1260, str(len(rows)))
    check("submission user IDs unique", len(set(ids)) == 1260)
    check("submission preserves template order", ids == template_users)
    check("every row has exactly five IDs",
          all(len(r[1].split()) == 5 for r in rows))
    check("no duplicate ID within a row",
          all(len(set(r[1].split())) == 5 for r in rows))
    check("no tv_show_id=0", "0" not in values)
    check("all IDs numeric", all(x.isdigit() for x in values))
    check("all IDs in weeks 31-42 labelled universe",
          all(x in candidate_pids for x in values))
    check("test candidate universe size 6636", len(candidate_pids) == 6636,
          str(len(candidate_pids)))
    check("submission hash matches metadata",
          sha256(SUBMISSION) == sub_meta["submission"]["sha256"],
          sub_meta["submission"]["sha256"][:16])
    check("submission row count matches metadata",
          len(rows) == sub_meta["submission"]["rows"])
    check("selected model recorded in submission metadata",
          sub_meta["selected_model"]["config"]
          == selected["selected"]["config"])

    # ------------------------------------------------------- external files
    for rec in status["existing_submissions"]:
        if not rec.get("exists"):
            continue
        check("external file unchanged: %s" % rec["label"],
              sha256(rec["path"]) == rec["sha256"],
              rec["sha256"][:16])

    # --------------------------------------------------------------- examples
    with open(os.path.join(REPORTS, "stage3_example_recommendations.csv"), "r",
              encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        examples = list(reader)
    check("example recommendations cover two users",
          len({r[0] for r in examples}) == 2,
          "%d rows" % len(examples))
    check("example recommendations have five rows per user",
          all(sum(1 for r in examples if r[0] == u) == 5
              for u in {r[0] for r in examples}))

    # ------------------------------------------------------------- write out
    passed = sum(1 for c in checks if c["passed"])
    failed = len(checks) - passed
    result = {
        "generated_by": "src/verify_stage3.py",
        "status": "complete" if failed == 0 else "failed",
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "digests": {
            "submission_sha256": sha256(SUBMISSION),
            "train_pairs_sha256":
                experiment["inputs_sha256"]["train_pairs"],
            "validation_pairs_sha256":
                experiment["inputs_sha256"]["validation_pairs"],
            "source_event_store_sha256":
                temporal["digests"]["source_event_store_sha256"],
        },
        "selected_model": selected["selected"],
        "submission": sub_meta["submission"],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    lines = []
    w = lines.append
    w("# Stage 3 — Submission and Experiment Audit")
    w("")
    w("**Result: %d/%d checks passed, %d failed.**"
      % (passed, len(checks), failed))
    w("")
    w("| Check | Passed | Detail |")
    w("|---|---|---|")
    for c in checks:
        w("| %s | %s | %s |" % (
            c["check"], "yes" if c["passed"] else "**NO**",
            c["detail"] if c["detail"] else "—"))
    w("")
    w("## Selected model")
    w("")
    w("`%s` / `%s`, validation MAP@5 **%.6f**, Recall@5 %.4f."
      % (selected["selected"]["method"], selected["selected"]["config"],
         selected["selected"]["validation_map5"],
         selected["selected"]["validation_recall5"]))
    w("")
    w("## Submission")
    w("")
    w("`%s`: %d rows, SHA-256 `%s`, %d distinct programme IDs."
      % (sub_meta["submission"]["path"], sub_meta["submission"]["rows"],
         sub_meta["submission"]["sha256"],
         sub_meta["submission"]["distinct_programme_ids"]))
    w("")
    w("The submission was not uploaded from this environment and no Kaggle "
      "score is claimed here.")
    w("")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[verify] %d/%d checks passed, %d failed" % (passed, len(checks), failed))
    print("[verify] wrote", OUT_JSON)
    print("[verify] wrote", OUT_MD)
    if failed:
        for c in checks:
            if not c["passed"]:
                print("[verify] FAILED:", c["check"], c["detail"])


if __name__ == "__main__":
    main()
