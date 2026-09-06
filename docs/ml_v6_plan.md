# V6 improvement experiment

V5 remains the frozen operational baseline. V6 is an experiment and must not replace it merely because training or validation numbers look better.

## Why augmentation alone is insufficient

The labelled archive contains 21 scenes. The leakage-safe manifest leaves 14 training scenes from nine acquisition dates, three validation scenes from two dates and four development-replay scenes from three dates. V5 already presents 2,310 sampled patches over as many as 40 epochs, producing roughly 92,400 stochastic training views. More variations help invariance, but they do not create new sea states, sensors, regions or trustworthy labels.

## V6-A: recommended first experiment

V6-A warm-starts the complete V5 network and fine-tunes it at a lower learning rate. Its `sar_v6` Albumentations policy adds synchronized D4 orientation changes, mild affine scale/translation/rotation, backscatter brightness/contrast and gamma shifts, multiplicative or Gaussian speckle-like noise, and mild blur/downsampling. Validation and test masks are never augmented.

Calibration can use `flip4` test-time augmentation. It averages the original, horizontal flip, vertical flip and double-flip predictions before choosing one validation-only threshold. The same inference policy is recorded in the calibration file and automatically used during evaluation and operational prediction.

Commands:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model_v6.ps1 -Smoke
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_model_v6.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\calibrate_sar_model.ps1 -Version v6 -Tta flip4 -BatchSize 4
powershell -ExecutionPolicy Bypass -File .\scripts\evaluate_sar_model.ps1 -Version v6 -BatchSize 4
```

The smoke run writes to `out\ml_training_v6_smoke`, so it cannot overwrite the full V6 or V5 checkpoints.

## Pretrained-model shortlist

| Candidate | SAR support | Fit for current VV-only labels | Decision |
|---|---:|---:|---|
| SSL4EO-S12 MoCo ResNet50 | Sentinel-1 VV+VH | Good after VV-channel adaptation | Current V5 baseline |
| SoftCon ResNet50 | Sentinel-1 VV+VH | Good after VV-channel adaptation | First encoder comparison |
| DeCUR ResNet50 | Sentinel-1 VV+VH | Technically compatible | Compare only if SoftCon is inconclusive |
| CROMA | Requires two-channel Sentinel-1; optional Sentinel-2 fusion | Poor until VH is available | Defer |
| TerraMind | Supports S1 GRD/RTC and multimodal segmentation | Promising but heavy; best with genuine multimodal inputs | Defer |
| Copernicus-FM | Flexible Copernicus modalities | Promising but substantially more integration risk | Research track |

SoftCon is the only useful immediate drop-in comparison because it retains the same ResNet50 structure. Downloading and training it are optional, long-running user tasks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_softcon_encoder.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_softcon_experiment.ps1 -Smoke
powershell -ExecutionPolicy Bypass -File .\scripts\train_sar_softcon_experiment.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\calibrate_sar_model.ps1 -Version v6_softcon -Tta flip4 -BatchSize 4
powershell -ExecutionPolicy Bypass -File .\scripts\evaluate_sar_model.ps1 -Version v6_softcon -BatchSize 4
```

Sources: [SSL4EO-S12](https://github.com/DLR-MF-DAS/SSL4EO-S12), [SoftCon](https://github.com/zhu-xlab/softcon), [CROMA](https://github.com/antofuller/CROMA), [TerraMind](https://github.com/IBM/terramind), and [Copernicus-FM](https://github.com/zhu-xlab/Copernicus-FM).

## Promotion rule

V6 is promoted only if it improves validation macro average precision and the four-scene development replay without materially worsening the weakest scene, precision or false-positive rate. Even then, it is a better development model—not a new blind-test claim. Production confidence still requires new labelled acquisition groups from additional regions.
