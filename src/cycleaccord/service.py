"""Application facade: ties graph model, analysis, verification and storage."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .candidates import (
    AnalysisResult,
    analyze,
    candidate_key,
    candidate_to_dict,
)
from .model import DEP_TYPES, Graph, VALID_OPINIONS
from .operations import EdgeOperation, verify
from .persistence import Store


class ServiceError(ValueError):
    pass


class AccordService:
    def __init__(self, store: Store, bootstrap: Optional[Dict[str, Any]] = None):
        self.store = store
        version = store.latest_graph_version()
        if version is None and bootstrap is not None:
            graph = self._build_validated(bootstrap)
            version = store.save_graph(graph.to_dict(), note="bootstrap sample")
        self._graph_version = version

    # -- graph --------------------------------------------------------------
    @staticmethod
    def _build_validated(payload: dict) -> Graph:
        graph = Graph.from_dict(payload)
        errors = graph.validate()
        if errors:
            raise ServiceError("invalid graph: " + "; ".join(errors))
        return graph

    @property
    def graph_version(self) -> int:
        if self._graph_version is None:
            raise ServiceError("no graph imported yet")
        return self._graph_version

    def current_graph(self) -> Graph:
        payload = self.store.get_graph(self.graph_version)
        return Graph.from_dict(payload)

    def import_graph(self, payload: dict, note: str = "") -> int:
        graph = self._build_validated(payload)
        # A new graph version invalidates prior approvals/opinions: they stay
        # in storage as history and are reported stale against the new version.
        self._graph_version = self.store.save_graph(graph.to_dict(), note=note)
        return self._graph_version

    # -- analysis -----------------------------------------------------------
    @staticmethod
    def analysis_id(graph_version: int, include_types: List[str]) -> str:
        # The identity of an analysis is its dependency-type projection; the
        # graph version is tracked separately so opinions/approvals recorded
        # under an older graph keep living under the same analysis and can be
        # reported as stale history.
        canonical = ",".join(sorted(include_types))
        return "an_" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]

    def compute_analysis(self, include_types: List[str]) -> Dict[str, Any]:
        include_types = self._normalize_types(include_types)
        graph = self.current_graph()
        result = analyze(graph, include_types)
        analysis_id = self.analysis_id(self.graph_version, include_types)
        payload = self._analysis_payload(graph, analysis_id, include_types, result)
        self.store.save_analysis(analysis_id, self.graph_version, include_types, payload)
        self.store.replace_candidates(
            analysis_id, self.graph_version, payload["candidates"]
        )
        return payload

    def _normalize_types(self, include_types: List[str]) -> List[str]:
        if not include_types:
            raise ServiceError("at least one dependency type must be selected")
        bad = [t for t in include_types if t not in DEP_TYPES]
        if bad:
            raise ServiceError("unknown dependency types: %s" % bad)
        # Stable canonical order while retaining the known type order.
        return [t for t in DEP_TYPES if t in include_types]

    def _analysis_payload(
        self,
        graph: Graph,
        analysis_id: str,
        include_types: List[str],
        result: AnalysisResult,
    ) -> Dict[str, Any]:
        sccs = []
        for info in result.sccs:
            sccs.append(
                {
                    "index": info.index,
                    "id": "scc%d" % info.index,
                    "members": info.members,
                    "self_loop": info.self_loop,
                    "cycles": [
                        {
                            "edges": cycle_edges,
                            "nodes": [graph.edges[e].source for e in cycle_edges]
                            + ([graph.edges[cycle_edges[0]].source] if cycle_edges else []),
                        }
                        for cycle_edges in info.cycles
                    ],
                    "splittable": info.splittable,
                    "unsplittable_reason": info.unsplittable_reason,
                }
            )
        candidates = [candidate_to_dict(c) for c in result.candidates]
        return {
            "analysis_id": analysis_id,
            "graph_version": self.graph_version,
            "include_types": include_types,
            "cyclic": result.cyclic,
            "sccs": sccs,
            "candidates": candidates,
        }

    def get_analysis(self, include_types: List[str]) -> Dict[str, Any]:
        include_types = self._normalize_types(include_types)
        analysis_id = self.analysis_id(self.graph_version, include_types)
        return self.compute_analysis(include_types)

    def _require_candidate(self, analysis_id: str, candidate_key_value: str) -> dict:
        for candidate in self.store.get_candidates(analysis_id):
            if candidate["key"] == candidate_key_value:
                return candidate
        raise ServiceError("unknown candidate %s for analysis %s"
                           % (candidate_key_value, analysis_id))

    # -- opinions -----------------------------------------------------------
    def submit_opinion(
        self,
        include_types: List[str],
        candidate_key_value: str,
        author: str,
        kind: str,
        alternative_ops: Optional[List[dict]] = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        include_types = self._normalize_types(include_types)
        author = (author or "").strip()
        if not author:
            raise ServiceError("author (responsible owner) is required")
        if kind not in VALID_OPINIONS:
            raise ServiceError("kind must be one of %s" % (VALID_OPINIONS,))
        analysis_id = self.analysis_id(self.graph_version, include_types)
        self._require_candidate(analysis_id, candidate_key_value)

        alternative_ops = alternative_ops or []
        if kind == "alternative":
            self._verify_alternative(include_types, alternative_ops)

        version = self.store.add_opinion(
            analysis_id=analysis_id,
            candidate_key=candidate_key_value,
            graph_version=self.graph_version,
            author=author,
            kind=kind,
            alternative_ops=alternative_ops,
            comment=comment,
        )
        return self._opinion_view(analysis_id, candidate_key_value, version)

    def _verify_alternative(self, include_types: List[str], raw_ops: List[dict]) -> None:
        if not raw_ops:
            raise ServiceError("alternative proposal requires edge operations")
        operations: List[EdgeOperation] = []
        for raw in raw_ops:
            try:
                operations.append(EdgeOperation.from_dict(raw))
            except (KeyError, TypeError) as exc:
                raise ServiceError("malformed edge operation: %s" % exc)
        graph = self.current_graph()
        report = verify(graph, operations, include_types)
        if not report.ok:
            detail = "; ".join(report.errors) if report.errors else \
                "cycles remain: %s" % report.cyclic_components
            raise ServiceError("alternative does not yield an acyclic graph copy: " + detail)

    def _opinion_view(self, analysis_id: str, candidate_key_value: str, version: int) -> dict:
        opinions = self.store.list_opinions(analysis_id, candidate_key_value)
        opinion = next(o for o in opinions if o["version"] == version)
        opinion["stale"] = opinion["graph_version"] != self.graph_version
        return opinion

    def candidate_discussion(self, include_types: List[str], candidate_key_value: str) -> dict:
        include_types = self._normalize_types(include_types)
        analysis_id = self.analysis_id(self.graph_version, include_types)
        # History must remain readable after the graph changes even when the
        # candidate no longer exists in the current analysis.
        opinions = self.store.list_opinions(analysis_id, candidate_key_value)
        for opinion in opinions:
            opinion["stale"] = opinion["graph_version"] != self.graph_version
        decision = self.store.latest_decision(analysis_id, candidate_key_value)
        if decision is not None:
            decision = dict(decision)
            decision["stale"] = decision["graph_version"] != self.graph_version
        return {
            "analysis_id": analysis_id,
            "candidate_key": candidate_key_value,
            "graph_version": self.graph_version,
            "opinions": opinions,
            "latest_decision": decision,
        }

    # -- decision -----------------------------------------------------------
    def decide(
        self,
        include_types: List[str],
        candidate_key_value: str,
        decided_by: str,
        note: str = "",
    ) -> dict:
        include_types = self._normalize_types(include_types)
        decided_by = (decided_by or "").strip()
        if not decided_by:
            raise ServiceError("decided_by is required")
        analysis_id = self.analysis_id(self.graph_version, include_types)
        self._require_candidate(analysis_id, candidate_key_value)
        new_id = self.store.add_decision(
            analysis_id, candidate_key_value, self.graph_version, decided_by, note
        )
        decision = self.store.latest_decision(analysis_id, candidate_key_value)
        decision["stale"] = False
        decision["id"] = new_id
        return decision

    def decisions_history(self, include_types: List[str]) -> List[dict]:
        include_types = self._normalize_types(include_types)
        analysis_id = self.analysis_id(self.graph_version, include_types)
        rows = self.store.list_decisions(analysis_id)
        for row in rows:
            row["stale"] = row["graph_version"] != self.graph_version
        return rows

    # -- ad-hoc verification ------------------------------------------------
    def verify_operations(self, include_types: List[str], raw_ops: List[dict]) -> dict:
        include_types = self._normalize_types(include_types)
        operations = [EdgeOperation.from_dict(raw) for raw in raw_ops]
        graph = self.current_graph()
        report = verify(graph, operations, include_types)
        return {
            "acyclic": report.ok,
            "errors": report.errors,
            "cyclic_components": report.cyclic_components,
            "candidate_key": candidate_key(operations),
        }

    # -- overall state ------------------------------------------------------
    def state(self) -> dict:
        graph = self.current_graph()
        return {
            "graph_version": self.graph_version,
            "dep_types": list(DEP_TYPES),
            "graph": graph.to_dict(),
            "graph_versions": self.store.list_graph_versions(),
            "analyses": self.store.list_analyses(),
        }
