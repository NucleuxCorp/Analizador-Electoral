"""Smoke + structure tests for train_dual_finetune.py."""
from __future__ import annotations

from collections import Counter

import pytest
import torch
import torch.nn as nn
from torch.utils.data import WeightedRandomSampler
from torchvision import models, transforms

import train_dual_finetune as dual
import train_digit_classifier


def test_module_imports():
    """Module loads without side effects."""
    assert dual.NUM_CLASSES == 10
    assert dual.BATCH_SIZE == 64
    assert dual.MAX_EPOCHS == 30
    assert dual.SEED == 42


def test_get_transforms_reused_from_train_script():
    """Transforms are imported unchanged from train_digit_classifier."""
    assert dual.get_transforms is train_digit_classifier.get_transforms
    tf = dual.get_transforms(train=False)
    assert isinstance(tf, transforms.Compose)
    assert len(tf.transforms) == 4
    assert isinstance(tf.transforms[0], transforms.Resize)
    assert isinstance(tf.transforms[1], transforms.Grayscale)
    assert isinstance(tf.transforms[2], transforms.ToTensor)
    assert isinstance(tf.transforms[3], transforms.Normalize)


def test_make_sampler_balances_classes():
    """WeightedRandomSampler weights are inversely proportional to class frequency."""
    labels = [0, 0, 1, 2, 2, 2]
    sampler = dual.make_sampler(labels)
    assert isinstance(sampler, WeightedRandomSampler)
    assert sampler.num_samples == len(labels)
    assert sampler.replacement is True

    counts = Counter(labels)
    expected_weights = [1.0 / counts[l] for l in labels]
    assert list(sampler.weights.tolist()) == expected_weights


def test_build_model_a_freezes_features():
    """Model A keeps feature extractor frozen and head trainable."""
    net = models.mobilenet_v2(weights=None)
    net.classifier[1] = nn.Linear(net.last_channel, 10)
    net = dual.build_model_a(net)

    for p in net.features.parameters():
        assert p.requires_grad is False
    for p in net.classifier.parameters():
        assert p.requires_grad is True


def test_build_model_b_unfreezes_all():
    """Model B leaves every parameter trainable."""
    net = models.mobilenet_v2(weights=None)
    net.classifier[1] = nn.Linear(net.last_channel, 10)
    net.features[0][0].weight.requires_grad = False  # simulate a frozen model
    net = dual.build_model_b(net)

    for p in net.parameters():
        assert p.requires_grad is True


def test_get_discriminative_lr_groups():
    """Model B optimizer groups have the expected LRs."""
    net = models.mobilenet_v2(weights=None)
    net.classifier[1] = nn.Linear(net.last_channel, 10)
    groups = dual.get_discriminative_lr_groups(net)

    assert len(groups) == 4
    assert groups[0]["lr"] == 1e-5
    assert groups[1]["lr"] == 1e-4
    assert groups[2]["lr"] == 1e-3
    assert groups[3]["lr"] == 1e-3

    # Each group must expose at least one parameter.
    for g in groups:
        params = list(g["params"])
        assert len(params) > 0
        assert all(isinstance(p, torch.nn.Parameter) for p in params)


def test_run_epoch_updates_only_with_train_true():
    """run_epoch must not change model weights when train=False."""
    net = models.mobilenet_v2(weights=None)
    net.classifier[1] = nn.Linear(net.last_channel, 10)
    criterion = nn.CrossEntropyLoss()

    # tiny synthetic dataset
    images = torch.randn(4, 3, 64, 64)
    labels = torch.tensor([0, 1, 2, 3])
    loader = [(images, labels)]

    before = {name: p.clone() for name, p in net.named_parameters()}
    loss, acc, f1 = dual.run_epoch(net, loader, None, criterion, train=False)
    after = {name: p.clone() for name, p in net.named_parameters()}

    assert 0.0 <= loss < float("inf")
    assert 0.0 <= acc <= 1.0
    assert 0.0 <= f1 <= 1.0
    for name in before:
        assert torch.equal(before[name], after[name])


def test_accuracy_score_and_f1_score_implemented():
    """Inline metric helpers replace sklearn and behave correctly."""
    y_true = [0, 0, 1, 1, 2, 2]
    y_pred = [0, 1, 1, 1, 2, 0]
    assert dual.accuracy_score(y_true, y_pred) == 4 / 6
    # Default macro average spans all 10 digit classes; classes 3-9 contribute 0.
    expected_macro_all_classes = (0.5 + 0.8 + 2 / 3) / 10
    assert abs(dual.f1_score(y_true, y_pred, average="macro") - expected_macro_all_classes) < 1e-9
    # Restricted label set gives the familiar 3-class macro average.
    expected_macro_three = (0.5 + 0.8 + 2 / 3) / 3
    assert (
        abs(dual.f1_score(y_true, y_pred, average="macro", labels=[0, 1, 2]) - expected_macro_three)
        < 1e-9
    )


def test_f1_score_zero_division():
    """Macro F1 returns zero_division when a class has no predictions."""
    y_true = [0, 0, 0]
    y_pred = [1, 1, 1]
    assert dual.f1_score(y_true, y_pred, average="macro", zero_division=0) == 0.0
