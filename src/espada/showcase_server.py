from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .demo import run_demo
from .attribution import rank_candidates
from .case_intake import CaseIntakeError, create_case_workspace


class ShowcaseHandler(SimpleHTTPRequestHandler):
    project_root: Path
    run_lock = threading.Lock()
    jobs_lock = threading.Lock()
    jobs: dict[str, dict[str, object]] = {}

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._json(
                200,
                {
                    "status": "PASS",
                    "service": "ESPADA local evidence engine",
                    "capabilities": [
                        "known-source-run",
                        "operations-ranking",
                        "sealed-challenge-ranking",
                        "validated-case-intake",
                        "background-case-run",
                        "static-showcase",
                    ],
                    "network_scope": "127.0.0.1 only",
                },
            )
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with self.jobs_lock:
                job = dict(self.jobs.get(job_id, {}))
            if not job:
                self._json(404, {"status": "FAIL", "error": "Unknown job"})
                return
            self._json(200, job)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        allowed = {
            "/api/run-known-source",
            "/api/run-operations",
            "/api/create-case",
            "/api/run-case",
        }
        if path not in allowed:
            self._json(404, {"status": "FAIL", "error": "Unknown endpoint"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        limit = 136 * 1024 * 1024 if path == "/api/create-case" else 32_768
        if length > limit:
            self._json(413, {"status": "FAIL", "error": "Request is too large"})
            return
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"status": "FAIL", "error": "Invalid JSON request"})
            return
        if path == "/api/create-case":
            try:
                result = create_case_workspace(self.project_root, request)
                self._json(201, result)
            except CaseIntakeError as error:
                self._json(422, {"status": "REJECTED", "error": str(error)})
            except Exception as error:
                self._json(500, {"status": "FAIL", "error": str(error)})
            return
        if path == "/api/run-case":
            try:
                case_id = str(request.get("case_id") or "")
                case_file = (
                    self.project_root / "out" / "case_intake" / case_id / "case.json"
                ).resolve()
                intake_root = (self.project_root / "out" / "case_intake").resolve()
                if intake_root not in case_file.parents or not case_file.is_file():
                    raise CaseIntakeError("Prepared case was not found.")
                job_id = uuid.uuid4().hex[:12]
                job = {
                    "status": "QUEUED",
                    "job_id": job_id,
                    "case_id": case_id,
                    "message": "Waiting for the local evidence engine.",
                }
                with self.jobs_lock:
                    self.jobs[job_id] = job
                threading.Thread(
                    target=self._execute_case,
                    args=(job_id, case_id, case_file),
                    daemon=True,
                ).start()
                self._json(202, job)
            except CaseIntakeError as error:
                self._json(422, {"status": "REJECTED", "error": str(error)})
            return
        if not self.run_lock.acquire(blocking=False):
            self._json(409, {"status": "BUSY", "error": "An evidence run is already active"})
            return
        try:
            if path == "/api/run-operations":
                challenge = self.project_root / "out/challenge"
                run = (
                    challenge
                    if (challenge / "ranking/candidates.json").exists()
                    else self.project_root
                    / "out/external_validation/corsica_2018/counterfactual_ais/run"
                )
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
                        "warning": "Controlled validation result; not a finding of guilt.",
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

    @classmethod
    def _set_job(cls, job_id: str, **updates: object) -> None:
        with cls.jobs_lock:
            cls.jobs[job_id] = {**cls.jobs[job_id], **updates}

    @classmethod
    def _execute_case(cls, job_id: str, case_id: str, case_file: Path) -> None:
        cls._set_job(
            job_id,
            status="RUNNING",
            message="Valid evidence accepted. Reverse-drift analysis is running.",
        )
        workspace = case_file.parent
        log_path = workspace / "run.log"
        output_dir = cls.project_root / "out" / "cases" / case_id
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(cls.project_root / "scripts" / "run_case.ps1"),
            "-CaseFile",
            str(case_file),
            "-PythonPath",
            sys.executable,
        ]
        gpu_python = os.environ.get("ESPADA_GPU_PYTHON", "").strip()
        if gpu_python:
            command.extend(["-GpuPythonPath", gpu_python])
        try:
            with cls.run_lock:
                completed = subprocess.run(
                    command,
                    cwd=cls.project_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=4 * 60 * 60,
                    check=False,
                )
            log_path.write_text(
                completed.stdout + ("\n" + completed.stderr if completed.stderr else ""),
                encoding="utf-8",
            )
            manifest_path = output_dir / "case_run_manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                if manifest_path.exists()
                else {}
            )
            dossier = output_dir / "dossier" / "evidence_dossier.html"
            status = "COMPLETE" if completed.returncode == 0 else "FAILED"
            if completed.returncode == 0 and str(manifest.get("status", "")).startswith("SAFE_STOP"):
                status = "SAFE_STOP"
            cls._set_job(
                job_id,
                status=status,
                return_code=completed.returncode,
                message=(
                    "Evidence dossier is ready."
                    if dossier.exists()
                    else "Analysis stopped safely; inspect the run record for the reason."
                    if completed.returncode == 0
                    else "The run failed. The log preserves the exact error."
                ),
                dossier_url=(
                    "/" + dossier.relative_to(cls.project_root).as_posix()
                    if dossier.exists()
                    else None
                ),
                manifest_url=(
                    "/" + manifest_path.relative_to(cls.project_root).as_posix()
                    if manifest_path.exists()
                    else None
                ),
                log_url="/" + log_path.relative_to(cls.project_root).as_posix(),
            )
        except subprocess.TimeoutExpired:
            cls._set_job(
                job_id,
                status="FAILED",
                message="The case exceeded the four-hour safety timeout.",
                log_url="/" + log_path.relative_to(cls.project_root).as_posix(),
            )
        except Exception as error:
            log_path.write_text(str(error), encoding="utf-8")
            cls._set_job(
                job_id,
                status="FAILED",
                message=str(error),
                log_url="/" + log_path.relative_to(cls.project_root).as_posix(),
            )

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
