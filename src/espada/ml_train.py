from __future__ import annotations

import argparse
import csv
import inspect
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from .ml_metrics import ProbabilityHistogram
from .ml_model import (
    BCEFocalTverskyLoss,
    BinaryConfusion,
    ModelConfig,
    build_segmentation_model,
)
from .ml_preprocess import (
    SCENE_CENTERED_S1_VV,
    db_to_unit,
    model_input_from_db,
    normalization_metadata,
    prepare_scene_db,
    unit_to_db,
)


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 26143
    epochs: int = 40
    batch_size: int = 2
    patch_size: int = 320
    training_stride: int = 192
    validation_stride: int = 240
    learning_rate: float = 2e-4
    weight_decay: float = 1e-4
    positive_weight: float = 2.5
    negative_patch_ratio: float = 4.0
    hard_negative_fraction: float = 0.75
    minimum_oil_pixels: int = 64
    patience: int = 10
    encoder: str = "resnet50"
    encoder_initialization: str = "SSL4EO-S12 MoCo Sentinel-1"
    encoder_checkpoint: str | None = None
    pretrained_encoder: bool = True
    attention_decoder: bool = True
    normalization_mode: str = SCENE_CENTERED_S1_VV
    augmentation_backend: str = "albumentations"


def tile_positions(length: int, patch_size: int, stride: int) -> list[int]:
    if length < patch_size:
        raise ValueError(f"Scene dimension {length} is smaller than patch size {patch_size}")
    positions = list(range(0, length - patch_size + 1, stride))
    final = length - patch_size
    if positions[-1] != final:
        positions.append(final)
    return positions


def _albumentations_pipeline(seed: int):
    try:
        import albumentations as albumentations
    except ImportError as exc:
        raise RuntimeError(
            "Albumentations is required for V4. Run scripts/setup_ml_v4.ps1 first."
        ) from exc
    brightness_parameters = inspect.signature(
        albumentations.RandomBrightnessContrast
    ).parameters
    brightness_arguments = (
        {"brightness_range": (-0.12, 0.12), "contrast_range": (-0.18, 0.18)}
        if "brightness_range" in brightness_parameters
        else {"brightness_limit": 0.12, "contrast_limit": 0.18}
    )
    compose_arguments: dict[str, object] = {"seed": seed}
    if "telemetry" in inspect.signature(albumentations.Compose).parameters:
        compose_arguments["telemetry"] = False
    return albumentations.Compose(
        [
            albumentations.HorizontalFlip(p=0.5),
            albumentations.VerticalFlip(p=0.5),
            albumentations.RandomRotate90(p=0.75),
            albumentations.RandomBrightnessContrast(p=0.55, **brightness_arguments),
            albumentations.OneOf(
                [
                    albumentations.GaussNoise(std_range=(0.01, 0.05), p=1.0),
                    albumentations.MultiplicativeNoise(
                        multiplier=(0.9, 1.1), per_channel=False, elementwise=True, p=1.0
                    ),
                ],
                p=0.45,
            ),
            albumentations.GaussianBlur(blur_limit=(3, 5), p=0.12),
        ],
        **compose_arguments,
    )


class SarPatchDataset(Dataset):
    def __init__(
        self,
        dataset_root: Path,
        manifest_path: Path,
        split: str,
        *,
        patch_size: int,
        stride: int,
        seed: int,
        minimum_oil_pixels: int = 64,
        negative_patch_ratio: float = 2.0,
        hard_negative_fraction: float = 0.0,
        normalization_mode: str = SCENE_CENTERED_S1_VV,
        augmentation_backend: str = "torch",
        augment: bool = False,
        limit: int | None = None,
    ) -> None:
        self.dataset_root = Path(dataset_root)
        self.patch_size = patch_size
        self.augment = augment
        self.normalization_mode = normalization_mode
        self.augmentation_backend = augmentation_backend
        self.albumentations_transform = None
        if augment and augmentation_backend == "albumentations":
            self.albumentations_transform = _albumentations_pipeline(seed)
        elif augment and augmentation_backend != "torch":
            raise ValueError(f"Unsupported augmentation backend: {augmentation_backend}")
        self._cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        with Path(manifest_path).open("r", encoding="utf-8-sig", newline="") as stream:
            self.scenes = [row for row in csv.DictReader(stream) if row["split"] == split]
        if not self.scenes:
            raise ValueError(f"Manifest contains no scenes for split '{split}'")

        positive: list[tuple[int, int, int]] = []
        background: list[tuple[tuple[int, int, int], float]] = []
        all_patches: list[tuple[int, int, int]] = []
        for scene_index, scene in enumerate(self.scenes):
            with Image.open(self.dataset_root / scene["mask_path"]) as mask_file:
                mask = np.asarray(mask_file, dtype=np.float32)
            image = None
            if split == "train" and hard_negative_fraction:
                with Image.open(self.dataset_root / scene["image_path"]) as image_file:
                    image = prepare_scene_db(
                        np.asarray(image_file, dtype=np.float32), self.normalization_mode
                    )
            for y in tile_positions(mask.shape[0], patch_size, stride):
                for x in tile_positions(mask.shape[1], patch_size, stride):
                    item = (scene_index, x, y)
                    all_patches.append(item)
                    oil_pixels = int(np.count_nonzero(mask[y : y + patch_size, x : x + patch_size]))
                    if oil_pixels >= minimum_oil_pixels:
                        positive.append(item)
                    elif oil_pixels == 0:
                        hardness = (
                            float(image[y : y + patch_size, x : x + patch_size].mean())
                            if image is not None
                            else 0.0
                        )
                        background.append((item, hardness))

        if split == "train":
            if not positive:
                raise ValueError("Training split contains no positive oil patches")
            rng = random.Random(seed)
            negative_count = min(len(background), round(len(positive) * negative_patch_ratio))
            hard_count = min(negative_count, round(negative_count * hard_negative_fraction))
            ranked_background = sorted(background, key=lambda item: item[1])
            hard_background = [item for item, _ in ranked_background[:hard_count]]
            remaining_background = [item for item, _ in ranked_background[hard_count:]]
            rng.shuffle(remaining_background)
            selected_background = hard_background + remaining_background[: negative_count - hard_count]
            self.patches = positive + selected_background
            rng.shuffle(self.patches)
        else:
            self.patches = all_patches
        if limit is not None:
            self.patches = self.patches[:limit]
        if not self.patches:
            raise ValueError(f"No patches were generated for split '{split}'")

    def __len__(self) -> int:
        return len(self.patches)

    def _scene_arrays(self, scene_index: int) -> tuple[np.ndarray, np.ndarray]:
        scene = self.scenes[scene_index]
        scene_id = scene["scene_id"]
        if scene_id not in self._cache:
            with Image.open(self.dataset_root / scene["image_path"]) as image_file:
                image = prepare_scene_db(
                    np.asarray(image_file, dtype=np.float32), self.normalization_mode
                )
            with Image.open(self.dataset_root / scene["mask_path"]) as mask_file:
                mask = np.asarray(mask_file, dtype=np.float32).copy()
            self._cache[scene_id] = image, mask
        return self._cache[scene_id]

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        scene_index, x, y = self.patches[index]
        image, mask = self._scene_arrays(scene_index)
        size = self.patch_size
        image_patch = image[y : y + size, x : x + size].copy()
        mask_patch = mask[y : y + size, x : x + size].copy()
        if self.albumentations_transform is not None:
            augmented = self.albumentations_transform(
                image=db_to_unit(image_patch)[..., None], mask=mask_patch
            )
            augmented_image = np.asarray(augmented["image"])
            if augmented_image.ndim == 3:
                augmented_image = augmented_image[..., 0]
            image_patch = unit_to_db(augmented_image)
            mask_patch = np.asarray(augmented["mask"], dtype=np.float32)
        if self.augment and self.augmentation_backend == "torch":
            unit_tensor = torch.from_numpy(db_to_unit(image_patch)).unsqueeze(0)
            mask_tensor = torch.from_numpy(mask_patch).unsqueeze(0)
            if torch.rand(()) < 0.5:
                unit_tensor = torch.flip(unit_tensor, dims=(-1,))
                mask_tensor = torch.flip(mask_tensor, dims=(-1,))
            if torch.rand(()) < 0.5:
                unit_tensor = torch.flip(unit_tensor, dims=(-2,))
                mask_tensor = torch.flip(mask_tensor, dims=(-2,))
            rotations = int(torch.randint(0, 4, ()).item())
            unit_tensor = torch.rot90(unit_tensor, rotations, dims=(-2, -1))
            mask_tensor = torch.rot90(mask_tensor, rotations, dims=(-2, -1))
            gain = 0.9 + 0.2 * torch.rand(())
            offset = -0.05 + 0.1 * torch.rand(())
            unit_tensor = torch.clamp(unit_tensor * gain + offset, 0.0, 1.0)
            if torch.rand(()) < 0.35:
                unit_tensor = torch.clamp(
                    unit_tensor + 0.015 * torch.randn_like(unit_tensor), 0.0, 1.0
                )
            image_patch = unit_to_db(unit_tensor.squeeze(0).numpy())
            mask_patch = mask_tensor.squeeze(0).numpy()
        image_tensor = torch.from_numpy(
            model_input_from_db(image_patch, self.normalization_mode)
        ).unsqueeze(0)
        mask_tensor = torch.from_numpy(mask_patch).unsqueeze(0)
        return image_tensor.contiguous(), mask_tensor.contiguous()


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _run_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    loss_function: torch.nn.Module,
    device: torch.device,
    *,
    optimizer: torch.optim.Optimizer | None,
    scaler: torch.amp.GradScaler,
) -> tuple[float, dict]:
    training = optimizer is not None
    model.train(training)
    confusion = BinaryConfusion()
    probability_histogram = ProbabilityHistogram(bins=500)
    loss_sum = 0.0
    batches = 0
    for image, mask in loader:
        image = image.to(device, non_blocking=True)
        mask = mask.to(device, non_blocking=True)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            with torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
                logits = model(image)
                loss = loss_function(logits, mask)
            if training:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
        confusion.update(logits.detach(), mask)
        probability_histogram.update(
            torch.sigmoid(logits.detach()).float().cpu().numpy(),
            mask.detach().float().cpu().numpy(),
        )
        loss_sum += float(loss.detach().item())
        batches += 1
    metrics = confusion.metrics()
    metrics["loss"] = loss_sum / max(batches, 1)
    metrics["average_precision"] = probability_histogram.average_precision()
    return float(metrics["loss"]), metrics


def train_model(
    dataset_root: Path,
    manifest_path: Path,
    output_dir: Path,
    config: TrainingConfig | None = None,
    *,
    max_train_patches: int | None = None,
    max_validation_patches: int | None = None,
) -> dict:
    config = config or TrainingConfig()
    _set_seed(config.seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    train_data = SarPatchDataset(
        dataset_root,
        manifest_path,
        "train",
        patch_size=config.patch_size,
        stride=config.training_stride,
        seed=config.seed,
        minimum_oil_pixels=config.minimum_oil_pixels,
        negative_patch_ratio=config.negative_patch_ratio,
        hard_negative_fraction=config.hard_negative_fraction,
        normalization_mode=config.normalization_mode,
        augmentation_backend=config.augmentation_backend,
        augment=True,
        limit=max_train_patches,
    )
    validation_data = SarPatchDataset(
        dataset_root,
        manifest_path,
        "validation",
        patch_size=config.patch_size,
        stride=config.validation_stride,
        seed=config.seed,
        normalization_mode=config.normalization_mode,
        augment=False,
        limit=max_validation_patches,
    )
    train_loader = DataLoader(
        train_data,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )
    validation_loader = DataLoader(
        validation_data,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=device.type == "cuda",
    )

    if config.pretrained_encoder and config.encoder == "resnet50" and not config.encoder_checkpoint:
        raise ValueError("V4 requires the downloaded SSL4EO-S12 Sentinel-1 encoder checkpoint")
    model_configuration = ModelConfig(
        architecture="ResNet50 U-Net" if config.encoder == "resnet50" else "ResNet34 U-Net",
        encoder=config.encoder,
        encoder_initialization=(
            config.encoder_initialization if config.pretrained_encoder else "random"
        ),
        pretrained_encoder=config.pretrained_encoder,
        attention_decoder=config.attention_decoder,
    )
    model = build_segmentation_model(
        model_configuration.to_dict(),
        pretrained_encoder=config.pretrained_encoder,
        encoder_checkpoint=Path(config.encoder_checkpoint) if config.encoder_checkpoint else None,
    ).to(device)
    loss_function = BCEFocalTverskyLoss(positive_weight=config.positive_weight).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=2, min_lr=1e-6
    )
    scaler = torch.amp.GradScaler(device.type, enabled=device.type == "cuda")
    history: list[dict] = []
    best_average_precision = -1.0
    stale_epochs = 0
    checkpoint_path = output_dir / "sar_segmentation_best.pt"

    for epoch in range(1, config.epochs + 1):
        _, training_metrics = _run_epoch(
            model, train_loader, loss_function, device, optimizer=optimizer, scaler=scaler
        )
        with torch.no_grad():
            _, validation_metrics = _run_epoch(
                model,
                validation_loader,
                loss_function,
                device,
                optimizer=None,
                scaler=scaler,
            )
        row = {
            "epoch": epoch,
            "training_loss": training_metrics["loss"],
            "training_iou": training_metrics["iou"],
            "training_dice_f1": training_metrics["dice_f1"],
            "training_average_precision": training_metrics["average_precision"],
            "validation_loss": validation_metrics["loss"],
            "validation_iou": validation_metrics["iou"],
            "validation_dice_f1": validation_metrics["dice_f1"],
            "validation_precision": validation_metrics["precision"],
            "validation_recall": validation_metrics["recall"],
            "validation_average_precision": validation_metrics["average_precision"],
            "learning_rate": float(optimizer.param_groups[0]["lr"]),
        }
        history.append(row)
        print(json.dumps(row), flush=True)
        scheduler.step(float(validation_metrics["average_precision"]))
        if float(validation_metrics["average_precision"]) > best_average_precision:
            best_average_precision = float(validation_metrics["average_precision"])
            stale_epochs = 0
            torch.save(
                {
                    "format_version": 1,
                    "model_state_dict": model.state_dict(),
                    "model_config": model_configuration.to_dict(),
                    "training_config": asdict(config),
                    "epoch": epoch,
                    "threshold": 0.5,
                    "validation_metrics": validation_metrics,
                    "selection_metric": "validation_average_precision",
                    "normalization": normalization_metadata(config.normalization_mode),
                    "dataset_doi": "10.5281/zenodo.4672426",
                },
                checkpoint_path,
            )
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break

    history_path = output_dir / "training_history.csv"
    with history_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    best = max(history, key=lambda item: item["validation_average_precision"])
    result = {
        "status": "PASS",
        "run_type": "smoke" if max_train_patches else "full_training",
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "architecture": f"attention-gated {model_configuration.architecture}",
        "encoder_initialization": model_configuration.encoder_initialization,
        "normalization": normalization_metadata(config.normalization_mode),
        "augmentation": config.augmentation_backend,
        "loss": "class-balanced BCE + false-alarm-aware focal Tversky",
        "training_patches": len(train_data),
        "validation_patches": len(validation_data),
        "epochs_completed": len(history),
        "checkpoint_selection": "highest validation average precision (threshold-independent)",
        "best_validation": best,
        "checkpoint": str(checkpoint_path.resolve()),
        "limitations": [
            "Validation is acquisition-group isolated, but the dataset is small and region-specific.",
            "Patch validation is for model selection; final claims require untouched full-scene test evaluation.",
            "Oil-versus-lookalike confusion requires analyst review in operational use.",
        ],
    }
    (output_dir / "training_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train ESPADA's SAR segmentation model")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--encoder", choices=("resnet34", "resnet50"), default="resnet50")
    parser.add_argument("--encoder-checkpoint", type=Path)
    parser.add_argument(
        "--normalization", choices=("fixed_minmax", "scene_centered_s1_vv"),
        default="scene_centered_s1_vv",
    )
    parser.add_argument(
        "--augmentation", choices=("torch", "albumentations"), default="albumentations"
    )
    parser.add_argument("--no-pretrained", action="store_true")
    parser.add_argument("--max-train-patches", type=int)
    parser.add_argument("--max-validation-patches", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = TrainingConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        encoder=args.encoder,
        encoder_initialization=(
            "SSL4EO-S12 MoCo Sentinel-1" if args.encoder == "resnet50" else "ImageNet"
        ),
        encoder_checkpoint=str(args.encoder_checkpoint) if args.encoder_checkpoint else None,
        pretrained_encoder=not args.no_pretrained,
        normalization_mode=args.normalization,
        augmentation_backend=args.augmentation,
    )
    try:
        result = train_model(
            args.dataset_root,
            args.manifest,
            args.out,
            config,
            max_train_patches=args.max_train_patches,
            max_validation_patches=args.max_validation_patches,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
