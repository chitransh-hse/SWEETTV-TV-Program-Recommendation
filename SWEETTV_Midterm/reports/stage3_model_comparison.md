# Stage 3 — Validation Model Comparison

Primary metric: **MAP@5** on 4,774 validation users (weeks 21–30). Models use training weeks 11–20 only.

| Method | Best configuration | MAP@5 | Recall@5 | Hit rate | Coverage | Runtime (s) |
|---|---|---:|---:|---:|---:|---:|
| popularity | `popularity_users` | 0.572049 | 0.0470 | 0.9026 | 0.0008 | 0.07 |
| content_based | `cbf_genre_log_sessions_k50` | 0.197621 | 0.0224 | 0.6973 | 0.1539 | 0.46 |
| association_rules | `ar_confidence_k50_sup5_pair5` | 0.570997 | 0.0469 | 0.9028 | 0.0016 | 0.35 |
| repeat_recency | `repeat_tau120_alpha1.0` | 0.622277 | 0.0505 | 0.9210 | 0.1421 | 0.18 |
| hybrid | `hybrid_1.00_0.20_0.05_repeat_recency+popularity+content_based` | 0.634319 | 0.0514 | 0.9286 | 0.1025 | 0.12 |

**Selected for the final submission:** `hybrid` / `hybrid_1.00_0.20_0.05_repeat_recency+popularity+content_based`, validation MAP@5 **0.634319**.

All repeat recommendations are allowed. Users without training history or with fewer than five scored candidates receive the same global training-popularity fallback. Validation interactions are used only for scoring and model selection.