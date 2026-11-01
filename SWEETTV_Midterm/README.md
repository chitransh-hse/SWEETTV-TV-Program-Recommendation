# SWEET.TV TV Program Recommendation — LLM4Rec Final Exam

**Stage 1: Environment and Dataset Audit (complete).**
**Stage 2: Viewing-to-Programme Matching and Interaction Reconstruction (complete, provisional).**
**Stage 2.5: Temporal Separation and Interaction Dataset Preparation (complete, provisional).**
**Stage 3: Recommendation Modelling, Offline Evaluation and Kaggle Submission (complete).**

Stage 3 implements and evaluates five recommenders on the verified Stage 2.5
split, selects a model on validation MAP@5 only, and writes a structurally
validated Kaggle submission. The Kaggle public/private scores for that
submission are **not** available and are not claimed. See
`reports/SWEETTV_Final_Exam_Report.pdf`.

## 1. Objective

Build a TV programme recommender for the SWEET.TV dataset: for each user in the
Kaggle test set, recommend up to five programme IDs for the test schedule,
using viewing history and programme metadata.

- Competition: <https://www.kaggle.com/competitions/sweettv-tv-program-recommender>
- Stage 1 profiles the data and the local environment.
- Stage 2 reconstructs user–programme interactions from the viewing log and the
  training/validation schedules, and compares candidate thresholds and
  multiple-match strategies without selecting either.
- Stage 2.5 fixes the corrected temporal split and the interaction datasets.
- Stage 3 trains, evaluates and selects recommenders, and produces the
  submission and final report.

## 2. Directory structure

```text
SWEETTV_Midterm/
├── README.md
├── data/                       # symlinks to the original competition CSVs (no copies)
│   ├── dataset11-30.csv -> ...
│   ├── export_arh_11-20-final.csv -> ...
│   ├── export_arh_21-30-final.csv -> ...
│   ├── export_arh_31-42-final.csv -> ...
│   └── submission.csv -> ...
├── src/
│   ├── audit_stage1.py         # streaming audit of all five CSVs
│   ├── environment_audit.py    # host/Python/package audit
│   ├── build_reports.py        # renders dataset_audit.md from the JSON
│   ├── stage2_common.py        # parsing, schedule index, overlap, rules, resolution
│   ├── build_schedule_index.py # compact per-channel schedule index
│   ├── analyze_timestamp_semantics.py  # offset / DST / duration diagnostics
│   ├── match_interactions.py   # interval join -> SQLite event store
│   ├── analyze_coverage.py     # coverage histograms and zero-overlap reasons
│   ├── aggregate_interactions.py       # 12 strategy x rule provisional datasets
│   ├── build_stage2_reports.py # renders the two Stage 2 markdown audits
│   ├── verify_stage2.py        # 96 integrity / determinism checks
│   ├── audit_candidate_universe.py     # programme universe + metadata coverage
│   ├── temporal_split.py       # temporal train/validation split + 24 datasets
│   ├── verify_temporal_split.py        # leakage / boundary / determinism checks
│   ├── build_temporal_report.py        # renders the Stage 2.5 markdown audit
│   ├── stage3_common.py        # shared loading, features, CBF, rules, ranking
│   ├── build_stage3_status.py  # pre-modelling status report
│   ├── evaluate_stage3_models.py  # 50-config validation grid + selection
│   ├── build_final_submission.py  # selected-model submission builder
│   ├── verify_stage3.py        # independent 33-check Stage 3 audit
│   ├── report_renderer.py      # Markdown + ReportLab PDF renderer
│   └── build_stage3_report.py  # final exam report (Markdown + PDF)
├── reports/
│   ├── environment_audit.md
│   ├── environment.json
│   ├── dataset_audit.md
│   ├── dataset_statistics.json
│   ├── timestamp_alignment_audit.md
│   ├── interaction_matching_audit.md
│   ├── timestamp_semantics.json
│   ├── schedule_index_stats.json
│   ├── match_diagnostics.json
│   ├── coverage_diagnostics.json
│   ├── threshold_sensitivity.csv
│   ├── matching_strategy_comparison.csv
│   ├── interaction_statistics.json
│   ├── stage2_verification.json
│   ├── temporal_split_audit.md
│   ├── temporal_split_statistics.json
│   ├── temporal_split_verification.json
│   ├── candidate_universe_audit.csv
│   ├── candidate_universe_statistics.json
│   ├── stage3_status.md, stage3_status.json
│   ├── stage3_experiment_results.json
│   ├── stage3_model_comparison.csv, stage3_model_comparison.md
│   ├── stage3_example_recommendations.csv
│   ├── stage3_audit.md, stage3_verification.json
│   └── SWEETTV_Final_Exam_Report.md, SWEETTV_Final_Exam_Report.pdf
└── outputs/
    ├── _intermediate/          # schedule_index.pkl, overlaps.sqlite, temporal_split.sqlite
    ├── provisional_interactions/   # 12 .csv.gz + metadata.json (whole period)
    ├── temporal_interactions/      # 24 .csv.gz (train/validation) + metadata.json
    ├── stage3/                 # selected_model.json, submission_metadata.json
    └── final_submission.csv    # Kaggle submission (1,260 rows, 5 IDs per row)
```

The `data/` entries are symbolic links to the downloaded files under
`/Users/chitranshgupta/Downloads/sweettv-tv-program-recommender/`. The original
archive and CSVs were not modified, moved, deleted, or duplicated.

## 3. Dataset files

| File | Description | Rows | Columns | Period |
|---|---|---:|---:|---|
| `dataset11-30.csv` | Viewing log (`user_id`, `vsetv_id`, `start_time`, `stop_time`, `duraton`) | 5,821,987 | 5 | 2020-03-09 → 2020-07-26 (weeks 11–30) |
| `export_arh_11-20-final.csv` | TV schedule, weeks 11–20 | 1,074,235 | 13 | 2020-03-08 → 2020-05-18 |
| `export_arh_21-30-final.csv` | TV schedule, weeks 21–30 | 1,074,394 | 13 | 2020-05-18 → 2020-07-27 |
| `export_arh_31-42-final.csv` | TV schedule, weeks 31–42 (test period, no viewing log) | 1,295,378 | 13 | 2020-07-27 → 2020-10-19 |
| `submission.csv` | Kaggle template: 1,260 users, 5 space-separated programme IDs per row, all placeholder `0` | 1,260 | 2 | — |

Key properties:

- Viewing log: 4,914 users, 202 channels, 139 active dates, no missing IDs and
  no unparsable timestamps; `duraton` agrees with `stop_time − start_time`
  except for 117 rows at the 2020-03-29 DST change (+1 h wall-clock artifact).
- Schedules: ~89% of rows have `tv_show_id = 0`; 9,671 distinct non-zero
  programme IDs across all periods; UTF-8 with Cyrillic titles.
- Channel matching: 197 of 202 viewing channels exist in the schedules;
  98.05% of viewing rows are on matched channels. Per period it is stricter:
  193/202 (weeks 11–20), 196/202 (weeks 21–30), 196/202 (weeks 31–42).
- 1,437 programme IDs in the test schedule (weeks 31–42) never appear in the
  earlier schedules.

Full details, tables, and caveats are in `reports/dataset_audit.md`; exact
machine-readable values are in `reports/dataset_statistics.json`.

## 4. Environment

| Item | Value |
|---|---|
| OS | macOS 26.1 (build 25B78), arm64 |
| CPU | Apple M2, 8 logical cores |
| RAM | 8 GiB total (memory pressure non-trivial at audit time) |
| Disk | 308 GiB free |
| Python | 3.9.6 (`/usr/bin/python3`, Apple Command Line Tools) |
| pandas / NumPy | 2.3.3 / 2.0.2 |
| ReportLab | 5.0.1 (PDF rendering, already installed) |
| SciPy / scikit-learn | **not installed** |
| Other DS packages | no PyArrow, Polars, DuckDB, Matplotlib, NetworkX, Jupyter, implicit, LightFM |

No packages were installed or upgraded. SciPy/scikit-learn would need explicit
approval before use. The stdlib `csv` module was sufficient: the full audit
streams 890 MB in ~107 s with ~717 MB peak RSS.

See `reports/environment_audit.md` for the full audit.

## 5. How to reproduce

From the project root:

```bash
cd "/Users/chitranshgupta/Downloads/SWEETTV_Midterm"

# 1. Host / Python / package audit
python3 src/environment_audit.py
#    -> reports/environment_audit.md, reports/environment.json

# 2. Stream all five CSVs (read-only)
python3 src/audit_stage1.py
#    -> reports/dataset_statistics.json

# 3. Render the markdown report from the JSON
python3 src/build_reports.py
#    -> reports/dataset_audit.md

# ---- Stage 2 -------------------------------------------------------------
# 4. Compact per-channel schedule index (union of weeks 11-20 and 21-30)
python3 src/build_schedule_index.py
#    -> outputs/_intermediate/schedule_index.pkl
#    -> reports/schedule_index_stats.json

# 5. Timestamp semantics / offset / DST diagnostics
python3 src/analyze_timestamp_semantics.py
#    -> reports/timestamp_semantics.json

# 6. Interval join -> disk-backed event store
python3 src/match_interactions.py
#    -> outputs/_intermediate/overlaps.sqlite
#    -> reports/match_diagnostics.json

# 7. Coverage diagnostics
python3 src/analyze_coverage.py
#    -> reports/coverage_diagnostics.json

# 8. Threshold x strategy aggregation (12 provisional datasets)
python3 src/aggregate_interactions.py
#    -> outputs/provisional_interactions/*.csv.gz + metadata.json
#    -> reports/threshold_sensitivity.csv
#    -> reports/matching_strategy_comparison.csv
#    -> reports/interaction_statistics.json

# 9. Verify (96 checks, including repeat-run determinism)
python3 src/verify_stage2.py
#    -> reports/stage2_verification.json

# 10. Render the Stage 2 markdown audits
python3 src/build_stage2_reports.py
#    -> reports/timestamp_alignment_audit.md
#    -> reports/interaction_matching_audit.md

# ---- Stage 2.5 -----------------------------------------------------------
# 11. Candidate programme universe (training / validation / test)
python3 src/audit_candidate_universe.py
#    -> reports/candidate_universe_audit.csv
#    -> reports/candidate_universe_statistics.json

# 12. Temporal split + all 12 threshold x strategy combinations per split
python3 src/temporal_split.py
#    -> outputs/_intermediate/temporal_split.sqlite
#    -> outputs/temporal_interactions/*.csv.gz + metadata.json
#    -> reports/temporal_split_statistics.json

# 13. Verify (leakage, boundaries, IDs, determinism), then render the audit
#     Run the split twice and compare stable digests.
python3 src/verify_temporal_split.py --digest-only > /tmp/run1.json
python3 src/temporal_split.py
python3 src/verify_temporal_split.py --compare /tmp/run1.json
python3 src/build_temporal_report.py
#    -> reports/temporal_split_verification.json
#    -> reports/temporal_split_audit.md

# ---- Stage 3 -------------------------------------------------------------
# 14. Pre-modelling status report (reads verified Stage 2.5 outputs)
python3 src/build_stage3_status.py
#    -> reports/stage3_status.md, reports/stage3_status.json

# 15. Full 50-configuration validation grid and model selection (~27 s)
python3 src/evaluate_stage3_models.py
#    -> reports/stage3_experiment_results.json
#    -> reports/stage3_model_comparison.csv
#    -> reports/stage3_model_comparison.md
#    -> outputs/stage3/selected_model.json

# 16. Build the Kaggle submission with the selected hybrid (~15 s)
python3 src/build_final_submission.py
#    -> outputs/final_submission.csv
#    -> outputs/stage3/submission_metadata.json
#    -> reports/stage3_example_recommendations.csv

# 17. Independent Stage 3 audit (33 checks)
python3 src/verify_stage3.py
#    -> reports/stage3_verification.json
#    -> reports/stage3_audit.md

# 18. Render the final exam report (Markdown + PDF)
python3 src/build_stage3_report.py
#    -> reports/SWEETTV_Final_Exam_Report.md
#    -> reports/SWEETTV_Final_Exam_Report.pdf
```

The audit script uses only the Python standard library (`csv`, `array`,
`hashlib`, `statistics`, `json`) and never loads a full file into pandas.

## 6. Stage 1 findings that constrain Stage 2

1. Test weeks 31–42 have a schedule but **no viewing log**; recommendations
   must come from history (weeks 11–30).
2. Programme-level interactions are limited by `tv_show_id = 0` on ~89% of
   schedule rows; channel-level matching is much more complete.
3. The five viewing channels absent from schedules (149, 1174, 1508, 1509, 1510)
   account for ~2% of viewing rows; per-period joins lose a little more
   (193–196 of 202 channels depending on period).
4. Timestamps are naive local wall-clock; a DST artifact proves local time, and
   no timezone offset may be assumed. No offset was applied.
5. 1,437 test-period programme IDs are new; content/channel fallbacks will be
   needed for cold-start items.
6. User IDs exceed signed int64 and must be read as strings.

These constraints shaped the Stage 2 matching, the Stage 2.5 split and the
Stage 3 modelling decisions documented below.

## 7. Stage 2 status

Stage 2 reconstructed interactions and compared candidate thresholds and
multiple-match strategies. Nothing was selected:

- Join: `vsetv_id = channel_id` plus interval overlap, offset **0 s**,
  schedule scope = union of weeks 11–20 and 21–30.
- 3,847,696 labelled events over 2,964,346 sessions (50.92% of all sessions);
  2,857,641 sessions (49.08%) have no labelled overlap.
- 12 provisional datasets exist (strategy A/B/C × rule A/B/C/D), each with the
  rule and threshold in its filename and in
  `outputs/provisional_interactions/metadata.json`.
- Verification: 96/96 checks passed, including a full repeat run with identical
  SHA-256 digests.

See `reports/interaction_matching_audit.md` and
`reports/timestamp_alignment_audit.md`.

## 8. Stage 2.5 status

Stage 2.5 separated the event store by schedule period and prepared training
and validation datasets. Nothing was selected or trained:

- Period membership comes from the source export (`period` 1 = weeks 11–20,
  2 = weeks 21–30); nominal boundaries are 2020-05-18 00:00 and
  2020-07-27 00:00, but the exports hand over per channel, so no timestamp
  predicate is used for membership.
- 2,384,996 training events (weeks 11–20) and 1,462,508 validation events
  (weeks 21–30); 192 events from 97 cross-period sessions were excluded by the
  earliest-matched-airing assignment rule (`pstart ASC`, then `period ASC`), which
  assigns all 97 to training. Neither viewing stop time nor total overlap seconds
  is used, so validation overlap cannot influence training-event inclusion.
- Boundary-rule correction: the earlier maximum-overlap rule had assigned 36 of
  the 97 cross-period sessions to training and 61 to validation, excluding 109
  events. The correction reassigns those 61 sessions validation → training,
  newly includes 69 training events and newly excludes 152 validation events.
  All 12 training combinations gain pairs (+175 in total, none removed) and all
  12 validation combinations lose pairs (−274, none added); corrected training
  pair sets are strict supersets of the old ones. See
  `reports/boundary_assignment_comparison.json` and `.csv`.
- 24 provisional datasets: 12 threshold × strategy combinations for each split,
  with IDs preserved as strings and `tv_show_id = 0` excluded.
- Candidate universe: 5,847 training / 6,363 validation / 6,636 test programme
  IDs; 9,671 in the union; 2,566 test programmes are cold-start, all with usable
  metadata, and none were removed.
- Verification: 323/323 checks passed, including explicit earliest-airing
  assignment checks and a full repeat run with identical split-database, dataset
  and metadata-projection digests. The full check list is in
  `reports/temporal_split_verification.json`.

See `reports/temporal_split_audit.md` for the full boundary, leakage and
candidate-universe audit.

## 9. Stage 3 status

Stage 3 is complete. All behavioural parameters (popularity, repeat-recency,
co-viewing) are estimated on training weeks 11–20; metadata TF-IDF weights use
schedule content available at prediction time. Weeks 21–30 are used only for
scoring and model selection. There is no test viewing log, and
`tv_show_id = 0` is excluded from every stage.

### 9.1 Methods and validation results

Five methods share one protocol: the same candidate universe, the same repeat
policy (repeats allowed) and the same fallback (global training popularity by
distinct users, restricted to the target universe). The primary metric is
MAP@5 over all 4,774 validation users; the candidate universe is the 6,363
labelled weeks 21–30 programmes.

| Method | Best configuration | Validation MAP@5 | Recall@5 | Coverage |
|---|---|---:|---:|---:|
| Popularity baseline | distinct users | 0.572049 | 0.0470 | 0.08% |
| Content-based filtering | genre features, log-sessions, K=50 | 0.197621 | 0.0224 | 15.39% |
| Association rules | confidence, K=50, support=5 | 0.570997 | 0.0469 | 0.16% |
| Repeat-recency | tau=120 d, alpha=1.0 | 0.622277 | 0.0505 | 14.21% |
| **Hybrid (RRF, selected)** | repeat 1.00 + popularity 0.20 + CBF 0.05, k=60 | **0.634319** | 0.0514 | 10.25% |

The exam requirement of at least two method families plus a popularity
baseline is met by content-based filtering and association rules. Implicit
matrix factorisation and personalised PageRank were not implemented because
the required libraries are absent and the brief forbids installing packages;
this is documented in Section 9.5 of the report.

All 50 configurations are listed in `reports/stage3_experiment_results.json`
and in Appendix A of the report. The top five are within 0.0023 MAP@5 of one
another, so the selection is best described as one of several nearly
equivalent configurations rather than a decisive winner.

### 9.2 Submission

`outputs/final_submission.csv`:

- 1,260 data rows in the exact template order, header `user_id,tv_show_id`.
- Exactly five space-separated programme IDs per row, no duplicates, no `0`.
- All IDs belong to the 6,636-programme weeks 31–42 labelled universe.
- 220 distinct programme IDs; 1,125 distinct recommendation lists.
- SHA-256 `b661738f2aecb928a133812f406edca98de7b7d55280d878c4449924fbca412e`.

The lists are repeat-heavy by design (mean 4.94 repeats of five; 1,239 of
1,260 rows all-repeat) because validation MAP@5 strongly favours
re-consumption. The 11 non-repeat rows are the 10 users with no training
history plus one user whose only training programme does not air in the test
universe. This is discussed as a business risk in Section 15 of the report.

### 9.3 Verification and report

- `python3 src/verify_stage3.py`: **33/33 checks pass** (data digests, no test
  viewing data, model present in the grid, submission structure and hash,
  unchanged external files).
- `reports/SWEETTV_Final_Exam_Report.pdf`: 22-page final report with all 18
  required sections, Appendix A (complete grid) and Appendix B (audit detail).
  Editable Markdown source: `reports/SWEETTV_Final_Exam_Report.md`.
- Kaggle public/private scores for this submission: **not available**. The
  earlier user-reported scores (public 0.35830, private 0.33986) belong to a
  different submission and are not claimed for this model.
