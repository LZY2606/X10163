"""核心领域模型：全部使用不可变数据与 JSON 可序列化的普通结构。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

# 依赖类型（由弱到强的“强度”顺序；弱类型可在被过滤的分析视图里消失）
EDGE_TYPES: Tuple[str, ...] = ("generated", "test", "runtime", "hard")
DEFAULT_TYPES: Tuple[str, ...] = ("hard", "runtime", "test", "generated")

# 允许的降级阶梯：只能沿此顺序把强依赖降为更弱的类型
DOWNGRADE_LADDER: Dict[str, Tuple[str, ...]] = {
    "hard": ("runtime", "test", "generated"),
    "runtime": ("test", "generated"),
    "test": ("generated",),
    "generated": (),
}

# 每种变更操作的基础成本
ACTION_BASE_COST: Dict[str, int] = {
    "downgrade": 3,
    "reverse": 6,
    "interface": 8,
}

# 跨一个额外负责人的成本
CROSS_OWNER_COST = 5


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    owner: str
    weight: int = 1  # 关键组件权重（越大越关键）
    kind: str = "component"  # component | interface


@dataclass(frozen=True)
class DepEdge:
    """src 依赖 target（构建/运行时 src 需要 target）。"""

    id: str
    src: str
    target: str
    type: str
    note: str = ""
    # 允许使用的接口登记 id 列表（为空表示该边没有登记的可替代接口）
    interface_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class InterfaceReg:
    """已登记的可替代接口。系统只能引用已登记接口，不能凭空发明。

    通过接口拆分 src->target 的具体依赖：
      * 若 provider 为空：删除 src->target，新增 src->interface；
      * 若 provider 非空且与 src 不同：删除 src->target，
        新增 src->interface 与 provider->interface。
    """

    id: str
    component: str  # 接口组件节点 id
    consumer: str  # 使用方 src
    provider: Optional[str]  # 提供方；None 表示外部/未知
    replaces_edge: str  # 可替代的具体依赖边 id
    added_type: str = "runtime"
    note: str = ""


@dataclass(frozen=True)
class Graph:
    components: Tuple[Component, ...]
    edges: Tuple[DepEdge, ...]
    interfaces: Tuple[InterfaceReg, ...] = ()

    def component(self, cid: str) -> Component:
        for c in self.components:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def edge(self, eid: str) -> DepEdge:
        for e in self.edges:
            if e.id == eid:
                return e
        raise KeyError(eid)

    def interface(self, iid: str) -> InterfaceReg:
        for i in self.interfaces:
            if i.id == iid:
                return i
        raise KeyError(iid)

    def with_edge(self, edge: DepEdge) -> "Graph":
        kept = tuple(e for e in self.edges if e.id != edge.id)
        return replace(self, edges=kept + (edge,))

    def without_edges(self, edge_ids: List[str]) -> "Graph":
        drop = set(edge_ids)
        return replace(self, edges=tuple(e for e in self.edges if e.id not in drop))

    def with_added(self, added: List["AddedEdge"]) -> "Graph":
        extra = tuple(
            DepEdge(
                id=a.id,
                src=a.src,
                target=a.target,
                type=a.type,
                note=a.note,
            )
            for a in added
        )
        return replace(self, edges=self.edges + extra)


@dataclass(frozen=True)
class AddedEdge:
    id: str
    src: str
    target: str
    type: str
    note: str = ""


@dataclass(frozen=True)
class EdgeOp:
    """可机器校验的原子边操作。

    kind:
      * remove  : 删除 edge_id 指定的边
      * add     : 新增一条边（id/src/target/type）
    """

    kind: str
    edge_id: str = ""
    src: str = ""
    target: str = ""
    type: str = ""
    note: str = ""


@dataclass(frozen=True)
class CandidateAction:
    """候选中的一条逻辑变更。kind 为 reverse / downgrade / interface。"""

    kind: str
    edge_id: str
    new_type: str = ""
    interface_id: str = ""

    def key(self) -> Tuple:
        return (self.kind, self.edge_id, self.new_type, self.interface_id)

    def label(self, graph: Graph) -> str:
        edge = graph.edge(self.edge_id)
        if self.kind == "reverse":
            return f"反转 {edge.id}({edge.src}→{edge.target})"
        if self.kind == "downgrade":
            return (
                f"降级 {edge.id} {edge.type}→{self.new_type}"
                f"({edge.src}→{edge.target})"
            )
        reg = graph.interface(self.interface_id)
        return f"接口化 {edge.id} → {reg.component}"


def action_to_ops(action: CandidateAction, graph: Graph) -> List[EdgeOp]:
    """把逻辑变更展开为可应用、可校验的原子边操作。"""
    edge = graph.edge(action.edge_id)
    if action.kind == "reverse":
        return [
            EdgeOp("remove", edge_id=edge.id),
            EdgeOp(
                "add",
                edge_id=edge.id + ":rev",
                src=edge.target,
                target=edge.src,
                type=edge.type,
                note="反转自 " + edge.id,
            ),
        ]
    if action.kind == "downgrade":
        return [
            EdgeOp("remove", edge_id=edge.id),
            EdgeOp(
                "add",
                edge_id=edge.id + ":down",
                src=edge.src,
                target=edge.target,
                type=action.new_type,
                note=f"降级自 {edge.type}",
            ),
        ]
    if action.kind == "interface":
        reg = graph.interface(action.interface_id)
        ops = [EdgeOp("remove", edge_id=edge.id)]
        ops.append(
            EdgeOp(
                "add",
                edge_id=f"{edge.id}:iface:{reg.id}",
                src=reg.consumer,
                target=reg.component,
                type=reg.added_type,
                note=f"接口边（登记 {reg.id}）",
            )
        )
        if reg.provider and reg.provider != reg.consumer:
            ops.append(
                EdgeOp(
                    "add",
                    edge_id=f"{reg.id}:provider",
                    src=reg.provider,
                    target=reg.component,
                    type=reg.added_type,
                    note=f"接口提供方边（登记 {reg.id}）",
                )
            )
        return ops
    raise ValueError(f"未知变更类型: {action.kind}")


@dataclass(frozen=True)
class Candidate:
    id: str
    analysis_id: str
    actions: Tuple[CandidateAction, ...]
    cost: int
    ops: Tuple[EdgeOp, ...]
    broken_cycles: Tuple[str, ...] = ()
    affected_paths: Tuple[Tuple[str, ...], ...] = ()
    owners_to_confirm: Tuple[str, ...] = ()
    rationale: str = ""
    rank: int = 0

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "analysis_id": self.analysis_id,
            "rank": self.rank,
            "cost": self.cost,
            "actions": [
                {
                    "kind": a.kind,
                    "edge_id": a.edge_id,
                    "new_type": a.new_type,
                    "interface_id": a.interface_id,
                }
                for a in self.actions
            ],
            "ops": [
                {
                    "kind": o.kind,
                    "edge_id": o.edge_id,
                    "src": o.src,
                    "target": o.target,
                    "type": o.type,
                    "note": o.note,
                }
                for o in self.ops
            ],
            "broken_cycles": list(self.broken_cycles),
            "affected_paths": [list(p) for p in self.affected_paths],
            "owners_to_confirm": list(self.owners_to_confirm),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class Opinion:
    id: int
    analysis_id: str
    candidate_id: str
    person: str
    role: str  # accept | reject | alternate
    comment: str
    alternate_ops: Tuple[EdgeOp, ...] = ()
    alternate_feasible: Optional[bool] = None
    created_at: str = ""
    graph_version: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "analysis_id": self.analysis_id,
            "candidate_id": self.candidate_id,
            "person": self.person,
            "role": self.role,
            "comment": self.comment,
            "alternate_ops": [
                {
                    "kind": o.kind,
                    "edge_id": o.edge_id,
                    "src": o.src,
                    "target": o.target,
                    "type": o.type,
                    "note": o.note,
                }
                for o in self.alternate_ops
            ],
            "alternate_feasible": self.alternate_feasible,
            "created_at": self.created_at,
            "graph_version": self.graph_version,
        }
