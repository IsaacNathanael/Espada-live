# ESPADA operator guide

ESPADA is an investigative decision-support prototype. It reconstructs where and when an observed oil slick may have originated, compares that probability field with vessel tracks, and either produces a reviewable shortlist or abstains when evidence is too weak.

## The fastest safe workflow

From the ESPADA project folder, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_case_portal.ps1
```

On Windows, you can instead double-click `ESPADA_CASE_PORTAL.cmd`.

The portal opens locally in the browser. Nothing is uploaded to a public server.

1. Choose **Approved slick** when an analyst has already supplied a polygon. Choose **SAR detection** only when V6 must examine a Sentinel-1 crop.
2. Add the slick/SAR file, AIS CSV and time-matched environment JSON. A Copernicus current grid and land mask are optional higher-fidelity inputs.
3. Select **Validate & prepare case**. Invalid evidence is rejected before drift analysis.
4. Wait while the local engine reconstructs probable release zones and compares candidates.
5. Open the dossier. Treat rankings as priorities for human review—not accusations.

Prepared evidence is stored under `out/case_intake/<case-id>`. Analysis outputs are stored under `out/cases/<case-id>`. Existing case IDs are never overwritten.

## Minimum data contracts

### Approved slick GeoJSON

- Polygon or MultiPolygon in WGS84.
- `observation_time_utc` property with a timezone.
- `detection_confidence` property from 0 to 1.
- The polygon must already be approved by an analyst.

### AIS CSV

Required columns, with common aliases accepted:

- MMSI
- UTC timestamp
- longitude
- latitude

Vessel name, speed and course are useful but optional. Missing reports are recorded as missing evidence; they do not increase suspicion.

### Environment JSON

At least two time samples containing:

- `time_utc`
- `current_east_ms`, `current_north_ms`
- `wind_east_ms`, `wind_north_ms`

The series must cover the complete reverse-drift window. ESPADA stops when the dates do not align.

## What each stage means

1. **Detection:** creates or accepts a reviewed slick boundary. A dark SAR region alone is not proof of oil.
2. **Reverse drift:** runs an ensemble backward through current, windage and diffusion uncertainty. The output is a probability region, not a single point.
3. **Traffic audit:** normalizes AIS, detects gaps and evaluates coverage quality.
4. **Ranking:** combines origin presence, temporal agreement, forward reconstruction and track quality.
5. **Decision gate:** permits review only when evidence thresholds pass; otherwise the system abstains.
6. **Dossier:** records provenance, hashes, assumptions, scores, limitations and visual evidence.

## Validation you can defend

- 24 controlled stress cases test current bias, wind bias, AIS dropout, position noise and combined stress.
- MV Wakashio is the transparent known-source reconstruction: the documented vessel ranked first of five.
- Princess Empress demonstrates safe abstention: a high comparative score did not override poor AIS quality.
- The physics benchmark checks deterministic trajectory parity with OpenDrift; it does not claim real-spill forecast accuracy.
- V6 is the strongest development SAR model, but its reported test is a development replay, not a new external blind trial.

## Share the frozen prototype

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package_release_v1.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify_release_v1.ps1
```

Share `out/releases/espada-prototype-v1.0.zip`. The recipient extracts it and opens `index.html`. This is a saved-evidence interactive replay; fresh inference requires the local engine.

## Safe interpretation

ESPADA can answer: “Which tracks best fit the available physical and temporal evidence, and is that evidence strong enough for analyst review?”

ESPADA cannot answer: “Who is guilty?” A candidate score is comparative evidence fit—not a probability of guilt. Any operational escalation requires independent records, better AIS where gaps exist, and human approval.
