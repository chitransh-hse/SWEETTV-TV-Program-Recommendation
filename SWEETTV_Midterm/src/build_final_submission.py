"""Build the Stage 3 Kaggle submission from the selected validation model.

The selected model is a reciprocal-rank fusion of:
  - personalised repeat-recency scores from training weeks 11-20,
  - global training popularity,
  - content-based metadata similarity.

The test candidate universe is the labelled programme set of the weeks 31-42
schedule.  No test-period viewing behaviour exists or is used.  Validation
interactions are not used to fit this submission.

    python3 src/build_final_submission.py
      -> outputs/final_submission.csv
      -> outputs/stage3/submission_metadata.json
      -> reports/stage3_example_recommendations.csv
"""

import csv
import hashlib
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage3_common as c

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
STAGE3_OUT = os.path.join(ROOT, "outputs", "stage3")
SELECTED = os.path.join(STAGE3_OUT, "selected_model.json")
TEMPLATE = os.path.join(DATA, "submission.csv")
OUT = os.path.join(ROOT, "outputs", "final_submission.csv")
META_OUT = os.path.join(STAGE3_OUT, "submission_metadata.json")
EXAMPLES = os.path.join(REPORTS, "stage3_example_recommendations.csv")

TRAIN_BOUNDARY = c.s.parse_view_timestamp("2020-05-18 00:00:00")

EXTERNAL = [
    ("solution.csv", "/Users/chitranshgupta/Downloads/solution.csv"),
    ("solution_improved.csv", "/Users/chitranshgupta/Downloads/solution_improved.csv"),
    ("final_submission.csv", "/Users/chitranshgupta/Downloads/final_submission.csv"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_template_users():
    with open(TEMPLATE, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        users = [row[0] for row in reader]
    if header != ["user_id", "tv_show_id"]:
        raise ValueError("unexpected template header: %r" % (header,))
    return users


def repeat_matrix(train, users, cand_pids, tau_days, alpha):
    days = np.clip((TRAIN_BOUNDARY - train["last"]) / 86400.0, 0.0, None)
    value = (np.log1p(train["sessions"].astype(np.float64)) ** alpha) \
        * np.exp(-days / tau_days)
    user_index = {u: i for i, u in enumerate(users)}
    cand_index = {p: j for j, p in enumerate(cand_pids)}
    R = np.zeros((len(users), len(cand_pids)), dtype=np.float32)
    for n, (u, i) in enumerate(zip(train["u"], train["i"])):
        r = user_index.get(train["users"][u])
        col = cand_index.get(train["items"][i])
        if r is not None and col is not None:
            R[r, col] = value[n]
    return R


def compare_external(sub_pids, users):
    out = {}
    for name, path in EXTERNAL:
        if not os.path.exists(path):
            out[name] = {"exists": False}
            continue
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader)
            rows = dict((r[0], r[1].split()) for r in reader)
        overlaps = []
        top1 = 0
        identical = 0
        for u, ids in zip(users, sub_pids):
            other = rows.get(u, [])
            overlaps.append(len(set(ids) & set(other)))
            if ids and other and ids[0] == other[0]:
                top1 += 1
            if ids == other:
                identical += 1
        out[name] = {
            "exists": True,
            "mean_overlap_at_5": round(sum(overlaps) / len(overlaps), 4),
            "top1_agreement": round(top1 / len(users), 4),
            "identical_rows": identical,
            "sha256": sha256(path),
        }
    return out


def main():
    t0 = time.time()
    os.makedirs(STAGE3_OUT, exist_ok=True)
    selected = json.load(open(SELECTED, encoding="utf-8"))
    method = selected["selected"]["method"]
    params = selected["selected"]["params"]
    if method != "hybrid":
        raise RuntimeError("unexpected selected method: %s" % method)
    components = params["components"]
    weights = params["weights"]
    rrf_k = params.get("rrf_k", 60)

    users = load_template_users()
    train = c.load_interactions(c.MAIN_TRAIN)
    cand_pids = c.load_candidate_pids(3)
    # Item features use every schedule export that is available at prediction
    # time: past exports supply metadata for training items, the weeks 31-42
    # export supplies metadata for the candidate items.  This is schedule
    # content only, never viewing behaviour.
    meta_all = c.load_metadata([1, 2, 3])
    fallback = c.fallback_order(train, cand_pids)

    comp_lists = []
    comp_names = []
    if "repeat_recency" in components:
        R = repeat_matrix(train, users, cand_pids,
                          params["repeat_tau_days"], params["repeat_alpha"])
        comp_lists.append(c.topl_indices(R, 50))
        comp_names.append("repeat_recency")
        del R
    if "popularity" in components:
        variant = selected["best_by_method"]["popularity"]["params"]["variant"]
        _scores, order = c.popularity_vector(train, cand_pids, variant)
        comp_lists.append(np.tile(order[:50], (len(users), 1)))
        comp_names.append("popularity")
    if "content_based" in components:
        cbf_params = selected["best_by_method"]["content_based"]["params"]
        features = c.build_feature_matrices(
            train["items"], cand_pids, meta_all,
            cbf_params["feature_set"])
        profile = c.build_profile(train, users, cbf_params["profile"])
        scores = c.cbf_scores(profile, features, cbf_params["neighbor_k"])
        comp_lists.append(c.topl_indices(scores, 50))
        comp_names.append("content_based")
        del scores, profile, features
    if "association_rules" in components:
        ar_params = selected["best_by_method"]["association_rules"]["params"]
        train_binary = c.build_user_item(train, "binary")
        profile = c.build_profile(train, users, "binary")
        scores, _stats = c.association_scores(
            train_binary, train["items"], cand_pids,
            metric=ar_params["metric"], top_k=ar_params["top_k"],
            min_support=ar_params["min_support"],
            min_pair=ar_params["min_pair_support"], profile=profile)
        comp_lists.append(c.topl_indices(scores, 50))
        comp_names.append("association_rules")
        del scores, profile, train_binary

    w = [weights[name] for name in comp_names]
    aug = c.rrf_scores(comp_lists, w, len(users), len(cand_pids), rrf_k)
    top = c.topk_indices(aug, 5, fallback)
    sub_pids = [[cand_pids[j] for j in row] for row in top]

    # ------------------------------------------------------------------
    # Write submission
    # ------------------------------------------------------------------
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["user_id", "tv_show_id"])
        for user, ids in zip(users, sub_pids):
            writer.writerow([user, " ".join(ids)])

    # ------------------------------------------------------------------
    # Structural checks
    # ------------------------------------------------------------------
    with open(OUT, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    ids = [r[0] for r in rows]
    values = [x for r in rows for x in r[1].split()]
    hist_users = set(train["users"])
    cand_set = set(cand_pids)
    checks = {
        "header_exact": header == ["user_id", "tv_show_id"],
        "row_count_1260": len(rows) == 1260,
        "unique_user_ids": len(set(ids)) == 1260,
        "template_order_preserved": ids == users,
        "five_ids_per_row": all(len(r[1].split()) == 5 for r in rows),
        "no_duplicate_ids_within_row": all(
            len(set(r[1].split())) == 5 for r in rows),
        "no_zero_id": "0" not in values,
        "all_ids_numeric": all(x.isdigit() for x in values),
        "all_ids_in_test_universe": all(x in cand_set for x in values),
    }
    if not all(checks.values()):
        raise RuntimeError("submission checks failed: %r" % checks)

    # ------------------------------------------------------------------
    # Example recommendations with titles
    # ------------------------------------------------------------------
    train_hist = {}
    for u, i, ss in zip(train["u"], train["i"], train["sessions"]):
        train_hist.setdefault(train["users"][u], {})[train["items"][i]] = int(ss)
    warm = next(i for i, u in enumerate(users) if u in hist_users)
    cold = next(i for i, u in enumerate(users) if u not in hist_users)
    example_idx = [warm, cold]
    with open(EXAMPLES, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["user_id", "rank", "tv_show_id", "title",
                         "source", "training_sessions_for_item"])
        for r in example_idx:
            user = users[r]
            hist = train_hist.get(user, {})
            for rank, pid in enumerate(sub_pids[r], 1):
                if pid in hist:
                    source = "repeat"
                elif not hist:
                    source = "fallback_popularity"
                else:
                    source = "model"
                writer.writerow([user, rank, pid,
                                 meta_all.get(pid, {}).get("title", ""),
                                 source, hist.get(pid, "")])

    meta = {
        "generated_by": "src/build_final_submission.py",
        "status": "complete",
        "selected_model": selected["selected"],
        "components_used": comp_names,
        "component_weights": dict(zip(comp_names, w)),
        "rrf_k": rrf_k,
        "candidate_universe": {
            "period": "weeks 31-42",
            "labelled_programmes": len(cand_pids),
            "contains_zero": False,
        },
        "metadata_sources": {
            "item_features": "schedule exports weeks 11-20, 21-30 and 31-42",
            "note": "schedule content only; no viewing behaviour from any period",
        },
        "users": {
            "total": len(users),
            "with_training_history": sum(1 for u in users if u in hist_users),
            "without_training_history": sum(1 for u in users
                                            if u not in hist_users),
        },
        "fallback_policy": "global training popularity by distinct users, "
                           "restricted to the test candidate universe",
        "repeats_allowed": True,
        "checks": checks,
        "submission": {
            "path": os.path.relpath(OUT, ROOT),
            "rows": len(rows),
            "distinct_programme_ids": len(set(values)),
            "distinct_recommendation_lists": len(set(r[1] for r in rows)),
            "sha256": sha256(OUT),
        },
        "inputs_sha256": {
            "train_pairs": sha256(c.MAIN_TRAIN),
            "submission_template": sha256(TEMPLATE),
        },
        "comparison_with_existing": compare_external(sub_pids, users),
        "runtime_seconds": round(time.time() - t0, 2),
    }
    with open(META_OUT, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print("[submit] wrote", OUT)
    print("[submit] rows=%d distinct_ids=%d distinct_lists=%d sha256=%s"
          % (len(rows), meta["submission"]["distinct_programme_ids"],
             meta["submission"]["distinct_recommendation_lists"],
             meta["submission"]["sha256"][:16]))
    print("[submit] users with training history=%d without=%d"
          % (meta["users"]["with_training_history"],
             meta["users"]["without_training_history"]))
    print("[submit] checks:", all(checks.values()))
    for name, rec in meta["comparison_with_existing"].items():
        if rec.get("exists"):
            print("[submit] overlap with %s: mean %.2f/5, top1 %.3f"
                  % (name, rec["mean_overlap_at_5"], rec["top1_agreement"]))
    print("[submit] wrote", META_OUT)
    print("[submit] wrote", EXAMPLES)


if __name__ == "__main__":
    main()
