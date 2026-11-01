# SWEET.TV TV Program Recommendation

## Project Overview

This project was developed for the LLM4Rec final exam at HSE University.

The objective is to build a TV programme recommendation system using historical user viewing behaviour and programme metadata, and evaluate recommendation quality using MAP@5.

## Recommendation Methods

The project implements and evaluates:

- Popularity-based recommendation
- Content-based filtering using programme metadata
- Association rules based on programme co-viewing
- Repeat-recency recommendation
- Hybrid reciprocal-rank fusion (RRF)

The selected hybrid combines repeat-recency, popularity and content-based recommendation.

## Evaluation

The primary evaluation metric is MAP@5.

The selected hybrid achieved an offline validation MAP@5 of 0.634319.

The final Kaggle submission achieved:

- Public score: 0.44181
- Private score: 0.43656

## Final Deliverables

- `outputs/final_submission.csv` — final Kaggle submission
- `reports/SWEETTV_Final_Exam_Report_FINAL.pdf` — final exam report
- `reports/SWEETTV_Final_Exam_Report_FINAL.md` — editable report
- `reports/kaggle_submission_screenshot.png` — Kaggle evaluation evidence

## Project Structure

- `src/` — model implementation, evaluation and verification scripts
- `reports/` — experiment results, audit reports and final report
- `outputs/` — final submission and model configuration

## Data Availability

The original competition datasets are not included in this repository. They must be obtained from the SWEET.TV Kaggle competition.

## Reproducibility

The Python source scripts and experiment configurations are provided in this repository. Refer to the final report for the methodology, temporal split, model configuration and evaluation protocol.

## Competition

https://www.kaggle.com/competitions/sweettv-tv-program-recommender
