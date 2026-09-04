# Espada master specification

## Product boundary

Espada is an investigation-support system. It produces ranked candidate vessels and explicit uncertainty; it does not establish intent, liability, or guilt.

## Planned workflow

1. Detect a suspected slick in Sentinel-1C/1D SAR imagery.
2. Apply environmental and morphological quality checks.
3. Evaluate multiple plausible release ages.
4. Run a backward drift ensemble to produce a space-time origin distribution.
5. Retrieve AIS-linked vessels intersecting that distribution.
6. Forward-simulate candidate releases.
7. Score space-time presence, forward consistency, data quality, and cautious AIS-gap evidence.
8. Produce a ranked investigation brief with assumptions and limitations.

## Immediate milestone

Verify forward and backward constant-field physics with reproducible synthetic particles. No SAR, AIS, attribution, or live data is claimed in this milestone.

