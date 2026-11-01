"""Stage 3 validation experiments: popularity, CBF, association rules and hybrids.

Protocol
--------
- Fit / estimate everything on training weeks 11-20 (strategy A, rule B).
- Score all users with at least one validation interaction (weeks 21-30).
- Candidate universe: labelled programmes in the weeks 21-30 schedule.
- Repeat recommendations allowed for every method.
- Fallback for cold / sparse users: global training popularity by distinct
  users, restricted to the candidate universe.
- Primary metric: MAP@5.

    python3 src/evaluate_stage3_models.py
      -> reports/stage3_experiment_results.json
      -> reports/stage3_model_comparison.csv
      -> reports/stage3_model_comparison.md
      -> outputs/stage3/selected_model.json
"""

import csv
import gc
import hashlib
import json
import os
import resource
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage3_common as c

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
STAGE3_OUT = os.path.join(ROOT, "outputs", "stage3")
OUT_JSON = os.path.join(REPORTS, "stage3_experiment_results.json")
OUT_CSV = os.path.join(REPORTS, "stage3_model_comparison.csv")
OUT_MD = os.path.join(REPORTS, "stage3_model_comparison.md")
SELECTED = os.path.join(STAGE3_OUT, "selected_model.json")

TRAIN_BOUNDARY = c.s.parse_view_timestamp("2020-05-18 00:00:00")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(results, method, config, params, top5, gt, users, cand_pids,
           runtime, extra=None):
    m = c.evaluate_map5(top5, gt, users, cand_pids)
    rec = {
        "method": method,
        "config": config,
        "params": params,
        "map5": round(m["map5"], 6),
        "recall5": round(m["recall5"], 6),
        "hit_rate": round(m["hit_rate"], 6),
        "users_with_hit": m["users_with_hit"],
        "users": m["users"],
        "catalog_coverage": round(m["catalog_coverage"], 6),
        "runtime_seconds": round(runtime, 3),
        "random_seed": None,
        "deterministic": True,
    }
    if extra:
        rec.update(extra)
    results.append(rec)
    print("[eval] %-22s %-38s MAP@5 %.6f  (%.1fs)"
          % (method, config, rec["map5"], runtime), flush=True)
    return rec


def build_repeat_matrix(train, users, cand_pids, tau_days=90.0, alpha=1.0):
    """Repeat-consumption score: log sessions * exp(-days since last view / tau)."""
    n_users = len(users)
    n_cand = len(cand_pids)
    cand_index = {p: j for j, p in enumerate(cand_pids)}
    user_index = {u: i for i, u in enumerate(users)}
    days = np.clip((TRAIN_BOUNDARY - train["last"]) / 86400.0, 0.0, None)
    score = (np.log1p(train["sessions"].astype(np.float64)) ** alpha) \
        * np.exp(-days / tau_days)
    R = np.zeros((n_users, n_cand), dtype=np.float32)
    for n, (u, i) in enumerate(zip(train["u"], train["i"])):
        r = user_index.get(train["users"][u])
        col = cand_index.get(train["items"][i])
        if r is not None and col is not None:
            R[r, col] = score[n]
    return R


def main():
    t_start = time.time()
    os.makedirs(STAGE3_OUT, exist_ok=True)

    print("[data] loading interactions", flush=True)
    train = c.load_interactions(c.MAIN_TRAIN)
    validation = c.load_interactions(c.MAIN_VALIDATION)
    users = validation["users"]
    cand_pids = c.load_candidate_pids(2)
    meta = c.load_metadata([1, 2])
    gt, missing = c.ground_truth(validation, cand_pids)
    if missing:
        raise RuntimeError("%d validation interactions outside candidate "
                           "universe" % missing)
    fallback = c.fallback_order(train, cand_pids)
    print("[data] train %d pairs / %d users / %d items; validation %d users; "
          "candidates %d"
          % (train["n_pairs"], train["n_users"], train["n_items"],
             len(users), len(cand_pids)), flush=True)

    binary_profile = c.build_profile(train, users, "binary")
    log_profile = c.build_profile(train, users, "log_sessions")
    train_binary = c.build_user_item(train, "binary")

    results = []
    best = {}

    # ------------------------------------------------------------------
    # Popularity baseline
    # ------------------------------------------------------------------
    pop_top = {}
    for variant in ("users", "sessions", "events"):
        t0 = time.time()
        scores, order = c.popularity_vector(train, cand_pids, variant)
        top5 = np.tile(order[:5], (len(users), 1))
        rec = record(results, "popularity", "popularity_%s" % variant,
                     {"variant": variant}, top5, gt, users, cand_pids,
                     time.time() - t0)
        pop_top[variant] = np.tile(order[:50], (len(users), 1))
        best.setdefault("popularity", rec)
        if rec["map5"] > best["popularity"]["map5"]:
            best["popularity"] = rec
    best_pop_variant = best["popularity"]["params"]["variant"]
    pop_component = pop_top[best_pop_variant]

    # ------------------------------------------------------------------
    # Content-based filtering
    # ------------------------------------------------------------------
    cbf_best = None
    cbf_component = None
    for feature_set in ("genre", "all"):
        features = c.build_feature_matrices(
            train["items"], cand_pids, meta, feature_set)
        for profile_name, profile in (("binary", binary_profile),
                                      ("log_sessions", log_profile)):
            for k in (None, 50, 200):
                t0 = time.time()
                scores = c.cbf_scores(profile, features, k)
                top_l = c.topl_indices(scores, 50)
                top5 = c.finalize_top5(top_l, fallback)
                rec = record(
                    results, "content_based",
                    "cbf_%s_%s_k%s" % (feature_set, profile_name,
                                       "all" if k is None else k),
                    {"feature_set": feature_set, "profile": profile_name,
                     "neighbor_k": k, "max_features": features["n_features"]},
                    top5, gt, users, cand_pids, time.time() - t0)
                if cbf_best is None or rec["map5"] > cbf_best["map5"]:
                    cbf_best = rec
                    cbf_component = top_l
                del scores, top_l, top5
                gc.collect()
        del features
        gc.collect()
    best["content_based"] = cbf_best

    # ------------------------------------------------------------------
    # Association rules
    # ------------------------------------------------------------------
    ar_best = None
    ar_component = None
    ar_grid = [
        ("confidence", 50, 3, 3),
        ("confidence", 100, 3, 3),
        ("confidence", 50, 5, 5),
        ("lift", 50, 3, 3),
        ("lift", 100, 3, 3),
        ("lift", 50, 5, 5),
    ]
    for metric, k, min_support, min_pair in ar_grid:
        t0 = time.time()
        scores, stats = c.association_scores(
            train_binary, train["items"], cand_pids, metric=metric,
            top_k=k, min_support=min_support, min_pair=min_pair,
            profile=binary_profile)
        top_l = c.topl_indices(scores, 50)
        top5 = c.finalize_top5(top_l, fallback)
        rec = record(
            results, "association_rules",
            "ar_%s_k%d_sup%d_pair%d" % (metric, k, min_support, min_pair),
            {"metric": metric, "top_k": k, "min_support": min_support,
             "min_pair_support": min_pair, "n_rules": stats["n_rules"],
             "avg_rules_per_item": round(stats["avg_rules_per_item"], 2)},
            top5, gt, users, cand_pids, time.time() - t0)
        if ar_best is None or rec["map5"] > ar_best["map5"]:
            ar_best = rec
            ar_component = top_l
        del scores, top_l, top5
        gc.collect()
    best["association_rules"] = ar_best

    # ------------------------------------------------------------------
    # Repeat-recency baseline and rank-fusion hybrids
    # ------------------------------------------------------------------
    repeat_best = None
    repeat_components = {}
    for tau in (60.0, 90.0, 120.0):
        for alpha in (1.0, 1.5):
            t0 = time.time()
            R = build_repeat_matrix(train, users, cand_pids, tau, alpha)
            top_l = c.topl_indices(R, 50)
            top5 = c.finalize_top5(top_l, fallback)
            rec = record(
                results, "repeat_recency",
                "repeat_tau%d_alpha%s" % (int(tau), alpha),
                {"tau_days": tau, "alpha": alpha, "score":
                 "log1p(n_sessions)^alpha * exp(-days_since_last_view/tau)"},
                top5, gt, users, cand_pids, time.time() - t0)
            repeat_components[(tau, alpha)] = top_l
            if repeat_best is None or rec["map5"] > repeat_best["map5"]:
                repeat_best = rec
            del R, top_l, top5
            gc.collect()
    best["repeat_recency"] = repeat_best
    best_repeat_key = (repeat_best["params"]["tau_days"],
                       repeat_best["params"]["alpha"])
    repeat_component = repeat_components[best_repeat_key]

    hybrid_best = None
    hybrid_results = []
    # Repeat + popularity at three prior strengths for each repeat variant.
    for key, top_l in repeat_components.items():
        for wp in (0.1, 0.2, 0.3):
            t0 = time.time()
            aug = c.rrf_scores([top_l, pop_component], [1.0, wp],
                               len(users), len(cand_pids))
            top5 = c.topk_indices(aug, 5, fallback)
            rec = record(
                results, "hybrid",
                "hybrid_rep_tau%d_a%s_pop%.1f"
                % (int(key[0]), key[1], wp),
                {"components": ["repeat_recency", "popularity"],
                 "repeat_tau_days": key[0], "repeat_alpha": key[1],
                 "weights": {"repeat_recency": 1.0, "popularity": wp},
                 "rrf_k": 60},
                top5, gt, users, cand_pids, time.time() - t0)
            hybrid_results.append((rec, ["repeat_recency", "popularity"],
                                   [1.0, wp], key))
            del aug, top5
            gc.collect()

    # Add the learned methods to the fusion, with small weights.
    extra_grid = [
        (["repeat_recency", "popularity", "association_rules"],
         [1.0, 0.2, 0.05], (repeat_component, pop_component, ar_component)),
        (["repeat_recency", "popularity", "association_rules"],
         [1.0, 0.2, 0.1], (repeat_component, pop_component, ar_component)),
        (["repeat_recency", "popularity", "content_based"],
         [1.0, 0.2, 0.05], (repeat_component, pop_component, cbf_component)),
        (["repeat_recency", "popularity", "content_based"],
         [1.0, 0.2, 0.1], (repeat_component, pop_component, cbf_component)),
        (["repeat_recency", "popularity", "association_rules", "content_based"],
         [1.0, 0.2, 0.05, 0.05],
         (repeat_component, pop_component, ar_component, cbf_component)),
    ]
    for names, weights, comps in extra_grid:
        t0 = time.time()
        aug = c.rrf_scores(list(comps), weights, len(users), len(cand_pids))
        top5 = c.topk_indices(aug, 5, fallback)
        cfg = "hybrid_" + "_".join(
            "%.2f" % w for w in weights) + "_" + "+".join(names)
        rec = record(results, "hybrid", cfg,
                     {"components": names,
                      "weights": dict(zip(names, weights)), "rrf_k": 60,
                      "repeat_tau_days": best_repeat_key[0],
                      "repeat_alpha": best_repeat_key[1]},
                     top5, gt, users, cand_pids, time.time() - t0)
        hybrid_results.append((rec, names, weights, best_repeat_key))
        del aug, top5
        gc.collect()

    for rec, names, weights, key in hybrid_results:
        if hybrid_best is None or rec["map5"] > hybrid_best["map5"]:
            hybrid_best = rec
    best["hybrid"] = hybrid_best

    # ------------------------------------------------------------------
    # Selection and outputs
    # ------------------------------------------------------------------
    selected = max(results, key=lambda r: (r["map5"], -r["runtime_seconds"]))
    if selected["method"] == "hybrid":
        selected_params = dict(selected["params"])
    else:
        selected_params = dict(selected["params"])

    summary = {
        "generated_by": "src/evaluate_stage3_models.py",
        "status": "complete",
        "protocol": {
            "train": c.MAIN_TRAIN,
            "validation": c.MAIN_VALIDATION,
            "candidate_universe": "weeks 21-30 labelled programmes",
            "n_candidates": len(cand_pids),
            "evaluation_users": len(users),
            "metric": "MAP@5 (denominator min(|ground truth|, 5))",
            "repeats_allowed": True,
            "fallback": "training popularity by distinct users",
            "ground_truth_items_outside_universe": missing,
            "validation_used_for_training": False,
        },
        "dataset": {
            "train_pairs": train["n_pairs"],
            "train_users": train["n_users"],
            "train_items": train["n_items"],
            "validation_pairs": validation["n_pairs"],
            "validation_users": validation["n_users"],
            "validation_items": validation["n_items"],
            "validation_users_without_training_history":
                len(set(users) - set(train["users"])),
            "candidate_pids": len(cand_pids),
        },
        "grid": {
            "popularity": ["users", "sessions", "events"],
            "content_based": "feature_set {genre,all} x profile {binary,log_sessions} "
                             "x neighbor_k {all,50,200}",
            "association_rules": "metric {confidence,lift} x top_k {50,100} x "
                                 "support {3,5}",
            "repeat_recency": "tau {60,90,120} x alpha {1.0,1.5}",
            "hybrid": "RRF over repeat/popularity plus optional AR/CBF components",
        },
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "platform": sys.platform,
            "gpu": "none",
        },
        "inputs_sha256": {
            "train_pairs": sha256(c.MAIN_TRAIN),
            "validation_pairs": sha256(c.MAIN_VALIDATION),
        },
        "best_by_method": best,
        "selected": {
            "method": selected["method"],
            "config": selected["config"],
            "params": selected_params,
            "validation_map5": selected["map5"],
            "validation_recall5": selected["recall5"],
        },
        "results": results,
        "runtime_seconds": round(time.time() - t_start, 2),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                             / 1048576, 1),
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    fields = ["method", "config", "map5", "recall5", "hit_rate",
              "catalog_coverage", "runtime_seconds", "users",
              "users_with_hit", "params"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = dict(r)
            row["params"] = json.dumps(r["params"], ensure_ascii=False,
                                       sort_keys=True)
            w.writerow(row)

    # Human-readable summary: best configuration of each method.
    lines = []
    w = lines.append
    w("# Stage 3 — Validation Model Comparison")
    w("")
    w("Primary metric: **MAP@5** on 4,774 validation users (weeks 21–30). "
      "Models use training weeks 11–20 only.")
    w("")
    w("| Method | Best configuration | MAP@5 | Recall@5 | Hit rate | Coverage | Runtime (s) |")
    w("|---|---|---:|---:|---:|---:|---:|")
    order = ["popularity", "content_based", "association_rules",
             "repeat_recency", "hybrid"]
    for method in order:
        r = best[method]
        w("| %s | `%s` | %.6f | %.4f | %.4f | %.4f | %.2f |" % (
            method, r["config"], r["map5"], r["recall5"], r["hit_rate"],
            r["catalog_coverage"], r["runtime_seconds"]))
    w("")
    w("**Selected for the final submission:** `%s` / `%s`, validation "
      "MAP@5 **%.6f**." % (selected["method"], selected["config"],
                           selected["map5"]))
    w("")
    w("All repeat recommendations are allowed. Users without training history "
      "or with fewer than five scored candidates receive the same global "
      "training-popularity fallback. Validation interactions are used only for "
      "scoring and model selection.")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(SELECTED, "w", encoding="utf-8") as f:
        json.dump({
            "generated_by": "src/evaluate_stage3_models.py",
            "selected": summary["selected"],
            "best_by_method": best,
            "protocol": summary["protocol"],
            "dataset": summary["dataset"],
            "environment": summary["environment"],
            "inputs_sha256": summary["inputs_sha256"],
            "fallback": "training popularity by distinct users",
            "repeats_allowed": True,
        }, f, indent=2, ensure_ascii=False)

    print("[done] selected %s / %s MAP@5 %.6f in %.1fs (peak %.1f MB)"
          % (selected["method"], selected["config"], selected["map5"],
             summary["runtime_seconds"], summary["peak_rss_mb"]))
    print("[done] wrote", OUT_JSON)
    print("[done] wrote", OUT_CSV)
    print("[done] wrote", OUT_MD)
    print("[done] wrote", SELECTED)


if __name__ == "__main__":
    main()
