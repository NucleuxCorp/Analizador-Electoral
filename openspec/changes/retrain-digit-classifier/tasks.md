# Tasks: Dual Fine-Tuning Pipeline for Digit Classifier

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900 (4 new files: fetch ~200, train ~350, compare ~150, tests ~200) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Fetch + Split + unit tests | PR 1 | ~400 loc; data pipeline is self-contained; tests cover dedup/split/filter/isolation/abort |
| 2 | Dual training (Model A + B) | PR 2 | ~350 loc; depends on PR 1 manifests + split cache |
| 3 | Compare + report + integration test | PR 3 | ~150 loc; depends on PR 2 model weights |

## Phase 1: Data Fetch (fetch_confirmed_crops.py)

- [x] 1.1 Create `fetch_confirmed_crops.py` — Supabase client init: `SUPABASE_SERVICE_ROLE_KEY` with fallback to `SUPABASE_ANON_KEY`, abort if neither set. Mirror pattern from `src/modules/labeler/db.py:35-43`. (~25 loc)
- [x] 1.2 Add confirmed query — `SELECT crop_id, confirmed_label, field_name, pdf_path FROM crops WHERE status='confirmed' AND confirmed_label IS NOT NULL` with `.range()` pagination. (~30 loc)
- [x] 1.3 Add train pool query — fetch labels `WHERE label_human != '_skip'` ordered `ts DESC`; client-side: dedupe by `crop_id` (keep first=most recent), exclude confirmed crop_ids, query crops table for status exclusion (`needs_third`, `disputed`). (~45 loc)
- [x] 1.4 Add local-first copy + Storage fallback — copy from `data/labels/digits/{label}/img_{crop_id}.png`; if missing, download from `{SUPABASE_URL}/storage/v1/object/public/crops/{crop_id}.png` with exponential backoff (3 retries). Skip if destination exists. (~50 loc)
- [x] 1.5 Add manifest writers — write `data/labels/confirmed_manifest.jsonl` and `data/labels/train_pool_manifest.jsonl`; copy images to `data/labels/confirmed/{label}/` and `data/labels/train_pool/{label}/` ImageFolder layout. (~30 loc)
- [x] 1.6 Add logging — totals per manifest, per-class distribution (0-9 + variants), list of crops not found locally or in Storage. (~20 loc)

## Phase 2: Split Logic (split_dataset.py)

- [x] 2.1 Add `_normalize_label()` — map `ZERO_VARIANTS` (`*`, `-`, `.`, `+`, `o`, `O`) → `'0'`, exclude `_skip`. Reuse logic from `db.py:62-69`. (~10 loc)
- [x] 2.2 Add stratified split — load `confirmed_manifest.jsonl` as test set; load + dedupe `train_pool_manifest.jsonl`; stratified 90/10 split with `seed=42`; abort if any class < 50 in train pool. (~40 loc)
- [x] 2.3 Add split cache — write `data/labels/test_crop_ids.json` and `data/labels/train_val_split.json`; load from cache if exists (idempotent re-run). (~20 loc)

## Phase 3: Dual Training (train_dual_finetune.py)

- [x] 3.1 Create `train_dual_finetune.py` — argparse, device=cpu, import `get_transforms` from `train_digit_classifier.py`. Load `models/digit_classifier.pth` into MobileNetV2 + `Linear(1280,10)`. Build DataLoaders with `WeightedRandomSampler`. (~60 loc)
- [x] 3.2 Implement Model A — freeze `model.features` (`requires_grad=False`), Adam `lr=1e-4`, train loop with per-epoch metrics (accuracy, F1 macro, loss, time), early stopping `patience=5` on val F1 macro. (~80 loc)
- [x] 3.3 Implement Model B — unfreeze all, 3 discriminative LR groups (`features[0:7]`=1e-5, `features[7:14]`=1e-4, `features[14:]+classifier`=1e-3), `weight_decay=1e-4`, early stopping `patience=3` on val F1 macro. (~80 loc)
- [x] 3.4 Add save logic — save `models/digit_classifier_model_a.pth`, `model_b.pth` (best val F1 weights), `models/dual_train_history.json` (per-epoch metrics + epochs_to_convergence for both). (~40 loc)
- [x] 3.5 Add F1 macro computation — `sklearn.metrics.f1_score(avg='macro')` or manual; per-epoch val F1 for early stopping. (~20 loc)
- [x] 3.6 Add `__main__` orchestration — sequential: train A → train B → save all artifacts. (~30 loc)

## Phase 4: Compare & Report (compare_models.py)

- [ ] 4.1 Create `compare_models.py` — load both `model_a.pth` and `model_b.pth` into same MobileNetV2+Linear architecture. Load test set from `data/labels/confirmed/` filtered by `test_crop_ids.json`. (~40 loc)
- [ ] 4.2 Add evaluation loop — for each model: accuracy, per-class F1 (0-9), F1 macro, 10x10 confusion matrix on the confirmed test set. (~40 loc)
- [ ] 4.3 Add report generation — `models/dual_finetune_report.json` (structured: per-model metrics + epochs_to_convergence from `dual_train_history.json`) and `models/dual_finetune_report.txt` (human-readable side-by-side). (~40 loc)
- [ ] 4.4 Add recommendation logic — combined score (accuracy + F1 macro), recommend model with justification. Assert `digit_classifier.pth` unchanged (hash check or mtime). (~30 loc)

## Phase 5: Tests (tests/test_retrain_pipeline.py)

- [x] 5.1 Create `tests/test_retrain_pipeline.py` — test `_normalize_label`: ZERO_VARIANTS → '0', `_skip` excluded, digits pass-through. (~25 loc)
- [x] 5.2 Test dedupe — most-recent label per crop_id from mock label rows (ts DESC ordering). (~30 loc)
- [x] 5.3 Test split determinism — same seed=42 → identical partition across two runs; stratified distribution preserved. (~30 loc)
- [x] 5.4 Test class threshold abort — class with < 50 samples raises error with class name and count in message. (~20 loc)
- [x] 5.5 Test test/train isolation — confirmed crop_ids ∩ train_pool crop_ids = ∅. (~20 loc)
- [x] 5.6 Test label filtering — `_skip` excluded, zero variants mapped to '0', only 0-9 classes remain. (~25 loc)
- [x] 5.7 Test integration (manifest generation) — skip if `SUPABASE_URL` not set; otherwise verify manifests written with correct schema. (~30 loc)

## Phase 6: Cleanup & Verification

- [ ] 6.1 Verify `models/digit_classifier.pth` is byte-identical after full pipeline run (no modification).
- [ ] 6.2 Verify `train_digit_classifier.py` and `src/modules/analyzer/ocr_engines.py` are unmodified.
- [x] 6.3 Run `pytest -xvs tests/test_retrain_pipeline.py` — all tests pass.
- [ ] 6.4 Run full pipeline E2E: `fetch_confirmed_crops.py` → `train_dual_finetune.py` → `compare_models.py` — verify all output artifacts generated.
