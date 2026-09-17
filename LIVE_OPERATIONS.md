# ESPADA Live Operations

This is the separate near-real-time operator system. It does not modify or depend on the public replay website.

## Start

Double-click `ESPADA_LIVE_OPERATIONS.cmd`, or run:

```powershell
cd "C:\Users\glori\Documents\Codex\2026-09-02\do-x20\outputs\espada"
powershell -ExecutionPolicy Bypass -File ".\scripts\start_live_operations.ps1"
```

The console opens at `http://127.0.0.1:4180/operator/live_operations/index.html`.

## What is real

- Open-Meteo current and wind fields are refreshed from the provider.
- Sentinel-1 acquisitions are discovered from the official Copernicus STAC catalogue.
- The scene selector labels acquisitions as either `historical AIS ready` or
  `AIS delay pending`, so operators can choose monitoring or complete attribution.
- **Analyze selected SAR** downloads calibrated VV pixels from Copernicus Data Space and runs the frozen V6 model.
- The physics screen measures local SAR contrast and date-matched acquisition wind.
- AISStream positions are rendered only when the provider actually sends them.
- An analyst must approve or reject every model candidate before attribution.
- An approved candidate triggers date-matched Copernicus currents, historical wind,
  reverse-particle inference and temporally matched AIS collection.
- Global Fishing Watch delayed vessel presence is used for older scenes; genuinely
  overlapping AISStream tracks can be used for recent scenes.
- Candidate tracks are forward-verified and shown as an investigative shortlist,
  or the policy returns an explicit abstention when evidence is insufficient.
- Completed analysis survives a server restart.

## What the console never fabricates

- No synthetic vessel appears when AIS is empty.
- No slick polygon appears before model inference.
- No model candidate becomes an attribution input before analyst approval.
- Candidate scores are never presented as guilt probabilities.

## Restart after an update

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start_live_operations.ps1" -Restart
```

Add the same region and bounding-box arguments used for the current watch area.

## Current operational boundary

Sentinel-1 is near-real-time rather than continuous: a usable observation exists only after an orbital pass intersects the watch area and the product is published. Global Fishing Watch is delayed by about 96 hours. When neither historical nor live AIS overlaps the inferred release window, ESPADA keeps the reverse-drift result but refuses to produce a vessel ranking.
