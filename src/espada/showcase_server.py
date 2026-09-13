from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .demo import run_demo
from .attribution import rank_candidates


class ShowcaseHandler(SimpleHTTPRequestHandler):
    project_root: Path
    run_lock = threading.Lock()

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/api/health":
            self._json(
                200,
                {
                    "status": "PASS",
                    "service": "ESPADA local evidence engine",
                    "capabilities": ["known-source-run", "operations-ranking", "static-showcase"],
                    "network_scope": "127.0.0.1 only",
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in {"/api/run-known-source", "/api/run-operations"}:
            self._json(404, {"status": "FAIL", "error": "Unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 32_768:
            self._json(413, {"status": "FAIL", "error": "Request is too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"status": "FAIL", "error": "Invalid JSON request"})
            return
        if not self.run_lock.acquire(blocking=False):
            self._json(409, {"status": "BUSY", "error": "An evidence run is already active"})
            return
        try:
            if self.path == "/api/run-operations":
                run = self.project_root / "out/external_validation/corsica_2018/counterfactual_ais/run"
                started = time.perf_counter()
                candidates, *_ = rank_candidates(
                    run / "ais/ais_normalized.csv",
                    run / "drift/reverse_endpoints.npz",
                    run / "drift/release_estimate.json",
                    run / "drift/forward_particles.npz",
                )
                self._json(
                    200,
                    {
                        "status": "PASS",
                        "run_type": "fresh local candidate ranking from saved evidence inputs",
                        "elapsed_seconds": round(time.perf_counter() - started, 2),
                        "candidate_count": len(candidates),
                        "top_candidates": candidates[:3],
                        "warning": "Hybrid validation result; not a finding of guilt.",
                    },
                )
                return
            seed = int(request.get("seed", 26143))
            seed = max(0, min(seed, 2_147_483_647))
            output = self.project_root / "out" / "interactive_known_source"
            environment_cache = self.project_root / "data" / "cache" / "environment_latest.json"
            mode = "cache" if environment_cache.exists() else "synthetic"
            started = time.perf_counter()
            result = run_demo(
                output,
                seed=seed,
                particles=1_200,
                ensemble_members=12,
                check_opendrift=False,
                environment_mode=mode,
                environment_cache=environment_cache,
            )
            elapsed = time.perf_counter() - started
            response = {
                "status": result.get("status", "FAIL"),
                "case_id": "RDA-LIVE-KS-001",
                "run_type": "fresh controlled known-source execution",
                "elapsed_seconds": round(elapsed, 2),
                "environment_mode": mode,
                "top_candidate": result.get("top_candidate", {}),
                "truth_opened_after_ranking": result.get("evaluation_only", {}),
                "acceptance": result.get("acceptance", {}),
                "artifacts": {
                    "result": str((output / "demo_result.json").resolve()),
                    "ranking": str((output / "candidates.json").resolve()),
                    "map": str((output / "attribution_map.png").resolve()),
                },
                "warning": "Controlled validation result; not an operational accusation.",
            }
            self._json(200 if response["status"] == "PASS" else 422, response)
        except Exception as error:  # keep the local UI responsive with a clear failure
            self._json(500, {"status": "FAIL", "error": str(error)})
        finally:
            self.run_lock.release()

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("ESPADA_SERVER_LOG") == "1":
            super().log_message(format, *args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ESPADA localhost showcase server")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    handler = lambda *handler_args, **kwargs: ShowcaseHandler(  # noqa: E731
        *handler_args, directory=str(root), **kwargs
    )
    ShowcaseHandler.project_root = root
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
