# Design: Dual Fine-Tuning Pipeline for Digit Classifier

## Technical Approach

Four-phase additive pipeline (fetch → split → dual-train → compare). Confirmed crops (~3,124, double/triple human-validated) form a fixed test set. Remaining labeled crops (~13,000) feed two fine-tuning variants — conservative (frozen features, head only) and aggressive (full unfreeze, discriminative LR). Both evaluated identically against the confirmed test set. `models/digit_classifier.pth` is never modified; outputs are new files only.

Maps to FR 0.1–3.6 and NFR 1–8 from `spec.md`. Reuses `get_transforms()` and `WeightedRandomSampler` pattern from `train_digit_classifier.py`.

## Architecture Decisions

### Decision 1: Most-recent-label dedupe strategy

| Option | Tradeoff |
|--------|----------|
| Earliest label (ts ASC) | First annotator may be least informed |
| Majority vote per crop | Requires all labels per crop; complex with partial data |
| **Most recent (ts DESC)** | If corrections occurred, last annotator saw previous labels |

**Choice**: Most recent label per `crop_id` — query labels ordered by `ts DESC`, dedupe client-side keeping first occurrence. Consistent with `evaluate_agreement()` which also orders by `ts`.

### Decision 2: Discriminative LR groups on MobileNetV2

**Choice**: 3 groups matching layer depth — `features[0:7]` (early conv, lr=1e-5), `features[7:14]` (mid blocks, lr=1e-4), `features[14:]` + `classifier` (late blocks + head, lr=1e-3). This follows the established fine-tuning pattern where shallower layers get smaller LR to preserve generic features learned in pre-training.

### Decision 3: F1 macro for early stopping (not val accuracy)

**Choice**: Monitor val F1 macro with patience=5 (Model A) / patience=3 (Model B). Class imbalance is extreme (class 0: ~1,500, class 9: ~170). Accuracy alone would mask poor minority-class recall. F1 macro equally weights all 10 classes.

### Decision 4: Supabase query as labels-first, status-filter client-side

PostgREST does not support arbitrary JOIN. Two-phase approach:
1. Fetch confirmed `crop_id`s (exclusion list)
2. Fetch all labels `WHERE label_human != '_skip'` ordered by `ts DESC`
3. Client-side: dedupe by `crop_id`, filter confirmed, then query crops table for status exclusion (`needs_third`, `disputed`)

This avoids N+1 queries and works within PostgREST constraints.

## Data Flow

```
Supabase crops table                         data/labels/digits/
(confirmed_label)                                    │
     │                                                │
     ▼                                                ▼
fetch_confirmed_crops.py ──local-first copy──► data/labels/confirmed/{label}/
     │                                                │
     │  also fetches train pool                        │
     ▼                                                ▼
train_pool_manifest.jsonl              confirmed_manifest.jsonl
     │                                                │
     ▼                                                │
Split (stratified 90/10, seed=42) ────────────► test_crop_ids.json
     │                                                │
     ├── train_loader ──► Model A (frozen features)    │
     │                        │                        │
     ├── val_loader   ──► Model A (early stop)         │
     │                        │                        │
     ├── train_loader ──► Model B (discrim LR)         │
     │                        │                        │
     └── val_loader   ──► Model B (early stop)         │
                              │                        │
                              ▼                        ▼
                    model_a.pth + model_b.pth    confirmed test set
                              │                        │
                              └────────┬───────────────┘
                                       ▼
                              compare_models.py
                                       │
                                       ▼
                           dual_finetune_report.{json,txt}
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `fetch_confirmed_crops.py` | Create (~200 loc) | Supabase fetch + local copy + manifest writer |
| `train_dual_finetune.py` | Create (~350 loc) | Dual model training with discriminative LR, early stopping, F1 tracking |
| `compare_models.py` | Create (~150 loc) | Evaluation against fixed test set + report generation |
| `tests/test_retrain_pipeline.py` | Create (~200 loc) | pytest: dedup, split determinism, label filtering, test isolation, class threshold abort |
| `data/labels/confirmed/` | Create (dir) | ImageFolder: `{label}/img_{crop_id}.png` |
| `data/labels/train_pool/` | Create (dir) | ImageFolder: `{label}/img_{crop_id}.png` |
| `data/labels/confirmed_manifest.jsonl` | Create | Test set manifest |
| `data/labels/train_pool_manifest.jsonl` | Create | Train/val pool manifest |
| `data/labels/test_crop_ids.json` | Create | Cached test crop_id list (deterministic) |
| `data/labels/train_val_split.json` | Create | Cached `crop_id → "train"|"val"` mapping |
| `models/digit_classifier_model_a.pth` | Create | Conservative variant weights |
| `models/digit_classifier_model_b.pth` | Create | Aggressive variant weights |
| `models/dual_finetune_report.json` | Create | Machine-readable comparison |
| `models/dual_finetune_report.txt` | Create | Human-readable comparison |
| `models/dual_train_history.json` | Create | Per-epoch metrics for both models |

## Interfaces / Contracts

**Confirmed query** (FR-0.2): `crops` table → `{crop_id, confirmed_label, field_name, pdf_path}` where `status='confirmed'` AND `confirmed_label IS NOT NULL`.

**Train pool query** (FR-0.3): `labels` table → `{crop_id, label_human, ts}` where `label_human != '_skip'`, ordered by `ts DESC`. Client-side filters: dedupe (first = most recent), exclude confirmed `crop_id`s, exclude `status IN ('needs_third','disputed')`.

**Local-first path pattern**: `data/labels/digits/{label}/img_{crop_id}.png` (verified). Storage fallback: `{SUPABASE_URL}/storage/v1/object/public/crops/{crop_id}.png` (per `db.get_storage_url`).

**ZERO_VARIANTS map** (from `db.py`): `{"*", "-", ".", "+", "o", "O"} → "0"`. `_skip` label excluded (FR-1.3).

**MobileNetV2 blocks** for discriminative LR (FR-2.5):
```python
# features[0] = stem ConvBNReLU
# features[1..6] = early inverted residuals
# features[7..13] = mid inverted residuals
# features[14..17] = late inverted residuals
# features[18] = final ConvBNReLU
# classifier[1] = Linear(1280, 10)
groups = [
    {"params": net.features[0:7].parameters(), "lr": 1e-5},
    {"params": net.features[7:14].parameters(), "lr": 1e-4},
    {"params": net.features[14:].parameters(), "lr": 1e-3},
    {"params": net.classifier.parameters(), "lr": 1e-3},
]
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `_normalize_label` / ZERO_VARIANTS mapping | pytest, pure function, no Supabase |
| Unit | Dedupe: most-recent label per crop_id | pytest with mock label rows |
| Unit | Stratified split determinism (seed=42 → identical partition) | pytest, local manifest fixture |
| Unit | Class < 50 abort logic | pytest, expect RuntimeError |
| Unit | Test/train isolation: confirmed ∩ train_pool = ∅ | pytest, assert set intersection empty |
| Integration | fetch_confirmed_crops → manifests written | Requires `SUPABASE_URL` env; skip if absent |

## Migration / Rollout

No migration required. All outputs are new files. Rollback: delete all 14 files/dirs listed in File Changes above. `models/digit_classifier.pth` and `src/modules/analyzer/ocr_engines.py` are untouched.

## Open Questions

- [ ] Are confirmed crops available locally in `data/labels/digits/` or only in Supabase Storage? (Local-first strategy handles both)
- [ ] What is the actual class distribution of the train pool (~13,000)? Spec assumes all classes ≥ 50; if not, pipeline aborts gracefully per FR-1.5
