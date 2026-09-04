# ESPADA release history

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
