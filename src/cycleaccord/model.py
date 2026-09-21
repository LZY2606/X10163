"""图与边操作的数据模型。

依赖类型强度：hard > runtime > test > generated。
一次分析视图选择包含哪些依赖类型；原始图始终保留所有边。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

DEP_TYPES = ("hard", "runtime", "test", "generated")
# 强度序号：数字越大越强
TYPE_RANK = {"hard": 3, "runtime": 2, "test": 1, "generated": 0}
DEFAULT_INCLUDE = ("hard", "runtime", "test", "generated")

# 每种分析类型组合下，"允许的较弱类型"集合（必须是被当前视图排除的类型）。
# 降为更弱但仍在视图中的类型无法切断该视图下的环，因此不合法。


@dataclass(frozen=True)
class Node:
    id: str
    name: str = ""
    owner: str = ""
    weight: int = 1
    critical: bool = False


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    dep_type: str = "hard"
    owner: str = ""
    weight: int = 1
    # 该边允许被降级到的具体类型（登记）；为空表示不允许降级
    downgrade: Tuple[str, ...] = ()
    # 锁定边不允许反转或降级；显式登记的接口替换仍然允许
    locked: bool = False


@dataclass(frozen=True)
class InterfaceOption:
    """已登记的可替代接口边。系统只能使用显式登记的接口，不能凭空发明。"""

    id: str
    replaces_edge: str
    new_src: str
    new_dst: str
    new_type: str
    owner: str = ""
    note: str = ""


@dataclass
class Graph:
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: Dict[str, Edge] = field(default_factory=dict)
    interfaces: Dict[str, InterfaceOption] = field(default_factory=dict)
    version: int = 1

    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def included_edges(self, include_types: Tuple[str, ...]) -> List[Edge]:
        wanted = frozenset(include_types)
        return [e for e in self.edges.values() if e.dep_type in wanted]


@dataclass(frozen=True)
class EdgeOp:
    """可机器校验的边操作，应用到图副本后必须无环。

    kind:
      reverse    反转一条边
      downgrade  把 hard（或较强类型）降为登记允许的较弱类型
      interface  用已登记接口替换一条边（删旧边 + 加接口边）
    """

    kind: str
    edge_id: str
    edge_src: str
    edge_dst: str
    edge_type: str
    edge_owner: str = ""
    target_type: str = ""
    interface_id: str = ""
    new_src: str = ""
    new_dst: str = ""
    new_type: str = ""
    note: str = ""

    def signature(self) -> str:
        if self.kind == "reverse":
            return "reverse:%s" % self.edge_id
        if self.kind == "downgrade":
            return "downgrade:%s->%s" % (self.edge_id, self.target_type)
        return "interface:%s:%s" % (self.edge_id, self.interface_id)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "edge_id": self.edge_id,
            "edge_src": self.edge_src,
            "edge_dst": self.edge_dst,
            "edge_type": self.edge_type,
            "edge_owner": self.edge_owner,
            "target_type": self.target_type,
            "interface_id": self.interface_id,
            "new_src": self.new_src,
            "new_dst": self.new_dst,
            "new_type": self.new_type,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d: dict) -> "EdgeOp":
        return EdgeOp(
            kind=d["kind"],
            edge_id=d["edge_id"],
            edge_src=d["edge_src"],
            edge_dst=d["edge_dst"],
            edge_type=d.get("edge_type", "hard"),
            edge_owner=d.get("edge_owner", ""),
            target_type=d.get("target_type", ""),
            interface_id=d.get("interface_id", ""),
            new_src=d.get("new_src", ""),
            new_dst=d.get("new_dst", ""),
            new_type=d.get("new_type", ""),
            note=d.get("note", ""),
        )
