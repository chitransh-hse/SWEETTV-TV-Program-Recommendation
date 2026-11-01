# Stage 2.5 — Temporal Separation and Interaction Dataset Preparation

**Status: complete, provisional. No threshold, strategy, model or recommendation was selected.**

This audit separates the Stage 2 interaction event store into training (schedule weeks 11–20) and validation (weeks 21–30) datasets, evaluates all 12 threshold × strategy combinations on both sides, and documents boundary, leakage and candidate-universe checks. Test viewing behaviour does not exist and was not used.

## 1. Exact temporal boundaries

The three schedule files are **broadcast-day exports**, not ISO-week slices. An airing belongs to the export that ships it. Membership is therefore taken from the source file (`period` column in the Stage 2 event store: 1 = weeks 11–20, 2 = weeks 21–30, 3 = weeks 31–42), never from ISO week arithmetic.

| Period | Source export | Rows | Labelled rows | Channels | First airing start | Last airing start | Last airing end |
|---|---|---:|---:|---:|---|---|---|
| training (weeks 11–20) | `weeks_11_20` | 1,074,235 | 115,613 | 400 | 2020-03-08 23:00:00 | 2020-05-18 11:45:00 | 2020-05-18 12:00:00 |
| validation (weeks 21–30) | `weeks_21_30` | 1,074,394 | 121,505 | 403 | 2020-05-18 00:00:00 | 2020-07-27 11:55:00 | 2020-07-27 12:00:00 |
| test (weeks 31–42) | `weeks_31_42` | 1,295,378 | 144,774 | 404 | 2020-07-27 00:00:00 | 2020-10-19 11:55:00 | 2020-10-19 12:00:00 |

Nominal broadcast-day boundaries: **2020-05-18 00:00:00** (training→validation) and **2020-07-27 00:00:00** (validation→test). They are reported for interpretation but are *not* used for membership, because the exports hand over per channel rather than at a single instant.

Inclusive/exclusive handling: a scheduled airing occupies `[start, start + duration)` — start inclusive, end exclusive. A viewing event requires a strictly positive overlap with that interval. Timestamps are naive local wall-clock; **offset applied = 0 s**.

Seam evidence (weeks 11–20 vs 21–30):

- 2,431 rows in the weeks 11–20 export start at or after 2020-05-18 00:00 (through 11:45), and 342 channels have a weeks 11–20 airing ending after midnight.
- 1,267 rows in the weeks 21–30 export start before 06:00 on 2020-05-18.
- Therefore no timestamp predicate can separate the two exports without losing or duplicating airings; the export label is the only unambiguous split.

| Handover | Shared channels | Exactly contiguous | With gap | With overlap | Only earlier | Only later |
|---|---:|---:|---:|---:|---|---|
| 1 → 2 | 399 | 398 | 0 | 1 | 823 | 763, 911, 1528, 1531 |
| 2 → 3 | 403 | 402 | 1 | 0 | — | 1541 |

The single cross-export interval overlap is channel 487: weeks 11–20 ends at 2020-05-18 01:00:00, weeks 21–30 starts at 2020-05-18 00:20:00 (2400 s overlap). The rows are distinct airings in distinct exports and both are unlabelled (`tv_show_id = 0`), so no interaction can be double-counted. Handover 2→3 has one 1800 s gap on channel 1528 and no overlaps.

No schedule row is assigned to two periods: each row exists in exactly one export, and the only cross-export interval overlap involves unlabelled rows.

## 2. Boundary sessions and event separation

The Stage 2 event store contains 3,847,696 labelled events across 2,964,346 sessions. A session can match airings from both exports near the seam.

**Deterministic rule.** A session that matches both exports is assigned to the period of its **earliest matched airing**, ordered by airing start (`pstart`) ascending and then period ascending as a deterministic tie-breaker. A session with matches in only one period keeps that period. Only events whose airing period equals the assigned period are retained. The other period's events from that session are dropped — the session is never counted in both splits, and is never silently duplicated. Neither viewing stop time nor total overlap seconds is used, so the amount of validation-period overlap cannot influence which training events are kept.

| Quantity | Value |
|---|---:|
| Sessions with labelled events | 2,964,346 |
| Assigned to training (weeks 11–20) | 1,898,828 |
| Assigned to validation (weeks 21–30) | 1,065,518 |
| Sessions matching both exports | 97 |
| — of which assigned to training | 97 |
| — of which assigned to validation | 0 |
| Events retained | 3,847,504 |
| Events excluded | 192 (0.005% of source events) |
| Retained overlap of boundary sessions | 356,468 s |
| Excluded overlap of boundary sessions | 405,856 s |
| Sessions present in both splits | 0 |

| Split | Sessions | Retained events | Retained overlap (s) | Excluded events |
|---|---:|---:|---:|---:|
| training weeks 11 20 | 1,898,828 | 2,384,996 | 2,456,164,878 | 192 |
| validation weeks 21 30 | 1,065,518 | 1,462,508 | 2,118,682,475 | 0 |

Each retained event is a complete `(session, airing)` match; only whole events move between splits. No event is split at its midpoint, and no session's events are spread across both outputs.

## 3. Boundary-rule correction: old vs corrected impact

The earlier implementation assigned a cross-period session to whichever export had the larger **total labelled overlap** (`SUM(ov) DESC`, ties to the earlier period). That decision consumed validation-period overlap, so validation information could change which training events were retained. The correction replaces it with the earliest-matched-airing rule above and removes that dependency; it is reconstructed from the read-only Stage 2 event store in `src/compare_boundary_assignment.py`.

| Quantity | Old (largest overlap) | Corrected (earliest airing) | Change |
|---|---:|---:|---:|
| Cross-period sessions | 97 | 97 | — |
| — assigned to training | 36 | 97 | +61 |
| — assigned to validation | 61 | 0 | -61 |
| Sessions reassigned | — | 61 | validation → training only |
| Events excluded | 109 | 192 | +83 |
| Events retained | 3,847,587 | 3,847,504 | -83 |
| Training events (retained) | 2,384,927 | 2,384,996 | +69 |
| Validation events (retained) | 1,462,660 | 1,462,508 | -152 |

Event movement between the two rules: **69** training events are newly included, **152** validation events are newly excluded, and **40** events are excluded under both rules. All 61 reassigned sessions moved from validation to training; none moved the other way.

Per-combination pair counts, old → corrected (all 12 combinations):

| Strategy | Rule | Train pairs old | Train pairs corrected | Δ | Validation pairs old | Validation pairs corrected | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| A | A | 449,118 | 449,135 | +17 | 390,558 | 390,530 | -28 |
| B | A | 383,967 | 383,983 | +16 | 287,561 | 287,538 | -23 |
| C | A | 379,682 | 379,701 | +19 | 283,965 | 283,940 | -25 |
| A | B | 335,801 | 335,816 | +15 | 312,960 | 312,934 | -26 |
| B | B | 288,289 | 288,303 | +14 | 224,579 | 224,560 | -19 |
| C | B | 284,242 | 284,259 | +17 | 221,057 | 221,036 | -21 |
| A | C | 335,606 | 335,621 | +15 | 312,879 | 312,853 | -26 |
| B | C | 288,080 | 288,094 | +14 | 224,492 | 224,473 | -19 |
| C | C | 284,048 | 284,065 | +17 | 220,977 | 220,956 | -21 |
| A | D | 177,837 | 177,846 | +9 | 197,423 | 197,399 | -24 |
| B | D | 153,685 | 153,695 | +10 | 137,665 | 137,645 | -20 |
| C | D | 151,303 | 151,315 | +12 | 135,361 | 135,339 | -22 |

**Do training aggregates differ?** Yes, for every one of the 12 training combinations: 175 training pairs are added and 0 removed, with no pair value changed except through the added rows. Validation changes are the mirror image: 274 pairs removed and 0 added. The sets confirm the expected monotonicity — corrected training is a strict superset of the old training set and corrected validation is a strict subset of the old validation set for every combination.

The correction is conservative: it can only move sessions toward training (the earlier export) on this dataset, so no training signal is lost, while the training/validation boundary no longer depends on validation overlap.

## 4. Aggregation and the 12 combinations

For each split independently, events are filtered by the rule, multiple matches in one session are resolved by the strategy, and events are aggregated by `(user_id, tv_show_id)`. IDs remain exact strings; `tv_show_id = 0` is never included. The validation split is built by the same code but is **held out**: it is not used for any profile, feature, popularity count or graph edge in this stage.

Thresholds: A = 60 s / 10%, B = 60 s / 20%, C = 120 s / 20%, D = 120 s / 50%. Strategies: A = keep all, B = maximum absolute overlap, C = maximum overlap ratio.

| Strategy | Rule | Min s | Min ratio | Train events | Train pairs | Train users | Train programmes | Validation events | Validation pairs | Validation users | Validation programmes | Total events | Stage 2 global | Δ excluded |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | A | 60 | 10% | 1,212,463 | 449,135 | 4,831 | 2,865 | 817,260 | 390,530 | 4,792 | 3,126 | 2,029,723 | 2,029,895 | 172 |
| A | B | 60 | 20% | 889,080 | 335,816 | 4,823 | 2,807 | 639,757 | 312,934 | 4,774 | 3,088 | 1,528,837 | 1,528,996 | 159 |
| A | C | 120 | 20% | 886,737 | 335,621 | 4,823 | 2,807 | 638,974 | 312,853 | 4,774 | 3,088 | 1,525,711 | 1,525,870 | 159 |
| A | D | 120 | 50% | 479,571 | 177,846 | 4,782 | 2,679 | 405,185 | 197,399 | 4,721 | 2,976 | 884,756 | 884,892 | 136 |
| B | A | 60 | 10% | 907,306 | 383,983 | 4,831 | 2,861 | 518,298 | 287,538 | 4,792 | 3,111 | 1,425,604 | 1,425,621 | 17 |
| B | B | 60 | 20% | 651,551 | 288,303 | 4,823 | 2,797 | 382,290 | 224,560 | 4,774 | 3,067 | 1,033,841 | 1,033,865 | 24 |
| B | C | 120 | 20% | 650,227 | 288,094 | 4,823 | 2,797 | 381,832 | 224,473 | 4,774 | 3,067 | 1,032,059 | 1,032,083 | 24 |
| B | D | 120 | 50% | 337,701 | 153,695 | 4,782 | 2,664 | 221,403 | 137,645 | 4,721 | 2,938 | 559,104 | 559,140 | 36 |
| C | A | 60 | 10% | 907,306 | 379,701 | 4,831 | 2,860 | 518,298 | 283,940 | 4,792 | 3,111 | 1,425,604 | 1,425,621 | 17 |
| C | B | 60 | 20% | 651,551 | 284,259 | 4,823 | 2,795 | 382,290 | 221,036 | 4,774 | 3,066 | 1,033,841 | 1,033,865 | 24 |
| C | C | 120 | 20% | 650,227 | 284,065 | 4,823 | 2,795 | 381,832 | 220,956 | 4,774 | 3,066 | 1,032,059 | 1,032,083 | 24 |
| C | D | 120 | 50% | 337,701 | 151,315 | 4,782 | 2,667 | 221,403 | 135,339 | 4,721 | 2,933 | 559,104 | 559,140 | 36 |

The totals reconcile with Stage 2: the difference is exactly the set of events excluded by the boundary-session rule that also pass that rule. With strategy A (keep all matches) that difference is 172 events for rule A, 159 for B/C and 136 for D. Strategies B/C resolve per session within a split, so their totals can differ by fewer events than the raw exclusion count.

Both splits have fewer users than the 4,914 viewing users because some users have no qualifying interaction under a given threshold; this is expected and no user is imputed. No threshold or strategy is preferred here.

## 5. Leakage and integrity verification

`src/verify_temporal_split.py` independently re-derives expected aggregates from the split tables and compares every row of all 24 files. It also re-hashes the Stage 2 event store and checks the candidate universe against the raw schedule exports.

**Result: 323/323 checks passed, 0 failed.**

| Verification group | Passed | Total |
|---|---:|---:|
| Candidate universe and cold start | 8 | 8 |
| Dataset integrity, ID preservation and leakage (24 files) | 276 | 276 |
| Event-store and aggregation integrity | 15 | 15 |
| Period purity, assignment and boundary sessions | 18 | 18 |
| Repeat-run determinism | 3 | 3 |
| Source immutability | 3 | 3 |

Specific guarantees, each backed by a measured check:

- Training events contain only weeks 11–20 airings; validation events only weeks 21–30; no weeks 31–42 airing is used.
- Every session is assigned to exactly one period; no session appears in both splits; every event's session agrees with its split.
- The earliest-matched-airing rule is checked independently: for every session the verifier recomputes argmin(`pstart`, `period`) from the source event store and confirms the assigned period and recorded `first_pstart` match it, and separately confirms the metadata documents this rule.
- The Stage 2 event store is unchanged (ordered SHA-256 `945863fae18d5b23`).…
- Every training aggregate row was recomputed from `events_train` alone and matched exactly, so no validation event contributes to a training aggregate. The same full-row recomputation was done for validation.
- No duplicate `(user_id, tv_show_id)` pair in any file; no `tv_show_id = 0`, empty or invalid programme ID; no duplicate `(session, programme, airing start)` boundary event; no overlap ≤ 0 or greater than programme duration.
- IDs are stored as TEXT and every file ID was found verbatim in the source event store / schedule; no float or signed-int64 conversion occurred.
- Repeat-run determinism: the split SQLite database, all 24 dataset content hashes and the stable metadata projection are identical across two full runs.

## 6. Candidate programme universe and cold start

Programme IDs were taken from the three schedule exports only. Test viewing behaviour does not exist and was not used. No programme was removed from the test universe.

| Quantity | Programme IDs |
|---|---:|
| Training schedule (weeks 11–20) | 5,847 |
| Validation schedule (weeks 21–30) | 6,363 |
| Test schedule (weeks 31–42) | 6,636 |
| Union of all three | 9,671 |
| Training-only (not validation or test) | 1,061 |
| Training-only vs validation | 1,871 |
| Shared training + validation | 3,976 |
| Validation-only (not training or test) | 1,258 |
| Validation-only vs training | 2,387 |
| Test-only (not training or validation) | 1,437 |
| Test absent from training | 2,566 |
| Test shared with training | 4,070 |
| Present in all three periods | 3,260 |

**Cold start:** 2,566 of 6,636 test programmes (38.668%) never appear in training. All 2,566 have usable metadata, so they can later be scored from content features; they are retained in the test universe.

Usable metadata is defined as a non-empty title plus at least one of category, genre 1–3, year, director or actors (the tokens `0`, empty, `None`, `nan`, `null` count as missing).

| Metadata field | All test programmes | Cold-start test programmes |
|---|---:|---:|
| tv_show_title | 6,636 | 2,566 |
| tv_show_category | 6,636 | 2,566 |
| tv_show_genre_1 | 6,486 | 2,492 |
| tv_show_genre_2 | 4,152 | 1,464 |
| tv_show_genre_3 | 1,643 | 525 |
| year_of_production | 6,627 | 2,563 |
| director | 6,487 | 2,537 |
| actors | 6,510 | 2,546 |

Per-programme evidence is in `reports/candidate_universe_audit.csv` (9,671 rows).

## 7. Runtime, memory and artifacts

| Step | Runtime | Peak RSS |
|---|---:|---:|
| Candidate-universe audit | 8.67 s | 25.6 MB |
| Temporal split + 24 datasets | 142.98 s | 481.0 MB |
| Verification | 126.78 s | — |

The split pipeline reuses the Stage 2 schedule index indirectly through the existing event store; it does not rerun interval matching. Only the Python standard library and SQLite were used; no packages were installed.

| Artifact | Purpose |
|---|---|
| `outputs/_intermediate/temporal_split.sqlite` | session assignment + `events_train` / `events_validation` |
| `outputs/temporal_interactions/{train,validation}_strategy*_rule*_*.csv.gz` | 24 provisional pair-level datasets |
| `outputs/temporal_interactions/metadata.json` | boundaries, rules, strategies, counts, SHA-256 |
| `reports/temporal_split_statistics.json` | machine-readable split statistics |
| `reports/candidate_universe_audit.csv` | one row per programme ID (9,671) |
| `reports/candidate_universe_statistics.json` | universe intersections and metadata coverage |
| `reports/temporal_split_verification.json` | 323 checks with digests |
| `reports/boundary_assignment_comparison.json` | old-vs-corrected assignment, event movement and per-combination deltas |
| `reports/boundary_assignment_comparison.csv` | one row per (split, strategy, rule) |
| `src/temporal_split.py`, `src/verify_temporal_split.py`, `src/build_temporal_report.py`, `src/compare_boundary_assignment.py` | reproducible pipeline |

## 8. Caveats and non-decisions

- **No final threshold or strategy is selected.** All 12 combinations remain provisional and equally documented.
- No CBF/PPR model, no training, no recommendations and no Kaggle submission were produced.
- The training/validation split is temporal at the schedule level. A user can appear in both splits (different sessions); that is intended for held-out evaluation and does not leak validation events into training.
- Validation events are labels only. Any later feature builder must consume `events_train` / `train_*` files exclusively.
- The session rule discards 192 of 3,847,696 labelled events (0.005%), all from the 97 sessions that matched both exports. This is deterministic and documented. The rejected maximum-overlap rule would have discarded 109 events and split the 97 boundary sessions 36/61 between training/validation; the adopted earliest-airing rule assigns all 97 to training because weeks 11–20 precedes weeks 21–30 for every cross-period session in this data.
- The test schedule has no viewing log; nothing in this stage uses test behaviour.
