import json
from pathlib import Path

from espada.evaluation import EvaluationConfig, run_synthetic_evaluation


def test_synthetic_evaluation_creates_auditable_outputs(tmp_path: Path) -> None:
    result = run_synthetic_evaluation(
        tmp_path,
        EvaluationConfig(cases=6, particles=100, ensemble_members=3),
    )
    assert 0.0 <= result["overall"]["top1_accuracy"] <= 1.0
    assert 0.0 <= result["overall"]["top3_accuracy"] <= 1.0
    assert len(result["by_condition"]) == 6
    for filename in result["artifacts"]:
        assert (tmp_path / filename).stat().st_size > 0


def test_model_card_separates_smoke_verified_ml_from_operational_core(tmp_path: Path) -> None:
    run_synthetic_evaluation(
        tmp_path,
        EvaluationConfig(cases=6, particles=80, ensemble_members=2),
    )
    card = json.loads((tmp_path / "model_card.json").read_text(encoding="utf-8"))
    assert card["current_operational_core"]["machine_learning"] is False
    assert (
        card["planned_sar_segmentation"]["status"]
        == "implemented_and_gpu_smoke_verified_not_fully_trained_or_integrated"
    )
