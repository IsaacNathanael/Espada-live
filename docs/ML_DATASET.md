# SAR segmentation dataset

ESPADA uses the open **Oil Spill Segmentation** dataset published on Zenodo under
CC BY 4.0: <https://doi.org/10.5281/zenodo.4672426>.

Zenodo describes 23 Sentinel-1A GRD VV scenes from the Gulf of Mexico (2018-2020)
with expert masks based on NOAA high-confidence oil-spill reports. The published
v1 archive actually contains 21 paired image/mask TIFF scenes. The source
archive is 487,624,870 bytes; ESPADA verifies its published MD5 checksum before
using it. The archive and extracted files are intentionally excluded from Git.

Download, verify, and extract it from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\download_oil_dataset.ps1
```

The command is resumable. If the connection stops, run exactly the same command
again. Then audit it:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\audit_oil_dataset.ps1
```

The supplied training and validation CSVs reuse all 14 training scenes, while
several nominal train/test files also share acquisition dates. ESPADA therefore
ignores those supplied splits and deterministically groups every related image by
acquisition date before assigning train, validation and test. This prevents model
evaluation on near-related imagery seen during training.

This is a compact, practical training source. It is not sufficient by itself to
claim universal real-world accuracy: different oceans, seasons, sensors and oil
lookalikes remain distribution shifts.
