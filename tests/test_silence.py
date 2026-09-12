from __future__ import annotations

import pandas as pd

from espada.silence import analyze_coverage_aware_silence


def _row(time: str, mmsi: str, lon: float, lat: float) -> dict[str, object]:
    return {
        "timestamp_utc": time,
        "mmsi": mmsi,
        "vessel_name": f"V-{mmsi[-1]}",
        "longitude": lon,
        "latitude": lat,
    }


def test_vessel_gap_is_coverage_supported_when_nearby_peers_continue_reporting() -> None:
    rows = [
        _row("2026-01-01T00:00:00Z", "111111111", 72.00, 18.00),
        _row("2026-01-01T02:00:00Z", "111111111", 72.02, 18.00),
    ]
    for minute in (20, 40, 60, 80, 100):
        stamp = f"2026-01-01T{minute // 60:02d}:{minute % 60:02d}:00Z"
        rows.append(_row(stamp, "222222222", 72.01, 18.01))
        rows.append(_row(stamp, "333333333", 72.01, 17.99))
    result = analyze_coverage_aware_silence(pd.DataFrame(rows))
    report = next(item for item in result["vessels"] if item["mmsi"] == "111111111")
    assert report["classification"] == "vessel_specific_gap_with_peer_coverage"
    assert report["gaps_with_local_peer_reception"] == 1
    assert result["safety_rule"].startswith("Silence classification never increases")


def test_gap_remains_unresolved_without_local_peer_reception() -> None:
    rows = [
        _row("2026-01-01T00:00:00Z", "111111111", 72.00, 18.00),
        _row("2026-01-01T02:00:00Z", "111111111", 72.02, 18.00),
    ]
    for minute in (0, 20, 40, 60, 80, 100, 120):
        stamp = f"2026-01-01T{minute // 60:02d}:{minute % 60:02d}:00Z"
        rows.append(_row(stamp, "222222222", 75.00, 21.00))
    frame = pd.DataFrame(rows)
    result = analyze_coverage_aware_silence(frame)
    report = next(item for item in result["vessels"] if item["mmsi"] == "111111111")
    assert report["classification"] == "coverage_unresolved"
    assert report["gaps_with_local_peer_reception"] == 0
