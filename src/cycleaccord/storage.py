"""SQLite 持久化。

原图、分析版本、意见与最终选择全部持久化。graph_versions / analyses /
opinions / selections 只追加（触发器拒绝 UPDATE/DELETE），因此多人并发
提交意见时各自保留独立版本，后写不能覆盖先写；图变化后旧分析及其批准
自动失效（以 graph_version 判定，旧记录仍作为历史保留）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_versions (
    version      INTEGER PRIMARY KEY,
    graph_json   TEXT NOT NULL,
    imported_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id             TEXT PRIMARY KEY,
    graph_version  INTEGER NOT NULL,
    include_types  TEXT NOT NULL,
    created_at     REAL NOT NULL,
    payload_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    analysis_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    graph_version  INTEGER NOT NULL,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (analysis_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS opinions (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    graph_version  INTEGER NOT NULL,
    owner          TEXT NOT NULL,
    decision       TEXT NOT NULL,
    comment        TEXT NOT NULL DEFAULT '',
    alternative    TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opinions_lookup
    ON opinions(analysis_id, candidate_id);

CREATE TABLE IF NOT EXISTS selections (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    graph_version  INTEGER NOT NULL,
    owner          TEXT NOT NULL,
    created_at     REAL NOT NULL,
    payload_json   TEXT NOT NULL
);
"""

APPEND_TABLES = ("graph_versions", "analyses", "opinions", "selections")
_VALID_DECISIONS = {"accept", "reject", "propose"}


class Storage:
    def __init__(self, path: str):
        self.path = path
        # check_same_thread=False：HTTP 服务每请求使用同一连接并加锁串行化写。
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            for table in APPEND_TABLES:
                self._conn.executescript(
                    "DROP TRIGGER IF EXISTS trg_%s_no_update;" % table
                )
                self._conn.executescript(
                    "DROP TRIGGER IF EXISTS trg_%s_no_delete;" % table
                )
                self._conn.execute(
                    "CREATE TRIGGER trg_%s_no_update BEFORE UPDATE ON %s "
                    "BEGIN SELECT RAISE(ABORT, '%s 为只追加记录，禁止更新'); END;"
                    % (table, table, table)
                )
                self._conn.execute(
                    "CREATE TRIGGER TRG_%s_NO_DELETE BEFORE DELETE ON %s "
                    "BEGIN SELECT RAISE(ABORT, '%s 为历史记录，禁止删除'); END;"
                    % (table.upper(), table, table)
                )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 图版本
    # ------------------------------------------------------------------

    def latest_graph_version(self) -> Optional[int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) AS v FROM graph_versions"
            ).fetchone()
            return int(row["v"]) if row and row["v"] is not None else None

    def import_graph(self, graph_json: Dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO graph_versions(graph_json, imported_at) VALUES (?, ?)",
                (json.dumps(graph_json, ensure_ascii=False, sort_keys=True), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_graph_json(self, version: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT graph_json FROM graph_versions WHERE version=?", (version,)
            ).fetchone()
            return json.loads(row["graph_json"]) if row else None

    # ------------------------------------------------------------------
    # 分析与候选
    # ------------------------------------------------------------------

    def save_analysis(self, analysis_id: str, graph_version: int,
                      include_types: List[str], payload: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO analyses"
                "(id, graph_version, include_types, created_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (analysis_id, graph_version, json.dumps(include_types),
                 time.time(), json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()

    def get_analysis(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM analyses WHERE id=?", (analysis_id,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["include_types"] = json.loads(data["include_types"])
            data["payload"] = json.loads(data.pop("payload_json"))
            return data

    def list_analyses(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, graph_version, include_types, created_at "
                "FROM analyses ORDER BY created_at"
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["include_types"] = json.loads(d["include_types"])
                out.append(d)
            return out

    def save_candidates(self, analysis_id: str, graph_version: int,
                        candidates: List[Dict[str, Any]]) -> None:
        with self._lock:
            for cand in candidates:
                self._conn.execute(
                    "INSERT OR IGNORE INTO candidates"
                    "(analysis_id, candidate_id, graph_version, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (analysis_id, cand["id"], graph_version,
                     json.dumps(cand, ensure_ascii=False)),
                )
            self._conn.commit()

    def get_candidate(self, analysis_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM candidates WHERE analysis_id=? AND candidate_id=?",
                (analysis_id, candidate_id),
            ).fetchone()
            return json.loads(row["payload_json"]) if row else None

    # ------------------------------------------------------------------
    # 意见（只追加，并发安全）
    # ------------------------------------------------------------------

    def add_opinion(self, analysis_id: str, candidate_id: str, graph_version: int,
                    owner: str, decision: str, comment: str = "",
                    alternative: str = "") -> Dict[str, Any]:
        if decision not in _VALID_DECISIONS:
            raise ValueError("decision 必须是 accept/reject/propose")
        if not owner.strip():
            raise ValueError("owner 不能为空")
        if decision == "propose" and not alternative.strip():
            raise ValueError("提出替代边时必须给出替代边说明")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO opinions"
                "(analysis_id, candidate_id, graph_version, owner, decision, "
                " comment, alternative, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (analysis_id, candidate_id, graph_version, owner.strip(), decision,
                 comment, alternative, time.time()),
            )
            self._conn.commit()
            seq = int(cur.lastrowid)
        return {"seq": seq, "analysis_id": analysis_id, "candidate_id": candidate_id,
                "graph_version": graph_version, "owner": owner.strip(),
                "decision": decision, "comment": comment, "alternative": alternative}

    def list_opinions(self, analysis_id: Optional[str] = None,
                      candidate_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM opinions WHERE 1=1"
        params: List[Any] = []
        if analysis_id is not None:
            sql += " AND analysis_id=?"
            params.append(analysis_id)
        if candidate_id is not None:
            sql += " AND candidate_id=?"
            params.append(candidate_id)
        sql += " ORDER BY seq"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 最终选择（版本化批准，历史保留）
    # ------------------------------------------------------------------

    def add_selection(self, analysis_id: str, candidate_id: str, graph_version: int,
                      owner: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not owner.strip():
            raise ValueError("owner 不能为空")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO selections"
                "(analysis_id, candidate_id, graph_version, owner, created_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, candidate_id, graph_version, owner.strip(),
                 time.time(), json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()
            seq = int(cur.lastrowid)
        return {"seq": seq, "analysis_id": analysis_id, "candidate_id": candidate_id,
                "graph_version": graph_version, "owner": owner.strip()}

    def list_selections(self, analysis_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT seq, analysis_id, candidate_id, graph_version, owner, created_at " \
              "FROM selections"
        params: List[Any] = []
        if analysis_id is not None:
            sql += " WHERE analysis_id=?"
            params.append(analysis_id)
        sql += " ORDER BY seq"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
