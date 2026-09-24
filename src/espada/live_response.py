from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _number(value: object, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _artifact_specs() -> list[tuple[str, str, str, str, bool]]:
    return [
        ("sentinel_source", "Observed SAR source", "OBSERVED", "input/sentinel1_vv.tif", True),
        ("sentinel_metadata", "Satellite acquisition metadata", "CATALOGUE", "input/sentinel1_subset_status.json", True),
        ("model_result", "Frozen SAR model result", "INFERRED", "segmentation/sar_result.json", True),
        ("physics_screen", "Physical plausibility screen", "INFERRED", "segmentation/physics_screen.json", True),
        ("analyst_decision", "Analyst-approved slick geometry", "REVIEWED", "review/approved_slick.geojson", True),
        ("environment", "Date-matched ocean and wind fields", "MODELLED", "attribution/environment/environment.json", True),
        ("release_estimate", "Reverse-drift release estimate", "INFERRED", "attribution/drift/release_estimate.json", True),
        ("origin_zone", "Probable origin-zone geometry", "INFERRED", "attribution/origin_zone.geojson", True),
        ("ais_positions", "Normalized historical AIS evidence", "OBSERVED", "attribution/ais/ais_normalized.csv", True),
        ("ais_quality", "AIS quality audit", "AUDIT", "attribution/ais/ais_quality.json", True),
        ("candidate_ranking", "Candidate ranking ledger", "INFERRED", "attribution/ranking/candidates.json", True),
        ("candidate_tracks", "Compared candidate tracks", "OBSERVED", "attribution/candidate_tracks.geojson", True),
        ("sar_diagnostic", "SAR interpretation overview", "DIAGNOSTIC", "segmentation/sar_segmentation_overview.png", False),
        ("drift_diagnostic", "Reverse-drift diagnostic", "DIAGNOSTIC", "attribution/drift/slick_reverse_analysis.png", False),
        ("ranking_diagnostic", "Candidate ranking chart", "DIAGNOSTIC", "attribution/ranking/candidate_ranking.png", False),
        ("attribution_diagnostic", "Forward-verification map", "DIAGNOSTIC", "attribution/ranking/attribution_map.png", False),
    ]


def _candidate_name(candidate: dict[str, Any]) -> str:
    supplied = str(candidate.get("vessel_name") or "").strip()
    return supplied if supplied and supplied.upper() != "UNKNOWN" else "Unverified vessel identity"


def build_live_response_package(
    run_root: Path,
    *,
    analysis: dict[str, Any],
    review: dict[str, Any],
    attribution: dict[str, Any],
    sources: dict[str, Any],
) -> dict[str, Any]:
    """Build an auditable response package without making an enforcement claim."""
    run_root = Path(run_root).resolve()
    response_dir = run_root / "response"
    response_dir.mkdir(parents=True, exist_ok=True)
    generated = _utc_now()

    artifacts: list[dict[str, Any]] = []
    for artifact_id, label, evidence_type, relative, required in _artifact_specs():
        path = run_root / relative
        digest = _sha256(path)
        artifacts.append(
            {
                "id": artifact_id,
                "label": label,
                "evidence_type": evidence_type,
                "relative_path": relative.replace("\\", "/"),
                "required": required,
                "status": "VERIFIED" if digest else "MISSING",
                "bytes": path.stat().st_size if digest else None,
                "sha256": digest,
            }
        )

    verified = [item for item in artifacts if item["status"] == "VERIFIED"]
    missing_required = [
        item for item in artifacts if item["required"] and item["status"] != "VERIFIED"
    ]
    chain_payload = "\n".join(
        f"{item['id']}|{item['relative_path']}|{item['sha256']}"
        for item in artifacts
        if item["sha256"]
    )
    chain_digest = hashlib.sha256(chain_payload.encode("utf-8")).hexdigest()

    candidates = list(attribution.get("candidates") or [])
    top = candidates[0] if candidates else dict(attribution.get("top_candidate") or {})
    top_score = float(top.get("total_score") or 0.0)
    tied_at_top = sum(
        1 for candidate in candidates if abs(float(candidate.get("total_score") or 0.0) - top_score) < 1e-9
    )
    nomination = dict(attribution.get("nomination_assessment") or {})
    near_tied_count = int(nomination.get("near_tied_count") or tied_at_top)
    score_margin = float(
        nomination.get("score_margin")
        if nomination.get("score_margin") is not None
        else attribution.get("score_margin") or 0.0
    )
    decision = str(attribution.get("decision") or "ABSTAIN_INSUFFICIENT_EVIDENCE")
    abstain = decision.startswith("ABSTAIN")
    integrity_status = "VERIFIED" if not missing_required else "INCOMPLETE"
    response_status = (
        "PACKAGE_INCOMPLETE"
        if missing_required
        else "SAFE_ABSTENTION_READY"
        if abstain
        else "ANALYST_SHORTLIST_READY"
    )
    permitted_action = "PRESERVE AND SEEK MORE EVIDENCE" if abstain else "HUMAN REVIEW ONLY"
    rationale = str(nomination.get("rationale") or "").strip() or (
        f"{near_tied_count} candidates sit within the ambiguity band and the lead is "
        f"{score_margin * 100:.1f} points. No vessel is nominated."
        if abstain
        else "The minimum shortlist gates passed. Independent corroboration remains mandatory."
    )
    recommended_actions = [
        "Preserve the generated evidence package and its integrity digest.",
        "Acquire terrestrial or commercial AIS for the incident window.",
        "Request an independent SAR, optical, port-log or sampling record.",
        "Re-run attribution only when new evidence changes the case record.",
    ]
    blocked_actions = [
        "Automatic vessel accusation",
        "Enforcement notification without human review",
        "Treating comparative scores as guilt probabilities",
    ]

    manifest = {
        "schema": "espada.live-evidence-manifest.v1",
        "generated_at_utc": generated,
        "scene_id": str(analysis.get("scene_id") or run_root.name),
        "integrity_status": integrity_status,
        "verified_files": len(verified),
        "required_files": sum(1 for item in artifacts if item["required"]),
        "missing_required_files": len(missing_required),
        "chain_digest_sha256": chain_digest,
        "artifacts": artifacts,
    }
    summary = {
        "schema": "espada.live-response-summary.v1",
        "generated_at_utc": generated,
        "scene_id": str(analysis.get("scene_id") or run_root.name),
        "response_status": response_status,
        "operational_decision": decision,
        "permitted_action": permitted_action,
        "vessel_nominated": False if abstain else None,
        "rationale": rationale,
        "candidate_count": int(attribution.get("candidate_count") or len(candidates)),
        "top_candidate": top or None,
        "score_margin": score_margin,
        "tied_at_top": tied_at_top,
        "near_tied_count": near_tied_count,
        "nomination_assessment": nomination or None,
        "release_time_utc": attribution.get("release_time_utc"),
        "observation_time_utc": attribution.get("observation_time_utc"),
        "credible_radius_90_km": attribution.get("credible_radius_90_km"),
        "review": {
            "status": review.get("status"),
            "reviewed_at_utc": review.get("reviewed_at_utc"),
            "assumed_age_hours": review.get("assumed_age_hours"),
        },
        "source_status_at_export": {
            name: {
                "provider": source.get("provider"),
                "status": source.get("status"),
                "latest_observation_utc": source.get("latest_observation_utc"),
            }
            for name, source in sources.items()
        },
        "recommended_actions": recommended_actions,
        "blocked_actions": blocked_actions,
        "claim_boundary": (
            "Investigative evidence only; not proof of identity, discharge, intent or guilt."
        ),
    }
    bundle = {
        "schema": "espada.live-evidence-bundle.v1",
        "summary": summary,
        "manifest": manifest,
        "attribution": {
            "decision": decision,
            "estimated_origin": attribution.get("estimated_origin"),
            "credible_radius_90_km": attribution.get("credible_radius_90_km"),
            "candidates": candidates,
            "nomination_assessment": nomination or None,
        },
    }

    manifest_path = response_dir / "evidence_manifest.json"
    summary_path = response_dir / "response_summary.json"
    bundle_path = response_dir / "evidence_bundle.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    candidate_rows = "".join(
        "<tr>"
        f"<td>#{html.escape(str(candidate.get('rank', '—')))}</td>"
        f"<td><b>{html.escape(_candidate_name(candidate))}</b><small>MMSI {html.escape(str(candidate.get('mmsi', '—')))}</small></td>"
        f"<td>{_percent(candidate.get('total_score'))}</td>"
        f"<td>{_number(candidate.get('forward_error_km'), ' km')}</td>"
        f"<td>{_percent(candidate.get('data_quality'))}</td>"
        "</tr>"
        for candidate in candidates[:8]
    )
    manifest_rows = "".join(
        "<tr>"
        f"<td><span class='type'>{html.escape(str(item['evidence_type']))}</span></td>"
        f"<td><b>{html.escape(str(item['label']))}</b><small>{html.escape(str(item['relative_path']))}</small></td>"
        f"<td><span class='state {str(item['status']).lower()}'>{html.escape(str(item['status']))}</span></td>"
        f"<td class='hash'>{html.escape(str(item['sha256'] or '—'))}</td>"
        "</tr>"
        for item in artifacts
    )
    action_rows = "".join(
        f"<li><span>{index:02d}</span><p>{html.escape(action)}</p><b>RECOMMENDED</b></li>"
        for index, action in enumerate(recommended_actions, 1)
    )
    blocked_rows = "".join(f"<li>{html.escape(action)}</li>" for action in blocked_actions)
    scene_id = html.escape(str(summary["scene_id"]))
    top_name = html.escape(_candidate_name(top)) if top else "No candidate"
    decision_label = "ABSTAIN · INSUFFICIENT EVIDENCE" if abstain else "LIMITED SHORTLIST"
    dossier_path = response_dir / "evidence_dossier.html"
    dossier_path.write_text(
        f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESPADA Evidence Dossier</title><style>
:root{{--navy:#082638;--ink:#13252e;--paper:#f4f0e6;--sheet:#fffdf7;--line:#b9b4aa;--amber:#c27a13;--red:#963d35;--green:#286c57;--blue:#2c647c;--muted:#657177}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,sans-serif}}main{{width:min(1180px,calc(100% - 32px));margin:auto;padding:26px 0 52px}}header{{display:flex;justify-content:space-between;gap:24px;padding:18px 20px;background:var(--navy);color:#fff}}.brand{{font:700 12px/1.2 Consolas,monospace;letter-spacing:.18em}}.brand small{{display:block;margin-top:7px;color:#adc0c8;font:400 9px Arial,sans-serif;letter-spacing:.02em}}.status{{align-self:center;padding:9px 12px;border:1px solid #d7a6a0;background:#401f20;color:#ffd9d3;font:700 10px Consolas,monospace}}.hero{{display:grid;grid-template-columns:1.2fr .8fr;border:1px solid var(--line);border-top:0;background:var(--sheet)}}.decision,.case{{padding:24px}}.case{{border-left:1px solid var(--line);background:#e9e5da}}.eyebrow{{color:var(--amber);font:700 9px Consolas,monospace;letter-spacing:.13em}}h1,h2,h3{{font-family:Georgia,serif}}h1{{max-width:700px;margin:9px 0 12px;font-size:38px;line-height:1.02}}p{{font-size:12px;line-height:1.55}}.decision p{{max-width:700px;color:var(--muted)}}.case strong{{display:block;margin:9px 0;color:var(--red);font:700 18px Georgia,serif}}.facts{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-top:0;background:var(--sheet)}}.facts div{{min-height:82px;padding:15px;border-right:1px solid var(--line)}}.facts div:last-child{{border:0}}.facts span,.panel-head span{{color:var(--muted);font:700 8px Consolas,monospace;letter-spacing:.1em}}.facts b{{display:block;margin-top:9px;font:700 18px Georgia,serif}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}.panel{{border:1px solid var(--line);background:var(--sheet)}}.panel-head{{padding:15px 17px;border-bottom:1px solid var(--line)}}.panel-head h2{{margin:6px 0 0;font-size:20px}}.visual img{{display:block;width:100%}}.actions ol{{list-style:none;margin:0;padding:0}}.actions li{{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;min-height:62px;padding:10px 16px;border-bottom:1px solid #ded9cf}}.actions li span{{color:#9b9b91;font:700 14px Consolas,monospace}}.actions li p{{margin:0}}.actions li b{{color:var(--green);font:700 8px Consolas,monospace}}.blocked{{margin:14px 17px;padding:12px 14px;border-left:4px solid var(--red);background:#f3dfdc}}.blocked b{{color:var(--red);font:700 9px Consolas,monospace}}.blocked ul{{margin:8px 0 0;padding-left:17px;font-size:10px;line-height:1.6}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 13px;border-bottom:1px solid #ded9cf;text-align:left;font-size:10px;vertical-align:top}}th{{background:#e9e5da;color:var(--muted);font:700 8px Consolas,monospace;letter-spacing:.08em}}td small{{display:block;margin-top:4px;color:var(--muted)}}.type{{color:var(--blue);font:700 8px Consolas,monospace}}.state{{font:700 8px Consolas,monospace}}.state.verified{{color:var(--green)}}.state.missing{{color:var(--red)}}.hash{{max-width:330px;overflow-wrap:anywhere;color:var(--muted);font:8px Consolas,monospace}}.wide{{margin-top:14px}}.digest{{padding:15px 17px;background:var(--navy);color:#fff;font:9px Consolas,monospace;overflow-wrap:anywhere}}.boundary{{margin-top:14px;padding:15px 17px;border-left:5px solid var(--amber);background:#eadfc9;color:#65420e;font-size:11px;line-height:1.5}}@media(max-width:760px){{.hero,.grid{{grid-template-columns:1fr}}.case{{border-left:0;border-top:1px solid var(--line)}}.facts{{grid-template-columns:1fr 1fr}}.facts div:nth-child(2){{border-right:0}}h1{{font-size:29px}}.actions li{{grid-template-columns:30px 1fr}}.actions li b{{grid-column:2}}}}
</style></head><body><main><header><div class='brand'>ESPADA · EVIDENCE DOSSIER<small>Reverse Drift Attribution · generated {generated}</small></div><div class='status'>{html.escape(decision_label)}</div></header><section class='hero'><article class='decision'><div class='eyebrow'>OPERATIONAL RESPONSE</div><h1>{'No vessel nominated.' if abstain else 'Shortlist for human review only.'}</h1><p>{html.escape(rationale)} The system preserves the evidence and controls the consequence instead of converting uncertainty into an accusation.</p></article><aside class='case'><div class='eyebrow'>CASE REFERENCE</div><strong>{scene_id}</strong><p>{html.escape(str(summary['permitted_action']))}</p></aside></section><section class='facts'><div><span>EVIDENCE FILES</span><b>{len(verified)} verified</b></div><div><span>CANDIDATES COMPARED</span><b>{html.escape(str(summary['candidate_count']))}</b></div><div><span>TOP COMPARATIVE MATCH</span><b>{_percent(top.get('total_score')) if top else '—'}</b></div><div><span>90% ORIGIN RADIUS</span><b>{_number(summary['credible_radius_90_km'], ' km')}</b></div></section><section class='grid'><article class='panel actions'><div class='panel-head'><span>CONTROLLED RESPONSE</span><h2>What happens next</h2></div><ol>{action_rows}</ol><div class='blocked'><b>AUTOMATIC ACTIONS BLOCKED</b><ul>{blocked_rows}</ul></div></article><article class='panel visual'><div class='panel-head'><span>FORWARD VERIFICATION</span><h2>Candidate tracks against the slick</h2></div><img src='../attribution/ranking/attribution_map.png' alt='Forward-verification map'></article></section><section class='grid'><article class='panel visual'><div class='panel-head'><span>OBSERVED → INFERRED</span><h2>Reverse-drift uncertainty</h2></div><img src='../attribution/drift/slick_reverse_analysis.png' alt='Reverse-drift reconstruction diagnostic'></article><article class='panel'><div class='panel-head'><span>TOP EVIDENCE MATCHES</span><h2>Review queue</h2></div><table><thead><tr><th>Rank</th><th>Identity</th><th>Score</th><th>Forward error</th><th>Track quality</th></tr></thead><tbody>{candidate_rows}</tbody></table><div class='boundary'>Top candidate: <b>{top_name}</b>. Comparative evidence prioritizes review; it does not verify identity or prove a discharge.</div></article></section><article class='panel wide'><div class='panel-head'><span>CHAIN OF CUSTODY</span><h2>Evidence integrity register</h2></div><table><thead><tr><th>Type</th><th>Artifact</th><th>Status</th><th>SHA-256</th></tr></thead><tbody>{manifest_rows}</tbody></table><div class='digest'>CHAIN DIGEST · {chain_digest}</div></article><div class='boundary'><b>CLAIM BOUNDARY</b> · Investigative evidence only. This dossier is not proof of vessel identity, pollutant discharge, intent or guilt. Any enforcement action requires independent corroboration and accountable human review.</div></main></body></html>""",
        encoding="utf-8",
    )

    return {
        "status": "PASS",
        "response_status": response_status,
        "operational_decision": decision,
        "permitted_action": permitted_action,
        "rationale": rationale,
        "generated_at_utc": generated,
        "verified_files": len(verified),
        "required_files": manifest["required_files"],
        "missing_required_files": len(missing_required),
        "chain_digest_sha256": chain_digest,
        "recommended_actions": recommended_actions,
        "blocked_actions": blocked_actions,
        "dossier_path": dossier_path,
        "bundle_path": bundle_path,
        "manifest_path": manifest_path,
        "summary_path": summary_path,
    }
