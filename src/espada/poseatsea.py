"""ESPADA adapter for the published POSEatSea SAR checkpoint.

Implements the documented input/output contract; does not execute upstream code.
Model attribution and license declaration: https://huggingface.co/23f2003521/poseatsea-weights
"""
from pathlib import Path

import numpy as np

from .dartis import _file_sha256

MODEL_SPEC = {
    "provider": "23f2003521 / POSEatSea",
    "repository": "https://github.com/23f2003521/SIH2026",
    "model_card": "https://huggingface.co/23f2003521/poseatsea-weights",
    "license_declaration": "MIT (publisher's model card)",
    "revision": "214e7c248ec09cccfcc36b087847fa7748a662df",
    "filename": "best_sar_model.pth",
    "sha256": "6a76ca8f06178fdaa36bba27e725fb6e2a7da46764ad76a494b4fcdf1ecd6489",
    "bytes": 110067419,
    "architecture": "SMP U-Net with MiT-B2 encoder, 3 input channels, 5 classes",
    "classes": ["sea", "oil", "lookalike", "ship", "land"],
    "input_size": 512,
}
MODEL_SPEC["download_url"] = (
    "https://huggingface.co/23f2003521/poseatsea-weights/resolve/"
    + MODEL_SPEC["revision"] + "/" + MODEL_SPEC["filename"]
)


def verify_checkpoint(path: Path) -> None:
    path = Path(path)
    if path.stat().st_size != MODEL_SPEC["bytes"] or _file_sha256(path) != MODEL_SPEC["sha256"]:
        raise ValueError("POSEatSea checkpoint size/hash differs from the pinned model")


def preprocess_rgb(image: np.ndarray) -> np.ndarray:
    import cv2

    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("POSEatSea requires RGB uint8 HxWx3 input")
    resized = cv2.resize(image, (512, 512), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray((resized.astype(np.float32) / 255.0).transpose(2, 0, 1))


def decode_logits(logits: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    logits = np.asarray(logits, dtype=np.float32)
    if logits.shape != (5, 512, 512) or not np.isfinite(logits).all():
        raise ValueError("Expected finite POSEatSea logits with shape (5,512,512)")
    if len(shape) != 2 or min(shape) < 1:
        raise ValueError("Invalid original image dimensions")
    labels = logits.argmax(axis=0).astype(np.uint8)
    native_labels = cv2.resize(labels, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    exponent = np.exp(logits - logits.max(axis=0, keepdims=True))
    oil_probability = exponent[1] / exponent.sum(axis=0)
    probability = cv2.resize(oil_probability, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
    # Multiclass argmax is the publisher's decision rule, not an oil threshold.
    return native_labels == 1, probability


def load_model(path: Path, device):
    verify_checkpoint(path)
    import torch
    import segmentation_models_pytorch as smp

    model = smp.Unet(encoder_name="mit_b2", encoder_weights=None, in_channels=3, classes=5)
    state = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


def predict(model, image_path: Path, device) -> tuple[np.ndarray, np.ndarray]:
    import cv2
    import torch

    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Cannot read image: {image_path}")
    image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(preprocess_rgb(image)).unsqueeze(0).to(device)
    with torch.inference_mode():
        logits = model(tensor)[0].float().cpu().numpy()
    return decode_logits(logits, image.shape[:2])
