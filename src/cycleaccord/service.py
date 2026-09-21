"""应用服务层：连接存储、图算法与协商流程。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .graphio import (
    GraphValidationError,
    graph_to_dict,
    normalize_types,
    parse_graph,
)
from .models import EdgeOp, Graph
from .paths import edge_endpoints_in_paths, reachability_after
from .scc import cyclic_components
from .solver import solve, verify_ops
from .storage import Store


class Service:
    def __init__(self, store: Store):
        self.store = store

    # ---------- 图 ----------
    def import_graph(
        self, payload: Any, note: str = ""
    ) -> Dict[str, Any]:
        graph = parse_graph(payload)
        version, ordinal, is_new = self.store.put_graph(graph, note)
        return {
            "graph_version": version,
            "ordinal": ordinal,
            "is_new": is_new,
            "components": len(graph.components),
            "edges": len(graph.edges),
            "interfaces": len(graph.interfaces),
        }

    def graph_state(
        self, version: Optional[str] = None
    ) -> Dict[str, Any]:
        graph, ver = self.store.load_graph(version)
        current = self.store.current_version()
        return {
            "graph_version": ver,
            "current_version": current,
            "is_current": ver == current,
            "graph": graph_to_dict(graph),
            "versions": self.store.list_graph_versions(),
        }

    # ---------- 分析 ----------
    def analyze(
        self,
        types: Optional[Sequence[str]] = None,
        graph_version: Optional[str] = None,
        label: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        graph, ver = self.store.load_graph(graph_version)
        types = normalize_types(types)
        aid = self.store.create_analysis(ver, types, label)
        existing = self.store.list_candidates(aid)
        diag: Dict[str, Any] = {"truncated": False}
        if not existing or force:
            candidates, diag = solve(graph, types, aid)
            self.store.save_candidates(aid, ver, candidates)
            existing = self.store.list_candidates(aid)
        comps = cyclic_components(graph, types)
        return {
            "analysis_id": aid,
            "graph_version": ver,
            "is_current": ver == self.store.current_version(),
            "types": list(types),
            "cyclic_components": comps,
            "candidates": existing,
            "diag": diag,
        }

    def analysis_detail(self, analysis_id: str) -> Dict[str, Any]:
        analysis = self.store.get_analysis(analysis_id)
        if not analysis:
            raise KeyError(f"未知分析: {analysis_id}")
        graph, _ = self.store.load_graph(analysis["graph_version"])
        candidates = self.store.list_candidates(analysis_id)
        opinions = self.store.list_opinions(analysis_id)
        selection = self.store.get_selection(analysis_id)
        statuses = self.store.opinions_status(self.store.current_version())
        for op in opinions:
            op["status"] = statuses.get(
                f"opinion-{op['id']}",
                "current" if op["graph_version"] == self.store.current_version()
                else "superseded",
            )
        sel_status = None
        if selection:
            sel_status = statuses.get(f"selection-{selection['id']}")
        return {
            "analysis": analysis,
            "graph": graph_to_dict(graph),
            "cyclic_components": cyclic_components(graph, analysis["types"]),
            "candidates": candidates,
            "opinions": opinions,
            "selection": selection,
            "selection_status": sel_status,
            "current_version": self.store.current_version(),
        }

    def candidate_detail(
        self, analysis_id: str, candidate_id: str
    ) -> Dict[str, Any]:
        analysis = self.store.get_analysis(analysis_id)
        if not analysis:
            raise KeyError(f"未知分析: {analysis_id}")
        cand = self.store.get_candidate(analysis_id, candidate_id)
        if not cand:
            raise KeyError(f"未知候选: {candidate_id}")
        graph, _ = self.store.load_graph(analysis["graph_version"])
        ops = [
            EdgeOp(
                o["kind"], o["edge_id"], o["src"], o["target"], o["type"],
                o.get("note", ""),
            )
            for o in cand["ops"]
        ]
        removed = {
            a["edge_id"]
            for a in cand["actions"]
        }
        added = [o for o in ops if o.kind == "add"]
        reach = reachability_after(graph, analysis["types"], removed, added)
        node_paths = edge_endpoints_in_paths(
            cand["affected_paths"], graph
        )
        verified = verify_ops(graph, analysis["types"], ops)
        opinions = self.store.list_opinions(analysis_id, candidate_id)
        statuses = self.store.opinions_status(self.store.current_version())
        for op in opinions:
            op["status"] = statuses.get(f"opinion-{op['id']}")
        return {
            "candidate": cand,
            "removed_edge_ids": sorted(removed),
            "added_edges": [
                {"id": o.edge_id, "src": o.src, "target": o.target,
                 "type": o.type, "note": o.note}
                for o in added
            ],
            "reachable_after": reach,
            "affected_node_paths": node_paths,
            "verified_acyclic": verified,
            "opinions": opinions,
        }

    # ---------- 意见 ----------
    def submit_opinion(
        self,
        analysis_id: str,
        candidate_id: str,
        person: str,
        role: str,
        comment: str = "",
        alternate_ops_raw: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        analysis = self.store.get_analysis(analysis_id)
        if not analysis:
            raise KeyError(f"未知分析: {analysis_id}")
        if not self.store.get_candidate(analysis_id, candidate_id):
            raise KeyError(f"未知候选: {candidate_id}")
        if not person or not person.strip():
            raise ValueError("必须提供提交人")
        graph, _ = self.store.load_graph(analysis["graph_version"])

        alternate_ops: List[EdgeOp] = []
        feasible: Optional[bool] = None
        if role == "alternate":
            if not alternate_ops_raw:
                raise ValueError("提出替代边必须提供边操作")
            alternate_ops = self._parse_ops(alternate_ops_raw)
            # 机器校验替代方案：应用到图副本后是否无环
            feasible = verify_ops(graph, analysis["types"], alternate_ops)

        oid = self.store.add_opinion(
            analysis_id,
            candidate_id,
            analysis["graph_version"],
            person.strip(),
            role,
            comment or "",
            alternate_ops,
            feasible,
        )
        return {"opinion_id": oid, "alternate_feasible": feasible}

    @staticmethod
    def _parse_ops(raw: Sequence[Dict[str, Any]]) -> List[EdgeOp]:
        ops: List[EdgeOp] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("边操作必须是对象")
            kind = item.get("kind")
            if kind not in ("remove", "add"):
                raise ValueError("原子边操作 kind 必须是 remove/add")
            ops.append(
                EdgeOp(
                    kind=kind,
                    edge_id=item.get("edge_id", ""),
                    src=item.get("src", ""),
                    target=item.get("target", ""),
                    type=item.get("type", ""),
                    note=item.get("note", ""),
                )
            )
        return ops

    def select(
        self, analysis_id: str, candidate_id: str, person: str
    ) -> Dict[str, Any]:
        analysis = self.store.get_analysis(analysis_id)
        if not analysis:
            raise KeyError(f"未知分析: {analysis_id}")
        cand = self.store.get_candidate(analysis_id, candidate_id)
        if not cand:
            raise KeyError(f"未知候选: {candidate_id}")
        graph, _ = self.store.load_graph(analysis["graph_version"])
        ops = self._parse_ops(cand["ops"])
        if not verify_ops(graph, analysis["types"], ops):
            raise ValueError("候选未通过无环机器校验，不能选择")
        sid = self.store.set_selection(
            analysis_id, candidate_id, analysis["graph_version"], person
        )
        current = self.store.current_version()
        return {
            "selection_id": sid,
            "status": "current"
            if analysis["graph_version"] == current else "superseded",
        }

    def history(self) -> Dict[str, Any]:
        current = self.store.current_version()
        opinions = self.store.list_opinions()
        statuses = self.store.opinions_status(current)
        for op in opinions:
            op["status"] = statuses.get(f"opinion-{op['id']}")
        selections = self.store.list_selections()
        for sel in selections:
            sel["status"] = statuses.get(f"selection-{sel['id']}")
        return {
            "current_version": current,
            "graph_versions": self.store.list_graph_versions(),
            "analyses": self.store.list_analyses(),
            "opinions": opinions,
            "selections": selections,
        }
