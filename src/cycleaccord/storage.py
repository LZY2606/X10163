"""SQLite 持久化：图版本、分析版本、候选、意见（只追加）、最终选择。

并发规则：
  * 每次提交意见都是 INSERT，后写不会覆盖先写；同一候选人对同一候选的
    多条意见作为各自版本全部保留（含同一 person 也保留历史）。
  * 批准/选择绑定具体 graph_version + analysis_id；图更新后生成新版本，
    旧记录保留并标记为历史（superseded），不再作为当前批准。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .graphio import canonical_json, graph_to_dict, parse_graph
from .models import EdgeOp, Graph

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_versions (
    version TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    graph_version TEXT NOT NULL,
    types_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    rank INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (analysis_id, id)
);
CREATE TABLE IF NOT EXISTS opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    person TEXT NOT NULL,
    role TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    alternate_ops_json TEXT NOT NULL DEFAULT '[]',
    alternate_feasible INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    graph_version TEXT NOT NULL,
    person TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_selections_analysis ON selections(analysis_id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------- 事件 ----------
    def event(self, kind: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, kind, payload) VALUES (?,?,?)",
                (_now(), kind, json.dumps(payload, ensure_ascii=False)),
            )

    # ---------- 图版本 ----------
    def current_version(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='current_graph_version'"
            ).fetchone()
            return row["value"] if row else None

    def put_graph(self, graph: Graph, note: str = "") -> Tuple[str, int, bool]:
        """写入图；内容相同则复用版本，不同则新建版本。

        返回 (version, ordinal, is_new)。
        """
        payload = canonical_json(graph)
        version = "gv-" + hashlib.sha256(payload.encode()).hexdigest()[:16]
        with self._lock:
            row = self._conn.execute(
                "SELECT ordinal FROM graph_versions WHERE version=?", (version,)
            ).fetchone()
            if row:
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta(key,value) "
                    "VALUES ('current_graph_version', ?)",
                    (version,),
                )
                self.event("graph_reuse", {"version": version, "note": note})
                return version, row["ordinal"], False
            ord_row = self._conn.execute(
                "SELECT COALESCE(MAX(ordinal),0)+1 AS n FROM graph_versions"
            ).fetchone()
            ordinal = ord_row["n"]
            self._conn.execute(
                "INSERT INTO graph_versions(version, ordinal, payload, created_at)"
                " VALUES (?,?,?,?)",
                (version, ordinal, payload, _now()),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) "
                "VALUES ('current_graph_version', ?)",
                (version,),
            )
            self.event("graph_new_version", {"version": version, "note": note})
            return version, ordinal, True

    def load_graph(self, version: Optional[str] = None) -> Tuple[Graph, str]:
        with self._lock:
            version = version or self.current_version()
            if not version:
                raise KeyError("图库为空")
            row = self._conn.execute(
                "SELECT payload FROM graph_versions WHERE version=?", (version,)
            ).fetchone()
            if not row:
                raise KeyError(f"未知图版本: {version}")
            return parse_graph(json.loads(row["payload"])), version

    def list_graph_versions(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, ordinal, created_at FROM graph_versions"
                " ORDER BY ordinal DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- 分析版本 ----------
    def create_analysis(
        self, graph_version: str, types: Sequence[str], label: str = ""
    ) -> str:
        types_json = json.dumps(list(types))
        aid = "an-" + hashlib.sha256(
            (graph_version + "|" + types_json).encode()
        ).hexdigest()[:12]
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO analyses"
                "(id, graph_version, types_json, created_at, label)"
                " VALUES (?,?,?,?,?)",
                (aid, graph_version, types_json, _now(), label),
            )
            self.event(
                "analysis_create",
                {"id": aid, "graph_version": graph_version, "types": list(types)},
            )
        return aid

    def get_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM analyses WHERE id=?", (analysis_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["types"] = json.loads(d.pop("types_json"))
            return d

    def list_analyses(self, graph_version: Optional[str] = None) -> List[Dict]:
        with self._lock:
            if graph_version:
                rows = self._conn.execute(
                    "SELECT * FROM analyses WHERE graph_version=? ORDER BY created_at",
                    (graph_version,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM analyses ORDER BY created_at"
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["types"] = json.loads(d.pop("types_json"))
                out.append(d)
            return out

    # ---------- 候选 ----------
    def save_candidates(
        self, analysis_id: str, graph_version: str, candidates: Sequence[Any]
    ) -> None:
        with self._lock:
            for cand in candidates:
                self._conn.execute(
                    "INSERT OR REPLACE INTO candidates"
                    "(id, analysis_id, graph_version, rank, payload)"
                    " VALUES (?,?,?,?,?)",
                    (
                        cand.id,
                        analysis_id,
                        graph_version,
                        cand.rank,
                        json.dumps(cand.to_dict(), ensure_ascii=False),
                    ),
                )

    def get_candidate(
        self, analysis_id: str, candidate_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM candidates WHERE analysis_id=? AND id=?",
                (analysis_id, candidate_id),
            ).fetchone()
            return json.loads(row["payload"]) if row else None

    def list_candidates(self, analysis_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM candidates WHERE analysis_id=? ORDER BY rank",
                (analysis_id,),
            ).fetchall()
            return [json.loads(r["payload"]) for r in rows]

    # ---------- 意见（只追加） ----------
    def add_opinion(
        self,
        analysis_id: str,
        candidate_id: str,
        graph_version: str,
        person: str,
        role: str,
        comment: str,
        alternate_ops: Sequence[EdgeOp] = (),
        alternate_feasible: Optional[bool] = None,
    ) -> int:
        if role not in ("accept", "reject", "alternate"):
            raise ValueError("role 必须是 accept/reject/alternate")
        ops_json = json.dumps(
            [
                {
                    "kind": o.kind,
                    "edge_id": o.edge_id,
                    "src": o.src,
                    "target": o.target,
                    "type": o.type,
                    "note": o.note,
                }
                for o in alternate_ops
            ],
            ensure_ascii=False,
        )
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO opinions"
                "(analysis_id, candidate_id, graph_version, person, role,"
                " comment, alternate_ops_json, alternate_feasible, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    analysis_id,
                    candidate_id,
                    graph_version,
                    person,
                    role,
                    comment,
                    ops_json,
                    None if alternate_feasible is None else int(alternate_feasible),
                    _now(),
                ),
            )
            oid = cur.lastrowid
            self.event(
                "opinion_add",
                {"id": oid, "person": person, "role": role,
                 "candidate_id": candidate_id},
            )
            return oid

    def list_opinions(
        self, analysis_id: Optional[str] = None, candidate_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM opinions WHERE 1=1"
        args: List[Any] = []
        if analysis_id:
            sql += " AND analysis_id=?"
            args.append(analysis_id)
        if candidate_id:
            sql += " AND candidate_id=?"
            args.append(candidate_id)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["alternate_ops"] = json.loads(d.pop("alternate_ops_json"))
                if d["alternate_feasible"] is not None:
                    d["alternate_feasible"] = bool(d["alternate_feasible"])
                out.append(d)
            return out

    # ---------- 最终选择 ----------
    def set_selection(
        self, analysis_id: str, candidate_id: str, graph_version: str, person: str
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO selections"
                "(analysis_id, candidate_id, graph_version, person, created_at)"
                " VALUES (?,?,?,?,?)",
                (analysis_id, candidate_id, graph_version, person, _now()),
            )
            sid = cur.lastrowid
            self.event(
                "selection_add",
                {"id": sid, "analysis_id": analysis_id,
                 "candidate_id": candidate_id, "person": person},
            )
            return sid

    def get_selection(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM selections WHERE analysis_id=? ORDER BY id DESC LIMIT 1",
                (analysis_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_selections(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM selections ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- 批准失效判定 ----------
    def opinions_status(self, current_version: str) -> Dict[str, str]:
        """返回每个 opinion/selection 相对当前图版本的状态。"""
        statuses: Dict[str, str] = {}
        with self._lock:
            for r in self._conn.execute("SELECT id, graph_version FROM opinions"):
                statuses[f"opinion-{r['id']}"] = (
                    "current" if r["graph_version"] == current_version
                    else "superseded"
                )
            for r in self._conn.execute("SELECT id, graph_version FROM selections"):
                statuses[f"selection-{r['id']}"] = (
                    "current" if r["graph_version"] == current_version
                    else "superseded"
                )
        return statuses
