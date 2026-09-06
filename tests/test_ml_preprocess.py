import numpy as np
import pytest

from espada.ml_preprocess import (
    FIXED_MINMAX,
    SCENE_CENTERED_S1_VV,
    db_to_unit,
    model_input_from_db,
    prepare_scene_db,
    unit_to_db,
)


def test_scene_centering_removes_constant_acquisition_shift() -> None:
    base = np.array([[-30.0, -25.0], [-20.0, -15.0]], dtype=np.float32)
    shifted = base + 11.0
    assert np.allclose(
        prepare_scene_db(base, SCENE_CENTERED_S1_VV),
        prepare_scene_db(shifted, SCENE_CENTERED_S1_VV),
    )


def test_unit_db_conversion_round_trips_inside_clip_range() -> None:
    image = np.array([[-35.0, -22.0, 5.0]], dtype=np.float32)
    assert np.allclose(unit_to_db(db_to_unit(image)), image)


def test_pretrained_normalization_differs_from_legacy_minmax() -> None:
    image = prepare_scene_db(np.array([[-22.0]], dtype=np.float32), FIXED_MINMAX)
    legacy = model_input_from_db(image, FIXED_MINMAX)
    pretrained = model_input_from_db(image, SCENE_CENTERED_S1_VV)
    assert legacy.item() == pytest.approx(0.325)
    assert pretrained.item() < 0
