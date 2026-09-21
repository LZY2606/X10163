"""SQLite persistence.

Stored artifacts:
  * versioned graph imports (the graph itself is immutable per version);
  * analysis views with the graph version they were computed against;
  * candidate snapshots per analysis;
  * append-only opinions (each submission gets a new monotone version, later
    writes never overwrite earlier ones);
  * final decisions.

Opinions and decisions are treated as history once a new graph version is
imported: the service layer marks them stale rather than deleting them.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """SQLite-backed storage with one connection per thread.

    Writers are serialized by an in-process lock plus ``BEGIN IMMEDIATE``;
    readers use their own connection. WAL mode allows concurrent readers while
    a writer is committed.
    """

    def __init__(self, str_path: str = None, path: str = None):
        self.path = path if path is not None else str_path
        self._write_lock = threading.RLock()
        self._local = threading.local()
        self._all_conns = []
        self._conns_lock = threading.Lock()
        self._init_schema()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        current = threading.get_ident()
        record = getattr(self._local, "record", None)
        conn = None
        if record is not None and record[0] == current:
            conn = record[1]
        if conn is None:
            conn = self._new_connection()
            # Bind the connection to the live thread that created it; the
            # tuple lets a later reuse on a different thread detect staleness.
            self._local.record = (current, conn)
        return conn

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS graphs (
                    graph_version INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    graph_version INTEGER NOT NULL,
                    include_types TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    analysis_id TEXT NOT NULL,
                    candidate_key TEXT NOT NULL,
                    graph_version INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (analysis_id, candidate_key)
                );
                CREATE TABLE IF NOT EXISTS opinions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL,
                    candidate_key TEXT NOT NULL,
                    graph_version INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    alternative_ops TEXT NOT NULL DEFAULT '[]',
                    comment TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (analysis_id, candidate_key, version)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL,
                    candidate_key TEXT NOT NULL,
                    graph_version INTEGER NOT NULL,
                    decided_by TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            # Connections created in worker threads cannot be closed from the
            # current thread; SQLite releases them at process exit.
            try:
                conn.close()
            except sqlite3.ProgrammingError:
                pass

    # -- graphs -------------------------------------------------------------
    def save_graph(self, payload: dict, note: str = "") -> int:
        with self._write_lock:
            cur = self._conn.execute(
                "INSERT INTO graphs(created_at, note, payload) VALUES (?, ?, ?)",
                (_now(), note, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            return int(cur.lastrowid)

    def latest_graph_version(self) -> Optional[int]:
        row = self._conn.execute("SELECT MAX(graph_version) AS v FROM graphs").fetchone()
        return row["v"] if row and row["v"] is not None else None

    def get_graph(self, version: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT payload FROM graphs WHERE graph_version=?", (version,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_graph_versions(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT graph_version, created_at, note FROM graphs ORDER BY graph_version"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- analyses -----------------------------------------------------------
    def save_analysis(
        self,
        analysis_id: str,
        graph_version: int,
        include_types: List[str],
        payload: dict,
    ) -> None:
        with self._write_lock:
            self._conn.execute(
                """
                INSERT INTO analyses(analysis_id, graph_version, include_types, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(analysis_id) DO UPDATE SET
                    graph_version=excluded.graph_version,
                    include_types=excluded.include_types,
                    created_at=excluded.created_at,
                    payload=excluded.payload
                """,
                (
                    analysis_id,
                    graph_version,
                    ",".join(include_types),
                    _now(),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_analyses(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT analysis_id, graph_version, include_types, created_at FROM analyses "
            "ORDER BY created_at DESC, analysis_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def replace_candidates(
        self, analysis_id: str, graph_version: int, candidates: List[dict]
    ) -> None:
        with self._write_lock:
            self._conn.execute(
                "DELETE FROM candidates WHERE analysis_id=?", (analysis_id,)
            )
            self._conn.executemany(
                "INSERT INTO candidates(analysis_id, candidate_key, graph_version, rank, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        analysis_id,
                        c["key"],
                        graph_version,
                        rank,
                        json.dumps(c, ensure_ascii=False, sort_keys=True),
                    )
                    for rank, c in enumerate(candidates)
                ],
            )

    def get_candidates(self, analysis_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM candidates WHERE analysis_id=? ORDER BY rank",
            (analysis_id,),
        ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # -- opinions (append-only, versioned) ----------------------------------
    def add_opinion(
        self,
        analysis_id: str,
        candidate_key: str,
        graph_version: int,
        author: str,
        kind: str,
        alternative_ops: List[dict],
        comment: str,
    ) -> int:
        with self._write_lock:
            # BEGIN IMMEDIATE makes concurrent submitters serialize; the later
            # transaction always observes the earlier version.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(version), 0) AS v FROM opinions "
                    "WHERE analysis_id=? AND candidate_key=?",
                    (analysis_id, candidate_key),
                ).fetchone()
                version = int(row["v"]) + 1
                self._conn.execute(
                    "INSERT INTO opinions(analysis_id, candidate_key, graph_version, author, "
                    "kind, alternative_ops, comment, version, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        candidate_key,
                        graph_version,
                        author,
                        kind,
                        json.dumps(alternative_ops, ensure_ascii=False, sort_keys=True),
                        comment,
                        version,
                        _now(),
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return version

    def list_opinions(self, analysis_id: str, candidate_key: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM opinions WHERE analysis_id=? AND candidate_key=? ORDER BY version",
            (analysis_id, candidate_key),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["alternative_ops"] = json.loads(d["alternative_ops"])
            result.append(d)
        return result

    # -- decisions ----------------------------------------------------------
    def add_decision(
        self,
        analysis_id: str,
        candidate_key: str,
        graph_version: int,
        decided_by: str,
        note: str,
    ) -> int:
        with self._write_lock:
            cur = self._conn.execute(
                "INSERT INTO decisions(analysis_id, candidate_key, graph_version, decided_by, "
                "note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, candidate_key, graph_version, decided_by, note, _now()),
            )
            return int(cur.lastrowid)

    def latest_decision(self, analysis_id: str, candidate_key: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE analysis_id=? AND candidate_key=? "
            "ORDER BY id DESC LIMIT 1",
            (analysis_id, candidate_key),
        ).fetchone()
        return dict(row) if row else None

    def list_decisions(self, analysis_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE analysis_id=? ORDER BY id", (analysis_id,)
        ).fetchall()
        return [dict(r) for r in rows]
