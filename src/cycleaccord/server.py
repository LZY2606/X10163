"""仅依赖标准库的 HTTP 服务，提供页面与 JSON API。"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlparse

from .model import DEP_TYPES
from .service import Service
from .storage import Storage

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class CycleHandler(BaseHTTPRequestHandler):
    server_version = "cycleaccord/0.1"

    # 由 make_server 注入
    def _service(self) -> Service:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # ------------------------------------------------------------------
    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._send_file(os.path.join(STATIC_DIR, "index.html"),
                                "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._send_file(os.path.join(STATIC_DIR, "app.js"),
                                "application/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._send_file(os.path.join(STATIC_DIR, "styles.css"),
                                "text/css; charset=utf-8")
                return
            if path == "/api/state":
                self._send_json(200, self._service().state())
                return
            if path == "/api/analyses":
                qs = parse_qs(parsed.query)
                aid = qs.get("id", [""])[0]
                record, graph = self._service().get_analysis(aid)
                payload = {
                    "record": {
                        "id": record["id"],
                        "graph_version": record["graph_version"],
                        "include_types": record["include_types"],
                        "stale": self._service().is_stale(record),
                    },
                    "analysis": record["payload"],
                    "graph": self._service().graph_summary(record["graph_version"]),
                    "opinions": self._service().storage.list_opinions(aid),
                    "selections": self._service().storage.list_selections(aid),
                }
                self._send_json(200, payload)
                return
            if path == "/api/verify":
                qs = parse_qs(parsed.query)
                result = self._service().machine_verify(
                    qs.get("analysis_id", [""])[0],
                    qs.get("candidate_id", [""])[0],
                )
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "未知路径: %s" % path})
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        service = self._service()
        try:
            data = self._read_json()
            if path == "/api/import":
                version = service.import_graph(data.get("graph", data))
                self._send_json(200, {"ok": True, "graph_version": version})
            elif path == "/api/analyses":
                include = data.get("include_types") or list(DEP_TYPES)
                version = data.get("graph_version")
                result = service.ensure_analysis(include, version)
                self._send_json(200, {"ok": True, "analysis": {
                    "id": result["id"],
                    "graph_version": result["graph_version"],
                    "include_types": result["include_types"],
                    "reused": result["reused"],
                    **result["payload"],
                }})
            elif path == "/api/opinions":
                saved = service.add_opinion(
                    str(data["analysis_id"]), str(data["candidate_id"]),
                    str(data.get("owner", "")), str(data.get("decision", "")),
                    str(data.get("comment", "")), str(data.get("alternative", "")),
                )
                self._send_json(200, {"ok": True, "opinion": saved})
            elif path == "/api/approve":
                saved = service.approve(
                    str(data["analysis_id"]), str(data["candidate_id"]),
                    str(data.get("owner", "")),
                )
                self._send_json(200, {"ok": True, "selection": saved})
            else:
                self._send_json(404, {"error": "未知路径: %s" % path})
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})


def make_server(host: str, port: int, db_path: str) -> Tuple[ThreadingHTTPServer, Service]:
    storage = Storage(db_path)
    service = Service(storage)
    httpd = ThreadingHTTPServer((host, port), CycleHandler)
    httpd.service = service  # type: ignore[attr-defined]
    return httpd, service
