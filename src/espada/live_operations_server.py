from __future__ import annotations

import argparse
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .live_operations import LiveOperationsEngine, LiveRegion


# The operational default is deliberately a compact offshore traffic sector rather
# than the port/anchorage-dense Singapore harbour. Operators can still override it
# from start_live_operations.ps1 for a specific incident or exercise.
DEFAULT_LIVE_REGION = LiveRegion(
    "East Singapore Offshore Watch",
    104.02,
    1.20,
    104.23,
    1.31,
)


class LiveOperationsHandler(SimpleHTTPRequestHandler):
    engine: LiveOperationsEngine

    def _request_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request length") from error
        if length <= 0:
            return {}
        if length > 64_000:
            raise ValueError("Request body is too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib server API
        path = urlsplit(self.path).path
        lowered = path.lower()
        if lowered in {"/.env", "/.env.example"} or lowered.startswith(
            ("/.git/", "/work/", "/data/")
        ):
            self._json(404, {"status": "FAIL", "error": "Not found"})
            return
        if path == "/api/live/health":
            self._json(
                200,
                {
                    "status": "PASS",
                    "service": "ESPADA near-real-time operations engine",
                    "running": self.engine.running,
                    "region": self.engine.region.to_dict(),
                    "synthetic_fallback": False,
                },
            )
            return
        if path == "/api/live/snapshot":
            self._json(200, self.engine.snapshot())
            return
        if path == "/api/live/cases":
            self._json(200, self.engine.case_register())
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib server API
        path = urlsplit(self.path).path
        if path == "/api/live/refresh":
            self.engine.request_refresh()
            self._json(
                202,
                {
                    "status": "QUEUED",
                    "message": "Fresh provider requests were queued; the map will update automatically.",
                },
            )
            return
        if path == "/api/live/analyze-latest-sar":
            try:
                payload = self._request_json()
                scene_id = str(payload.get("scene_id") or "").strip() or None
                analysis = self.engine.start_latest_sar_analysis(scene_id)
                self._json(202, analysis)
            except RuntimeError as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/review":
            try:
                payload = self._request_json()
                review = self.engine.review_candidate(
                    str(payload.get("decision", "")),
                    age_hours=float(payload.get("age_hours", 19.0)),
                )
                self._json(200, review)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/build-attribution":
            try:
                attribution = self.engine.start_attribution()
                self._json(202, attribution)
            except RuntimeError as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/build-response":
            try:
                response = self.engine.build_response_package()
                self._json(200, response)
            except (RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/build-evidence-plan":
            try:
                plan = self.engine.build_evidence_plan()
                self._json(200, plan)
            except (RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/stage-evidence-return":
            try:
                payload = self._request_json()
                result = self.engine.stage_follow_up_evidence(payload)
                self._json(200, result)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/review-evidence-return":
            try:
                payload = self._request_json()
                result = self.engine.review_follow_up_evidence(
                    str(payload.get("receipt_id") or ""),
                    str(payload.get("decision") or ""),
                    str(payload.get("analyst_note") or ""),
                )
                self._json(200, result)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/start-reanalysis":
            try:
                result = self.engine.start_reanalysis()
                self._json(202, result)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/record-case-disposition":
            try:
                result = self.engine.record_case_disposition(self._request_json())
                self._json(200, result)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/live/verify-case":
            try:
                payload = self._request_json()
                result = self.engine.verify_case(str(payload.get("scene_id") or ""))
                self._json(200, result)
            except (ValueError, RuntimeError, FileNotFoundError) as error:
                self._json(409, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        self._json(404, {"status": "FAIL", "error": "Unknown endpoint"})

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("ESPADA_SERVER_LOG") == "1":
            super().log_message(format, *args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ESPADA live operations server")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--name", default=DEFAULT_LIVE_REGION.name)
    parser.add_argument("--bbox", nargs=4, type=float, default=list(DEFAULT_LIVE_REGION.bbox))
    parser.add_argument("--ais-window-seconds", type=float, default=55.0)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    region = LiveRegion(args.name, *args.bbox)
    engine = LiveOperationsEngine(root, region, ais_capture_seconds=args.ais_window_seconds)
    handler = lambda *handler_args, **kwargs: LiveOperationsHandler(  # noqa: E731
        *handler_args, directory=str(root), **kwargs
    )
    LiveOperationsHandler.engine = engine
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    engine.start()
    try:
        server.serve_forever()
    finally:
        engine.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
