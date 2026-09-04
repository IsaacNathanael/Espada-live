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

This milestone includes time-varying forcing, live/cache environmental adapters, SAR segmentation, synthetic and imported AIS ranking, AISStream live collection, delayed Global Fishing Watch AIS history, evaluation, an offline dashboard, and a tested Copernicus Marine subset/normalization adapter. It does not yet include automated Sentinel-1 preprocessing or spatially varying OpenDrift readers.

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

The working attribution core is not ML: it is physics plus transparent evidence scoring. The planned SAR segmentation model is a ResNet34-based U-Net, with adaptive thresholding retained as the fallback until training data and held-out splits are verified. Synthetic evaluation measures the complete physics-and-ranking pipeline; it is not a claim of real-world accuracy.

## Analyze a slick GeoJSON

The input must contain one polygon with `observation_time_utc` and `detection_confidence` properties. The verified demo creates a representative input at `out\demo\slick_observation.geojson`.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\analyze_slick.ps1
```

This produces a normalized GeoJSON, sampled observation particles, reverse endpoints, a two-panel slick/origin graph, and a machine-readable analysis report under `out\slick`.

## Run SAR segmentation baseline

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

`scripts\run_real_case.ps1` joins a prepared SAR PNG/TIFF, its WGS84 bounds, observation time, Copernicus forcing and an AIS CSV. It stops rather than fabricating a polygon when no slick is detected. The resulting mask, drift estimate, quality report and vessel ranking are saved under `out\real_case`.

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
