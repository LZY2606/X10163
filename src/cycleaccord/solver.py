"""候选变更集求解。

思路：
  * 每个“逻辑动作”（反转 / 降级 / 接口化）都映射为原子边操作；
  * 在“当前视图中仍参与环的边集合”上做 Dijkstra 搜索，
    每个状态应用一个动作后重新检测环；无环即得到一个候选；
  * 成本为非负整数（动作基础成本 + 关键组件权重 + 跨负责人数量），
    Dijkstra 按 (cost, 动作序列稳定键) 出队，保证同成本结果稳定；
  * 最终只保留“极小”候选（去掉任一动作就重新有环），即每条候选都足够。
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import hashlib
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from .models import (
    ACTION_BASE_COST,
    EDGE_TYPES,
    CROSS_OWNER_COST,
    Candidate,
    CandidateAction,
    DOWNGRADE_LADDER,
    EdgeOp,
    Graph,
    action_to_ops,
)
from .paths import affected_paths
from .scc import all_cycles, apply_ops, find_one_cycle, is_acyclic, view_edges

# 搜索规模上限（图过大时仍然给出结果；超过则标记 truncated）
MAX_EXPANSIONS = 20000
MAX_CANDIDATES = 6


@dataclass(frozen=True)
class SearchState:
    removed: FrozenSet[str]  # 从当前视图消失/被替换的原始边 id
    added: FrozenSet[str]    # 新增边（src|target|type）
    actions: Tuple[CandidateAction, ...]


def _added_key(op: EdgeOp) -> str:
    return f"{op.src}>{op.target}|{op.type}"


def _enumerate_actions(graph: Graph, types: Sequence[str]) -> List[CandidateAction]:
    """列出当前分析视图下所有合法动作（稳定顺序）。"""
    wanted = set(types)
    actions: List[CandidateAction] = []
    for edge in sorted(graph.edges, key=lambda e: e.id):
        if edge.type not in wanted:
            continue
        # 反转（自环反转无意义，排除）
        if edge.src != edge.target:
            actions.append(CandidateAction("reverse", edge.id))
        # 降级：只允许降到“当前视图包含”的较弱类型；
        # 降到被过滤类型等价于在本视图移除，也允许（必须沿登记阶梯）。
        # 只有降到“当前视图不包含”的较弱类型，才会让边离开视图、
        # 从而可能切断环（边仍保留在原图，仅改变类型）。
        for weaker in DOWNGRADE_LADDER.get(edge.type, ()):
            if weaker not in wanted:
                actions.append(CandidateAction("downgrade", edge.id, weaker))
        # 接口化：只能使用该边已登记的接口
        for iid in edge.interface_ids:
            actions.append(CandidateAction("interface", edge.id, interface_id=iid))
    actions.sort(key=lambda a: (a.edge_id, a.kind, a.new_type, a.interface_id))
    return actions


def _state_graph(graph: Graph, state: SearchState) -> Graph:
    """根据状态构造工作图：移除被处理的原始边，加入新增边。"""
    from dataclasses import replace

    kept = tuple(e for e in graph.edges if e.id not in state.removed)
    added_edges = []
    for spec in sorted(state.added):
        head, etype = spec.rsplit("|", 1)
        src, target = head.split(">", 1)
        eid = "gen:" + hashlib.sha1(spec.encode()).hexdigest()[:12]
        from .models import DepEdge

        added_edges.append(DepEdge(eid, src, target, etype, "状态边", ()))
    return replace(graph, edges=kept + tuple(added_edges))


def _active_edges(graph: Graph, types: Sequence[str]) -> Set[str]:
    return {e.id for e in view_edges(graph, types)}


def _action_cost(graph: Graph, action: CandidateAction) -> int:
    edge = graph.edge(action.edge_id)
    base = ACTION_BASE_COST[action.kind]
    touched = {edge.src, edge.target}
    if action.kind == "interface":
        reg = graph.interface(action.interface_id)
        touched.add(reg.component)
        if reg.provider:
            touched.add(reg.provider)
    weight_sum = sum(graph.component(c).weight for c in touched)
    owners = {graph.component(c).owner for c in touched}
    cross = max(0, len(owners) - 1) * CROSS_OWNER_COST
    return base + weight_sum * 2 + cross


def _actions_cost(graph: Graph, actions: Sequence[CandidateAction]) -> int:
    """一组动作的总成本（按被触及组件与负责人的并集计算跨负责人）。"""
    if not actions:
        return 0
    touched: Set[str] = set()
    base = 0
    for action in actions:
        base += ACTION_BASE_COST[action.kind]
        edge = graph.edge(action.edge_id)
        touched.update({edge.src, edge.target})
        if action.kind == "interface":
            reg = graph.interface(action.interface_id)
            touched.add(reg.component)
            if reg.provider:
                touched.add(reg.provider)
    weight_sum = sum(graph.component(c).weight for c in touched)
    owners = {graph.component(c).owner for c in touched}
    cross = max(0, len(owners) - 1) * CROSS_OWNER_COST
    return base + weight_sum * 2 + cross


def _owners_to_confirm(
    graph: Graph, actions: Sequence[CandidateAction]
) -> Tuple[str, ...]:
    touched: Set[str] = set()
    for action in actions:
        edge = graph.edge(action.edge_id)
        touched.update({edge.src, edge.target})
        if action.kind == "interface":
            reg = graph.interface(action.interface_id)
            touched.add(reg.component)
            if reg.provider:
                touched.add(reg.provider)
    return tuple(sorted({graph.component(c).owner for c in touched}))


def _is_minimal(
    graph: Graph,
    types: Sequence[str],
    actions: Sequence[CandidateAction],
) -> bool:
    """极小性：应用全部动作无环，去掉任意一个动作就有环。"""
    if not actions:
        return False
    full_ops = [op for a in actions for op in action_to_ops(a, graph)]
    if not is_acyclic(apply_ops(graph, full_ops), types):
        return False
    for i in range(len(actions)):
        rest = actions[:i] + actions[i + 1 :]
        ops = [op for a in rest for op in action_to_ops(a, graph)]
        if is_acyclic(apply_ops(graph, ops), types):
            return False
    return True


def _broken_cycles(
    graph: Graph,
    types: Sequence[str],
    removed: Set[str],
) -> Tuple[List[List[str]], List[List[str]]]:
    """返回（被切断的环, 全部环）。被切断 = 原环中至少一条边被移除。"""
    cycles = all_cycles(graph, types)
    broken = [c for c in cycles if any(eid in removed for eid in c)]
    return broken, cycles


def solve(
    graph: Graph,
    types: Sequence[str],
    analysis_id: str,
    max_candidates: int = MAX_CANDIDATES,
) -> Tuple[List[Candidate], Dict[str, object]]:
    """生成若干打破当前视图全部环的候选。

    返回（候选列表, 诊断信息）。无环时返回空列表；无合法拆分时诊断中给出
    卡住的环。
    """
    diag: Dict[str, object] = {"truncated": False}
    all_actions = _enumerate_actions(graph, types)

    if is_acyclic(graph, types):
        return [], diag

    # 初始环：若没有任何动作能碰到某条环，则该环无法被合法拆分
    initial_cycles = all_cycles(graph, types)
    actionable_edges = {a.edge_id for a in all_actions}
    stuck = [c for c in initial_cycles if not any(e in actionable_edges for e in c)]
    if stuck:
        diag["stuck_cycles"] = stuck
        diag["reason"] = "存在无法用任何已允许变更触及的环（无合法拆分）"
        return [], diag

    init = SearchState(frozenset(), frozenset(), ())
    # 堆项：(cost, 稳定序列键, 计数器, 状态)
    counter = 0
    heap: List = []
    heapq.heappush(heap, (0, (), counter, init))
    best_dist: Dict[Tuple, int] = {(init.removed, init.added): 0}
    found: List[Tuple[int, Tuple, SearchState]] = []
    expansions = 0

    def state_key(state: SearchState) -> Tuple:
        return state.removed, state.added

    while heap and expansions < MAX_EXPANSIONS:
        cost, seq_key, _, state = heapq.heappop(heap)
        if best_dist.get(state_key(state)) != cost:
            continue
        work = _state_graph(graph, state)
        if is_acyclic(work, types):
            found.append((cost, seq_key, state))
            # 必须继续搜索：先按成本出队的极小解才是我们要保留的，
            # 但要凑足 max_candidates 个互不相同的极小集合。
            # 上界保护，避免在大图上枚举过多可行解。
            if len(found) >= max_candidates * 12:
                break
            continue
        expansions += 1
        # 只考虑仍能触及当前某个环的动作（剪枝），且不重复处理同一原始边
        cycle = find_one_cycle(work, types)
        if cycle is None:
            found.append((cost, seq_key, state))
            continue
        cycle_edges = set(cycle)
        used_original = {a.edge_id for a in state.actions}
        for action in all_actions:
            if action.edge_id in used_original:
                continue
            # 工作图中原始边可能已被移除：跳过已不存在的边
            live_original = any(
                e.id == action.edge_id for e in view_edges(work, types)
            )
            if not live_original:
                continue
            if action.edge_id not in cycle_edges:
                # 只有能切断当前环的动作才扩展（Dijkstra 仍保证最优：
                # 不在任何环上的边永远不必处理）
                if action.edge_id not in {
                    eid for c in all_cycles(work, types) for eid in c
                }:
                    continue
            ops = action_to_ops(action, graph)
            new_removed = set(state.removed)
            new_added = set(state.added)
            new_removed.add(action.edge_id)
            for op in ops:
                if op.kind == "add":
                    new_added.add(_added_key(op))
            new_actions = state.actions + (action,)
            new_state = SearchState(
                frozenset(new_removed), frozenset(new_added), new_actions
            )
            new_cost = _actions_cost(graph, new_actions)
            key = state_key(new_state)
            if new_cost < best_dist.get(key, 10 ** 18):
                best_dist[key] = new_cost
                counter += 1
                skey = tuple(
                    (a.kind, a.edge_id, a.new_type, a.interface_id)
                    for a in new_actions
                )
                heapq.heappush(heap, (new_cost, skey, counter, new_state))

    if expansions >= MAX_EXPANSIONS:
        diag["truncated"] = True

    # 极小化并稳定排序
    # 对每个“非降级动作签名”（反转/接口的边集合），降级目标只保留
    # 弱化程度最小的一种，避免近似变体占满候选列表。
    def downgrade_strength(actions) -> int:
        # new_type 在 EDGE_TYPES 中越强（下标越大）越优先
        return sum(
            EDGE_TYPES.index(a.new_type)
            for a in actions if a.kind == "downgrade"
        )

    grouped: Dict[Tuple, Tuple[int, Tuple, Tuple[CandidateAction, ...]]] = {}
    found.sort(key=lambda item: (item[0], item[1]))
    for cost, seq_key, state in found:
        actions = state.actions
        if not _is_minimal(graph, types, actions):
            continue
        signature = tuple(sorted(
            (a.kind, a.edge_id, a.interface_id)
            for a in actions if a.kind != "downgrade"
        )) + (("__downgraded_edges__",) + tuple(sorted(
            a.edge_id for a in actions if a.kind == "downgrade"
        )),)
        incumbent = grouped.get(signature)
        if incumbent is None or downgrade_strength(actions) > downgrade_strength(
            incumbent[2]
        ):
            grouped[signature] = (cost, seq_key, actions)

    minimal = sorted(
        grouped.values(), key=lambda t: (t[0], _actions_sort_key(t[2]))
    )[:max_candidates]

    if not minimal and found:
        diag["reason"] = "找到可行变更但均非极小集（理论上不应发生）"
    if not minimal and not found:
        # 动作互相冲突（如反转/接口新增边又形成环）导致搜索未找到解
        remaining = find_one_cycle(graph, types)
        diag["stuck_cycles"] = [remaining] if remaining else []
        diag["reason"] = "所有合法变更组合后仍存在环（无合法拆分）"

    candidates: List[Candidate] = []
    for rank, (cost, _, actions) in enumerate(
        sorted(minimal, key=lambda t: (t[0], _actions_sort_key(t[2])))
    ):
        ops = tuple(op for a in actions for op in action_to_ops(a, graph))
        removed = {a.edge_id for a in actions}
        broken, cycles = _broken_cycles(graph, types, removed)
        apaths = affected_paths(graph, types, removed)
        owners = _owners_to_confirm(graph, actions)
        rationale = _rationale(
            graph, types, actions, broken, cycles, owners, cost
        )
        cid = "cand-" + hashlib.sha1(
            (analysis_id + "|" +
             ";".join(sorted(a.key()[0] + a.key()[1] + a.key()[2] +
                             a.key()[3] for a in actions))).encode()
        ).hexdigest()[:10]
        candidates.append(
            Candidate(
                id=cid,
                analysis_id=analysis_id,
                actions=tuple(actions),
                cost=_actions_cost(graph, actions),
                ops=ops,
                broken_cycles=tuple(
                    "→".join(_cycle_nodes(graph, c)) for c in broken
                ),
                affected_paths=tuple(tuple(p) for p in apaths),
                owners_to_confirm=owners,
                rationale=rationale,
                rank=rank,
            )
        )
    return candidates, diag


def _actions_sort_key(actions: Sequence[CandidateAction]) -> Tuple:
    return tuple(sorted(
        (a.kind, a.edge_id, a.new_type, a.interface_id) for a in actions
    ))


def _cycle_nodes(graph: Graph, edge_ids: Sequence[str]) -> List[str]:
    by_id = {e.id: e for e in graph.edges}
    nodes: List[str] = []
    for eid in edge_ids:
        e = by_id.get(eid)
        if not e:
            continue
        if not nodes:
            nodes.append(e.src)
        nodes.append(e.target)
    return nodes


def _rationale(
    graph: Graph,
    types: Sequence[str],
    actions: Sequence[CandidateAction],
    broken: Sequence[Sequence[str]],
    cycles: Sequence[Sequence[str]],
    owners: Sequence[str],
    cost: int,
) -> str:
    parts = [
        f"应用 {len(actions)} 条变更后在视图[{','.join(types)}] 中无环",
    ]
    parts.append(f"切断了全部 {len(cycles)} 个基础环中的 {len(broken)} 个")
    parts.append("该集合极小：撤销任何一条变更都会重新成环")
    parts.append(f"需 {', '.join(owners)} 确认，综合成本 {cost}")
    return "；".join(parts) + "。"


def verify_ops(graph: Graph, types: Sequence[str], ops: Sequence[EdgeOp]) -> bool:
    """机器校验：把原子边操作应用到图副本后，视图是否无环。"""
    new_graph = apply_ops(graph, ops)
    return is_acyclic(new_graph, types)
