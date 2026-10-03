# Recorded known-source exercise

Open the unchanged recorded console at `/recorded/index.html`, then use its
existing **Cases** page to select **Mauritius · known-source exercise**. All
five original pages load the selected case; select the Singapore row to return.

The case is deliberately **not** a historical AIS attribution or a blind test.

| Element | Status |
| --- | --- |
| MV Wakashio grounding and responsibility for the spill | Documented by the [Mauritius Ministry of Environment](https://environment.govmu.org/Pages/wakashio.aspx) |
| 11 August 2020 Sentinel-1 spill preview | Real [ESA/Sentinel Hub image on Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Sentinel-1_(IW-VVVH)_image_on_2020-08-11_(1).jpg), CC BY-SA 4.0; public display image, not calibrated raster pixels |
| 06:00 UTC observation hour and 02:00 UTC release hour | Exercise assumptions; the image source verifies the date, not the hour |
| Slick outline, constant drift, origin zone, uncertainty and forward replay | Constructed exercise inputs; not recovered measurements |
| MV Coral Trader and MV Aster Point | Fictional named alternatives with synthetic offshore routes and plausible waypoint speeds |
| Exercise IDs | Not MMSIs or received AIS fixes |
| Score and limited shortlist | Comparative exercise output only, not a guilt probability or enforcement finding |

The deterministic builder is `scripts/build_recorded_known_source_case.py`.
It checks that the source lies within 0.3 km of the reconstructed exercise
origin, that underway leg speeds are 3–12 kn, that two routes pass the 20 km
origin filter, and that Wakashio clearly outranks the other retained route.
`tests/recorded_two_cases_browser.mjs` verifies the five-page UI, switching in
both directions, the local SAR image, ranking, map routes and dossier link.

The coastline comes from [geoBoundaries Mauritius ADM0](https://www.geoboundaries.org/api/current/gbOpen/MUS/ADM0/) (CC BY 4.0). The D3 copy reverses polygon winding for display but preserves the vertices.
