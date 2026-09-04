from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .models import parse_utc


@dataclass(frozen=True)
class VesselFixture:
    mmsi: str
    vessel_name: str
    lon_at_release: float
    lat_at_release: float
    east_deg_per_hour: float
    north_deg_per_hour: float
    gap_start_hours: float | None = None
    gap_end_hours: float | None = None


def _vessels(release_lon: float, release_lat: float, seed: int) -> list[VesselFixture]:
    rng = np.random.default_rng(seed)
    fixtures = [
        VesselFixture("419000123", "MV SYNTHETIC", release_lon, release_lat, 0.060, 0.034),
        VesselFixture(
            "419000456",
            "MV INNOCENT",
            release_lon - 0.070,
            release_lat + 0.018,
            0.046,
            -0.012,
        ),
        VesselFixture(
            "419000789",
            "MV GAP DECOY",
            release_lon + 0.28,
            release_lat - 0.22,
            -0.020,
            0.018,
            -0.8,
            1.0,
        ),
    ]
    for index in range(11):
        angle = 2.0 * np.pi * index / 11.0
        radius = float(rng.uniform(0.20, 0.55))
        fixtures.append(
            VesselFixture(
                str(419001000 + index),
                f"MV DECOY {index + 1:02d}",
                release_lon + radius * float(np.cos(angle)),
                release_lat + radius * float(np.sin(angle)),
                float(rng.uniform(-0.045, 0.045)),
                float(rng.uniform(-0.035, 0.035)),
            )
        )
    return fixtures


def generate_synthetic_ais(
    truth_path: Path,
    output_path: Path,
    *,
    seed: int = 26143,
    interval_minutes: int = 10,
) -> pd.DataFrame:
    """Create a labelled evaluation fixture. Attribution never calls this function."""
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    release_time = parse_utc(truth["release_time_utc"])
    release_lon = float(truth["release_lon"])
    release_lat = float(truth["release_lat"])
    start = release_time - timedelta(hours=3)
    end = release_time + timedelta(hours=3)
    times = pd.date_range(start=start, end=end, freq=f"{interval_minutes}min")
    rows: list[dict] = []
    for vessel in _vessels(release_lon, release_lat, seed):
        for timestamp in times:
            hours = (timestamp.to_pydatetime() - release_time).total_seconds() / 3600.0
            if (
                vessel.gap_start_hours is not None
                and vessel.gap_end_hours is not None
                and vessel.gap_start_hours <= hours <= vessel.gap_end_hours
            ):
                continue
            rows.append(
                {
                    "timestamp_utc": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mmsi": vessel.mmsi,
                    "vessel_name": vessel.vessel_name,
                    "longitude": vessel.lon_at_release + vessel.east_deg_per_hour * hours,
                    "latitude": vessel.lat_at_release + vessel.north_deg_per_hour * hours,
                    "is_interpolated": False,
                    "source": "synthetic_validation_fixture",
                }
            )
    frame = pd.DataFrame(rows).sort_values(["mmsi", "timestamp_utc"]).reset_index(drop=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
    return frame

