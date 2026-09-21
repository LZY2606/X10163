"""图的解析与校验。导入格式对调用方友好，缺失字段使用安全默认值。"""

from __future__ import annotations

from typing import Any, Dict

from .model import DEP_TYPES, Edge, Graph, InterfaceOption, Node


def parse_graph(data: Dict[str, Any], version: int = 1) -> Graph:
    if not isinstance(data, dict):
        raise ValueError("图定义必须是对象")

    graph = Graph(version=version)

    for raw in data.get("nodes", []) or []:
        node_id = str(raw["id"])
        graph.nodes[node_id] = Node(
            id=node_id,
            name=str(raw.get("name", node_id)),
            owner=str(raw.get("owner", "")),
            weight=int(raw.get("weight", 1)),
            critical=bool(raw.get("critical", False)),
        )

    def require_node(ref: str, where: str) -> None:
        if ref not in graph.nodes:
            raise ValueError("%s 引用了不存在的节点: %s" % (where, ref))

    for raw in data.get("edges", []) or []:
        edge_id = str(raw["id"])
        src = str(raw["src"])
        dst = str(raw["dst"])
        dep_type = str(raw.get("type", raw.get("dep_type", "hard")))
        require_node(src, "边 %s 的 src" % edge_id)
        require_node(dst, "边 %s 的 dst" % edge_id)
        if dep_type not in DEP_TYPES:
            raise ValueError("边 %s 的依赖类型非法: %s" % (edge_id, dep_type))
        downgrade = tuple(str(t) for t in (raw.get("downgrade") or ()))
        for t in downgrade:
            if t not in DEP_TYPES:
                raise ValueError("边 %s 的降级类型非法: %s" % (edge_id, t))
        if edge_id in graph.edges:
            raise ValueError("边 id 重复: %s" % edge_id)
        graph.edges[edge_id] = Edge(
            id=edge_id,
            src=src,
            dst=dst,
            dep_type=dep_type,
            owner=str(raw.get("owner", "")),
            weight=int(raw.get("weight", 1)),
            downgrade=downgrade,
            locked=bool(raw.get("locked", False)),
        )

    for raw in data.get("interfaces", []) or []:
        iid = str(raw["id"])
        replaces = str(raw["replaces_edge"])
        new_src = str(raw["new_src"])
        new_dst = str(raw["new_dst"])
        new_type = str(raw.get("new_type", "hard"))
        if replaces not in graph.edges:
            raise ValueError("接口 %s 替换的边不存在: %s" % (iid, replaces))
        require_node(new_src, "接口 %s 的 new_src" % iid)
        require_node(new_dst, "接口 %s 的 new_dst" % iid)
        if new_type not in DEP_TYPES:
            raise ValueError("接口 %s 的类型非法: %s" % (iid, new_type))
        if iid in graph.interfaces:
            raise ValueError("接口 id 重复: %s" % iid)
        graph.interfaces[iid] = InterfaceOption(
            id=iid,
            replaces_edge=replaces,
            new_src=new_src,
            new_dst=new_dst,
            new_type=new_type,
            owner=str(raw.get("owner", "")),
            note=str(raw.get("note", "")),
        )

    return graph
