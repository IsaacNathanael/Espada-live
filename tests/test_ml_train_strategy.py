import pytest


try:
    import torch
except (ImportError, OSError) as error:
    pytest.skip(f"optional PyTorch environment is unavailable: {error}", allow_module_level=True)

from espada.ml_model import ResNet34UNet
from espada.ml_train import (
    encoder_and_decoder_parameters,
    freeze_encoder_batch_norm_statistics,
    set_encoder_trainable,
)


def test_encoder_can_be_frozen_then_progressively_unfrozen() -> None:
    model = ResNet34UNet(pretrained_encoder=False)
    encoder, decoder = encoder_and_decoder_parameters(model)
    set_encoder_trainable(model, False)
    assert encoder and not any(parameter.requires_grad for parameter in encoder)
    assert decoder and all(parameter.requires_grad for parameter in decoder)
    set_encoder_trainable(model, True)
    assert all(parameter.requires_grad for parameter in encoder)


def test_encoder_batch_norm_statistics_are_frozen() -> None:
    model = ResNet34UNet(pretrained_encoder=False).train()
    freeze_encoder_batch_norm_statistics(model)
    encoder_batch_norm = [
        module
        for name, module in model.named_modules()
        if name.startswith(("stem.", "encoder"))
        and isinstance(module, torch.nn.BatchNorm2d)
    ]
    assert encoder_batch_norm
    assert not any(module.training for module in encoder_batch_norm)
