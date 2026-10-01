# ESPADA historical case site

This is a separate, read-only reconstruction of the August 2020 MV Wakashio incident. It uses the same five-page visual language as the live operator console, but makes **no live provider calls** and requires no API keys. Open `index.html` directly or through the ESPADA local server at `/operator/historical_case/index.html`.

## Evidence boundaries

- The slick polygon is independent UNITAR-UNOSAT Sentinel-2 mapping, not an oil slick newly detected by ESPADA. It appears in the timeline only at its satellite observation time.
- The 72 vessel positions are recorded Global Fishing Watch hourly presence at grid-cell centres for five MMSIs, not continuous AIS tracks. Joined map lines are visual orientation only. The fixed wreck position comes from the official casualty report, not AIS.
- The origin region, candidate ranking, and review decision are saved outputs from the existing ESPADA analysis. The replay does not rerun models or prove that a vessel caused the spill.
- Several display names come from later registry references, so their names at the 2020 incident time are unverified. One vessel is intentionally identified only by MMSI.

Source links and SHA-256 hashes are available on the Evidence & decision page. Historical Global Fishing Watch vessel-presence data is used noncommercially under CC BY-NC 4.0; [Powered by Global Fishing Watch.](https://globalfishingwatch.org)

## Rebuild and verify

From the repository root, `python scripts/build_wakashio_site.py` rebuilds the frozen data and analytical image from the saved case artifacts. Run `python -m pytest tests/test_historical_case_site.py` and `node tests/historical_case_browser.mjs` for data and browser checks. The browser test uses a locally installed Chrome executable; the site itself needs no Node packages.
