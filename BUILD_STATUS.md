# Build status

This is a verified integration milestone, not the complete SIH26143 model.

## Completed

- Reproducible Python package definition.
- Verified OpenDrift 1.14.11 environment in the originating workspace.
- Stable UTC and forcing data models.
- Geographic conversion, interpolation, GeoJSON and polygon-sampling utilities.
- Fast analytic time-varying current/wind advection-diffusion backend.
- OpenDrift constant-reader adapter supporting forward and reverse runs.
- A real `espada verify` command with visible and machine-readable output.
- Automated tests for geographic utilities, analytic physics, OpenDrift round-trip behaviour, output generation, and reproducibility.
- Thirty-eight automated tests currently pass.
- Full Copernicus-forcing verification result: 2.18 km estimated-origin error under biased forcing and 0.324 m OpenDrift deterministic round-trip error.
- Synthetic AIS generator with fourteen vessel tracks and one realistic missing-data interval.
- Candidate scoring from inferred space-time proximity, forward consistency and data quality.
- Answer-key separation: the ranker cannot receive the synthetic truth file.
- Full offline demo correctly ranks the known synthetic source first with a 96.1% score.
- Attribution map, candidate ranking chart, CSV tracks and machine-readable JSON results.
- Self-contained responsive investigation dashboard with searchable candidate table.
- Live Open-Meteo current-and-wind adapter with normalized local cache.
- Explicit `live`, `cache`, `synthetic`, and automatic fallback modes.
- Offline-failure verification that never mislabels synthetic values as real data.
- Full live-data-forcing demo: 2.18 km origin error and correct synthetic vessel ranked first at 99.0%.
- Dashboard provenance badge and real current/wind time-series panel.
- Twenty-four-case synthetic robustness evaluation across six failure conditions.
- Measured results: 70.8% Top-1, 100% Top-3, 3.73 km median origin error, and 87.5% coverage of the nominal 90% region.
- Evaluation report, four-panel graph, per-case CSV, machine-readable summary and honest ML model card.
- One-command end-to-end runner: `scripts/run_all.ps1`.
- One-command offline demo runner: `scripts/run_demo.ps1`.
- Offline PowerShell runner that uses the existing prepared environment without downloading packages.
- Project documentation describing scope, interfaces, and frozen decisions.
- Hourly time-varying forcing integration with a saved, auditable forcing series.
- Tested Copernicus Marine subset request builder for surface `uo`/`vo`, with no credentials accepted or stored by Espada.
- NetCDF grid normalizer that selects the nearest surface cell, interpolates native current timestamps to hourly steps, merges cached wind, and writes the verified environment-cache contract.
- Isolated Copernicus setup/sync scripts; the stable OpenDrift environment is not modified.
- Authenticated Copernicus download verified on this machine: 8 native six-hour current steps normalized into 43 hourly forcing samples with zero warnings.
- Validated slick GeoJSON contract with timestamp, confidence, geometry and bounds checks.
- Independent slick-polygon backward inference using real Copernicus forcing, with normalized input, probability endpoints, uncertainty radii and a two-panel graph.
- PNG/TIFF SAR-image loader and adaptive dark-anomaly segmentation fallback with mask, confidence surface, overlay and GeoJSON export.
- Synthetic SAR evaluation: 64.2% IoU, 78.2% Dice, 87.1% precision and 70.9% recall; explicitly not presented as real-world accuracy.
- SAR model card clearly separates the working classical baseline from the planned ResNet34 U-Net.
- Real-world AIS CSV normalization with common vendor-column aliases, UTC/MMSI/coordinate validation, duplicate removal, gap detection and speed-jump checks.
- One-command imported-AIS ranking against the latest reverse-drift case, plus dashboard AIS-quality visualization.
- One-command uploaded-case chain from prepared SAR raster and bounds through segmentation, Copernicus drift, AIS validation and ranking.
- AISStream live WebSocket adapter with geographic/MMSI filters, server-side environment-variable credentials, reconnect backoff, rolling cache, normalization and optional ranking handoff.
- External AISStream connection verified: the documented Miami test region returned three real vessel positions for three vessels in five seconds with zero warnings.
- Downloaded and checksum-verified the CC BY 4.0 Zenodo labelled Sentinel-1 oil-spill dataset.
- Audited 21 valid image/mask pairs and replaced the leaking supplied splits with acquisition-date-grouped 14/3/4 train/validation/test splits.
- Implemented and GPU-smoke-tested the 24.4-million-parameter, one-channel ResNet34 U-Net training pipeline.
- Added weighted BCE + Dice loss, mixed precision, early stopping, checkpointing, IoU/Dice/precision/recall and a pixel confusion matrix.

## Verified manually

- OpenDrift 1.14.11 imports successfully.
- A one-hour forward/reverse controlled run returns to within one metre of the starting point.
- OpenDrift rejects timezone-aware seed times in this installed dependency combination; the adapter therefore expects timezone-naive UTC `datetime` values internally and serializes UTC explicitly at file boundaries.
- The AISStream transport, parsing, rolling cache and credential sanitization pass both simulated-server tests and a real provider connection.

## Not built yet

- Moving-vessel line-release slick generator; the current fixture is a point release.
- A live AIS provider with dependable west-India coverage; AISStream accepted the subscription but returned no positions for both Mumbai/JNPT and the wider 68-75 E, 15-23 N diagnostic region.
- Full ResNet34 U-Net training, untouched full-scene test evaluation and checkpoint integration into the real Sentinel-1 review path.
- Spatially varying CMEMS current fields inside OpenDrift; the current adapter samples one surface cell and varies it through time.
- Global Fishing Watch adapter.
- Live analyst controls, downloadable investigation brief and SIH presentation.

The slick command now consumes a validated polygon and time-varying environmental fields. It still requires an analyst-supplied spill-age hypothesis until age-search calibration is implemented.
