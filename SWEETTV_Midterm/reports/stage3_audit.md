# Stage 3 — Submission and Experiment Audit

**Result: 33/33 checks passed, 0 failed.**

| Check | Passed | Detail |
|---|---|---|
| Stage 2.5 verification still 323/323 | yes | 323/323 |
| source event store digest unchanged | yes | 945863fae18d5b23 |
| temporal split DB digest unchanged | yes | 395532d3a1324d63 |
| metadata projection digest unchanged | yes | c1f8a53e8020ab71 |
| main training data hash matches experiment | yes | — |
| main validation data hash matches experiment | yes | — |
| no test-period viewing log is used | yes | only schedule 31-42 is read for the candidate universe |
| validation used for scoring/selection only | yes | — |
| selected model exists in experiment grid | yes | — |
| selected MAP@5 matches experiment | yes | 0.634319 |
| experiment includes a popularity baseline | yes | — |
| experiment includes content-based filtering | yes | — |
| experiment includes association rules | yes | — |
| experiment includes a hybrid | yes | — |
| all reported metrics are finite | yes | — |
| submission header exact | yes | — |
| submission has exactly 1260 rows | yes | 1260 |
| submission user IDs unique | yes | — |
| submission preserves template order | yes | — |
| every row has exactly five IDs | yes | — |
| no duplicate ID within a row | yes | — |
| no tv_show_id=0 | yes | — |
| all IDs numeric | yes | — |
| all IDs in weeks 31-42 labelled universe | yes | — |
| test candidate universe size 6636 | yes | 6636 |
| submission hash matches metadata | yes | b661738f2aecb928 |
| submission row count matches metadata | yes | — |
| selected model recorded in submission metadata | yes | — |
| external file unchanged: solution.csv | yes | ef7c25829560f849 |
| external file unchanged: solution_improved.csv | yes | cf075c2379a16215 |
| external file unchanged: final_submission.csv | yes | 7c330b884d793404 |
| example recommendations cover two users | yes | 10 rows |
| example recommendations have five rows per user | yes | — |

## Selected model

`hybrid` / `hybrid_1.00_0.20_0.05_repeat_recency+popularity+content_based`, validation MAP@5 **0.634319**, Recall@5 0.0514.

## Submission

`outputs/final_submission.csv`: 1260 rows, SHA-256 `b661738f2aecb928a133812f406edca98de7b7d55280d878c4449924fbca412e`, 220 distinct programme IDs.

The submission was not uploaded from this environment and no Kaggle score is claimed here.
