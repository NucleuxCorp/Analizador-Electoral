#!/usr/bin/env python3
"""Dual fine-tuning: Model A (conservative) vs Model B (aggressive)."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms

from split_dataset import load_or_create_split
from train_digit_classifier import get_transforms

# === Constants ===
MODEL_PATH = "models/digit_classifier.pth"
NUM_CLASSES = 10
BATCH_SIZE = 64
MAX_EPOCHS = 30
SEED = 42


# === Metrics (sklearn is not a project dependency) ===
def accuracy_score(y_true: list[int], y_pred: list[int]) -> float:
    """Return the fraction of correct predictions."""
    if not y_true:
        return 0.0
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    return correct / len(y_true)


def f1_score(
    y_true: list[int],
    y_pred: list[int],
    average: str = "macro",
    zero_division: float = 0,
    labels: list[int] | None = None,
) -> float:
    """Compute F1 score.

    Supports macro averaging over the supplied label set (defaults to 0..9).
    """
    if average != "macro":
        raise ValueError("Only average='macro' is implemented")

    if labels is None:
        labels = list(range(NUM_CLASSES))

    per_class_f1: list[float] = []
    for c in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)

        precision = tp / (tp + fp) if (tp + fp) > 0 else zero_division
        recall = tp / (tp + fn) if (tp + fn) > 0 else zero_division

        if precision + recall == 0:
            per_class_f1.append(float(zero_division))
        else:
            per_class_f1.append(2 * precision * recall / (precision + recall))

    if not per_class_f1:
        return float(zero_division)
    return sum(per_class_f1) / len(per_class_f1)


# === Model loading ===
def load_existing_model(weights_path: str | Path):
    """Load MobileNetV2 + Linear(1280,10) from existing weights."""
    net = models.mobilenet_v2(weights=None)
    net.classifier[1] = nn.Linear(net.last_channel, NUM_CLASSES)
    net.load_state_dict(torch.load(weights_path, map_location="cpu"))
    return net


# === Dataset ===
class CropDataset(Dataset):
    """Loads crops from ImageFolder-style directory."""

    def __init__(self, records: list[dict], image_dir: str | Path, transform):
        self.records = records
        self.image_dir = Path(image_dir)
        self.transform = transform

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        rec = self.records[idx]
        label = int(rec.get("label_human") or rec.get("label") or rec.get("confirmed_label"))
        crop_id = rec["crop_id"]
        img_path = self.image_dir / str(label) / f"img_{crop_id}.png"
        img = Image.open(img_path).convert("L")
        return self.transform(img), label


# === WeightedRandomSampler ===
def make_sampler(labels: list[int]) -> WeightedRandomSampler:
    class_counts = Counter(labels)
    weights = [1.0 / class_counts[l] for l in labels]
    return WeightedRandomSampler(weights, num_samples=len(labels), replacement=True)


# === Model A: conservative ===
def build_model_a(net: nn.Module) -> nn.Module:
    """Freeze features, only train classifier head."""
    for param in net.features.parameters():
        param.requires_grad = False
    return net


# === Model B: aggressive ===
def build_model_b(net: nn.Module) -> nn.Module:
    """Unfreeze all, use discriminative LR groups."""
    for param in net.parameters():
        param.requires_grad = True
    return net


def get_discriminative_lr_groups(net: nn.Module) -> list[dict]:
    """3 groups: early(1e-5), mid(1e-4), late+classifier(1e-3)."""
    groups = [
        {"params": net.features[0:7].parameters(), "lr": 1e-5},
        {"params": net.features[7:14].parameters(), "lr": 1e-4},
        {"params": net.features[14:].parameters(), "lr": 1e-3},
        {"params": net.classifier.parameters(), "lr": 1e-3},
    ]
    return groups


# === Training loop ===
def train_model(
    net: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    max_epochs: int = MAX_EPOCHS,
    patience: int = 5,
    model_name: str = "A",
) -> tuple[list[dict], int]:
    """Train with early stopping based on val F1 macro.

    Returns:
        (history, best_epoch)
    """
    best_f1 = 0.0
    no_improve = 0
    best_epoch = 0
    history: list[dict] = []

    for epoch in range(max_epochs):
        t0 = time.time()
        # Train
        net.train()
        train_loss, train_acc, train_f1 = run_epoch(
            net, train_loader, optimizer, criterion, train=True
        )
        # Val
        net.eval()
        val_loss, val_acc, val_f1 = run_epoch(
            net, val_loader, None, criterion, train=False
        )
        elapsed = time.time() - t0
        epoch_data = {
            "epoch": epoch + 1,
            "train_loss": round(train_loss, 4),
            "train_acc": round(train_acc, 4),
            "train_f1": round(train_f1, 4),
            "val_loss": round(val_loss, 4),
            "val_acc": round(val_acc, 4),
            "val_f1": round(val_f1, 4),
            "time_sec": round(elapsed, 1),
        }
        history.append(epoch_data)
        print(
            f"  [{model_name}] Epoch {epoch + 1}: "
            f"val_f1={val_f1:.4f} val_acc={val_acc:.4f} ({elapsed:.1f}s)"
        )

        # Early stopping
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_epoch = epoch + 1
            no_improve = 0
            torch.save(
                net.state_dict(),
                f"models/digit_classifier_model_{model_name.lower()}.pth",
            )
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"  [{model_name}] Early stopping at epoch {epoch + 1}")
                break

    return history, best_epoch


def run_epoch(
    net: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer | None,
    criterion: nn.Module,
    train: bool = True,
) -> tuple[float, float, float]:
    """Run one epoch, return (loss, accuracy, f1_macro)."""
    all_preds: list[int] = []
    all_labels: list[int] = []
    total_loss = 0.0
    n_batches = 0

    for images, labels in loader:
        if train and optimizer is not None:
            optimizer.zero_grad()
        outputs = net(images)
        loss = criterion(outputs, labels)
        if train and optimizer is not None:
            loss.backward()
            optimizer.step()

        total_loss += loss.item()
        n_batches += 1
        preds = outputs.argmax(dim=1).tolist()
        all_preds.extend(preds)
        all_labels.extend(labels.tolist())

    avg_loss = total_loss / max(n_batches, 1)
    acc = accuracy_score(all_labels, all_preds)
    f1 = f1_score(all_labels, all_preds, average="macro", zero_division=0)
    return avg_loss, acc, f1


# === Main ===
def main() -> None:
    parser = argparse.ArgumentParser(description="Dual fine-tuning for digit classifier")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE)
    parser.add_argument("--max-epochs", type=int, default=MAX_EPOCHS)
    parser.add_argument(
        "--confirmed-manifest",
        type=str,
        default="data/labels/confirmed_manifest.jsonl",
    )
    parser.add_argument(
        "--train-pool-manifest",
        type=str,
        default="data/labels/train_pool_manifest.jsonl",
    )
    parser.add_argument(
        "--split-cache-dir",
        type=str,
        default="data/labels",
    )
    parser.add_argument(
        "--train-pool-dir",
        type=str,
        default="data/labels/train_pool",
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        default="models",
    )
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cpu")
    print(f"Device: {device}")

    # 1. Load split
    print("Loading train/val split...")
    test_crop_ids, train_records, val_records = load_or_create_split(
        args.confirmed_manifest,
        args.train_pool_manifest,
        args.split_cache_dir,
    )
    print(f"  test: {len(test_crop_ids)}  train: {len(train_records)}  val: {len(val_records)}")

    # 2. Create datasets + loaders + samplers
    train_pool_dir = Path(args.train_pool_dir)
    train_ds = CropDataset(train_records, train_pool_dir, get_transforms(train=True))
    val_ds = CropDataset(val_records, train_pool_dir, get_transforms(train=False))

    train_labels = [int(r.get("label_human") or r.get("label") or r.get("confirmed_label")) for r in train_records]
    train_sampler = make_sampler(train_labels)

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch,
        sampler=train_sampler,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch,
        shuffle=False,
        num_workers=0,
    )

    criterion = nn.CrossEntropyLoss()

    # 3. Train Model A (conservative)
    print("\nTraining Model A (conservative, frozen features)...")
    net_a = load_existing_model(MODEL_PATH)
    net_a = build_model_a(net_a)
    optimizer_a = torch.optim.Adam(net_a.classifier.parameters(), lr=1e-4)
    history_a, best_epoch_a = train_model(
        net_a,
        train_loader,
        val_loader,
        optimizer_a,
        criterion,
        max_epochs=args.max_epochs,
        patience=5,
        model_name="A",
    )

    # 4. Train Model B (aggressive)
    print("\nTraining Model B (aggressive, discriminative LR)...")
    net_b = load_existing_model(MODEL_PATH)
    net_b = build_model_b(net_b)
    optimizer_b = torch.optim.Adam(
        get_discriminative_lr_groups(net_b),
        lr=1e-3,
        weight_decay=1e-4,
    )
    history_b, best_epoch_b = train_model(
        net_b,
        train_loader,
        val_loader,
        optimizer_b,
        criterion,
        max_epochs=args.max_epochs,
        patience=3,
        model_name="B",
    )

    # 5. Save training history
    history = {
        "model_a": {
            "history": history_a,
            "epochs_to_convergence": best_epoch_a,
        },
        "model_b": {
            "history": history_b,
            "epochs_to_convergence": best_epoch_b,
        },
    }
    history_path = models_dir / "dual_train_history.json"
    with history_path.open("w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    print(f"\nHistory saved: {history_path}")
    print(f"  Model A best epoch: {best_epoch_a}")
    print(f"  Model B best epoch: {best_epoch_b}")


if __name__ == "__main__":
    main()
