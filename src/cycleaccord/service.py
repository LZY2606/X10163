"""应用服务：连接持久化、图算法与候选协商逻辑。"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .algorithms import verify_candidate
from .candidates import candidate_to_dict, generate_candidates
from .graphio import parse_graph
from .model import DEP_TYPES, EdgeOp
from .storage import Storage


def analysis_id_for(graph_version: int, include_types: List[str]) -> str:
    raw = "%s:%s" % (graph_version, ",".join(sorted(include_types)))
    return "A" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


class Service:
    def __init__(self, storage: Storage):
        self.storage = storage

    # ------------------------------------------------------------------
    # 图
    # ------------------------------------------------------------------

    def latest_version(self) -> Optional[int]:
        return self.storage.latest_graph_version()

    def import_graph(self, graph_json: Dict[str, Any]) -> int:
        # 先解析校验，非法图不会产生新版本
        version = (self.latest_version() or 0) + 1
        parse_graph(graph_json, version=version)
        return self.storage.import_graph(graph_json)

    def load_graph(self, version: Optional[int] = None):
        if version is None:
            version = self.latest_version()
        if version is None:
            raise LookupError("尚未导入任何图")
        raw = self.storage.get_graph_json(version)
        if raw is None:
            raise LookupError("图版本不存在: %s" % version)
        return parse_graph(raw, version=version), version

    def graph_summary(self, version: int) -> Dict[str, Any]:
        raw = self.storage.get_graph_json(version)
        if raw is None:
            raise LookupError("图版本不存在: %s" % version)
        return raw

    # ------------------------------------------------------------------
    # 分析
    # ------------------------------------------------------------------

    def ensure_analysis(self, include_types: List[str],
                        graph_version: Optional[int] = None) -> Dict[str, Any]:
        include_types = [t for t in include_types if t in DEP_TYPES]
        if not include_types:
            raise ValueError("至少包含一种依赖类型")
        graph, version = self.load_graph(graph_version)
        analysis_id = analysis_id_for(version, include_types)

        existing = self.storage.get_analysis(analysis_id)
        if existing is not None:
            return {"id": analysis_id, "graph_version": version,
                    "include_types": include_types, "payload": existing["payload"],
                    "reused": True}

        result = generate_candidates(graph, tuple(include_types))
        candidates = [candidate_to_dict(c) for c in result.candidates]
        payload = {
            "sccs": result.sccs,
            "infeasible_sccs": result.infeasible_sccs,
            "complete": result.complete,
            "include_types": include_types,
            "candidates": candidates,
        }
        self.storage.save_analysis(analysis_id, version, include_types, payload)
        self.storage.save_candidates(analysis_id, version, candidates)
        return {"id": analysis_id, "graph_version": version,
                "include_types": include_types, "payload": payload, "reused": False}

    def get_analysis(self, analysis_id: str):
        record = self.storage.get_analysis(analysis_id)
        if record is None:
            return None
        graph, _ = self.load_graph(record["graph_version"])
        return record, graph

    # ------------------------------------------------------------------
    # 意见与批准
    # ------------------------------------------------------------------

    def add_opinion(self, analysis_id: str, candidate_id: str, owner: str,
                    decision: str, comment: str = "", alternative: str = "") -> Dict[str, Any]:
        record = self.storage.get_analysis(analysis_id)
        if record is None:
            raise LookupError("分析不存在: %s" % analysis_id)
        candidate = self.storage.get_candidate(analysis_id, candidate_id)
        if candidate is None:
            raise LookupError("候选不存在: %s" % candidate_id)
        return self.storage.add_opinion(
            analysis_id, candidate_id, record["graph_version"],
            owner, decision, comment, alternative,
        )

    def approve(self, analysis_id: str, candidate_id: str, owner: str) -> Dict[str, Any]:
        record = self.storage.get_analysis(analysis_id)
        if record is None:
            raise LookupError("分析不存在: %s" % analysis_id)
        candidate = self.storage.get_candidate(analysis_id, candidate_id)
        if candidate is None:
            raise LookupError("候选不存在: %s" % candidate_id)
        if not candidate.get("verified"):
            raise ValueError("未通过无环校验的候选不能批准")
        # 图变化后旧分析不再是最新版本：只追加历史，不作为当前批准
        latest = self.latest_version()
        current = latest == record["graph_version"]
        payload = {"candidate": candidate, "current": current}
        saved = self.storage.add_selection(
            analysis_id, candidate_id, record["graph_version"], owner, payload
        )
        saved["current"] = current
        saved["latest_graph_version"] = latest
        return saved

    def is_stale(self, record: Dict[str, Any]) -> bool:
        return record["graph_version"] != self.latest_version()

    # ------------------------------------------------------------------
    # 机器校验（外部也可独立调用）
    # ------------------------------------------------------------------

    def machine_verify(self, analysis_id: str, candidate_id: str) -> Dict[str, Any]:
        record = self.storage.get_analysis(analysis_id)
        if record is None:
            raise LookupError("分析不存在: %s" % analysis_id)
        candidate = self.storage.get_candidate(analysis_id, candidate_id)
        if candidate is None:
            raise LookupError("候选不存在: %s" % candidate_id)
        graph, _ = self.load_graph(record["graph_version"])
        ops = [EdgeOp.from_dict(op) for op in candidate["ops"]]
        ok = verify_candidate(graph, ops, record["include_types"])
        return {"analysis_id": analysis_id, "candidate_id": candidate_id,
                "acyclic": ok, "graph_version": record["graph_version"]}

    # ------------------------------------------------------------------
    # 页面状态
    # ------------------------------------------------------------------

    def state(self) -> Dict[str, Any]:
        latest = self.latest_version()
        analyses = []
        for rec in self.storage.list_analyses():
            full = self.storage.get_analysis(rec["id"])
            stale = rec["graph_version"] != latest
            opinions = self.storage.list_opinions(rec["id"])
            selections = self.storage.list_selections(rec["id"])
            analyses.append({
                "id": rec["id"],
                "graph_version": rec["graph_version"],
                "include_types": rec["include_types"],
                "created_at": rec["created_at"],
                "stale": stale,
                "payload": full["payload"] if full else None,
                "opinions": opinions,
                "selections": selections,
            })
        return {
            "latest_graph_version": latest,
            "graph": self.graph_summary(latest) if latest else None,
            "dep_types": list(DEP_TYPES),
            "analyses": analyses,
        }
