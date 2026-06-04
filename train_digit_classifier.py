"""
train_digit_classifier.py — Fine-tune MobileNetV2 on labeled digit crops.

Usage:
    python train_digit_classifier.py
    python train_digit_classifier.py --epochs 30 --batch 32
    python train_digit_classifier.py --colab  # paths adjusted for Google Drive

Output:
    models/digit_classifier.pth      — best model weights
    models/training_report.json      — accuracy + confusion matrix
"""
import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision import datasets, models, transforms
from torchvision.datasets import ImageFolder

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DIGITS_DIR  = Path("data/labels/digits")
MODELS_DIR  = Path("models")
MODEL_PATH  = MODELS_DIR / "digit_classifier.pth"
REPORT_PATH = MODELS_DIR / "training_report.json"

# Map variant folders → class 0
LABEL_MAP = {
    "asterisco": "0",
    "guion":     "0",
    "punto":     "0",
}

# Classes for the CNN (exactly 10 digits)
CLASSES = [str(i) for i in range(10)]


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

def build_dataset(digits_dir: Path, transform):
    """
    Load ImageFolder but:
      - merge variant folders (asterisco, guion, punto) into class 0
      - skip non-digit folders (_fullcell, enmiendas, etc.)
    """
    # Build a flat list: (img_path, class_idx)
    samples = []
    class_to_idx = {c: i for i, c in enumerate(CLASSES)}

    for folder in sorted(digits_dir.iterdir()):
        if not folder.is_dir():
            continue
        label = LABEL_MAP.get(folder.name, folder.name)
        if label not in class_to_idx:
            continue  # skip _fullcell, enmiendas, etc.
        idx = class_to_idx[label]
        for img in folder.glob("*.png"):
            samples.append((str(img), idx))

    return samples, class_to_idx


class DigitDataset(torch.utils.data.Dataset):
    def __init__(self, samples, transform=None):
        self.samples   = samples
        self.transform = transform
        from PIL import Image
        self._loader = Image.open

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        from PIL import Image
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label


# ---------------------------------------------------------------------------
# Augmentation
# ---------------------------------------------------------------------------

def get_transforms(train: bool):
    if train:
        return transforms.Compose([
            transforms.Resize((64, 64)),
            transforms.Grayscale(num_output_channels=3),
            transforms.RandomRotation(8),
            transforms.RandomAffine(0, translate=(0.10, 0.10), shear=5),
            transforms.RandomAffine(0, scale=(0.85, 1.15)),
            transforms.ColorJitter(brightness=0.25, contrast=0.25),
            transforms.GaussianBlur(3, sigma=(0.1, 1.0)),
            transforms.ToTensor(),
            transforms.Normalize([0.5]*3, [0.5]*3),
        ])
    return transforms.Compose([
        transforms.Resize((64, 64)),
        transforms.Grayscale(num_output_channels=3),
        transforms.ToTensor(),
        transforms.Normalize([0.5]*3, [0.5]*3),
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs",    type=int,   default=20)
    parser.add_argument("--batch",     type=int,   default=32)
    parser.add_argument("--lr",        type=float, default=1e-3)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument("--colab",     action="store_true")
    args = parser.parse_args()

    digits_dir = Path("/content/drive/MyDrive/e14c/data/labels/digits") if args.colab else DIGITS_DIR
    models_dir = Path("/content/drive/MyDrive/e14c/models")              if args.colab else MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Build dataset
    all_samples, class_to_idx = build_dataset(digits_dir, None)
    print(f"Total samples: {len(all_samples)}")

    from collections import Counter
    dist = Counter(lbl for _, lbl in all_samples)
    for c, name in enumerate(CLASSES):
        print(f"  class {name}: {dist[c]}")

    # Train / val split
    import random
    random.seed(42)
    random.shuffle(all_samples)
    split = int(len(all_samples) * (1 - args.val_split))
    train_samples = all_samples[:split]
    val_samples   = all_samples[split:]
    print(f"Train: {len(train_samples)}  Val: {len(val_samples)}")

    train_ds = DigitDataset(train_samples, get_transforms(train=True))
    val_ds   = DigitDataset(val_samples,   get_transforms(train=False))

    # Weighted sampler to balance classes
    train_labels = [lbl for _, lbl in train_samples]
    class_counts = Counter(train_labels)
    weights = [1.0 / class_counts[lbl] for lbl in train_labels]
    sampler = WeightedRandomSampler(weights, num_samples=len(weights), replacement=True)

    train_loader = DataLoader(train_ds, batch_size=args.batch, sampler=sampler,   num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=args.batch, shuffle=False, num_workers=0)

    # Model — MobileNetV2, replace classifier head
    model = models.mobilenet_v2(weights=models.MobileNet_V2_Weights.IMAGENET1K_V1)
    model.classifier[1] = nn.Linear(model.last_channel, 10)
    model = model.to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=7, gamma=0.3)
    criterion = nn.CrossEntropyLoss()

    best_acc = 0.0
    history  = []

    print(f"\nTraining MobileNetV2 for {args.epochs} epochs...\n")

    for epoch in range(1, args.epochs + 1):
        # Train
        model.train()
        t0 = time.time()
        train_loss, train_correct, train_total = 0.0, 0, 0
        for imgs, labels in train_loader:
            imgs, labels = imgs.to(device), labels.to(device)
            optimizer.zero_grad()
            out  = model(imgs)
            loss = criterion(out, labels)
            loss.backward()
            optimizer.step()
            train_loss    += loss.item() * imgs.size(0)
            preds          = out.argmax(1)
            train_correct += (preds == labels).sum().item()
            train_total   += imgs.size(0)
        scheduler.step()

        # Validate
        model.eval()
        val_correct, val_total = 0, 0
        all_preds, all_labels  = [], []
        with torch.no_grad():
            for imgs, labels in val_loader:
                imgs, labels = imgs.to(device), labels.to(device)
                out   = model(imgs)
                preds = out.argmax(1)
                val_correct += (preds == labels).sum().item()
                val_total   += imgs.size(0)
                all_preds.extend(preds.cpu().tolist())
                all_labels.extend(labels.cpu().tolist())

        train_acc = train_correct / train_total * 100
        val_acc   = val_correct   / val_total   * 100
        elapsed   = time.time() - t0

        print(f"Epoch {epoch:2d}/{args.epochs}  "
              f"loss={train_loss/train_total:.4f}  "
              f"train={train_acc:.1f}%  "
              f"val={val_acc:.1f}%  "
              f"({elapsed:.0f}s)")

        history.append({"epoch": epoch, "train_acc": round(train_acc, 2), "val_acc": round(val_acc, 2)})

        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), str(models_dir / "digit_classifier.pth"))
            print(f"  -> Best model saved ({val_acc:.1f}%)")

    # Confusion matrix
    confusion = [[0]*10 for _ in range(10)]
    for pred, true in zip(all_preds, all_labels):
        confusion[true][pred] += 1

    report = {
        "best_val_acc": round(best_acc, 2),
        "epochs": args.epochs,
        "samples": {"train": len(train_samples), "val": len(val_samples)},
        "class_distribution": {CLASSES[c]: dist[c] for c in range(10)},
        "history": history,
        "confusion_matrix": confusion,
        "classes": CLASSES,
    }

    with open(str(models_dir / "training_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nBest val accuracy: {best_acc:.1f}%")
    print(f"Model: {models_dir / 'digit_classifier.pth'}")
    print(f"Report: {models_dir / 'training_report.json'}")


if __name__ == "__main__":
    main()
