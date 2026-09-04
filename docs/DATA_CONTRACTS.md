# Data contracts

## Slick observation

- Format: GeoJSON FeatureCollection containing one Polygon or MultiPolygon.
- Required properties: `observation_time_utc`, `detection_confidence`, `source_scene_id`.
- Coordinates: WGS84 longitude/latitude.

## Environmental forcing

- Time-indexed eastward and northward ocean-current components in m/s.
- Time-indexed eastward and northward 10-m wind components in m/s.
- Source identifiers, spatial resolution, temporal resolution, and missing-data flags.

## Origin distribution

- Probability raster in WGS84.
- UTC release-time window.
- Ensemble-member and forcing-uncertainty metadata.
- Credible-region summaries.

## AIS tracks

- Required fields: `timestamp_utc`, `mmsi`, `longitude`, `latitude`.
- Recommended fields: `sog_knots`, `cog_degrees`, `navigation_status`, `source`, `quality_flag`, `is_interpolated`.

## Ranked candidates

- `mmsi`, vessel metadata, total score, component scores, evidence, uncertainty, data-quality flags, and limitations.

These interfaces may be versioned but must not be changed silently.

