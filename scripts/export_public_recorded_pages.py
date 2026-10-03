"""Export the original five-page operator UI as a read-only GitHub Pages case."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operator/live_command"
TARGET = ROOT / "docs/prototype/recorded"
PAGES = ("index", "detection", "investigation", "cases", "case-file")
SERVER_ASSETS = "/operator/live_command/recorded_case/assets/"
STATIC_ASSETS = "recorded_case/assets/"


def rewrite(value):
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    if isinstance(value, str) and value.startswith(SERVER_ASSETS):
        return STATIC_ASSETS + value[len(SERVER_ASSETS):]
    return value


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    document = (SOURCE / "index.html").read_bytes()
    for page in PAGES:
        (TARGET / f"{page}.html").write_bytes(document)
    for filename in (
        "app.js", "styles.css", "demo.html", "demo.js", "demo.css",
        "wakashio.html", "wakashio.js", "wakashio-core.js", "wakashio.css",
    ):
        shutil.copy2(SOURCE / filename, TARGET / filename)
    (TARGET / "assets").mkdir(exist_ok=True)
    shutil.copy2(
        SOURCE / "assets/wakashio_sentinel2_20200806.jpg",
        TARGET / "assets/wakashio_sentinel2_20200806.jpg",
    )
    source_assets = SOURCE / "recorded_case"
    destination_assets = TARGET / "recorded_case"
    if destination_assets.exists():
        if not destination_assets.resolve().is_relative_to(TARGET.resolve()):
            raise SystemExit("Unsafe static export path")
        shutil.rmtree(destination_assets)
    shutil.copytree(source_assets, destination_assets)
    for path in destination_assets.rglob("*"):
        if path.suffix.lower() not in {".json", ".geojson"}:
            continue
        data = rewrite(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(PAGES)} original operator pages to {TARGET}")


if __name__ == "__main__":
    main()
