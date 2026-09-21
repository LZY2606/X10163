"""Batteries-included HTTP layer (stdlib only) serving JSON APIs and the SPA."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .service import AccordService, ServiceError

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class AccordHandler(BaseHTTPRequestHandler):
    server_version = "cycleaccord/0.1"

    # -- helpers ------------------------------------------------------------
    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ServiceError("invalid JSON body: %s" % exc)
        if not isinstance(data, dict):
            raise ServiceError("JSON body must be an object")
        return data

    def _static(self, filename: str, content_type: str) -> None:
        path = os.path.join(WEB_DIR, filename)
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._json(404, {"error": "asset missing: %s" % filename})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def service(self) -> AccordService:
        return self.server.service  # type: ignore[attr-defined]

    def _types_from_query(self, query: Dict[str, list]) -> list:
        raw = query.get("types") or query.get("include_types")
        if not raw:
            return ["hard", "runtime", "test", "generated"]
        value = raw[0]
        return [part for part in value.split(",") if part]

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet access log
        return

    # -- routing ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._static("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._static("app.js", "application/javascript; charset=utf-8")
            elif path == "/styles.css":
                self._static("styles.css", "text/css; charset=utf-8")
            elif path == "/api/state":
                self._json(200, self.service.state())
            elif path == "/api/analysis":
                types = self._types_from_query(query)
                self._json(200, self.service.get_analysis(types))
            elif path == "/api/discussion":
                types = self._types_from_query(query)
                key = (query.get("candidate") or [""])[0]
                self._json(200, self.service.candidate_discussion(types, key))
            elif path == "/api/decisions":
                types = self._types_from_query(query)
                self._json(200, {"decisions": self.service.decisions_history(types)})
            else:
                self._json(404, {"error": "not found: %s" % path})
        except ServiceError as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            data = self._read_json()
            if path == "/api/analysis":
                types = data.get("include_types") or self._types_from_query(query)
                self._json(200, self.service.compute_analysis(types))
            elif path == "/api/import":
                payload = data.get("graph")
                if payload is None:
                    raise ServiceError("body requires a 'graph' field")
                version = self.service.import_graph(payload, note=str(data.get("note", "")))
                self._json(200, {"graph_version": version})
            elif path == "/api/opinion":
                types = data.get("include_types") or self._types_from_query(query)
                result = self.service.submit_opinion(
                    include_types=types,
                    candidate_key_value=str(data.get("candidate_key", "")),
                    author=str(data.get("author", "")),
                    kind=str(data.get("kind", "")),
                    alternative_ops=data.get("alternative_ops", []),
                    comment=str(data.get("comment", "")),
                )
                self._json(200, result)
            elif path == "/api/decide":
                types = data.get("include_types") or self._types_from_query(query)
                result = self.service.decide(
                    include_types=types,
                    candidate_key_value=str(data.get("candidate_key", "")),
                    decided_by=str(data.get("decided_by", "")),
                    note=str(data.get("note", "")),
                )
                self._json(200, result)
            elif path == "/api/verify":
                result = self.service.verify_operations(
                    include_types=data.get("include_types")
                    or self._types_from_query(query),
                    raw_ops=data.get("operations", []),
                )
                self._json(200, result)
            else:
                self._json(404, {"error": "not found: %s" % path})
        except ServiceError as exc:
            self._json(400, {"error": str(exc)})
        except KeyError as exc:
            self._json(400, {"error": "missing field: %s" % exc})


def create_server(host: str, port: int, service: AccordService) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), AccordHandler)
    server.service = service  # type: ignore[attr-defined]
    return server
