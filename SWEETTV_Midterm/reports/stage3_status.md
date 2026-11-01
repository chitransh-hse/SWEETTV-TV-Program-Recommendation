# Stage 3 — Pre-modelling Status Report

**Status: Stage 2.5 verified; Stage 3 modelling not yet started.**

## 1. Scope

This report records the verified starting point for Stage 3: the corrected temporal split, the main interaction configuration, candidate universes, existing submission assets, environment constraints and the evaluation protocol to be applied. No model has been fitted at this point.

## 2. Corrected Stage 2.5 state

| Check | Value |
|---|---|
| Boundary rule | `earliest_matched_airing` |
| Cross-period sessions | 97 |
| — assigned to training | 97 |
| — assigned to validation | 0 |
| Events excluded | 192 |
| Sessions in both splits | 0 |
| Verification | **323/323 passed, 0 failed** |

The maximum-overlap rule is not used. The split verified here is the one whose cross-period sessions follow the earliest matched airing (`pstart` ascending, then period ascending).

Source event store SHA-256: `945863fae18d5b239d71879eb239ed99307f4c527a76a014056b6574ab2cd1e5`.

## 3. Main interaction configuration

Strategy **A** (keep every match) with rule **B** (at least 60 s and at least 20% programme overlap).

| Split | Pair rows | Users | Programmes | Mean pairs/user | Median | P90 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| training (weeks 11–20) | 335,816 | 4,823 | 2,807 | 69.63 | 62 | 130 | 788 |
| validation (weeks 21–30) | 312,934 | 4,774 | 3,088 | 65.55 | 50 | 141 | 428 |

Users present in validation but absent from training: **61** (they will receive the documented fallback). Training users absent from validation: **110** (not evaluated, but still available for fitting).

## 4. Candidate universe and cold start

Candidate universes contain labelled programmes from the corresponding schedule export only; `tv_show_id = 0` is excluded everywhere.

| Period | Labelled programmes |
|---|---:|
| Training schedule (weeks 11–20) | 5,847 |
| Validation schedule (weeks 21–30) | 6,363 |
| Test schedule (weeks 31–42) | 6,636 |
| Union | 9,671 |

Of 6,636 test programmes, **2,566** never occur in training. All 2,566 cold-start programmes have usable metadata, so content-based scoring can still place them in a user profile.

## 5. Existing submission assets

The Kaggle template has **1,260 users**, all with five placeholder IDs. 1,250 template users occur in training and **10** have no training history; 2 have neither training nor validation history.

No recommendation script or submission existed inside the project. Three submission files were found in `~/Downloads` and were inspected read-only:

| File | Rows | 5 IDs/row | Template order | Zero IDs | Distinct IDs | Distinct lists |
|---|---:|---|---|---:|---:|---:|
| `solution.csv` | 1,260 | True | True | 0 | 268 | 1,222 |
| `solution_improved.csv` | 1,260 | True | True | 0 | 237 | 1,155 |
| `final_submission.csv` | 1,260 | True | True | 0 | 114 | 1,092 |

All three pass the structural submission checks. They are outside the repository and will not be overwritten. Their methods are not recorded locally, so they cannot be exactly reproduced here.

## 6. Environment and tooling

| Item | Value |
|---|---|
| python | 3.9.6 |
| numpy | 2.0.2 |
| pandas | 2.3.3 |
| scipy | not installed |
| scikit-learn | not installed |
| implicit | not installed |
| networkx | not installed |
| reportlab | installed (PDF rendering) |
| gpu | none (CPU only, Apple M2, 8 GiB RAM) |

SciPy, scikit-learn, `implicit`, NetworkX, PyTorch and Matplotlib are not installed. Stage 3 therefore uses NumPy and the standard library only. PDF rendering uses the already-installed ReportLab. No package will be installed.

## 7. Protocol to be applied

- **Primary metric:** MAP@5 over all 4,774 validation users.
- **Fitting:** all parameters from training weeks 11–20; validation used only for selection/tuning.
- **Candidate universe:** the target period's labelled schedule programmes; no test viewing data exists and none is used.
- **Repeats:** allowed, identically for every method.
- **Fallback:** training-popularity ranking restricted to the candidate universe for users with no history or fewer than five scored items.
- **Methods:** popularity baseline, content-based filtering, association rules and a rank-fusion hybrid (additional methods only if reliable).
- **Tuning:** small documented grid; one primary configuration per method reported.

## 8. User-reported Kaggle results and open items

The first submission's **public 0.35830 / private 0.33986** are user-reported and are not verified locally. The improved submission's offline validation MAP@5 of approximately 0.63747 is not a Kaggle score; the improved Kaggle result has not been provided. No Kaggle API response or screenshot is stored in the project.

Open items: improved-submission Kaggle score, submission screenshot, GitHub URL, and (if required by the exam) the original AI-session export.

## 9. Readiness

The corrected split, datasets, metadata, candidate universe and template are internally consistent and verified. Stage 3 can proceed without rebuilding or modifying any Stage 1/2/2.5 artifact.
