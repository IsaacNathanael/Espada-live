from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

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

    _PUBLIC_PREFIXES = (
        "/operator/live_command/",
        "/out/live_operations/",
    )

    @classmethod
    def _static_route_allowed(cls, raw_path: str) -> bool:
        """Allow only the operator client and generated live evidence artifacts.

        The server's filesystem root contains source code, credentials and working
        data, so the default SimpleHTTPRequestHandler behaviour is unsafe on a
        public host. Resolve URL traversal before applying the explicit allowlist.
        """
        path = unquote(urlsplit(raw_path).path).replace("\\", "/")
        parts = [part for part in path.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            return False
        normalized = "/" + "/".join(parts)
        if path.endswith("/") and normalized != "/":
            normalized += "/"
        return normalized == "/operator/live_command" or normalized.startswith(
            cls._PUBLIC_PREFIXES
        )

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", "0")
        self.end_headers()

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

    def _serve_static(self, *, include_body: bool = True) -> None:
        """Serve the public allowlist with compression and explicit cache policy."""
        root = Path(self.directory or os.getcwd()).resolve()
        relative = unquote(urlsplit(self.path).path).replace("\\", "/").lstrip("/")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json(404, {"status": "FAIL", "error": "Not found"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._json(404, {"status": "FAIL", "error": "Not found"})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        compressible = target.suffix.lower() in {".html", ".css", ".js", ".json", ".geojson", ".csv", ".svg"}
        compressed = compressible and "gzip" in self.headers.get("Accept-Encoding", "").lower() and len(body) > 1024
        if compressed:
            body = gzip.compress(body, compresslevel=5)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Vary", "Accept-Encoding")
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if relative.startswith("operator/live_command/") and target.suffix.lower() in {".css", ".js"}:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib server API
        path = urlsplit(self.path).path
        if path == "/":
            self._redirect("/operator/live_command/index.html")
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
        if self._static_route_allowed(self.path):
            self._serve_static()
            return
        self._json(404, {"status": "FAIL", "error": "Not found"})

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib server API
        path = urlsplit(self.path).path
        if path == "/":
            self._redirect("/operator/live_command/index.html")
            return
        if self._static_route_allowed(self.path):
            self._serve_static(include_body=False)
            return
        self._json(404, {"status": "FAIL", "error": "Not found"})

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
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("ESPADA_PROJECT_ROOT", ".")),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get(
            "ESPADA_HOST", "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1"
        ),
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4180")))
    parser.add_argument("--name", default=DEFAULT_LIVE_REGION.name)
    parser.add_argument("--bbox", nargs=4, type=float, default=list(DEFAULT_LIVE_REGION.bbox))
    parser.add_argument(
        "--ais-window-seconds",
        type=float,
        default=float(os.environ.get("ESPADA_AIS_SESSION_SECONDS", "900")),
    )
    parser.add_argument(
        "--environment-interval-seconds",
        type=float,
        default=float(os.environ.get("ESPADA_ENVIRONMENT_INTERVAL_SECONDS", "3600")),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    region = LiveRegion(args.name, *args.bbox)
    engine = LiveOperationsEngine(
        root,
        region,
        ais_capture_seconds=args.ais_window_seconds,
        environment_interval_seconds=args.environment_interval_seconds,
    )
    handler = lambda *handler_args, **kwargs: LiveOperationsHandler(  # noqa: E731
        *handler_args, directory=str(root), **kwargs
    )
    LiveOperationsHandler.engine = engine
    server = ThreadingHTTPServer((args.host, args.port), handler)
    engine.start()
    try:
        server.serve_forever()
    finally:
        engine.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
