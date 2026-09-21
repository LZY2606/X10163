"""图的 JSON 导入、校验与序列化。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .models import (
    DEFAULT_TYPES,
    EDGE_TYPES,
    Component,
    DepEdge,
    Graph,
    InterfaceReg,
)

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class GraphValidationError(ValueError):
    pass


def _require_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise GraphValidationError(f"{what} 的 id 非法: {value!r}")
    return value


def parse_graph(payload: Any) -> Graph:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise GraphValidationError("图定义必须是 JSON 对象")

    raw_components = payload.get("components", [])
    raw_edges = payload.get("edges", [])
    raw_interfaces = payload.get("interfaces", [])
    if not isinstance(raw_components, list) or not isinstance(raw_edges, list):
        raise GraphValidationError("components 与 edges 必须是数组")
    if not isinstance(raw_interfaces, list):
        raise GraphValidationError("interfaces 必须是数组")

    components: List[Component] = []
    seen = set()
    for item in raw_components:
        if not isinstance(item, dict):
            raise GraphValidationError("component 必须是对象")
        cid = _require_id(item.get("id"), "component")
        if cid in seen:
            raise GraphValidationError(f"component id 重复: {cid}")
        seen.add(cid)
        name = str(item.get("name", cid))
        owner = item.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            raise GraphValidationError(f"component {cid} 缺少负责人 owner")
        weight = item.get("weight", 1)
        if not isinstance(weight, int) or weight < 0:
            raise GraphValidationError(f"component {cid} 的 weight 必须是非负整数")
        kind = item.get("kind", "component")
        if kind not in ("component", "interface"):
            raise GraphValidationError(f"component {cid} 的 kind 非法: {kind}")
        components.append(Component(cid, name, owner.strip(), weight, kind))

    comp_ids = {c.id for c in components}

    edges: List[DepEdge] = []
    seen_edge = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            raise GraphValidationError("edge 必须是对象")
        eid = _require_id(item.get("id"), "edge")
        if eid in seen_edge:
            raise GraphValidationError(f"edge id 重复: {eid}")
        seen_edge.add(eid)
        src, target = item.get("src"), item.get("target")
        if src not in comp_ids:
            raise GraphValidationError(f"edge {eid} 的 src 未知: {src}")
        if target not in comp_ids:
            raise GraphValidationError(f"edge {eid} 的 target 未知: {target}")
        etype = item.get("type")
        if etype not in EDGE_TYPES:
            raise GraphValidationError(
                f"edge {eid} 的 type 非法: {etype}（允许 {EDGE_TYPES}）"
            )
        iface = item.get("interface_ids", ()) or ()
        if isinstance(iface, str):
            iface = (iface,)
        if not isinstance(iface, (list, tuple)):
            raise GraphValidationError(f"edge {eid} 的 interface_ids 必须是数组")
        edges.append(
            DepEdge(
                eid,
                src,
                target,
                etype,
                str(item.get("note", "")),
                tuple(iface),
            )
        )

    edge_ids = {e.id for e in edges}
    interfaces: List[InterfaceReg] = []
    seen_iface = set()
    for item in raw_interfaces:
        if not isinstance(item, dict):
            raise GraphValidationError("interface 必须是对象")
        iid = _require_id(item.get("id"), "interface")
        if iid in seen_iface:
            raise GraphValidationError(f"interface id 重复: {iid}")
        seen_iface.add(iid)
        comp = item.get("component")
        consumer = item.get("consumer")
        provider = item.get("provider")
        replaces = item.get("replaces_edge")
        if comp not in comp_ids:
            raise GraphValidationError(f"interface {iid} 的 component 未知: {comp}")
        if consumer not in comp_ids:
            raise GraphValidationError(f"interface {iid} 的 consumer 未知: {consumer}")
        if provider is not None and provider not in comp_ids:
            raise GraphValidationError(f"interface {iid} 的 provider 未知: {provider}")
        if replaces not in edge_ids:
            raise GraphValidationError(
                f"interface {iid} 的 replaces_edge 未知: {replaces}"
            )
        added_type = item.get("added_type", "runtime")
        if added_type not in EDGE_TYPES:
            raise GraphValidationError(f"interface {iid} 的 added_type 非法")
        interfaces.append(
            InterfaceReg(
                iid,
                comp,
                consumer,
                provider,
                replaces,
                added_type,
                str(item.get("note", "")),
            )
        )

    # 交叉校验：边上声明的 interface_ids 必须存在且确实替代该边
    iface_by_id = {i.id: i for i in interfaces}
    for edge in edges:
        for iid in edge.interface_ids:
            reg = iface_by_id.get(iid)
            if reg is None:
                raise GraphValidationError(
                    f"edge {edge.id} 引用了未登记的接口: {iid}"
                )
            if reg.replaces_edge != edge.id:
                raise GraphValidationError(
                    f"接口 {iid} 登记替代 {reg.replaces_edge}，"
                    f"不能用于边 {edge.id}"
                )
            if reg.consumer != edge.src:
                raise GraphValidationError(
                    f"接口 {iid} 的 consumer 与边 {edge.id} 的 src 不一致"
                )

    graph = Graph(tuple(components), tuple(edges), tuple(interfaces))
    return graph


def graph_to_dict(graph: Graph) -> Dict[str, Any]:
    return {
        "components": [
            {
                "id": c.id,
                "name": c.name,
                "owner": c.owner,
                "weight": c.weight,
                "kind": c.kind,
            }
            for c in graph.components
        ],
        "edges": [
            {
                "id": e.id,
                "src": e.src,
                "target": e.target,
                "type": e.type,
                "note": e.note,
                "interface_ids": list(e.interface_ids),
            }
            for e in graph.edges
        ],
        "interfaces": [
            {
                "id": i.id,
                "component": i.component,
                "consumer": i.consumer,
                "provider": i.provider,
                "replaces_edge": i.replaces_edge,
                "added_type": i.added_type,
                "note": i.note,
            }
            for i in graph.interfaces
        ],
    }


def canonical_json(graph: Graph) -> str:
    return json.dumps(
        graph_to_dict(graph), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def normalize_types(types: Any) -> Tuple[str, ...]:
    if types is None:
        return DEFAULT_TYPES
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    out: List[str] = []
    for t in types:
        if t not in EDGE_TYPES:
            raise GraphValidationError(f"未知依赖类型: {t}")
        if t not in out:
            out.append(t)
    # 按规范顺序输出，保证稳定
    return tuple(t for t in EDGE_TYPES if t in out)
