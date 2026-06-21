#!/usr/bin/env python3
"""Compare Model A vs Model B against the confirmed test set."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from split_dataset import load_manifest, load_or_create_split
from train_dual_finetune import CropDataset, get_transforms, load_existing_model

MODEL_A_PATH = "models/digit_classifier_model_a.pth"
MODEL_B_PATH = "models/digit_classifier_model_b.pth"
PRODUCTION_MODEL_PATH = "models/digit_classifier.pth"
TEST_DIR = Path("data/labels/confirmed")
HISTORY_PATH = "models/dual_train_history.json"
REPORT_JSON = "models/dual_finetune_report.json"
REPORT_TXT = "models/dual_finetune_report.txt"


def compute_metrics(all_preds: list[int], all_labels: list[int]) -> dict:
    """Calculate accuracy, per-class F1, macro F1, and confusion matrix.

    Metrics are computed inline because ``sklearn`` is not a project dependency.
    """
    total = len(all_labels)
    accuracy = sum(p == l for p, l in zip(all_preds, all_labels)) / total if total else 0.0

    f1_per_class: dict[int, float] = {}
    for cls in range(10):
        tp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls and l == cls)
        fp = sum(1 for p, l in zip(all_preds, all_labels) if p == cls and l != cls)
        fn = sum(1 for p, l in zip(all_preds, all_labels) if p != cls and l == cls)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision + recall == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        f1_per_class[cls] = round(f1, 4)

    f1_macro = sum(f1_per_class.values()) / 10

    confusion = [[0] * 10 for _ in range(10)]
    for pred, label in zip(all_preds, all_labels):
        confusion[label][pred] += 1

    return {
        "accuracy": round(accuracy, 4),
        "f1_macro": round(f1_macro, 4),
        "f1_per_class": f1_per_class,
        "confusion_matrix": confusion,
        "total_samples": total,
    }


def evaluate_model(model_path: str | Path, test_records: list[dict], test_dir: Path, transform) -> dict:
    """Load model, evaluate against test set, return metrics dict."""
    net = load_existing_model(model_path)
    net.eval()

    test_ds = CropDataset(test_records, test_dir, transform)
    test_loader = DataLoader(test_ds, batch_size=64, shuffle=False, num_workers=0)

    all_preds: list[int] = []
    all_labels: list[int] = []
    with torch.no_grad():
        for images, labels in test_loader:
            outputs = net(images)
            preds = outputs.argmax(dim=1).tolist()
            all_preds.extend(preds)
            all_labels.extend(labels.tolist())

    return compute_metrics(all_preds, all_labels)


def recommend_model(metrics_a: dict, metrics_b: dict) -> tuple[str, str]:
    """Recommend a model based on the combined accuracy + F1 macro score."""
    score_a = metrics_a["accuracy"] + metrics_a["f1_macro"]
    score_b = metrics_b["accuracy"] + metrics_b["f1_macro"]

    if score_a > score_b:
        rec = "Model A (conservative)"
    elif score_b > score_a:
        rec = "Model B (aggressive)"
    else:
        rec = "Tie"

    justification = (
        f"Combined score = accuracy + F1 macro. "
        f"Model A: accuracy={metrics_a['accuracy']:.4f}, f1_macro={metrics_a['f1_macro']:.4f} "
        f"(score={score_a:.4f}); "
        f"Model B: accuracy={metrics_b['accuracy']:.4f}, f1_macro={metrics_b['f1_macro']:.4f} "
        f"(score={score_b:.4f})."
    )
    return rec, justification


def _format_confusion_matrix(matrix: list[list[int]]) -> list[str]:
    """Render a 10x10 confusion matrix as aligned text lines."""
    max_val = max(max(row) for row in matrix) if matrix else 0
    width = max(3, len(str(max_val)))
    header = " " * 4 + " ".join(f"{j:>{width}}" for j in range(10))
    lines = [header]
    for i, row in enumerate(matrix):
        lines.append(f"{i:>2} |" + " ".join(f"{v:>{width}}" for v in row))
    return lines


def generate_report(metrics_a: dict, metrics_b: dict, history: dict) -> str:
    """Generate a human-readable text report comparing both models."""
    epochs_a = history.get("model_a", {}).get("epochs_to_convergence", "?")
    epochs_b = history.get("model_b", {}).get("epochs_to_convergence", "?")
    rec, justification = recommend_model(metrics_a, metrics_b)

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("REPORTE COMPARATIVO: Model A vs Model B")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Test set: {metrics_a['total_samples']} crops confirmed")
    lines.append("")
    lines.append("METRICAS GENERALES:")
    lines.append(f"  {'Metrica':<20} {'Model A':>10} {'Model B':>10}")
    lines.append(
        f"  {'Accuracy':<20} {metrics_a['accuracy'] * 100:>9.2f}% {metrics_b['accuracy'] * 100:>9.2f}%"
    )
    lines.append(
        f"  {'F1 Macro':<20} {metrics_a['f1_macro'] * 100:>9.2f}% {metrics_b['f1_macro'] * 100:>9.2f}%"
    )
    lines.append(f"  {'Epocas convergencia':<20} {epochs_a:>10} {epochs_b:>10}")
    lines.append("")
    lines.append("F1 POR CLASE:")
    lines.append(f"  {'Clase':<10} {'Model A':>10} {'Model B':>10}")
    for cls in range(10):
        lines.append(
            f"  {cls:<10} {metrics_a['f1_per_class'][cls] * 100:>9.2f}% {metrics_b['f1_per_class'][cls] * 100:>9.2f}%"
        )
    lines.append("")
    lines.append("MATRIZ DE CONFUSION (Model A):")
    lines.extend(_format_confusion_matrix(metrics_a["confusion_matrix"]))
    lines.append("")
    lines.append("MATRIZ DE CONFUSION (Model B):")
    lines.extend(_format_confusion_matrix(metrics_b["confusion_matrix"]))
    lines.append("")
    lines.append(f"RECOMENDACION: {rec}")
    lines.append(f"Justificacion: {justification}")
    lines.append("")
    lines.append("NOTA: Este reporte NO reemplaza digit_classifier.pth automaticamente.")
    lines.append("Para promover un modelo, copiar manualmente el .pth a digit_classifier.pth")
    lines.append("=" * 60)

    return "\n".join(lines)


def build_report_data(metrics_a: dict, metrics_b: dict, history: dict) -> dict:
    """Build the machine-readable report payload."""
    rec, justification = recommend_model(metrics_a, metrics_b)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_set_size": metrics_a["total_samples"],
        "model_a": {
            "path": MODEL_A_PATH,
            "accuracy": metrics_a["accuracy"],
            "f1_macro": metrics_a["f1_macro"],
            "f1_per_class": metrics_a["f1_per_class"],
            "confusion_matrix": metrics_a["confusion_matrix"],
            "epochs_to_convergence": history.get("model_a", {}).get("epochs_to_convergence"),
        },
        "model_b": {
            "path": MODEL_B_PATH,
            "accuracy": metrics_b["accuracy"],
            "f1_macro": metrics_b["f1_macro"],
            "f1_per_class": metrics_b["f1_per_class"],
            "confusion_matrix": metrics_b["confusion_matrix"],
            "epochs_to_convergence": history.get("model_b", {}).get("epochs_to_convergence"),
        },
        "recommendation": {
            "recommended_model": rec,
            "justification": justification,
            "combined_score": {
                "model_a": round(metrics_a["accuracy"] + metrics_a["f1_macro"], 4),
                "model_b": round(metrics_b["accuracy"] + metrics_b["f1_macro"], 4),
            },
        },
        "note": "Production model (digit_classifier.pth) was not modified.",
    }


def _file_hash(path: Path) -> str:
    """Return MD5 hash of a file; empty string if the file does not exist."""
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare fine-tuned digit classifier models")
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
    parser.add_argument("--split-cache-dir", type=str, default="data/labels")
    parser.add_argument("--test-dir", type=str, default=str(TEST_DIR))
    parser.add_argument("--models-dir", type=str, default="models")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    production_path = Path(PRODUCTION_MODEL_PATH)
    production_hash_before = _file_hash(production_path)

    print("Loading test set...")
    test_crop_ids, _train, _val = load_or_create_split(
        args.confirmed_manifest,
        args.train_pool_manifest,
        args.split_cache_dir,
    )
    test_ids_set = set(test_crop_ids)
    confirmed_records = load_manifest(args.confirmed_manifest)
    test_records = [r for r in confirmed_records if r.get("crop_id") in test_ids_set]
    print(f"  Test records: {len(test_records)}")

    print("Loading training history...")
    history_path = models_dir / "dual_train_history.json"
    if history_path.exists():
        with history_path.open("r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {}
        print(f"  Warning: history not found at {history_path}")

    test_dir = Path(args.test_dir)
    transform = get_transforms(train=False)

    print("\nEvaluating Model A (conservative)...")
    t0 = time.time()
    metrics_a = evaluate_model(models_dir / "digit_classifier_model_a.pth", test_records, test_dir, transform)
    print(f"  Accuracy: {metrics_a['accuracy']:.4f}  F1 macro: {metrics_a['f1_macro']:.4f}  ({time.time() - t0:.1f}s)")

    print("\nEvaluating Model B (aggressive)...")
    t0 = time.time()
    metrics_b = evaluate_model(models_dir / "digit_classifier_model_b.pth", test_records, test_dir, transform)
    print(f"  Accuracy: {metrics_b['accuracy']:.4f}  F1 macro: {metrics_b['f1_macro']:.4f}  ({time.time() - t0:.1f}s)")

    print("\nGenerating report...")
    report_data = build_report_data(metrics_a, metrics_b, history)
    report_text = generate_report(metrics_a, metrics_b, history)

    report_json_path = models_dir / "dual_finetune_report.json"
    with report_json_path.open("w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    report_txt_path = models_dir / "dual_finetune_report.txt"
    with report_txt_path.open("w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"Report saved: {report_json_path}")
    print(f"Report saved: {report_txt_path}")

    production_hash_after = _file_hash(production_path)
    if production_hash_before and production_hash_after != production_hash_before:
        raise RuntimeError("Production model digit_classifier.pth was modified during comparison.")
    print("\nProduction model unchanged.")
    print(report_text)


if __name__ == "__main__":
    main()
