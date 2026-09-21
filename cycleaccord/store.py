"""Persistent, versioned store: original graph, analysis versions, opinions,
final selection. Opinions are append-only: concurrent submissions each keep
their own version and a later write never overwrites an earlier one."""
from __future__ import annotations

import json
import os
import threading

from .analysis import analyze
from .model import EDGE_TYPES, Graph

SCHEMA = 1


def _empty_state():
    return {
        "schema": SCHEMA,
        "graph": None,
        "graph_version": 0,
        "view": {"types": list(EDGE_TYPES)},
        "analyses": [],      # each: {version, graph_version, types, result}
        "opinions": [],      # append-only
        "opinion_seq": 0,
        "selection": None,   # {candidate_id, graph_version, analysis_version, by}
    }


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.data = json.load(fh)
        else:
            self.data = _empty_state()

    # -- persistence --------------------------------------------------------
    def _save(self):
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # -- graph / view ---------------------------------------------------------
    def load_graph(self, graph_dict):
        """Replace the original graph. Bumps graph_version, which turns every
        prior approval into history."""
        with self.lock:
            self.data["graph"] = Graph.from_dict(graph_dict).to_dict()
            self.data["graph_version"] += 1
            self._recompute()
            self._save()
            return self.data["graph_version"]

    def set_view(self, types):
        """Change the analysis view (included edge types). Original graph is
        not modified."""
        with self.lock:
            keep = [t for t in types if t in EDGE_TYPES]
            self.data["view"] = {"types": keep}
            self._recompute()
            self._save()
            return self.current_analysis_version()

    def _recompute(self):
        if not self.data["graph"]:
            return
        graph = Graph.from_dict(self.data["graph"])
        result = analyze(graph, self.data["view"]["types"])
        self.data["analyses"].append({
            "version": len(self.data["analyses"]) + 1,
            "graph_version": self.data["graph_version"],
            "types": list(self.data["view"]["types"]),
            "result": result,
        })

    def current_analysis(self):
        return self.data["analyses"][-1] if self.data["analyses"] else None

    def current_analysis_version(self):
        a = self.current_analysis()
        return a["version"] if a else 0

    # -- opinions -------------------------------------------------------------
    def add_opinion(self, candidate_id, owner, action, comment="",
                    alternative_edge=None):
        """Append an opinion. Never overwrites: each (owner, candidate) pair
        gets its own monotonically increasing version, so concurrent or
        repeated submissions are all preserved."""
        if action not in ("accept", "reject", "propose"):
            raise ValueError("action must be accept|reject|propose")
        with self.lock:
            analysis = self.current_analysis()
            if not analysis:
                raise ValueError("no analysis available")
            known = {c["id"] for c in analysis["result"]["candidates"]}
            if candidate_id not in known:
                raise ValueError("unknown candidate: %s" % candidate_id)
            prior = [o for o in self.data["opinions"]
                     if o["owner"] == owner and o["candidate_id"] == candidate_id]
            self.data["opinion_seq"] += 1
            opinion = {
                "id": "op-%04d" % self.data["opinion_seq"],
                "seq": self.data["opinion_seq"],
                "owner_version": len(prior) + 1,
                "candidate_id": candidate_id,
                "owner": owner,
                "action": action,
                "comment": comment,
                "alternative_edge": alternative_edge,
                "graph_version": self.data["graph_version"],
                "analysis_version": analysis["version"],
            }
            self.data["opinions"].append(opinion)
            self._save()
            return dict(opinion)

    def select(self, candidate_id, by):
        with self.lock:
            analysis = self.current_analysis()
            if not analysis:
                raise ValueError("no analysis available")
            known = {c["id"] for c in analysis["result"]["candidates"]}
            if candidate_id not in known:
                raise ValueError("unknown candidate: %s" % candidate_id)
            self.data["selection"] = {
                "candidate_id": candidate_id,
                "graph_version": self.data["graph_version"],
                "analysis_version": analysis["version"],
                "by": by,
            }
            self._save()
            return dict(self.data["selection"])

    # -- read -------------------------------------------------------------------
    def state(self):
        with self.lock:
            gv = self.data["graph_version"]
            analysis = self.current_analysis()
            opinions = []
            for o in self.data["opinions"]:
                op = dict(o)
                op["status"] = "current" if (
                    o["graph_version"] == gv
                    and analysis
                    and o["analysis_version"] == analysis["version"]
                ) else "historical"
                opinions.append(op)
            selection = self.data["selection"]
            if selection:
                selection = dict(selection)
                selection["status"] = "current" if (
                    selection["graph_version"] == gv
                    and analysis
                    and selection["analysis_version"] == analysis["version"]
                ) else "historical"
            return {
                "graph": self.data["graph"],
                "graph_version": gv,
                "view": dict(self.data["view"]),
                "edge_types": list(EDGE_TYPES),
                "analysis": analysis,
                "analysis_versions": [
                    {"version": a["version"], "graph_version": a["graph_version"],
                     "types": a["types"]}
                    for a in self.data["analyses"]
                ],
                "opinions": opinions,
                "selection": selection,
            }
