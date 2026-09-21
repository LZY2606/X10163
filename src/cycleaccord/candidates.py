"""候选拆环变更集生成、成本、稳定性与解释。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Dict, List, Sequence, Tuple

from .algorithms import (
    apply_operations,
    cyclic_sccs,
    cycles_to_edge_ids,
    elementary_cycles,
    effective_edges,
    has_cycle,
    verify_candidate,
)
from .model import Edge, EdgeOp, Graph, TYPE_RANK

BASE_COST = {"reverse": 10, "downgrade": 4, "interface": 12}
CRITICAL_BONUS = 5
CROSS_OWNER_PENALTY = 8
MAX_FAS_SIZE = 6
MAX_COMBOS_PER_K = 3000
MAX_LOCAL_PLANS = 12
MAX_GLOBAL_CANDIDATES = 40


@dataclass(frozen=True)
class LocalPlan:
    scc_index: int
    ops: Tuple[EdgeOp, ...]
    removed_edge_ids: Tuple[str, ...]
    cost: int
    sort_key: tuple


@dataclass
class Candidate:
    id: str
    ops: List[EdgeOp]
    cost: int
    affected_sccs: List[List[str]]
    owners: List[str]
    cut_edge_ids: List[str]
    broken_cycles: List[List[str]]
    reachable_edge_ids: List[str]
    added_edge_ids: List[str]
    rationale: List[str]
    verified: bool


@dataclass
class CandidateSet:
    include_types: Tuple[str, ...]
    sccs: List[List[str]]
    candidates: List[Candidate] = field(default_factory=list)
    infeasible_sccs: List[List[str]] = field(default_factory=list)
    complete: bool = True


# ---------------------------------------------------------------------------
# 单条边的可行操作
# ---------------------------------------------------------------------------

def actions_for_edge(edge: Edge, graph: Graph, include_types: Sequence[str]) -> List[EdgeOp]:
    actions: List[EdgeOp] = []

    if not edge.locked:
        # 1) 反转（需双方负责人确认）。自环反转后仍是自环，无法拆环，不提供。
        if edge.src != edge.dst:
            actions.append(EdgeOp(
                kind="reverse",
                edge_id=edge.id,
                edge_src=edge.src,
                edge_dst=edge.dst,
                edge_type=edge.dep_type,
                edge_owner=edge.owner,
                note="反转 %s（%s -> %s），方向由被依赖方指回" % (edge.id, edge.src, edge.dst),
            ))

        # 2) 降级：只能降到登记允许、更弱、且被当前视图排除的类型
        for target in edge.downgrade:
            if (TYPE_RANK[target] < TYPE_RANK[edge.dep_type]
                    and target not in include_types):
                actions.append(EdgeOp(
                    kind="downgrade",
                    edge_id=edge.id,
                    edge_src=edge.src,
                    edge_dst=edge.dst,
                    edge_type=edge.dep_type,
                    edge_owner=edge.owner,
                    target_type=target,
                    note="把 %s 从 %s 降为 %s（当前视图排除该类型）"
                         % (edge.id, edge.dep_type, target),
                ))

    # 3) 已登记接口替换：不能凭空发明，只接受显式登记项（锁定边也允许）
    for iface in graph.interfaces.values():
        if iface.replaces_edge == edge.id:
            actions.append(EdgeOp(
                kind="interface",
                edge_id=edge.id,
                edge_src=edge.src,
                edge_dst=edge.dst,
                edge_type=edge.dep_type,
                edge_owner=edge.owner,
                interface_id=iface.id,
                new_src=iface.new_src,
                new_dst=iface.new_dst,
                new_type=iface.new_type,
                note=iface.note or "用登记接口 %s 替换 %s（%s -> %s, %s）"
                     % (iface.id, edge.id, iface.new_src, iface.new_dst, iface.new_type),
            ))

    return actions


def confirm_owners(op: EdgeOp, graph: Graph) -> List[str]:
    owners = set()
    original = graph.edges.get(op.edge_id)
    if original is not None and original.owner:
        owners.add(original.owner)
    for node_id in (op.edge_src, op.edge_dst):
        node = graph.nodes.get(node_id)
        if node and node.owner:
            owners.add(node.owner)
    if op.kind == "interface":
        iface = graph.interfaces.get(op.interface_id)
        if iface and iface.owner:
            owners.add(iface.owner)
    return sorted(owners)


def op_cost(op: EdgeOp, graph: Graph) -> int:
    original = graph.edges.get(op.edge_id)
    base = BASE_COST[op.kind]
    endpoint_weight = 0
    critical_count = 0
    for node_id in (op.edge_src, op.edge_dst):
        node = graph.nodes.get(node_id)
        if node:
            endpoint_weight += node.weight
            if node.critical:
                critical_count += 1
    edge_weight = original.weight if original else 1
    return base + edge_weight * 2 + endpoint_weight + CRITICAL_BONUS * critical_count


def plan_cost(ops: Sequence[EdgeOp], graph: Graph) -> int:
    base_sum = sum(op_cost(op, graph) for op in ops)
    owners = set()
    for op in ops:
        owners.update(confirm_owners(op, graph))
    cross = max(0, len(owners) - 1)
    return base_sum + CROSS_OWNER_PENALTY * cross


def op_sort_key(op: EdgeOp) -> tuple:
    kind_rank = {"reverse": 0, "downgrade": 1, "interface": 2}
    return (op.edge_id, kind_rank[op.kind], op.target_type, op.interface_id)


def ops_sort_key(ops: Sequence[EdgeOp]) -> tuple:
    return tuple(sorted(op.signature() for op in ops))


# ---------------------------------------------------------------------------
# 单个 SCC 的最小反馈边集
# ---------------------------------------------------------------------------

def _edges_inside(comp: Sequence[str], edges: Sequence[Edge]) -> List[Edge]:
    nodes = set(comp)
    return [e for e in edges if e.src in nodes and e.dst in nodes]


def _removal_makes_acyclic(comp: Sequence[str], remove_ids: Sequence[str],
                           edges: Sequence[Edge]) -> bool:
    remove = frozenset(remove_ids)
    kept = [e for e in edges if e.id not in remove]
    return not has_cycle(list(comp), kept)


def minimal_feedback_sets(comp: Sequence[str], comp_edges: Sequence[Edge],
                          actionable: Dict[str, List[EdgeOp]]) -> List[Tuple[str, ...]]:
    actionable_ids = sorted(actionable.keys())
    # 不可行动边全部移除后仍有环 => 该 SCC 无法合法拆分
    if not _removal_makes_acyclic(comp, actionable_ids, comp_edges):
        return []
    # 收集最小规模集合；若数量很少，再补充次小规模集合，给协商留出选择。
    upper = min(len(actionable_ids), MAX_FAS_SIZE)

    def bucket_at(k: int) -> List[Tuple[str, ...]]:
        bucket = []
        for n, chosen in enumerate(combinations(actionable_ids, k), start=1):
            if n > MAX_COMBOS_PER_K:
                break
            if _removal_makes_acyclic(comp, chosen, comp_edges):
                bucket.append(chosen)
        return bucket

    for k in range(1, upper + 1):
        minimum = bucket_at(k)
        if minimum:
            if len(minimum) >= 3 or k == upper:
                return minimum
            second = bucket_at(k + 1)
            return minimum + second
    return []


def local_plans_for_scc(scc_index: int, comp: Sequence[str], graph: Graph,
                        include_types: Sequence[str]) -> Tuple[List[LocalPlan], bool]:
    """返回 (局部方案, 是否无合法拆分)。"""
    edges = _edges_inside(comp, effective_edges(graph, include_types))
    actionable: Dict[str, List[EdgeOp]] = {}
    for edge in edges:
        acts = actions_for_edge(edge, graph, include_types)
        if acts:
            actionable[edge.id] = acts

    if not _removal_makes_acyclic(comp, list(actionable.keys()), edges):
        return [], True

    fas_list = minimal_feedback_sets(comp, edges, actionable)
    if not fas_list:
        return [], True

    plans: List[LocalPlan] = []
    for removed in fas_list:
        choices = [sorted(actionable[eid], key=op_sort_key) for eid in removed]
        for chosen_ops in product(*choices):
            ops = tuple(chosen_ops)
            cost = plan_cost(ops, graph)
            plans.append(LocalPlan(
                scc_index=scc_index,
                ops=ops,
                removed_edge_ids=removed,
                cost=cost,
                sort_key=(cost, len(ops), ops_sort_key(ops)),
            ))
    plans.sort(key=lambda p: p.sort_key)
    return plans, False


# ---------------------------------------------------------------------------
# 解释信息
# ---------------------------------------------------------------------------

def _candidate_id(ops: Sequence[EdgeOp]) -> str:
    digest_src = "|".join(sorted(op.signature() for op in ops))
    return "C" + hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:8]


def _broken_cycles(sccs: Sequence[Sequence[str]], cut: Sequence[str],
                   graph: Graph, include_types: Sequence[str]) -> List[List[str]]:
    cut_set = frozenset(cut)
    broken: List[List[str]] = []
    for comp in sccs:
        edges = _edges_inside(comp, effective_edges(graph, include_types))
        node_cycles = elementary_cycles(comp, edges)
        for edge_cycle in cycles_to_edge_ids(node_cycles, graph):
            if any(eid in cut_set for eid in edge_cycle):
                broken.append(edge_cycle)
    broken.sort(key=lambda c: (len(c), c))
    return broken


def _reachable_after(graph: Graph, ops: Sequence[EdgeOp],
                     include_types: Sequence[str]) -> Tuple[List[str], List[str]]:
    new_edges, added = apply_operations(graph, ops, include_types)
    wanted = frozenset(include_types)
    kept = [e for e in new_edges if e.dep_type in wanted]
    return sorted(e.id for e in kept), sorted(e.id for e in added)


def _build_rationale(ops: Sequence[EdgeOp], broken: Sequence[Sequence[str]],
                     owners: Sequence[str], verified: bool) -> List[str]:
    lines = []
    for op in sorted(ops, key=op_sort_key):
        lines.append(op.note)
    lines.append("该变更集切断 %d 条枚举到的有向环，每个受影响强连通分量都至少移除一条回边。"
                 % len(broken))
    lines.append("机器校验：把 %d 条边操作应用到图副本后，当前视图下%s。"
                 % (len(ops), "无环" if verified else "仍有环（不满足）"))
    lines.append("需要确认的负责人：%s。" % ("、".join(owners) if owners else "无"))
    return lines


# ---------------------------------------------------------------------------
# 全局候选生成
# ---------------------------------------------------------------------------

def generate_candidates(graph: Graph, include_types: Sequence[str]) -> CandidateSet:
    include_types = tuple(include_types)
    sccs = cyclic_sccs(graph, include_types)
    result = CandidateSet(include_types=include_types, sccs=sccs)
    if not sccs:
        return result

    per_scc: List[List[LocalPlan]] = []
    for i, comp in enumerate(sccs):
        plans, infeasible = local_plans_for_scc(i, comp, graph, include_types)
        if infeasible:
            result.infeasible_sccs.append(comp)
        per_scc.append(plans)

    if result.infeasible_sccs:
        # 存在无法合法拆分的 SCC：不存在"打破全部环"的完整解，
        # 但仍为可行动的 SCC 生成部分候选，供团队先解决可解决的部分。
        result.complete = False

    feasible_groups = [(i, plans) for i, plans in enumerate(per_scc) if plans]
    if not feasible_groups:
        return result
    cheap_plans = [plans[:MAX_LOCAL_PLANS] for _, plans in feasible_groups]
    seen_ops: set = set()
    raw: List[Tuple[tuple, Tuple[LocalPlan, ...], List[EdgeOp]]] = []
    for combo in product(*cheap_plans):
        all_ops: List[EdgeOp] = []
        for plan in combo:
            all_ops.extend(plan.ops)
        key = ops_sort_key(all_ops)
        if key in seen_ops:
            continue
        seen_ops.add(key)
        all_ops = sorted(all_ops, key=op_sort_key)
        raw.append((key, combo, all_ops))

    # 成本（含跨负责人惩罚）在整个变更集层面统一计算，保证口径一致。
    decorated = [(plan_cost(ops, graph), key, combo, ops)
                 for key, combo, ops in raw]
    decorated.sort(key=lambda item: (item[0], len(item[2]),
                                     sum(len(p.ops) for p in item[2]), item[1]))

    scc_index_to_comp = {i: comp for i, comp in enumerate(sccs)}
    for cost, _, combo, ops in decorated:
        if len(result.candidates) >= MAX_GLOBAL_CANDIDATES:
            break
        affected: List[List[str]] = []
        cut: List[str] = []
        for plan in combo:
            affected.append(scc_index_to_comp[plan.scc_index])
            cut.extend(plan.removed_edge_ids)
        cut = sorted(set(cut))
        verified = verify_candidate(graph, ops, include_types)
        if not verified:
            # 接口/反转可能在 SCC 之外重新成环：不满足"应用后无环"即丢弃
            continue
        owners = sorted({o for op in ops for o in confirm_owners(op, graph)})
        broken = _broken_cycles(affected, cut, graph, include_types)
        reachable, added_ids = _reachable_after(graph, ops, include_types)
        rationale = _build_rationale(ops, broken, owners, verified)
        result.candidates.append(Candidate(
            id=_candidate_id(ops),
            ops=ops,
            cost=cost,
            affected_sccs=affected,
            owners=owners,
            cut_edge_ids=cut,
            broken_cycles=broken,
            reachable_edge_ids=reachable,
            added_edge_ids=added_ids,
            rationale=rationale,
            verified=verified,
        ))

    return result


def candidate_to_dict(cand: Candidate) -> dict:
    return {
        "id": cand.id,
        "cost": cand.cost,
        "ops": [op.to_dict() for op in cand.ops],
        "affected_sccs": cand.affected_sccs,
        "owners": cand.owners,
        "cut_edge_ids": cand.cut_edge_ids,
        "broken_cycles": cand.broken_cycles,
        "reachable_edge_ids": cand.reachable_edge_ids,
        "added_edge_ids": cand.added_edge_ids,
        "rationale": cand.rationale,
        "verified": cand.verified,
    }
