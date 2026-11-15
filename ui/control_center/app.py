"""Serve the built Vite app and approved operations on a loopback address."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from starlette.concurrency import run_in_threadpool

from .contracts import ARGUMENTS
from .workspace import Workspace


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "fileroute" / "ui" / "static"
ALWAYS_APPROVED = {
    "transfer.run",
    "discovery.run",
    "auth.check",
    "cli.completion.install",
}
LIVE = ALWAYS_APPROVED - {"cli.completion.install"}


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    arguments: dict[str, JsonValue]


def requires_approval(operation, args):
    return (
        operation in ALWAYS_APPROVED
        or operation.startswith("auth.login.")
        or (operation == "resolve" and args.get("online") is True)
    )


def reads_provider(operation, args):
    return (
        operation in LIVE
        or operation.startswith("auth.login.")
        or (operation == "resolve" and args.get("online") is True)
    )


def fingerprint(operation, args):
    return json.dumps([operation, args], sort_keys=True, default=str)


def create_app(root: Path, descriptor: str | None = None, port: int = 8765) -> FastAPI:
    workspace = Workspace(root, descriptor)
    host = f"127.0.0.1:{port}"
    origin = f"http://{host}"
    token = secrets.token_urlsafe(32)
    approvals: dict[str, tuple[str, float]] = {}
    jobs: dict[str, dict] = {}
    transfer_jobs: dict[str, str] = {}
    lock = RLock()

    @asynccontextmanager
    async def lifespan(app):
        with ThreadPoolExecutor(max_workers=2) as executor:
            app.state.executor = executor
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def check_session(request: Request, call_next):
        if request.headers.get("host") != host:
            return JSONResponse({"detail": "Unexpected host."}, status_code=400)
        if request.url.path.startswith("/api/"):
            claimed = request.headers.get("x-fileroute-session", "")
            if not secrets.compare_digest(claimed, token):
                return JSONResponse(
                    {"detail": "Reload the local workspace to start a session."},
                    status_code=403,
                )
            actual_origin = request.headers.get("origin")
            if (request.method == "POST" or actual_origin) and actual_origin != origin:
                return JSONResponse(
                    {"detail": "Unexpected request origin."}, status_code=403
                )
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.get("/")
    def index():
        file = STATIC / "index.html"
        if not file.is_file():
            return HTMLResponse(
                "Build the frontend with npm run build.", status_code=503
            )
        html = (
            file.read_text(encoding="utf-8")
            .replace(
                '<meta name="fileroute-runtime" content="fixture"',
                '<meta name="fileroute-runtime" content="local"',
            )
            .replace(
                "</head>", f'<meta name="fileroute-session" content="{token}" /></head>'
            )
        )
        return HTMLResponse(html)

    def git_value(*args):
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace.root), *args],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None

    @app.get("/api/state")
    def reported_state():
        return {
            "branch": git_value("branch", "--show-current"),
            "commit": git_value("rev-parse", "HEAD"),
            "workingRoot": str(workspace.root),
        }

    @app.get("/api/jobs")
    def job_list():
        with lock:
            return {"jobs": list(jobs.values())}

    def validated(operation, arguments):
        adapter = ARGUMENTS.get(operation)
        if adapter is None:
            raise HTTPException(404, "Unknown Fileroute operation.")
        try:
            validated = adapter.validate_python(arguments)
            return validated.model_dump(exclude_none=True) if isinstance(validated, BaseModel) else validated
        except ValidationError as error:
            details = error.errors(
                include_url=False, include_input=False, include_context=False
            )
            raise HTTPException(422, details) from None

    def process_job(job_id, operation, arguments):
        with lock:
            jobs[job_id] = {**jobs[job_id], "status": "running"}
        try:
            result = workspace.invoke(operation, arguments)
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, ValueError)
                and str(error).startswith(
                    (
                        "The transfer plan",
                        "Create and review",
                        "Sign-in did not complete",
                        "This URL's provider",
                    )
                )
                else "Operation failed. Inspect the same scope in the local CLI. Partial completion may be unknown."
            )
            with lock:
                jobs[job_id] = {**jobs[job_id], "status": "failed", "error": message}
        else:
            with lock:
                jobs[job_id] = {**jobs[job_id], "status": "succeeded", "result": result}

    @app.post("/api/operations")
    async def perform(body: Operation, request: Request):
        operation = body.operation
        arguments = validated(operation, body.arguments)
        if operation == "jobs.get":
            with lock:
                if arguments["job_id"] not in jobs:
                    raise HTTPException(404, "Job not found in this session.")
                return jobs[arguments["job_id"]]
        if operation == "approval.request":
            target = arguments["operation"]
            payload = arguments["arguments"]
            validated(target, payload)
            if not requires_approval(target, payload):
                raise HTTPException(
                    422, "Choose an operation that needs explicit approval."
                )
            key = secrets.token_urlsafe(24)
            with lock:
                for expired in [
                    key
                    for key, (_, expiry) in approvals.items()
                    if expiry <= monotonic()
                ]:
                    del approvals[expired]
                approvals[key] = (fingerprint(target, payload), monotonic() + 300)
            return {"approval_id": key}
        if requires_approval(operation, arguments):
            key = request.headers.get("x-fileroute-approval", "")
            with lock:
                approval = approvals.pop(key, None)
                if (
                    approval is None
                    or approval[0] != fingerprint(operation, body.arguments)
                    or monotonic() > approval[1]
                ):
                    raise HTTPException(
                        403,
                        "Approve this operation and its exact arguments before running.",
                    )
                if reads_provider(operation, arguments):
                    preview_id = arguments.get("preview_id")
                    if operation == "transfer.run" and preview_id in transfer_jobs:
                        return jobs[transfer_jobs[preview_id]]
                    job_id = uuid4().hex
                    job = {
                        "kind": "job",
                        "job_id": job_id,
                        "status": "queued",
                        "operation": operation,
                        "scope": dict(body.arguments),
                    }
                    if operation in {"resolve", "discovery.run"}:
                        try:
                            arguments["descriptor"] = workspace.descriptor(
                                arguments.get("descriptor")
                            )
                        except ValueError as error:
                            raise HTTPException(422, str(error)) from None
                        job["scope"]["descriptor"] = workspace.label(
                            arguments["descriptor"]
                        )
                        job["catalogScope"] = {
                            "descriptor": job["scope"]["descriptor"],
                            "selector": arguments.get("selector") or "$",
                        }
                    if operation == "transfer.run" and preview_id in workspace.previews:
                        scope, _ = workspace.previews[preview_id]
                        job["catalogScope"] = {
                            "descriptor": workspace.label(scope["descriptor"]),
                            "selector": scope.get("selector") or "$",
                        }
                    jobs[job_id] = job
                    if operation == "transfer.run":
                        transfer_jobs[preview_id] = job_id
                    app.state.executor.submit(process_job, job_id, operation, arguments)
                    return job

        def invoke_local():
            with lock:
                return workspace.invoke(operation, arguments)

        try:
            return await run_in_threadpool(invoke_local)
        except (ValueError, OSError, TypeError) as error:
            raise HTTPException(422, str(error)) from None

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("descriptor", nargs="?")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    args.root = args.root.resolve()
    os.chdir(args.root)
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))

    import uvicorn

    class LocalServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                url = f"http://127.0.0.1:{args.port}"
                print(url, flush=True)
                if not args.no_open:
                    import threading

                    threading.Thread(
                        target=webbrowser.open, args=(url,), daemon=True
                    ).start()

    app = create_app(args.root, args.descriptor, args.port)
    LocalServer(uvicorn.Config(app, host="127.0.0.1", port=args.port)).run()


if __name__ == "__main__":
    main()
