# Espada - Reverse Drift Attribution

Espada is an investigation-support prototype for SIH26143. The planned system reconstructs probable oil-discharge zones and times, correlates them with vessel tracks, and reports ranked candidates with uncertainty. It does not determine guilt.

## Current verified milestone

The repository now runs an offline end-to-end validation path:

1. Release particles from a known synthetic point.
2. Advect and diffuse them to a later observation.
3. Run a separate backward ensemble that sees only the observed particles and environmental beliefs.
4. Estimate an origin distribution.
5. Compare the result with the hidden answer key after inference.
6. Generate fourteen synthetic AIS vessel tracks, including one incomplete track.
7. Rank vessels using only the inferred origin, release time, track proximity and forward consistency.
8. Open the hidden answer key only after ranking and save a machine-readable PASS/FAIL report.
9. Export the observed slick as the production GeoJSON contract and independently reconstruct its probable release zone.

This milestone includes time-varying forcing, live/cache environmental adapters, calibrated V6 SAR segmentation, synthetic and imported AIS ranking, AISStream live collection, delayed Global Fishing Watch AIS history, evaluation, an offline dashboard, and tested Copernicus Marine adapters. It does not yet include automatic full-scene Sentinel-1 tiling or spatially varying OpenDrift readers.

Sentinel-1 discovery now uses the official Copernicus Data Space STAC catalogue and checks scene overlap before any large download. The authenticated Processing API then returns a bounded, calibrated VV crop; automatic full-scene tiling remains a future production step.

## Setup

Use Python 3.11 or 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Run the verified demo

On the prepared hackathon machine, double-click `scripts/run_demo.ps1`, or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_demo.ps1
```

The main outputs appear in `out\demo`:

- `dashboard.html` — open this first
- `attribution_map.png`
- `candidate_ranking.png`
- `candidates.json`
- `ais_tracks.csv`
- `demo_result.json`

## Run everything

This refreshes environmental data, runs the 24-case evaluation, executes the full demo, and regenerates the dashboard:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_all.ps1
```

## Run physics verification only

On the prepared hackathon machine, the offline runner automatically finds the existing environment:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_verification.ps1
```

Or run through an installed environment:

```powershell
.\.venv\Scripts\espada.exe verify --out out\verification
```

Expected files:

- `forward_slick.png`
- `reverse_probability.png`
- `forward_particles.npz`
- `reverse_endpoints.npz`
- `truth.json`
- `release_estimate.json`
- `verification_result.json`

The command exits with a non-zero status when an acceptance condition fails.

## Refresh environmental data

This command downloads a small Open-Meteo current-and-wind response, saves it locally, and automatically falls back to the last cache or clearly labelled synthetic values when offline:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_environment.ps1
```

Copernicus Marine remains the planned primary competition source; Open-Meteo is the no-login live adapter and offline-cache proof.

The main demo automatically refreshes live data, uses an existing cache if offline, and finally falls back to clearly labelled synthetic forcing. When real/cache data is available, its mean current and wind drive the controlled spill simulation.

## Connect Copernicus Marine

Browser login and command-line login are separate. Run this once; it installs the official toolbox in an isolated environment and securely prompts for your Copernicus credentials:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_copernicus.ps1
```

Then download and validate a small Mumbai surface-current subset:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_copernicus.ps1
```

The raw NetCDF stays in `data\raw`, while the normalized current-plus-wind cache is `data\cache\environment_copernicus.json`. `run_all.ps1` automatically prefers that cache once it exists. Espada never accepts or stores the Copernicus password.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Run synthetic evaluation

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_evaluation.ps1
```

This evaluates 24 hidden-truth cases across current bias, wind bias, AIS dropout, position noise, combined stress, and nominal conditions. Open `out\evaluation\evaluation_report.html` for the results and graphs.

The working attribution core is not ML: it is physics plus transparent evidence scoring. SAR segmentation now uses the calibrated Sentinel-1-pretrained V6 attention U-Net by default and retains adaptive thresholding as an explicit fallback. Synthetic evaluation measures the physics-and-ranking pipeline; it is not a claim of real-world accuracy.

## Prepare and train the SAR model

Download and audit the labelled Sentinel-1 data:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_oil_dataset.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\audit_oil_dataset.ps1
```

ESPADA does not use the archive's supplied split because it repeats scenes and acquisition dates across partitions. The generated manifest keeps every acquisition date in exactly one of train, validation or test. Run a small engineering check with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model.ps1 -Smoke
```

The smoke run is not an accuracy claim. V5 full GPU training uses the complete group-safe train/validation data. It freezes the pretrained encoder for four epochs, uses a ten-times lower encoder learning rate after unfreezing, keeps encoder BatchNorm statistics fixed, replaces decoder BatchNorm with GroupNorm, accumulates gradients to an effective batch of eight, balances sampling across scenes, and selects the checkpoint using scene-macro validation average precision:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model.ps1 -Epochs 40 -BatchSize 2
```

V3 used an attention-gated ResNet34 U-Net and achieved 48.5% validation IoU, 65.3% validation Dice and 72.9% validation average precision after threshold calibration. Its one-time acquisition-group-isolated full-scene test achieved 29.3% IoU, 45.4% Dice, 32.6% precision and 74.6% recall. Two test scenes generalized well while two scenes from 2020-02-24 exposed strong brightness/domain shift, false alarms on dark lookalikes and missed small slicks. The 96.2% pixel accuracy is background-dominated and is not used as the headline result. These numbers are retained as the honest V3 baseline; any V4 changes informed by these scenes require a new external untouched test set for a final claim.

V4 addresses those failures without claiming a guaranteed score. It uses scene-relative SAR normalization, stronger mask-synchronized Albumentations, more hard dark-background patches, a false-alarm-aware focal Tversky loss, and a ResNet50 encoder self-supervised on global Sentinel-1 imagery by SSL4EO-S12. ESPADA pins the legacy MIT-licensed Albumentations 2.0.8 release. The official encoder weights are CC BY 4.0 and supplied through TorchGeo; they are adapted from dual-polarization input to the archive's VV-only imagery. Prepare the V4 dependency and resumable 94.3 MB encoder download with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_ml_v4.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\download_sar_encoder.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model.ps1 -Smoke
```

V4 peaked early and remained unstable with batch size two, so V5 protects the pretrained features and removes small-batch decoder statistics. Training writes to `out\ml_training_v5`; calibration and evaluation automatically use the matching V5 directories. Pass `-Version v4` to the calibration or evaluation script only when reproducing the archived V4 model.

V5 selected epoch 33 by scene-macro validation AP. Validation-only calibration selected threshold 0.114 and achieved 55.9% oil IoU, 71.7% Dice, 68.8% precision, 74.9% recall and 76.8% AP. On the four previously examined V3 holdout scenes, the V5 development replay achieved 59.6% IoU, 74.7% Dice, 68.2% precision, 82.5% recall and 74.7% AP. This is a large diagnostic improvement over V3 (29.3% IoU and 45.4% Dice), but it is not a fresh blind-test claim because those scenes informed model development.

After V5 calibration, `evaluate_sar_model.ps1` deliberately labels the old four-scene result a development replay. Those scenes informed development and are no longer an untouched test. A newly acquired, acquisition-isolated labelled set is required before publishing a new final generalization claim.

V6 warm-starts V5, uses a stronger SAR-specific augmentation profile, fine-tunes at a lower learning rate and uses four-view flip averaging during calibration and inference. It is the current development winner: validation-only calibration achieved 55.7% IoU, 71.6% Dice, 78.7% precision, 65.7% recall and 80.7% AP. On the four-scene development replay it achieved 61.3% IoU, 76.0% Dice, 71.7% precision, 80.9% recall and 77.4% AP. It improved V5's IoU, Dice, precision, AP, false-positive rate and weakest-scene IoU while reducing aggregate recall by 1.6 percentage points. This is not an external blind-test claim.

Reproduce V6 with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model_v6.ps1 -Smoke
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model_v6.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\calibrate_sar_model.ps1 -Version v6 -Tta flip4 -BatchSize 4
powershell -ExecutionPolicy Bypass -File .\scripts\evaluate_sar_model.ps1 -Version v6 -BatchSize 4
```

See `docs\ml_v6_plan.md` for the promotion rules and the researched pretrained-model comparison. SoftCon ResNet50 is the first optional encoder test because it is a direct Sentinel-1-compatible substitute. CROMA and TerraMind become more meaningful after the data pipeline supplies genuine VV+VH or multimodal inputs.

## Analyze a slick GeoJSON

The input must contain one polygon with `observation_time_utc` and `detection_confidence` properties. The verified demo creates a representative input at `out\demo\slick_observation.geojson`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\analyze_slick.ps1
```

This produces a normalized GeoJSON, sampled observation particles, reverse endpoints, a two-panel slick/origin graph, and a machine-readable analysis report under `out\slick`.

## Run SAR segmentation

Discover Sentinel-1 GRD scenes matching the current historical AIS window:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\discover_sentinel1.ps1
```

The command saves the ranked catalogue audit, scene footprints and a small preview under `out\sentinel1`. A `PARTIAL` result means the satellite footprint intersects the broad search box but does not cover the chosen target point; do not use it for attribution. The preview is only for visual triage, not scientific segmentation.

After selecting a `PASS` scene, create a Copernicus Data Space OAuth client and place its client ID and secret in `.env`. Download a bounded, calibrated VV crop with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_sentinel1_subset.ps1
```

The default crop is 1536 × 1400 pixels and covers the verified target area. It avoids the roughly 1.23 GB full-scene download and remains below the Processing API's 2500-pixel synchronous limit.

Run the calibrated V6 model on that crop with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_sentinel_v6.ps1
```

The launcher uses the CUDA/PyTorch environment for inference and the normal Espada environment for maps and reports. It verifies that the validation threshold belongs to the selected checkpoint and automatically applies the calibrated flip4 inference policy. Output is saved under `out\sentinel1_case\segmentation_v6`. The current downloaded crop correctly returns `NO_DETECTION`: its maximum oil probability is 0.0222, below the frozen 0.05 threshold. This is not an error and no slick polygon is fabricated. The crop has no ground-truth mask, so this run proves operational compatibility rather than accuracy.

Real detections are deliberately exported as `slick_candidate.geojson` with status `REVIEW_REQUIRED`. They do not become `slick_observation.geojson` or enter reverse-drift attribution until an analyst explicitly approves the candidate. Numerous disconnected dark regions or unusually broad coverage are flagged because low wind, rain and natural films can resemble oil in SAR imagery.

The offline evaluated baseline simulates a speckled SAR scene, hides its truth mask during inference, detects dark anomalies, exports a slick GeoJSON, and reports pixel metrics:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_sar_demo.ps1
```

The current synthetic result is 64.2% IoU, 78.2% Dice, 87.1% precision and 70.9% recall. These are synthetic-baseline metrics, not real Sentinel-1 accuracy. Prepared PNG/TIFF images can be processed with `espada sar`; a WGS84 bounding box is required for GeoJSON export.

## Import and rank AIS

The adapter recognizes common columns such as `BaseDateTime`, `MMSI`, `LAT`, `LON` and `VesselName`, removes invalid/duplicate rows, detects gaps and implausible jumps, then ranks the normalized tracks against the latest drift case:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\import_and_rank_ais.ps1 -InputPath "C:\path\to\ais.csv"
```

Results are written under `out\ais_import` and `out\ais_ranking`. AIS identity, reception coverage and candidate evidence still require analyst verification.

## Capture live AIS positions

Espada can subscribe to the AISStream WebSocket for a focused geographic box, reconnect with backoff, retain a rolling cache, normalize the positions and optionally rank them against a current drift case. Install the small optional dependency once:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_live_ais.ps1
```

Create an AISStream account/key, paste it after `AISSTREAM_API_KEY=` in the private `.env` file, then capture a five-minute Mumbai/JNPT-area window:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_live_ais.ps1
```

The key is never accepted as a command-line argument and is not saved in output files. `.env` is ignored by Git; `.env.example` contains only blank placeholders. The rolling cache is `data\cache\ais_live.csv`; normalized data and connection status are under `out\live_ais`. AISStream is event-driven, has no replay/SLA guarantee, and live messages do not prove vessel identity.

## Download dependable historical AIS evidence

For case replay, ESPADA can request Global Fishing Watch's worldwide AIS Vessel Presence dataset. Create a non-commercial API token in the [Global Fishing Watch API portal](https://globalfishingwatch.org/our-apis/) and add it only to the private `.env` file:

```text
GFW_API_ACCESS_TOKEN=paste_token_here
```

Then download a small west-India window. By default this requests 24 hours ending five days ago, safely outside the provider delay:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_historical_ais.ps1
```

To rank the returned vessels against the controlled drift case:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_historical_ais.ps1 -CasePath .\out\demo
```

Outputs are under `out\historical_ais` and, when ranking is requested, `out\historical_ais_ranking`. This product provides one AIS-derived position per vessel per hour at grid-cell centres and normally stops about 96 hours before the present. It is delayed evidence—not raw or real-time AIS—and the output preserves those limitations.

## Prepare a date-matched environment

After historical AIS succeeds, this command reads its actual time range, adds six hours of safety padding, downloads archived Open-Meteo forecast wind and a Copernicus surface-current subset for the same window, then combines them into one validated cache:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_case_environment.ps1
```

The combined cache is `data\cache\environment_historical.json`; its status and graph are under `out\historical_environment`. Archived forecast wind and Copernicus currents are model estimates rather than direct observations.

## Run a complete uploaded case

`scripts\run_real_case.ps1` joins a prepared SAR PNG/TIFF, its WGS84 bounds, observation time, Copernicus forcing and an AIS CSV. It runs calibrated V6 inference by default, pauses for human review when a candidate exists, and stops rather than fabricating a polygon when no slick is detected. The resulting mask, drift estimate, quality report and vessel ranking are saved under `out\real_case`.

Before reverse drift or ranking, the runner writes `case_alignment.json` and stops unless the environmental series covers the full assumed spill age and AIS positions exist within two hours of the inferred release time. This prevents convincing-looking results made from mismatched dates.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_real_case.ps1 `
  -SarImage "C:\data\scene.tif" -AisCsv "C:\data\ais.csv" `
  -ObservationTimeUtc "2026-09-03T22:00:00Z" `
  -MinLongitude 71.2 -MinLatitude 18.5 -MaxLongitude 71.8 -MaxLatitude 19.0
```

## Stable future interfaces

- Slick: GeoJSON polygon, UTC observation timestamp, and detection confidence.
- Space-time origin: probability raster, release-time window, and uncertainty metadata.
- Candidates: MMSI, component scores, evidence, data-quality flags, and limitations.

`truth.json` is evaluation-only. Reverse inference does not accept it as an input.

## Scientific limitation

The fast backend now integrates hourly current and wind vectors through time while treating them as spatially uniform near the case. It is still a controlled verification harness, not an operational ocean forecast. The next scientific upgrade is spatially varying Copernicus grids through a validated OpenDrift reader.
