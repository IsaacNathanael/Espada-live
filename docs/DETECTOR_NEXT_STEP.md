# Detector decision after the DARTIS comparison

Decision: keep POSEatSea as the leading segmentation challenger and V6 as the
baseline. Neither is an operational detector. No checkpoint has been replaced.

## Evidence and its limits

On the same 100 DARTIS images, POSEatSea matched 43 of 95 oil objects, compared
with V6's 42. Its main improvement was reducing unmatched proposals from 583
to 81. No-oil images with false alarms fell from 36/50 to 11/50.
The object F1 scores were 39.27% and 11.67%. These are bounding-box matching
scores, not segmentation Dice or pixel IoU.

Coastal recall is the immediate weakness: POSEatSea matched 15/58 coastal oil
objects, versus 28/37 open-water objects. These counts establish the failure
pattern, not its cause. Review saved overlays for missed objects, fragmented
predictions, merged predictions and localization errors before changing training.

Filtering false alarms alone cannot recover the 52 missed objects. Physics
context should flag detectability and ambiguity for an analyst; wind or proximity
to a ship cannot establish that an oil detection is correct. Reverse drift remains
a separate, downstream calculation.

## Data preparation completed

The metadata-only planner is `src/espada/dartis_split.py`. Its output is
`out/dartis_data_plan/partition_plan.json`. It groups patches transitively by
any shared source scene or UTC acquisition date, including multi-scene mosaics.
All groups touching the reviewed 100-image audit are excluded from new validation
and test partitions. Seed 2614307 is fixed before further model results.

| Partition | Images | Acquisition groups |
|---|---:|---:|
| Previously reviewed acquisition groups, development only | 1452 | 84 |
| New training allocation | 1478 | 144 |
| New validation allocation | 428 | 48 |
| Reserved test allocation | 297 | 28 |

Every allocation contains oil/water, oil/coast, no-oil/water and no-oil/coast.
These are reservations from metadata; the additional images have not been
downloaded. After download, check exact/perceptual duplicates and persistent
events across partitions. Date isolation alone does not establish geographic or
event independence. Unknown POSEatSea training overlap also prevents calling its
reserved test definitively blind.

## Next implementation: a small box-supervised pilot

1. Completed: the resumable pilot downloader selects 60 training and 20
   validation images per subset (240 + 80 total) across groups deterministically.
   It keeps reserved test images unopened, saves metadata, attribution and file
   hashes, and flags cross-partition duplicates. Run
   `scripts\download_dartis_pilot.ps1`; use `-PlanOnly` for a network-free check.
2. Use DARTIS boxes to train an object detector for oil candidates, with no-oil
   images providing negative examples. Select one modest pretrained detector
   after checking its input contract and license. Keep training on the user's GPU.
   Do not paint oil boxes as pixel masks: much of each box can be ordinary water.
3. Compare candidate localization and false alarms on the pilot validation set.
   Include coastal/open-water breakdowns and review images. Use validation for
   thresholds; never optimize the reserved test. Expand the training allocation
   only after the pilot proves the data pipeline and shows useful learning.
4. Use a validated candidate detector to propose regions for segmentation and
   human review. POSEatSea's outlines remain experimental; box detections alone
   are not measured oil polygons. Any segmentation fine-tuning needs genuine
   pixel masks or an explicitly validated weak-supervision method.
5. Measure the combined detector, segmentation and context checks on new data,
   including independently labelled Indian Ocean imagery, before operational
   claims. A satellite image alone does not supply an oil label.

Physics, AIS and multiple satellites cannot compensate for missing or incorrect
training labels. Additional satellites should be integrated only with compatible
preprocessing, timestamps and evaluation data. Human-approved polygons continue
through reverse drift, historical AIS ranking and forward verification.

## Handoff and checks

Completed: comparison interpretation, metadata partition allocation, three tests
covering transitive scene/date overlap, mosaic dates, determinism and invalid
input; successful preparation against all 3655 metadata records.

Pending: pilot image download, annotation/duplicate review, box-detector training
adapter and pilot evaluation. No new model training, network download or physics
filter was run while preparing this code. No score target is guaranteed.

Sources:
- DARTIS metadata and license: https://doi.org/10.1594/PANGAEA.980773
- Dataset/annotation description: https://essd.copernicus.org/articles/17/6807/2025/
- POSEatSea checkpoint contract: https://huggingface.co/23f2003521/poseatsea-weights
- Local measured results: `out/model_comparison/comparison.json`
