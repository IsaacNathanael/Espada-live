from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, UTC_FORMAT)


def format_utc(value: datetime) -> str:
    return value.strftime(UTC_FORMAT)


@dataclass(frozen=True)
class Forcing:
    current_east_ms: float
    current_north_ms: float
    wind_east_ms: float
    wind_north_ms: float
    windage: float = 0.02
    diffusivity_m2s: float = 12.0

    @property
    def drift_east_ms(self) -> float:
        return self.current_east_ms + self.windage * self.wind_east_ms

    @property
    def drift_north_ms(self) -> float:
        return self.current_north_ms + self.windage * self.wind_north_ms

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Forcing":
        return cls(**{key: float(item) for key, item in value.items()})


@dataclass(frozen=True)
class Scenario:
    seed: int
    observation_time: datetime
    track_start_time: datetime
    track_end_time: datetime
    release_start: datetime
    release_end: datetime
    track_start_lon: float
    track_start_lat: float
    track_end_lon: float
    track_end_lat: float
    forcing: Forcing
    polluter_mmsi: str = "419000123"
    polluter_name: str = "MV SYNTHETIC"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "observation_time": format_utc(self.observation_time),
            "track_start_time": format_utc(self.track_start_time),
            "track_end_time": format_utc(self.track_end_time),
            "release_start": format_utc(self.release_start),
            "release_end": format_utc(self.release_end),
            "track_start_lon": self.track_start_lon,
            "track_start_lat": self.track_start_lat,
            "track_end_lon": self.track_end_lon,
            "track_end_lat": self.track_end_lat,
            "forcing": self.forcing.to_dict(),
            "polluter_mmsi": self.polluter_mmsi,
            "polluter_name": self.polluter_name,
        }

