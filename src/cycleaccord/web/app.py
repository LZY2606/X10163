"""标准库 HTTP 服务：静态页面 + JSON API。"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from ..graphio import GraphValidationError
from ..service import Service
from ..storage import Store

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def create_server(store: Store, host: str = "127.0.0.1", port: int = 5236):
    service = Service(store)

    class Handler(BaseHTTPRequestHandler):
        server_version = "CycleAccord/0.1"

        def log_message(self, fmt, *args):  # 安静一点
            pass

        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status)

        def _read_json(self) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    self._serve_static("index.html")
                elif path.startswith("/static/"):
                    self._serve_static(path[len("/static/"):])
                elif path == "/api/state":
                    self._send_json(service.graph_state())
                elif path == "/api/history":
                    self._send_json(service.history())
                elif re.match(r"^/api/analyses/[^/]+$", path):
                    aid = path.rsplit("/", 1)[-1]
                    self._send_json(service.analysis_detail(aid))
                elif re.match(
                    r"^/api/analyses/[^/]+/candidates/[^/]+$", path
                ):
                    parts = path.split("/")
                    aid, cid = parts[3], parts[5]
                    self._send_json(service.candidate_detail(aid, cid))
                else:
                    self._send_error_json(404, "未找到")
            except KeyError as exc:
                self._send_error_json(404, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._send_error_json(500, f"服务器错误: {exc}")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json()
                if path == "/api/import":
                    result = service.import_graph(body, note="Web 导入")
                    self._send_json(result)
                elif path == "/api/analyze":
                    result = service.analyze(
                        types=body.get("types"),
                        graph_version=body.get("graph_version"),
                        label=body.get("label", ""),
                        force=bool(body.get("force", False)),
                    )
                    self._send_json(result)
                elif re.match(
                    r"^/api/analyses/[^/]+/candidates/[^/]+/opinions$", path
                ):
                    parts = path.split("/")
                    aid, cid = parts[3], parts[5]
                    result = service.submit_opinion(
                        analysis_id=aid,
                        candidate_id=cid,
                        person=body.get("person", ""),
                        role=body.get("role", ""),
                        comment=body.get("comment", ""),
                        alternate_ops_raw=body.get("alternate_ops"),
                    )
                    self._send_json(result)
                elif re.match(
                    r"^/api/analyses/[^/]+/candidates/[^/]+/select$", path
                ):
                    parts = path.split("/")
                    aid, cid = parts[3], parts[5]
                    result = service.select(
                        aid, cid, body.get("person", "")
                    )
                    self._send_json(result)
                else:
                    self._send_error_json(404, "未找到")
            except json.JSONDecodeError:
                self._send_error_json(400, "请求体不是合法 JSON")
            except (ValueError, GraphValidationError) as exc:
                self._send_error_json(400, str(exc))
            except KeyError as exc:
                self._send_error_json(404, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._send_error_json(500, f"服务器错误: {exc}")

        def _serve_static(self, rel: str) -> None:
            # 防目录穿越
            rel = rel.lstrip("/")
            full = os.path.normpath(os.path.join(_STATIC_DIR, rel))
            if not full.startswith(_STATIC_DIR) or not os.path.isfile(full):
                self._send_error_json(404, "静态资源不存在")
                return
            ext = os.path.splitext(full)[1]
            with open(full, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                _CONTENT_TYPES.get(ext, "application/octet-stream"),
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer((host, port), Handler)
