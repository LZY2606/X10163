"""Stdlib HTTP server: JSON API + single-page UI."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store

STATIC = os.path.join(os.path.dirname(__file__), "static")


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cycleaccord/0.1"

        def log_message(self, *args):  # keep demo output clean
            pass

        # -- helpers ------------------------------------------------------
        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, message, status=400):
            self._send_json({"error": message}, status=status)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        # -- routes ---------------------------------------------------------
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._send_json(store.state())
            else:
                self._send_error("not found", 404)

        def do_POST(self):
            try:
                body = self._read_json()
                if self.path == "/api/graph":
                    version = store.load_graph(body.get("graph", body))
                    self._send_json({"ok": True, "graph_version": version})
                elif self.path == "/api/view":
                    version = store.set_view(body.get("types", []))
                    self._send_json({"ok": True, "analysis_version": version})
                elif self.path == "/api/opinion":
                    opinion = store.add_opinion(
                        candidate_id=body["candidate_id"],
                        owner=body["owner"],
                        action=body["action"],
                        comment=body.get("comment", ""),
                        alternative_edge=body.get("alternative_edge"),
                    )
                    self._send_json({"ok": True, "opinion": opinion})
                elif self.path == "/api/select":
                    sel = store.select(body["candidate_id"],
                                       body.get("by", "unknown"))
                    self._send_json({"ok": True, "selection": sel})
                else:
                    self._send_error("not found", 404)
            except KeyError as exc:
                self._send_error("missing field: %s" % exc, 400)
            except ValueError as exc:
                self._send_error(str(exc), 400)

    return Handler


def serve(host, port, store):
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print("依赖环协商器 listening on http://%s:%d" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
