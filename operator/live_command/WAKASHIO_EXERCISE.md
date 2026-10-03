# Wakashio known-source exercise

This is an **interactive controlled exercise**, not a live watch and not a historical AIS replay. It is intentionally separate from the original KESTREL-01 demo and from the operational API.

## Provenance

| Element | Status | Source |
| --- | --- | --- |
| Oil-spill before/after image | Historical observation: Copernicus Sentinel-2, 1 and 6 August 2020 | [Copernicus description](https://www.copernicus.eu/en/media/image-day-gallery/oil-spill-mauritius); [image and reuse terms](https://commons.wikimedia.org/wiki/File:Oil_Spill_in_Mauritius.jpg) |
| MV Wakashio identity and grounding position | Historical official case evidence, **not AIS** | [Mauritius Court of Investigation report](https://blueconomy.govmu.org/Documents/Publications/Report%20Court%20of%20Investigation-MV%20Wakashio.pdf) |
| MV Aster Point and MV Coral Trader | Fictional named exercise vessels | Waypoints in `wakashio-core.js` |
| Wind, current, slick age and oil point | Synthetic exercise assumptions | `wakashio-core.js` |
| Reverse-drift point and ranking | Computed in the browser from the exercise assumptions | `wakashio-core.js` |

The downloaded satellite file is the original credited Copernicus collage. Its SHA-1 is `f3defe8ecf0a009ddf8927a1d40dbcb6bf1e019e`, matching the source file listed on Wikimedia Commons. Attribution: European Union, Copernicus Sentinel-2 imagery (2020). The image is displayed without claiming that ESPADA segmented it.

## Exercise mechanics

At 06:24:49 UTC on 6 August 2020, the interface sets an *illustrative*, not image-extracted, oil point. A synthetic current, windage and small observation offset define it relative to the documented wreck. The starting release-age assumption is 1.5 hours; the judge can vary it from 1 to 3 hours. The page reverses the same stated forcing to infer a candidate origin and time. It compares the official fixed wreck position with two synthetic routes interpolated between explicit waypoints. Their legs are approximately 6–8 knots. The displayed comparative score is `100 / [1 + (distance-to-origin / 3 km)^2]`, rounded. It is **not** a calibrated probability of oil release or guilt. Wakashio ranks first under every age value exposed by the control; the output is priority analyst review, not an accusation.

The satellite image itself names the source vessel. Consequently, this is a known-source plausibility and workflow exercise, **not** a blind identification test. The routes are shown on a separate coordinate chart because the source collage is not a georeferenced analysis raster in this web page. The illustrated point must never be described as a polygon extracted from those pixels.

The old saved Wakashio replay is **not** the evidence source for this exercise: its local oil polygon is named `TSX_20200810_OilSpillExtent_ReefPointeEsny` while its metadata labels an August 6 Sentinel-2 observation. That mismatch requires an independent provenance correction before using that replay as date-matched validation. This exercise uses neither its polygon nor its saved ranking.

## Run and verify

Open `wakashio.html` from the ESPADA live-command static path. It requires no API keys, GPU or provider connection. Run `node --test tests/wakashio_exercise.test.cjs`. The browser smoke check is `node tests/wakashio_exercise_browser.mjs` with `ESPADA_WAKASHIO_URL` pointing to a local HTTP preview. The original KESTREL-01 demo remains at `demo.html`.
