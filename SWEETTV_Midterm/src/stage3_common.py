"""Shared primitives for Stage 3 recommendation modelling.

Everything here consumes only the corrected Stage 2.5 temporal datasets and the
schedule exports (metadata / candidate universe).  Validation interactions are
never used to fit a model; they are used only for MAP@5 scoring and model
selection.

Design notes
------------
- Python 3.9 + NumPy only (SciPy/scikit-learn are not installed).
- All IDs remain strings at the API boundary; dense matrices use int64 indices.
- Candidate universes are the labelled programmes of the target schedule
  export (`tv_show_id != "0"`).
- Repeat recommendations are allowed for every method.
- The fallback ranking is training popularity by distinct users, restricted to
  the candidate universe; it is identical for every method.
"""

import csv
import gzip
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage2_common as s

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TEMP = os.path.join(ROOT, "outputs", "temporal_interactions")

MAIN_TRAIN = os.path.join(TEMP, "train_strategyA_ruleB_60s_20pct.csv.gz")
MAIN_VALIDATION = os.path.join(TEMP, "validation_strategyA_ruleB_60s_20pct.csv.gz")

PLACEHOLDERS = {"", "0", "none", "nan", "null", "n/a", "na"}


# ---------------------------------------------------------------------------
# Interaction loading
# ---------------------------------------------------------------------------

def load_interactions(path):
    """Load a pair-level dataset into sorted dense-index arrays.

    Returns a dict with sorted string ID lists, index maps and aligned NumPy
    arrays for sessions, overlap seconds and overlap-event counts.
    """
    raw = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if header[:2] != ["user_id", "tv_show_id"]:
            raise ValueError("unexpected header in %s" % path)
        col = {name: i for i, name in enumerate(header)}
        for row in reader:
            key = (row[col["user_id"]], row[col["tv_show_id"]])
            raw[key] = (
                float(row[col["n_sessions"]]),
                float(row[col["total_overlap_seconds"]]),
                float(row[col["n_overlap_events"]]),
                s.parse_view_timestamp(row[col["first_view_start"]]),
                s.parse_view_timestamp(row[col["last_view_start"]]),
            )
    users = sorted({k[0] for k in raw})
    items = sorted({k[1] for k in raw})
    uidx = {u: i for i, u in enumerate(users)}
    iidx = {p: i for i, p in enumerate(items)}
    n = len(raw)
    u = np.empty(n, dtype=np.int64)
    i = np.empty(n, dtype=np.int64)
    sess = np.empty(n, dtype=np.float32)
    ov = np.empty(n, dtype=np.float32)
    ev = np.empty(n, dtype=np.float32)
    first = np.empty(n, dtype=np.int64)
    last = np.empty(n, dtype=np.int64)
    for n_, (key, (a, b, c, fs, ls)) in enumerate(raw.items()):
        u[n_] = uidx[key[0]]
        i[n_] = iidx[key[1]]
        sess[n_] = a
        ov[n_] = b
        ev[n_] = c
        first[n_] = fs
        last[n_] = ls
    return {
        "users": users,
        "items": items,
        "user_index": uidx,
        "item_index": iidx,
        "u": u,
        "i": i,
        "sessions": sess,
        "overlap": ov,
        "events": ev,
        "first": first,
        "last": last,
        "n_pairs": n,
        "n_users": len(users),
        "n_items": len(items),
    }


def build_profile(inter, row_users, value="binary"):
    """User x training-item matrix for an explicit ordered list of users."""
    row_index = {u: r for r, u in enumerate(row_users)}
    X = np.zeros((len(row_users), inter["n_items"]), dtype=np.float32)
    if value == "binary":
        vals = np.ones(inter["n_pairs"], dtype=np.float32)
    elif value == "log_sessions":
        vals = np.log1p(inter["sessions"]).astype(np.float32)
    elif value == "log_overlap":
        vals = np.log1p(inter["overlap"]).astype(np.float32)
    else:
        raise ValueError("unknown value type: %s" % value)
    for n, (u, i) in enumerate(zip(inter["u"], inter["i"])):
        r = row_index.get(inter["users"][u])
        if r is not None:
            X[r, i] = vals[n]
    return X


def build_user_item(inter, value="binary"):
    """Dense user x item matrix (float32)."""
    X = np.zeros((inter["n_users"], inter["n_items"]), dtype=np.float32)
    if value == "binary":
        vals = np.ones(inter["n_pairs"], dtype=np.float32)
    elif value == "log_sessions":
        vals = np.log1p(inter["sessions"]).astype(np.float32)
    elif value == "log_overlap":
        vals = np.log1p(inter["overlap"]).astype(np.float32)
    else:
        raise ValueError("unknown value type: %s" % value)
    X[inter["u"], inter["i"]] = vals
    return X


# ---------------------------------------------------------------------------
# Candidate universe and metadata
# ---------------------------------------------------------------------------

def load_candidate_pids(period):
    """Sorted labelled programme IDs in a schedule export.

    IDs are kept exactly as stored (some have leading zeros) because Stage 2
    preserved them as strings and the submission must match them verbatim.
    """
    pids = set()
    for _ch, _start, _dur, pid, _per, _n in s.iter_schedule(period):
        if pid != "0":
            if not pid.isdigit():
                raise ValueError("non-numeric programme ID: %r" % pid)
            pids.add(pid)
    return sorted(pids)


def _norm(text):
    if text is None:
        return ""
    text = " ".join(text.strip().lower().split())
    return "" if text in PLACEHOLDERS else text


def _add(field_set, text, split=False):
    text = _norm(text)
    if not text:
        return
    if split:
        for part in text.replace(";", ",").split(","):
            part = _norm(part)
            if part and part not in PLACEHOLDERS:
                field_set.add(part)
    else:
        field_set.add(text)


def load_metadata(periods):
    """Aggregate schedule metadata per programme across the given exports.

    Uses only schedule content (no viewing behaviour).  Repeated airings of the
    same programme contribute the union of genres / actors / directors, which
    is more stable than picking one episode row.
    """
    meta = {}
    for period in periods:
        path = os.path.join(
            DATA, next(name for per, _label, name in s.SCHEDULE_FILES
                       if per == period))
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row["tv_show_id"]
                if pid == "0":
                    continue
                m = meta.get(pid)
                if m is None:
                    m = meta[pid] = {
                        "titles": [],
                        "title_counts": {},
                        "categories": set(),
                        "genres": set(),
                        "years": set(),
                        "directors": set(),
                        "actors": set(),
                    }
                title = row["tv_show_title"].strip()
                if title:
                    m["title_counts"][title] = m["title_counts"].get(title, 0) + 1
                    if title not in m["titles"] and len(m["titles"]) < 3:
                        m["titles"].append(title)
                _add(m["categories"], row["tv_show_category"])
                for j in (1, 2, 3):
                    _add(m["genres"], row["tv_show_genre_%d" % j])
                _add(m["years"], row["year_of_production"])
                _add(m["directors"], row["director"])
                _add(m["actors"], row["actors"], split=True)
    for m in meta.values():
        # Titles vary by episode, so use the most frequently aired variant.
        counts = m["title_counts"]
        if counts:
            m["title"] = max(counts.items(),
                             key=lambda kv: (kv[1], -len(kv[0]), kv[0]))[0]
        else:
            m["title"] = ""
    return meta


def _metadata_tokens(m, feature_set):
    tokens = set()
    for cat in m["categories"]:
        tokens.add(("c:" + cat, 1.0))
    for g in m["genres"]:
        tokens.add(("g:" + g, 1.2))
    for y in m["years"]:
        if y.isdigit():
            tokens.add(("y:" + y, 0.4))
            tokens.add(("d:" + y[:3] + "0", 0.3))
    if feature_set == "all":
        for d in m["directors"]:
            tokens.add(("dir:" + d, 0.6))
        for a in m["actors"]:
            tokens.add(("act:" + a, 0.4))
    elif feature_set != "genre":
        raise ValueError("unknown feature set: %s" % feature_set)
    return tokens


def build_feature_matrices(train_pids, cand_pids, meta, feature_set="all",
                           max_features=4000, min_df=2):
    """TF-IDF metadata matrices for training items and candidate items.

    The vocabulary and IDF weights are estimated from the union of the two
    programme sets in the same period pair (schedule content only).
    """
    all_pids = list(dict.fromkeys(list(train_pids) + list(cand_pids)))
    tok = {pid: _metadata_tokens(meta.get(pid, {
        "categories": set(), "genres": set(), "years": set(),
        "directors": set(), "actors": set()}), feature_set)
        for pid in all_pids}
    df = {}
    for pid in all_pids:
        for token, _w in tok[pid]:
            df[token] = df.get(token, 0) + 1
    forced = lambda t: t.startswith(("c:", "g:", "y:"))
    kept = [t for t, c in df.items() if c >= min_df or forced(t)]
    if len(kept) > max_features:
        # Keep all category/genre/year tokens, then the most frequent others.
        forced_tokens = [t for t in kept if forced(t)]
        optional = sorted((t for t in kept if not forced(t)),
                          key=lambda t: (-df[t], t))
        kept = forced_tokens + optional[:max(0, max_features - len(forced_tokens))]
    vocab = {t: k for k, t in enumerate(sorted(kept))}
    n_features = len(vocab)
    idf = np.empty(n_features, dtype=np.float32)
    for t, k in vocab.items():
        idf[k] = math.log((1.0 + len(all_pids)) / (1.0 + df[t])) + 1.0

    def matrix(pids):
        A = np.zeros((len(pids), n_features), dtype=np.float32)
        for row, pid in enumerate(pids):
            for token, weight in tok.get(pid, ()):
                k = vocab.get(token)
                if k is not None:
                    A[row, k] = weight * idf[k]
            norm = float(np.linalg.norm(A[row]))
            if norm > 0:
                A[row] /= norm
        return A

    A_train = matrix(train_pids)
    A_cand = matrix(cand_pids)
    return {
        "train": A_train,
        "cand": A_cand,
        "n_features": n_features,
        "feature_set": feature_set,
    }


# ---------------------------------------------------------------------------
# Popularity
# ---------------------------------------------------------------------------

def popularity_vector(inter, cand_pids, variant="users"):
    """Training popularity restricted to the candidate universe."""
    value = {}
    if variant == "users":
        seen = {}
        for u, i in zip(inter["u"], inter["i"]):
            seen.setdefault(i, set()).add(u)
        for i, us in seen.items():
            value[inter["items"][i]] = float(len(us))
    elif variant == "sessions":
        for i, v in zip(inter["i"], inter["sessions"]):
            value[inter["items"][i]] = value.get(inter["items"][i], 0.0) + float(v)
    elif variant == "events":
        for i, v in zip(inter["i"], inter["events"]):
            value[inter["items"][i]] = value.get(inter["items"][i], 0.0) + float(v)
    elif variant == "overlap":
        for i, v in zip(inter["i"], inter["overlap"]):
            value[inter["items"][i]] = value.get(inter["items"][i], 0.0) + float(v)
    else:
        raise ValueError("unknown popularity variant: %s" % variant)
    scores = np.array([value.get(p, 0.0) for p in cand_pids], dtype=np.float32)
    # Candidate list is pid-sorted, so lexsort on -score gives deterministic
    # pid-ascending tie-breaking.
    order = np.lexsort((np.arange(len(cand_pids)), -scores))
    return scores, order


def fallback_order(inter, cand_pids):
    _scores, order = popularity_vector(inter, cand_pids, "users")
    return order


# ---------------------------------------------------------------------------
# Content-based filtering
# ---------------------------------------------------------------------------

def cbf_scores(profile, features, neighbor_k=None):
    """Score candidates as profile x item-item cosine similarity.

    `profile` is a dense user x training-item matrix; `features` comes from
    build_feature_matrices.  If neighbor_k is set, each training item keeps
    only its K most similar candidate items.
    """
    sim = features["train"] @ features["cand"].T
    if neighbor_k and neighbor_k < sim.shape[1]:
        k = int(neighbor_k)
        idx = np.argpartition(-sim, k - 1, axis=1)[:, :k]
        mask = np.zeros(sim.shape, dtype=bool)
        mask[np.arange(sim.shape[0])[:, None], idx] = True
        sim = np.where(mask, sim, 0.0).astype(np.float32, copy=False)
    return profile @ sim


# ---------------------------------------------------------------------------
# Association rules
# ---------------------------------------------------------------------------

def association_scores(X_binary, train_items, cand_pids, metric="confidence",
                       top_k=50, min_support=3, min_pair=3, profile=None):
    """Item-to-item association rules from co-viewing counts.

    support(i, j) = users who watched both i and j
    confidence    = support(i, j) / support(i)
    lift          = confidence / support(j) * N

    For each antecedent the top-K rules are kept (self-rules excluded).  A
    user's candidate score is the sum of rule strengths fired by the user's
    training items.  `profile` optionally supplies the scoring matrix (for
    example validation users or session-weighted history); rules are always
    estimated from `X_binary`.
    """
    n_users = X_binary.shape[0]
    n_items = X_binary.shape[1]
    counts = X_binary.sum(axis=0)
    co = X_binary.T @ X_binary
    np.fill_diagonal(co, 0.0)
    if metric == "confidence":
        strength = co / np.maximum(counts[:, None], 1.0)
    elif metric == "lift":
        strength = co * float(n_users) / np.maximum(
            counts[:, None] * counts[None, :], 1.0)
    else:
        raise ValueError("unknown association metric: %s" % metric)
    strength[counts < min_support, :] = 0.0
    strength[:, counts < 1] = 0.0
    strength[co < min_pair] = 0.0

    k = min(int(top_k), n_items - 1)
    cand_index = {p: j for j, p in enumerate(cand_pids)}
    item_to_cand = np.array(
        [cand_index.get(p, -1) for p in train_items], dtype=np.int64)
    R = np.zeros((n_items, len(cand_pids)), dtype=np.float32)
    n_rules = 0
    for i in range(n_items):
        row = strength[i]
        if not np.any(row > 0):
            continue
        idx = np.argpartition(-row, k - 1)[:k]
        idx = idx[row[idx] > 0]
        cols = item_to_cand[idx]
        good = cols >= 0
        if np.any(good):
            R[i, cols[good]] = row[idx[good]]
            n_rules += int(good.sum())
    scores = (X_binary if profile is None else profile) @ R
    return scores, {"n_rules": n_rules, "n_items": int(n_items),
                    "avg_rules_per_item": n_rules / max(n_items, 1)}


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------

def _fallback_rank(order, n_cand):
    rank = np.empty(n_cand, dtype=np.int64)
    rank[order] = np.arange(n_cand)
    return rank


def topk_indices(scores, k, fallback):
    """Top-k candidate columns per user, filling with the fallback ranking.

    Positive scores rank first (score descending, popularity-rank ascending);
    remaining slots are filled from the fallback order without duplicates.
    """
    scores = np.asarray(scores)
    n_users, n_cand = scores.shape
    k = min(k, n_cand)
    frank = _fallback_rank(fallback, n_cand)
    out = np.empty((n_users, k), dtype=np.int64)
    for r in range(n_users):
        row = scores[r]
        top = np.argpartition(-row, k - 1)[:k]
        top = top[np.lexsort((frank[top], -row[top]))]
        chosen = [int(j) for j in top if row[j] > 0]
        if len(chosen) < k:
            have = set(chosen)
            for j in fallback:
                j = int(j)
                if j not in have:
                    chosen.append(j)
                    have.add(j)
                    if len(chosen) == k:
                        break
        out[r] = chosen
    return out


def topl_indices(scores, limit):
    """Top-L positive candidates per user, padded with -1 (no fallback)."""
    scores = np.asarray(scores)
    n_users, n_cand = scores.shape
    limit = min(limit, n_cand)
    out = np.full((n_users, limit), -1, dtype=np.int64)
    for r in range(n_users):
        row = scores[r]
        pos = np.flatnonzero(row > 0)
        if pos.size == 0:
            continue
        if pos.size > limit:
            part = np.argpartition(-row[pos], limit - 1)[:limit]
            pos = pos[part]
        pos = pos[np.argsort(-row[pos], kind="stable")]
        out[r, :pos.size] = pos[:limit]
    return out


def finalize_top5(top_lists, fallback):
    """Convert positive top-L lists into exactly-5 lists with fallback."""
    n_users, _ = top_lists.shape
    k = 5
    out = np.empty((n_users, k), dtype=np.int64)
    for r in range(n_users):
        chosen = [int(j) for j in top_lists[r] if j >= 0]
        if len(chosen) < k:
            have = set(chosen)
            for j in fallback:
                j = int(j)
                if j not in have:
                    chosen.append(j)
                    have.add(j)
                    if len(chosen) == k:
                        break
        out[r] = chosen[:k]
    return out


def rrf_scores(components, weights, n_users, n_cand, rrf_k=60):
    """Reciprocal-rank fusion over per-method top-L lists."""
    aug = np.zeros((n_users, n_cand), dtype=np.float32)
    users = np.arange(n_users)
    for top, weight in zip(components, weights):
        if weight <= 0:
            continue
        for rank in range(top.shape[1]):
            col = top[:, rank]
            good = col >= 0
            aug[users[good], col[good]] += weight / (rrf_k + rank + 1)
    return aug


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def ground_truth(inter, cand_pids):
    """Per-user validation programme sets, mapped to candidate columns."""
    cand_index = {p: j for j, p in enumerate(cand_pids)}
    gt = {}
    missing = 0
    for u, i in zip(inter["u"], inter["i"]):
        pid = inter["items"][i]
        col = cand_index.get(pid)
        if col is None:
            missing += 1
            continue
        gt.setdefault(inter["users"][u], set()).add(col)
    return gt, missing


def evaluate_map5(top5, gt, users, cand_pids):
    """MAP@5 plus secondary diagnostics.

    `top5` is an (n_users, 5) array of candidate columns aligned to `users`.
    Only users with at least one ground-truth item are scored.
    """
    ap_sum = 0.0
    hits_total = 0
    gt_total = 0
    hit_users = 0
    recommended = []
    n_eval = 0
    for r, user in enumerate(users):
        truth = gt.get(user)
        if not truth:
            continue
        n_eval += 1
        hits = 0
        ap = 0.0
        for rank, col in enumerate(top5[r], 1):
            if int(col) in truth:
                hits += 1
                ap += hits / float(rank)
        ap /= min(len(truth), 5)
        ap_sum += ap
        hits_total += hits
        gt_total += len(truth)
        if hits:
            hit_users += 1
        recommended.extend(int(c) for c in top5[r])
    coverage = len(set(recommended)) / float(len(cand_pids))
    return {
        "users": n_eval,
        "map5": ap_sum / n_eval,
        "recall5": hits_total / float(gt_total) if gt_total else 0.0,
        "users_with_hit": hit_users,
        "hit_rate": hit_users / float(n_eval),
        "catalog_coverage": coverage,
        "mean_recommendations_per_user": len(recommended) / float(n_eval),
    }
