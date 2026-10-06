from __future__ import annotations

import argparse
import os
import random
import time

import torch
import wandb
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

IMAGE_SIZE = 64
CHANNELS = 3
DEFAULT_EPOCHS = 10
PROVENANCE_VARIABLES = (
    "HIRAKU_RECORD_ID",
    "HIRAKU_COMMIT",
    "HIRAKU_GIT_STATE",
    "HIRAKU_ENTRYPOINT",
    "HIRAKU_ROOT",
)


class FlattenedPixelMLP(nn.Module):
    """One hidden layer over flattened RGB pixels."""

    def __init__(self, num_classes: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Flatten(),
            nn.Linear(CHANNELS * IMAGE_SIZE * IMAGE_SIZE, 128),
            nn.ReLU(),
            nn.Linear(128, num_classes),
        )

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return self.layers(images)


class ShallowCNN(nn.Module):
    """Two convolutional layers with local connectivity and pooling."""

    def __init__(self, num_classes: int) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(CHANNELS, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(kernel_size=2),
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Flatten(),
        )
        self.classifier = nn.Linear(32, num_classes)

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(images))


def build_model(model_name: str, num_classes: int) -> nn.Module:
    if model_name == "mlp":
        return FlattenedPixelMLP(num_classes)
    if model_name == "shallow_cnn":
        return ShallowCNN(num_classes)
    raise ValueError(f"Unsupported model: {model_name}")


def stratified_indices(
    targets: list[int], validation_fraction: float, seed: int
) -> tuple[list[int], list[int]]:
    grouped: dict[int, list[int]] = {}
    for index, target in enumerate(targets):
        grouped.setdefault(target, []).append(index)

    rng = random.Random(seed)
    train_indices: list[int] = []
    validation_indices: list[int] = []
    for indices in grouped.values():
        rng.shuffle(indices)
        validation_count = max(1, round(len(indices) * validation_fraction))
        validation_indices.extend(indices[:validation_count])
        train_indices.extend(indices[validation_count:])
    rng.shuffle(train_indices)
    rng.shuffle(validation_indices)
    return train_indices, validation_indices


def provenance() -> dict[str, str]:
    missing = [name for name in PROVENANCE_VARIABLES if name not in os.environ]
    if missing:
        raise RuntimeError(f"Missing Hiraku provenance variables: {', '.join(missing)}")
    return {name: os.environ[name] for name in PROVENANCE_VARIABLES}


def train_one_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        optimizer.zero_grad(set_to_none=True)
        logits = model(images)
        loss = criterion(logits, labels)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * labels.size(0)
        correct += (logits.argmax(dim=1) == labels).sum().item()
        total += labels.size(0)

    return total_loss / total, correct / total


def evaluate(
    model: nn.Module, loader: DataLoader, criterion: nn.Module
) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    with torch.inference_mode():
        for images, labels in loader:
            logits = model(images)
            total_loss += criterion(logits, labels).item() * labels.size(0)
            correct += (logits.argmax(dim=1) == labels).sum().item()
            total += labels.size(0)
    return total_loss / total, correct / total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["mlp", "shallow_cnn"], required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.epochs < 1:
        raise ValueError("--epochs must be positive")
    if not 0 < args.validation_fraction < 1:
        raise ValueError("--validation-fraction must be between 0 and 1")
    if args.batch_size < 1 or args.learning_rate <= 0:
        raise ValueError("batch size and learning rate must be positive")

    attribution = provenance()
    with wandb.init(
        entity=os.environ.get("WANDB_ENTITY") or None,
        project=os.environ.get("WANDB_PROJECT", "eurosat-rgb"),
        group=attribution["HIRAKU_RECORD_ID"],
        name=os.environ.get("WANDB_RUN_NAME"),
        tags=["hiraku", "eurosat-rgb", args.model],
        config={
            "hiraku.record_id": attribution["HIRAKU_RECORD_ID"],
            "hiraku.commit": attribution["HIRAKU_COMMIT"],
            "hiraku.git_state": attribution["HIRAKU_GIT_STATE"],
            "hiraku.entrypoint": attribution["HIRAKU_ENTRYPOINT"],
            "dataset.name": "EuroSAT RGB",
            "dataset.classes": 10,
            "dataset.image_size": IMAGE_SIZE,
            "model": args.model,
            "seed": args.seed,
            "validation_fraction": args.validation_fraction,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
        },
    ) as run:
        try:
            _train_and_log(args, run)
        except Exception:
            run.summary["status"] = "failed"
            raise


def _train_and_log(args: argparse.Namespace, run: wandb.sdk.wandb_run.Run) -> None:
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    dataset = datasets.EuroSAT(
        root="data", download=True, transform=transforms.ToTensor()
    )
    train_indices, validation_indices = stratified_indices(
        dataset.targets, args.validation_fraction, args.seed
    )
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(
        Subset(dataset, train_indices),
        batch_size=args.batch_size,
        shuffle=True,
        generator=generator,
    )
    validation_loader = DataLoader(
        Subset(dataset, validation_indices),
        batch_size=args.batch_size,
        shuffle=False,
    )

    model = build_model(args.model, len(dataset.classes)).to("cpu")
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    criterion = nn.CrossEntropyLoss()
    model_parameters = sum(parameter.numel() for parameter in model.parameters())
    for epoch in range(1, args.epochs + 1):
        epoch_started = time.process_time()
        train_loss, train_accuracy = train_one_epoch(
            model, train_loader, optimizer, criterion
        )
        train_cpu_seconds = time.process_time() - epoch_started
        validation_loss, validation_accuracy = evaluate(
            model, validation_loader, criterion
        )
        metrics = {
            "epoch": epoch,
            "train/loss": train_loss,
            "train/accuracy": train_accuracy,
            "val/loss": validation_loss,
            "val/accuracy": validation_accuracy,
            "train/cpu_seconds": train_cpu_seconds,
            "model/parameters": model_parameters,
        }
        run.log(metrics, step=epoch)
        print(
            f"epoch={epoch} model={args.model} train_loss={train_loss:.4f} "
            f"train_accuracy={train_accuracy:.4f} val_loss={validation_loss:.4f} "
            f"val_accuracy={validation_accuracy:.4f}"
        )

    run.summary["status"] = "completed"


if __name__ == "__main__":
    main()
