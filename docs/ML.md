# Access prediction: methodology

## Task

Given everything known about a file at a cutoff time *t*, predict whether it will be accessed **at least once in (t, t + H]**, where *H* is the horizon (default 30 days). The prediction informs storage-class recommendations. It never moves or deletes anything by itself.

## Data

| Source | Label in DB | Used for |
| --- | --- | --- |
| Synthetic generator (`app/ml/synthetic.py`) | `dataset_kind = synthetic` | Demonstrating and testing the method |
| Imported access logs (CSV `path,timestamp`) | `access_events.source = imported_log` | Training on observed data (`dataset = observed`) and real predictions |
| S3 server access logs (upload, or read from the log bucket) | `access_events.source = s3_access_log` | Same as above, for S3 objects |
| Synthetic demo history attached to real files | `access_events.source = synthetic_demo` | Showing the prediction workflow on the demo dataset only |

Filesystem access times (`atime`) are recorded but **never used as access history**. They are commonly disabled, or updated by indexers and antivirus software.

The synthetic generator is seeded and reproducible. It simulates type-dependent base rates, popularity that decays after creation (several half-lives), periodic re-reads (about 12% of files), revival bursts (about 8%), and background noise. It is a plausible but invented process, and every result derived from it is labelled *synthetic*.

### S3 server access logs

`app/services/access_logs.py` parses the [documented log format](https://docs.aws.amazon.com/AmazonS3/latest/userguide/LogFormat.html). Only `REST.GET.OBJECT` requests with a 2xx status count as an access. Keys are URL-decoded and matched to scanned objects by bucket and key, and re-importing the same log never duplicates events. Logs can be uploaded on the *Access prediction* page, or read directly from the log bucket on the *AWS S3* page (`s3:ListBucket` and `s3:GetObject` on the log bucket are required).

## Features (computed only from events strictly before the cutoff)

`days_since_last_access`, `never_accessed`, `accesses_7d`, `accesses_30d`, `accesses_prev_30_60d`, `accesses_90d`, `accesses_total`, `access_rate_per_30d`, `trend_30d` (recent vs previous month), `days_since_creation`, `log_size`, and `category` (one-hot encoded). Numeric features are standardised inside the scikit-learn `Pipeline`, so preprocessing is fitted on training data only.

## Chronological split (leakage control)

- The **test** snapshot is taken at `test_cutoff = history_end − H`.
- **Training** snapshots are taken at up to 6 earlier cutoffs, 30 days apart, all satisfying `cutoff + H ≤ test_cutoff`. No training label window overlaps the test period.
- Histories shorter than `2H + 30` days are rejected with an explicit error.
- `tests/test_ml.py` asserts these properties.

## Models

| Model | Notes |
| --- | --- |
| Heuristic baseline | "Accessed within the last H days ⇒ will be accessed" (score 0.9 / 0.1) |
| Logistic regression | `class_weight="balanced"` |
| Random forest | 200 trees, depth 10, min leaf 5, `class_weight="balanced_subsample"` |

The ML model with the higher test ROC AUC is selected and persisted with `joblib` (`data/models/access_model_<id>.joblib`). Metrics stored per model: ROC AUC, accuracy, precision, recall, F1 (threshold 0.5), and Brier score.

**Example run** (synthetic, 1,500 files, seed 42, H = 30, from the dev environment): heuristic AUC 0.812, logistic regression 0.890, random forest **0.901** (selected; F1 0.815, Brier 0.114). The most important features were `days_since_last_access`, `accesses_30d`, and `accesses_90d`. Remember that these numbers measure how well the model recovers *our simulator*, not real-world behaviour.

## Inference and reliability

For each file, the active model (the newest observed-data model, otherwise the newest synthetic one) is applied to the file's recorded events. Each prediction carries a reliability:

- `unavailable`: no model, or no access history. No probability is produced.
- `low`: the model was trained on synthetic data, the history is synthetic demo data, or fewer than 3 events exist. The reason is shown in the UI.
- `ok`: observed-data model and observed history.

The cost engine's *conservative* strategy only trusts `ok` predictions. *Balanced* and *aggressive* use `low` ones but flag the risk on every affected recommendation.

## Limitations and next steps

- Synthetic data cannot validate real-world accuracy. Import S3 server access logs or CloudTrail data events to train on observed behaviour.
- Probabilities are not calibrated (no `CalibratedClassifierCV`). Calibration on observed data is a natural next step.
- A single global threshold of 0.5 is reported. Cost-sensitive threshold selection would be better aligned with storage decisions.
