"""Freeze one previously completed local case for the public read-only console.

This command does not run inference or invent vessels. It copies only artifacts
referenced by the saved case snapshot into the existing operator web client.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.request import urlopen


PUBLIC_PREFIX = "/operator/live_command/recorded_case/assets/"
LOCAL_PREFIX = "/out/live_operations/"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--snapshot-url", default="http://127.0.0.1:4186/api/live/snapshot")
    parser.add_argument("--target-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    output = args.target_root.resolve() / "operator/live_command/recorded_case"
    if not output.resolve().is_relative_to(args.target_root.resolve()):
        raise SystemExit("Unsafe output directory")
    with urlopen(args.snapshot_url, timeout=30) as response:
        snapshot = json.load(response)

    scene_id = snapshot.get("analysis", {}).get("scene_id")
    if not scene_id or snapshot.get("attribution", {}).get("status") != "COMPLETE":
        raise SystemExit("The local snapshot has no completed active investigation")
    if snapshot.get("response", {}).get("status") != "READY":
        raise SystemExit("The active investigation has no completed evidence dossier")

    snapshot["recorded_case"] = {
        "mode": "RECORDED_READ_ONLY",
        "scene_id": scene_id,
        "description": "Previously computed real-data case; not a live provider snapshot or a new inference run.",
        "public_copy": "View-only subset with machine paths redacted; source manifest verification was performed locally.",
    }
    snapshot["status"] = "RECORDED"
    snapshot["generated_at_utc"] = snapshot.get("response", {}).get("generated_at_utc")
    snapshot["updated_at_utc"] = snapshot.get("response", {}).get("generated_at_utc")
    snapshot["ais"] = {
        "positions": [], "tracks": {}, "position_count": 0,
        "vessel_count": snapshot["attribution"].get("candidate_count", 0),
        "underway_count": 0, "stationary_count": 0, "window_minutes": None,
        "mode": "recorded_case",
        "evidence_time_utc": snapshot["attribution"].get("release_time_utc"),
    }
    snapshot["sources"]["ais"] = {
        "status": "RECORDED", "kind": "HISTORICAL AIS CONTEXT",
        "provider": snapshot["attribution"].get("ais_source"),
        "latest_observation_utc": snapshot["attribution"].get("release_time_utc"),
        "message": "Historical AIS context retained in the case file; not current ship positions.",
    }
    snapshot["sources"]["environment"] = {
        "status": "RECORDED", "kind": "HISTORICAL MODEL FORCING",
        "provider": snapshot["attribution"].get("forcing_source"),
        "latest_observation_utc": snapshot["attribution"].get("release_time_utc"),
        "current": {}, "temporal_resolution": "historical case",
        "message": "Date-matched forcing preserved in the recorded reconstruction.",
    }
    analysis = snapshot["analysis"]
    snapshot["sources"]["sentinel"] = {
        "status": "RECORDED", "kind": "RECORDED SAR ACQUISITION",
        "provider": "Copernicus Data Space Sentinel-1",
        "latest_observation_utc": analysis.get("acquisition_time_utc"),
        "scenes_returned": 1,
        "latest_scene": {"id": scene_id, "platform": "Sentinel-1", "polarizations": ["VV"], "acquisition_time_utc": analysis.get("acquisition_time_utc")},
        "scenes": [{"id": scene_id, "platform": "Sentinel-1", "polarizations": ["VV"], "acquisition_time_utc": analysis.get("acquisition_time_utc"), "processed_evidence": True}],
        "message": "Previously downloaded and analysed scene; not today's satellite catalogue.",
    }
    register = snapshot.get("case_register", {})
    active = [case for case in register.get("cases", []) if case.get("scene_id") == scene_id]
    if not active:
        raise SystemExit("Active case is missing from the case register")
    register["cases"] = active
    register["case_count"] = 1
    register["sealed_count"] = 1
    register["review_required_count"] = 0
    register["integrity_verified_count"] = int(active[0].get("integrity_status") == "VERIFIED")
    register["provenance_warning_count"] = len(active[0].get("provenance_warnings", []))

    # Only the active recorded case and its coastline can be published. Other
    # local cases may be incomplete, private, or unrelated to this recording.
    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items() if key not in {"last_error", "api_key", "token", "secret"}}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and re.match(r"^[A-Za-z]:[\\/]", value):
            return "Local source artifact: " + Path(value).name
        if isinstance(value, str) and value.startswith(LOCAL_PREFIX):
            return PUBLIC_PREFIX + value[len(LOCAL_PREFIX):]
        return value

    snapshot = rewrite(snapshot)
    body = json.dumps(snapshot, indent=2, ensure_ascii=False)
    referenced = sorted(set(re.findall(re.escape(PUBLIC_PREFIX) + r'[^"\\\s]+', body)))
    allowed_roots = {f"analysis/{scene_id}/", "coast/"}
    if output.exists():
        shutil.rmtree(output)
    (output / "assets").mkdir(parents=True)
    for url in referenced:
        relative = url[len(PUBLIC_PREFIX):]
        if not any(relative.startswith(root) for root in allowed_roots):
            raise SystemExit(f"Unexpected artifact outside this case: {relative}")
        source = (source_root / "out/live_operations" / relative).resolve()
        if not source.is_relative_to(source_root) or not source.is_file():
            raise SystemExit(f"Missing recorded artifact: {source}")
        target = output / "assets" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if target.suffix.lower() in {".json", ".geojson"}:
            try:
                public_data = rewrite(json.loads(target.read_text(encoding="utf-8")))
            except json.JSONDecodeError as error:
                raise SystemExit(f"Cannot safely publish malformed JSON: {relative}") from error
            target.write_text(json.dumps(public_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "snapshot.json").write_text(body + "\n", encoding="utf-8")
    if re.search(r"[A-Z]:\\\\Users\\\\|/Users/|api_key|client_secret", body, re.IGNORECASE):
        raise SystemExit("Potential private path or credential in public snapshot")
    print(f"Packaged {scene_id}: {len(referenced)} evidence assets, read-only snapshot")


if __name__ == "__main__":
    main()
