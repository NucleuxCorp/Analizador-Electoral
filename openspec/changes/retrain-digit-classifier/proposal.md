# Proposal: Dual Fine-Tuning Pipeline for Digit Classifier

## Intent

The current MobileNetV2 digit classifier (`models/digit_classifier.pth`, 98.7% val acc, ~9.2 MB) was trained on all ~4,593 human-labeled crops regardless of confirmation status. Supabase now holds ~3,124 `status='confirmed'` crops — an agreement-validated, higher-quality subset (source of truth: `src/modules/labeler/db.py:evaluate_agreement()`). This change adds a fetch → split → dual-train → compare pipeline that uses the confirmed crops as a **fixed test set** and trains two fine-tuning variants on the remaining labeled crops, then compares them against that fixed test set, **without ever auto-replacing the production model**.

## Scope

### In Scope
- `fetch_confirmed_crops.py` — download `status='confirmed'` crops from Supabase to `data/labels/confirmed/` + `confirmed_manifest.jsonl`
- **Two-tier data split**: confirmed crops = FIXED TEST SET (~3,124); remaining labeled crops (status `pending` with 1 real label, excluding `needs_third`, `disputed`, `_skip`) = TRAIN/VAL pool (~13,000)
- Stratified 90/10 split on the train/val pool with a deterministic seed
- `train_dual_finetune.py` — Model A (conservative) + Model B (aggressive) from existing weights
- `compare_models.py` — eval both vs the fixed confirmed test set; emit `dual_finetune_report.{json,txt}`
- Reuse existing transforms (`get_transforms()`) and `WeightedRandomSampler`

### Out of Scope
- Auto-replacing `models/digit_classifier.pth`
- Modifying existing transforms or `src/modules/analyzer/ocr_engines.py` inference path
- GPU support; E-14 Delegados pipeline; labeler portal changes
- Manual model promotion (deferred to a follow-up change)

## Capabilities

> Contract with `sdd-spec`. `openspec/specs/` is currently empty.

### New Capabilities
- `digit-retraining-pipeline`: end-to-end fetch → split → dual-train → compare workflow that retrains the digit classifier on human-labeled crops and evaluates against a confirmed-only fixed test set, without touching production weights.

### Modified Capabilities
None — existing classifier inference and the labeling portal are untouched.

## Approach

Four sequential phases:

1. **Fetch** (`fetch_confirmed_crops.py`): Supabase service_role query `SELECT crop_id, confirmed_label, field_name, pdf_path FROM crops WHERE status='confirmed' AND confirmed_label IS NOT NULL`. For each crop: **local-first** copy from `data/labels/digits/{label}/img_{crop_id}.png`, else download from Supabase Storage. Write `data/labels/confirmed/{label}/img_{crop_id}.png` (ImageFolder layout) + `data/labels/confirmed_manifest.jsonl`. Log total count + per-class distribution. Also fetch the remaining labeled crops (status `pending`, 1 real label) for the train/val pool — write to `data/labels/train_pool/` + `train_pool_manifest.jsonl`.
2. **Split**: 
   - **Test set** = ALL confirmed crops (~3,124) — this is the ground truth with double/triple human validation. Fixed, never changes.
   - **Train/val pool** = remaining labeled crops with 1 real label (~13,000), excluding `needs_third`, `disputed`, `_skip`.
   - Dedupe by `crop_id` (manifest has duplicates: 4,642 lines vs 4,593 unique IDs).
   - Filter labels 0–9 (drop `_skip`/variants).
   - Stratified 90/10 split on train/val pool with deterministic seed.
   - Cache test crop_ids to disk so both models see the identical test set.
   - Abort if any class has < 50 examples in the train/val pool.
3. **Dual Train** (`train_dual_finetune.py`): load `models/digit_classifier.pth` into `mobilenet_v2` + `Linear(1280, 10)`; reuse `get_transforms()` + `WeightedRandomSampler`.
   - **Model A (conservative)**: freeze `model.features`, train classifier head only, Adam lr=1e-4, early stopping.
   - **Model B (aggressive)**: unfreeze all, discriminative LR (early features 1e-5 / mid 1e-4 / classifier 1e-3), weight decay 1e-4, shorter early-stopping patience.
   - Per-epoch metrics: accuracy, F1 macro, loss, time. Save `models/digit_classifier_model_a.pth`, `models/digit_classifier_model_b.pth`.
4. **Compare** (`compare_models.py`): eval both on the confirmed-only fixed test set — accuracy, per-class F1 (0–9), confusion matrix, epochs-to-convergence. Write `models/dual_finetune_report.json` + `.txt`. Human decides promotion.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `fetch_confirmed_crops.py` | New | Supabase confirmed-crop downloader + train-pool downloader + manifest writer |
| `train_dual_finetune.py` | New | Dual fine-tune trainer (Model A + Model B) |
| `compare_models.py` | New | Fixed-test evaluation + report generator |
| `data/labels/confirmed/` | New | Local confirmed ImageFolder dataset (TEST SET) |
| `data/labels/confirmed_manifest.jsonl` | New | `crop_id` + `confirmed_label` manifest for test |
| `data/labels/train_pool/` | New | Local train/val ImageFolder dataset |
| `data/labels/train_pool_manifest.jsonl` | New | `crop_id` + `label_human` manifest for train/val |
| `models/digit_classifier_model_{a,b}.pth` | New | Variant weights |
| `models/dual_finetune_report.{json,txt}` | New | Comparison report |
| `train_digit_classifier.py` | Unchanged | Baseline script kept as-is |
| `models/digit_classifier.pth` | Unchanged | Never modified or replaced |
| `src/modules/analyzer/ocr_engines.py` | Unchanged | Inference path untouched |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Train/val pool has noisy single-user labels (not confirmed) | High | Model already learned from this data; fine-tuning refines features. Confirmed test set is the clean evaluation. |
| Severe class imbalance (cls 0 ~1,500 vs cls 9 ~170) | High | Reuse `WeightedRandomSampler`; abort if any class < 50 in train pool; report per-class counts |
| CPU-only training slow, esp. Model B full unfreeze on ~13k crops | High | Early stopping; log epoch times; accept longer runtime |
| Supabase rate limits / Storage download failures | Med | Retry w/ backoff; local-first fallback; resumable fetch by `crop_id` |
| Test leakage between A/B if split not cached | Low | Single deterministic seed; test crop_ids cached to disk and reused |
| Overfitting on aggressive variant (large train set, unfrozen model) | Med | Weight decay + early stopping + val F1 monitoring |
| Local crop path: `data/labels/digits/` is ImageFolder, `data/labels/crops/` is transient | Resolved | Use `data/labels/digits/` as local-first source (verified) |

## Rollback Plan

All outputs are additive. To revert: delete `fetch_confirmed_crops.py`, `train_dual_finetune.py`, `compare_models.py`, `data/labels/confirmed/`, `data/labels/confirmed_manifest.jsonl`, `data/labels/train_pool/`, `data/labels/train_pool_manifest.jsonl`, `models/digit_classifier_model_{a,b}.pth`, and `models/dual_finetune_report.*`. `models/digit_classifier.pth` is never modified — production inference is unaffected. No DB schema changes; Supabase access is read-only.

## Dependencies

- Supabase service_role key (env var) for fetch
- Existing `models/digit_classifier.pth` as initial weights
- `torch 2.12.0+cpu`, `torchvision 0.27.0+cpu` (installed; not pinned in `requirements.txt`)
- Supabase `crops` rows with `status='confirmed'` (~3,124) — test set
- Supabase `crops` + `labels` rows with 1 real label (~13,000) — train/val pool

## Success Criteria

- [ ] `fetch_confirmed_crops.py` downloads all confirmed crops (test set) + remaining labeled crops (train/val pool) and writes both manifests with per-class distribution logs
- [ ] Test set = ALL confirmed crops (~3,124), deterministic and identical for both models (verified via cached crop_ids)
- [ ] Train/val pool excludes `needs_third`, `disputed`, `_skip`, and confirmed crops (no leakage)
- [ ] Model A and Model B both reach early-stopping convergence on CPU
- [ ] `compare_models.py` emits `dual_finetune_report.{json,txt}` with accuracy, per-class F1, confusion matrix, epochs-to-convergence evaluated on the confirmed-only test set
- [ ] `models/digit_classifier.pth` is NOT modified or replaced
- [ ] Existing transforms reused unchanged (no edits to `ocr_engines.py`)
- [ ] Unit tests (pytest) cover fetch dedup, split determinism, label filtering, and test-set/train-pool isolation per `strict_tdd` config
