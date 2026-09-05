# ESPADA release history

## Unreleased — Leakage-safe SAR learning foundation

- Added checksum-verified acquisition for the CC BY 4.0 Zenodo oil-spill segmentation dataset.
- Audited all 21 image/mask pairs in the published archive and identified leakage in its supplied splits.
- Added a deterministic acquisition-date-grouped split: 14 train, 3 validation, 4 test scenes, with zero group overlap.
- Implemented a one-channel ResNet34 U-Net, combined weighted-BCE/Dice loss, GPU mixed-precision training, early stopping and oil-class confusion metrics.
- Verified the complete training path on an RTX 5050 with a 64-patch smoke run; this is an engineering check, not an accuracy result.

## v0.4.0 — Real Sentinel-1 ingestion and review gate

- Added official Copernicus Data Space STAC discovery for Sentinel-1 GRD scenes.
- Scene ranking verifies AOI overlap, VV availability and target-point coverage before download.
- Added small public preview retrieval for visual triage; previews are explicitly blocked from analysis use.
- Discovery prevents partially overlapping scenes from being mistaken for valid case coverage.
- Added an authenticated Processing API client for a bounded, calibrated, orthorectified VV crop.
- OAuth client credentials and short-lived access tokens are never written to project artifacts.
- Live-verified a 1536 × 1400 calibrated VV crop from the selected Sentinel-1D scene without downloading its 1.23 GB source product.
- Fixed big-endian float GeoTIFF decoding and no-data handling before image analysis.
- Real detector output is now `REVIEW_REQUIRED`; only synthetic truth or explicit analyst approval can create an attribution-ready slick observation.
- Rebuilt the selected case with 10,051 AIS positions from 499 vessels and 31 date-matched hourly environmental steps.
- The complete engineering suite now contains 59 passing tests; the strict warning audit only reports a third-party OpenDrift/cmocean Matplotlib deprecation.

## v0.3.0 — Date-aligned operational inputs

- Added a mandatory temporal-alignment gate for real SAR, forcing and AIS inputs.
- The real-case runner now stops before drift/ranking when sources describe different dates.
- Added a machine-readable `case_alignment.json` audit with source ranges and evidence counts.
- Verified a live historical window with 36 archived wind samples and six native Copernicus current steps normalized to 31 hourly forcing steps, with no warnings.
- The complete engineering suite now contains 49 passing automated tests.
- Added exact-window historical wind retrieval from the Open-Meteo Historical Forecast API.
- Added a one-command case-environment workflow that derives the AIS window and downloads matching Copernicus currents with safety padding.

## v0.2.0 — Historical AIS replay

- Added a Global Fishing Watch AIS Vessel Presence adapter for small custom ocean regions.
- Requests delayed hourly vessel-presence cells, validates dates and bounds, and converts them to ESPADA's canonical AIS contract.
- Runs the existing duplicate, gap, coordinate, MMSI, and implausible-speed checks on downloaded evidence.
- Can pass the normalized history directly into the existing transparent vessel ranker.
- Reads the bearer token only from `GFW_API_ACCESS_TOKEN`; status and evidence files never contain it.
- Labels 96-hour latency, hourly/grid-cell sampling, AIS incompleteness, non-commercial terms, and evidentiary limits.
- Added a one-command PowerShell runner plus adapter, request-contract, delay, and secret-handling tests.
- Verified the live v4 API response: 8,067 accepted positions from 473 vessels; 32 rows without valid MMSI were safely rejected.
- Added support for the API's dataset-version response wrapper and alternate nested field layouts; 45 automated tests pass.
- Made AIS gap quality and ranking penalties sampling-cadence aware, so hourly GFW data is not treated like missing raw/live messages.

## v0.1.0 — Verified controlled prototype

This baseline reconstructs probable oil-release locations with reverse-drift
physics and ranks vessel tracks using transparent evidence. It is an
investigation-support prototype, not a system for determining guilt.

### Important milestones included

- Time-varying forward and reverse advection–diffusion physics.
- Copernicus Marine surface currents and Open-Meteo wind with offline caching.
- Slick GeoJSON validation, particle sampling, origin estimation, and uncertainty radii.
- AIS CSV normalization, duplicate removal, gap detection, and speed checks.
- AISStream WebSocket collection with reconnects and a rolling cache.
- Transparent vessel ranking from proximity, forward consistency, and data quality.
- Adaptive SAR dark-anomaly segmentation baseline with pixel-level evaluation.
- A 24-case synthetic robustness evaluation and machine-readable model cards.
- One-command controlled demo, uploaded-case pipeline, plots, JSON reports, and dashboard.
- Thirty-eight automated tests covering the current engineering core.

### Current verified results

- Attribution Top-1: 70.8% on 24 controlled synthetic cases.
- Attribution Top-3: 100% on those cases.
- Median estimated-origin error: 3.73 km; P90: 7.50 km.
- SAR baseline: 64.2% IoU, 78.2% Dice, 87.1% precision, and 70.9% recall on a synthetic scene.
- Real environmental forcing was downloaded and normalized successfully.
- Live AIS collection was validated using real AISStream messages, although tested west-India coverage returned no positions.

### Known limitations

- The SAR detector is classical computer vision; the proposed ResNet34 U-Net is not trained yet.
- Currents vary through time but remain spatially uniform near the analysis point.
- Spill age is supplied by the analyst rather than inferred.
- Evaluation results are controlled and synthetic, not real-world accuracy claims.
- Candidate scores are comparative evidence, not calibrated guilt probabilities.
