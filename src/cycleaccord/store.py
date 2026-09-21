"""Persistence for graphs, analyses, opinions and the final selection.

Everything lives in a single JSON document written atomically. Opinions
are append-only: concurrent submissions each get their own sequential id
and version, a later write never overwrites an earlier one. Approvals are
versioned against the graph: once the graph changes, old approvals remain
on record but are reported as history only.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from .model import Graph


def _now():
    return datetime.now(timezone.utc).isoformat()


class StoreError(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.data = json.load(fh)
        else:
            self.data = {
                "graph_version": 0,
                "graph": None,
                "analyses": [],
                "opinions": [],
                "selection": None,
            }
            self._save()

    def _save(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # -- graph -------------------------------------------------------------
    def get_graph(self):
        with self.lock:
            if self.data["graph"] is None:
                return None
            return Graph.from_dict(self.data["graph"])

    def save_graph(self, graph_dict):
        """Replace the original graph; bumps the version, which retires all
        previous approvals to history (they are kept, never deleted)."""
        graph = Graph.from_dict(graph_dict)  # validates
        with self.lock:
            self.data["graph_version"] += 1
            self.data["graph"] = graph.to_dict()
            self._save()
            return self.data["graph_version"]

    # -- analyses ----------------------------------------------------------
    def add_analysis(self, active_types, sccs, candidates, message=None):
        with self.lock:
            record = {
                "id": "an-%d" % (len(self.data["analyses"]) + 1),
                "graph_version": self.data["graph_version"],
                "active_types": list(active_types),
                "sccs": sccs,
                "candidates": candidates,
                "message": message,
                "created_at": _now(),
            }
            self.data["analyses"].append(record)
            self._save()
            return record

    def get_analysis(self, analysis_id):
        with self.lock:
            for record in self.data["analyses"]:
                if record["id"] == analysis_id:
                    return record
        raise StoreError("unknown analysis: %s" % analysis_id)

    # -- opinions (append-only, versioned) ---------------------------------
    def add_opinion(self, analysis_id, candidate_id, owner, action, detail=None):
        if action not in ("accept", "reject", "propose"):
            raise StoreError("unknown opinion action: %s" % action)
        with self.lock:
            analysis = self.get_analysis(analysis_id)
            if candidate_id not in {c["id"] for c in analysis["candidates"]}:
                raise StoreError("unknown candidate: %s" % candidate_id)
            record = {
                "id": "op-%d" % (len(self.data["opinions"]) + 1),
                "analysis_id": analysis_id,
                "candidate_id": candidate_id,
                "owner": owner,
                "action": action,
                "detail": detail or {},
                "graph_version": self.data["graph_version"],
                "created_at": _now(),
            }
            self.data["opinions"].append(record)
            self._save()
            return record

    # -- final selection ----------------------------------------------------
    def set_selection(self, analysis_id, candidate_id, decided_by):
        with self.lock:
            analysis = self.get_analysis(analysis_id)
            if candidate_id not in {c["id"] for c in analysis["candidates"]}:
                raise StoreError("unknown candidate: %s" % candidate_id)
            record = {
                "analysis_id": analysis_id,
                "candidate_id": candidate_id,
                "decided_by": decided_by,
                "graph_version": self.data["graph_version"],
                "created_at": _now(),
            }
            self.data["selection"] = record
            self._save()
            return record

    # -- views ---------------------------------------------------------------
    def _opinion_view(self, opinion):
        view = dict(opinion)
        view["valid"] = opinion["graph_version"] == self.data["graph_version"]
        view["status"] = "有效" if view["valid"] else "历史"
        return view

    def state(self):
        with self.lock:
            selection = self.data["selection"]
            if selection is not None:
                selection = dict(selection)
                selection["valid"] = (
                    selection["graph_version"] == self.data["graph_version"]
                )
                selection["status"] = "有效" if selection["valid"] else "历史"
            return {
                "graph": self.data["graph"],
                "graph_version": self.data["graph_version"],
                "analyses": [
                    {
                        "id": a["id"],
                        "graph_version": a["graph_version"],
                        "active_types": a["active_types"],
                        "created_at": a["created_at"],
                        "candidate_count": len(a["candidates"]),
                        "current": a["graph_version"] == self.data["graph_version"],
                    }
                    for a in self.data["analyses"]
                ],
                "opinions": [self._opinion_view(o) for o in self.data["opinions"]],
                "selection": selection,
            }
