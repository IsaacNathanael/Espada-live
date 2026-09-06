# ESPADA V5 SAR milestone

V5 uses an attention-gated ResNet50 U-Net initialized from SSL4EO-S12 Sentinel-1 MoCo weights. The encoder is frozen for four epochs, then fine-tuned at one tenth of the decoder learning rate. Encoder BatchNorm statistics stay frozen, decoder normalization uses GroupNorm, gradients accumulate to an effective batch size of eight, and training patches are sampled with equal expected mass per scene.

## Validation-only calibration

- Checkpoint epoch: 33
- Threshold: 0.114
- Oil IoU: 55.87%
- Dice/F1: 71.69%
- Precision: 68.75%
- Recall: 74.90%
- Average precision: 76.84%

## Development replay

The replay uses four scenes from three acquisition dates that were originally held out for V3. Because V3 failures informed later engineering, these scenes are no longer an untouched final test.

- Oil IoU: 59.58%
- Dice/F1: 74.67%
- Precision: 68.19%
- Recall: 82.51%
- Average precision: 74.72%
- False-positive rate: 0.83%

Per-scene IoU was 62.58%, 51.95%, 41.72%, and 63.16%. The previous catastrophic failure on the 2020-02-24 acquisition improved substantially, but an external acquisition-isolated labelled dataset is still required for a defensible generalization claim.
