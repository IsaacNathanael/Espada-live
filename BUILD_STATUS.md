# ESPADA build status

Status date: 2026-09-15
Release: Research prototype v1.0

## Working end to end

- Approved slick or Sentinel-1 SAR case intake.
- Input validation for geometry, UTC time, AIS columns, environmental coverage and file formats.
- Calibrated V6 SAR candidate segmentation with a mandatory human-review gate.
- Time-varying reverse particle drift with optional spatial Copernicus currents and land blocking.
- Plausible release-time search and probabilistic origin region.
- AIS normalization, quality audit, coverage-aware silence analysis and vessel ranking.
- Forward replay, evidence-quality gates and safe abstention.
- Portable evidence dossier, run manifest, input hashes and technical log.
- Local operator portal and a separate, frozen browser-only prototype package.

## Measured evidence

- Controlled stress suite: 24 seeded cases, 70.8% Top-1, 100% Top-3, 3.73 km median origin error and 7.50 km P90 origin error.
- Known-source history: MV Wakashio ranked #1 of 5, with 0.74 km centroid error.
- Evidence-limited history: Princess Empress compared 52 candidates and correctly abstained because track quality was only 20%.
- SAR V6 development replay: 61.3% oil IoU, 76.0% Dice, 71.7% precision, 80.9% recall and 77.4% average precision.
- Deterministic physics parity: maximum combined endpoint separation from OpenDrift was 11.52 m against a preregistered 250 m acceptance limit.

These results are not a population accuracy estimate, external blind certification, calibrated guilt probability or legal finding.

## Production blockers—not prototype blockers

- More independently verified source-vessel cases across oceans are required for external validity.
- SAR V6 needs a new cross-region blind test and full-scene tiling before operational promotion.
- Licensed terrestrial/satellite AIS is needed where public coverage is delayed or incomplete.
- Operational ocean forecasting should add gridded wind, oil weathering, beaching and resuspension.
- Authentication, access control, durable job storage and monitored deployment are required before multi-user agency use.

## Release controls

- `scripts/package_release_v1.ps1` builds the isolated prototype ZIP.
- `scripts/verify_release_v1.ps1` checks every packaged hash and evidence link.
- `scripts/start_case_portal.ps1` opens the private local operator workflow.
- `docs/prototype` and the public deployment workflow are intentionally outside these release scripts.
