"""Candidate change sets that break every dependency cycle in an analysis view.

Candidates combine three kinds of machine-verifiable edge operations:
  * reverse an existing edge;
  * weaken a hard edge to an allowed weaker type;
  * introduce a previously *registered* interface (never invented).

Generation is exhaustive for small action spaces and greedy-bounded for large
ones; ranking is deterministic so equal-cost results have a stable order.
"""
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .model import (
    Graph,
    WEAKER_THAN_HARD,
)
from .operations import (
    EdgeOperation,
    INTRODUCE_INTERFACE,
    REVERSE,
    WEAKEN,
    verify,
)
from .scc import (
    cyclic_components,
    enumerate_simple_cycles,
    reachable_nodes,
    shortest_path_edges,
)

# Base costs of each change kind.
BASE_COST = {
    REVERSE: 10,
    WEAKEN: 6,
    INTRODUCE_INTERFACE: 8,
}
PER_REPLACED_EDGE_COST = 2
COMBO_CAP = 20000          # max action-subsets inspected per SCC / globally
LOCAL_SOLUTION_CAP = 10     # per-SCC solutions kept for global combination
GLOBAL_CANDIDATE_CAP = 12
MAX_ACTION_SPACE = 30      # above this, exact enumeration is replaced by greedy


@dataclass
class Action:
    operation: EdgeOperation
    edge_id: str
    kind: str
    nodes: Tuple[str, ...]
    base_cost: float
    summary: str
    # SCC member nodes this action actually touches within one component.
    scc_edges: Tuple[str, ...] = ()

    def signature(self) -> str:
        return self.operation.signature()


@dataclass
class Candidate:
    key: str
    operations: List[EdgeOperation]
    cost: float
    cost_detail: Dict[str, float]
    actions: List[Action] = field(default_factory=list)
    # per-SCC explanation data, filled in by the analyzer
    explanations: List[dict] = field(default_factory=list)
    affected_sccs: List[str] = field(default_factory=list)
    owners: List[str] = field(default_factory=list)
    breaks_all_cycles: bool = False


@dataclass
class SCCInfo:
    index: int
    members: List[str]
    self_loop: bool
    cycles: List[List[str]]  # edge-id lists, capped for display
    splittable: bool
    unsplittable_reason: str = ""


@dataclass
class AnalysisResult:
    include_types: List[str]
    sccs: List[SCCInfo]
    candidates: List[Candidate]
    cyclic: bool


def candidate_key(operations: Sequence[EdgeOperation]) -> str:
    canonical = "|".join(op.signature() for op in sorted(operations, key=lambda o: o.signature()))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Action enumeration within one SCC
# ---------------------------------------------------------------------------

def _local_acyclic(
    graph: Graph,
    members: Set[str],
    operations: List[EdgeOperation],
    include_types: List[str],
) -> bool:
    """Fast local check: do these operations remove every cycle inside one SCC?

    We simulate each operation on the edges that belong to the induced subgraph
    of the SCC and run Kahn's algorithm. Interface edges terminate on a fresh
    sink node, so they cannot sustain a cycle; only their removed edges matter.
    """
    # Build induced directed graph using surviving, in-view edges.
    remaining: Dict[str, Set[str]] = {m: set() for m in members}
    scc_edges: Set[str] = set()
    for edge in graph.edges_for_view(include_types):
        if edge.source in members and edge.target in members:
            scc_edges.add(edge.id)

    removed: Set[str] = set()
    reversed_edges: Set[str] = set()
    weakened_out: Set[str] = set()
    for operation in operations:
        if operation.op == INTRODUCE_INTERFACE:
            spec = graph.interfaces[operation.interface_id]
            for replaced in spec.replaces:
                if replaced in scc_edges:
                    removed.add(replaced)
        elif operation.edge_id in scc_edges:
            if operation.op == REVERSE:
                reversed_edges.add(operation.edge_id)
            elif operation.op == WEAKEN:
                weakened_out.add(operation.edge_id)

    for edge in graph.edges_for_view(include_types):
        if edge.id not in scc_edges or edge.id in removed or edge.id in weakened_out:
            continue
        if edge.id in reversed_edges:
            remaining[edge.target].add(edge.source)
        else:
            remaining[edge.source].add(edge.target)

    indegree = {m: 0 for m in members}
    for src, targets in remaining.items():
        for dst in targets:
            indegree[dst] += 1
    queue = [m for m, deg in indegree.items() if deg == 0]
    processed = 0
    while queue:
        node = queue.pop()
        processed += 1
        for nxt in remaining[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return processed == len(members)


def _scc_actions(graph: Graph, info: SCCInfo, include_types: List[str]) -> List[Action]:
    members = set(info.members)
    in_scc: List[str] = []
    for edge in graph.edges_for_view(include_types):
        if edge.source in members and edge.target in members:
            in_scc.append(edge.id)
    in_scc_set = set(in_scc)

    actions: List[Action] = []
    for edge_id in sorted(in_scc):
        edge = graph.edges[edge_id]
        is_loop = edge.source == edge.target
        # A locked edge cannot be reversed or weakened directly; its only
        # legal escape is replacement via a pre-registered interface.
        if not is_loop and not edge.locked:
            operations_action = Action(
                operation=EdgeOperation(op=REVERSE, edge_id=edge.id),
                edge_id=edge.id,
                kind=REVERSE,
                nodes=tuple(sorted((edge.source, edge.target))),
                base_cost=BASE_COST[REVERSE],
                summary="reverse edge %s (%s -> %s)" % (edge.id, edge.source, edge.target),
                scc_edges=(edge.id,),
            )
            actions.append(operations_action)
        if edge.type == "hard" and not edge.locked:
            # A weakening only breaks a cycle in this view when the edge leaves
            # the projection: target type must be an allowed weaker type that
            # is NOT currently included in the view.
            candidate_types = sorted(set(WEAKER_THAN_HARD) - set(include_types))
            for new_type in candidate_types:
                actions.append(
                    Action(
                        operation=EdgeOperation(op=WEAKEN, edge_id=edge.id, new_type=new_type),
                        edge_id=edge.id,
                        kind=WEAKEN,
                        nodes=tuple(sorted((edge.source, edge.target))),
                        base_cost=BASE_COST[WEAKEN],
                        summary="weaken edge %s hard -> %s" % (edge.id, new_type),
                        scc_edges=(edge.id,),
                    )
                )

    for spec_id in sorted(graph.interfaces):
        spec = graph.interfaces[spec_id]
        touched = tuple(sorted(e for e in spec.replaces if e in in_scc_set))
        if not touched:
            continue
        nodes: Set[str] = set([spec.provider])
        nodes.update(spec.consumers)
        actions.append(
            Action(
                operation=EdgeOperation(op=INTRODUCE_INTERFACE, interface_id=spec.id),
                edge_id="",
                kind=INTRODUCE_INTERFACE,
                nodes=tuple(sorted(nodes)),
                base_cost=BASE_COST[INTRODUCE_INTERFACE]
                + PER_REPLACED_EDGE_COST * len(touched),
                summary="introduce registered interface %s (replaces %s)"
                % (spec.id, ", ".join(touched)),
                scc_edges=touched,
            )
        )
    return actions


def _stable_key(actions: Sequence[Action]) -> Tuple:
    return tuple(sorted(a.signature() for a in actions))


def _enumerate_local_solutions(
    graph: Graph,
    info: SCCInfo,
    actions: List[Action],
    include_types: List[str],
) -> List[Tuple[Tuple[Action, ...], float]]:
    """Return valid action subsets that break all cycles within one SCC.

    Exact subset enumeration while the action space is small; otherwise a
    greedy feedback-arc approximation plus all single actions. Deterministic
    ordering by (size, stable signatures).
    """
    members = set(info.members)
    members_edges = {
        edge.id
        for edge in graph.edges_for_view(include_types)
        if edge.source in members and edge.target in members
    }
    valid: List[Tuple[Tuple[Action, ...], float]] = []
    seen_signatures: Set[Tuple[str, ...]] = set()

    def consider(chosen: List[Action]) -> bool:
        operations = [a.operation for a in chosen]
        consumed: Set[str] = set()
        for action in chosen:
            if action.kind == INTRODUCE_INTERFACE:
                spec = graph.interfaces[action.operation.interface_id]
                consumed.update(e for e in spec.replaces if e in members_edges)
        for action in chosen:
            if action.edge_id and action.edge_id in consumed:
                return False
        if not _local_acyclic(graph, members, operations, include_types):
            return False
        sig = tuple(a.signature() for a in chosen)
        if sig in seen_signatures:
            return True
        seen_signatures.add(sig)
        cost = _local_cost(graph, chosen)
        valid.append((tuple(chosen), cost))
        return True

    ordered = sorted(actions, key=lambda a: (a.base_cost, a.signature()))

    if len(ordered) <= MAX_ACTION_SPACE:
        combos = 0
        for size in range(1, len(ordered) + 1):
            for combo in itertools.combinations(range(len(ordered)), size):
                combos += 1
                if combos > COMBO_CAP:
                    break
                chosen = [ordered[i] for i in combo]
                consider(chosen)
            if combos > COMBO_CAP:
                break
    else:
        # Greedy: repeatedly apply the locally cheapest action that destroys
        # the most remaining internal edges, re-checking acyclicity.
        for action in ordered:
            consider([action])
        remaining_edges = {
            edge.id
            for edge in graph.edges_for_view(include_types)
            if edge.source in members and edge.target in members
        }
        chosen: List[Action] = []
        available = list(ordered)
        while available and not _local_acyclic(
            graph, members, [a.operation for a in chosen], include_types
        ):
            def gain(action: Action) -> Tuple[int, float, str]:
                touched = len(set(action.scc_edges) & remaining_edges)
                return (-touched, action.base_cost, action.signature())

            available.sort(key=gain)
            best = available.pop(0)
            chosen.append(best)
            remaining_edges.difference_update(best.scc_edges)
            if len(chosen) <= 4:
                consider(list(chosen))

    # Stable order: lower cost first, then fewer operations (minimum change
    # set), then canonical action signatures for deterministic ties.
    valid.sort(
        key=lambda item: (item[1], len(item[0]), _stable_key(item[0]))
    )
    return valid[:LOCAL_SOLUTION_CAP]


def _local_cost(graph: Graph, actions: Sequence[Action]) -> float:
    nodes: Set[str] = set()
    owners: Set[str] = set()
    total = 0.0
    for action in actions:
        total += action.base_cost
        for node in action.nodes:
            if node in graph.components:
                nodes.add(node)
                owner = graph.owner_of(node)
                if owner:
                    owners.add(owner)
    weight_sum = sum(graph.weight_of(n) for n in nodes)
    cross = max(0, len(owners) - 1)
    return round(total + weight_sum + 3.0 * cross, 3)


def _global_cost(graph: Graph, actions: Sequence[Action]) -> Tuple[float, Dict[str, float]]:
    nodes: Set[str] = set()
    owners: Set[str] = set()
    base = 0.0
    for action in actions:
        base += action.base_cost
        for node in action.nodes:
            if node in graph.components:
                nodes.add(node)
                owner = graph.owner_of(node)
                if owner:
                    owners.add(owner)
    weight_sum = round(sum(graph.weight_of(n) for n in nodes), 3)
    cross = float(max(0, len(owners) - 1))
    cross_penalty = 3.0 * cross
    total = round(base + weight_sum + cross_penalty, 3)
    return total, {
        "base_change_cost": round(base, 3),
        "key_component_weight": weight_sum,
        "cross_owner_penalty": cross_penalty,
    }


# ---------------------------------------------------------------------------
# Explanation: sufficiency, affected paths, owner confirmation
# ---------------------------------------------------------------------------

def _removed_edge_set(graph: Graph, operations: List[EdgeOperation]) -> Set[str]:
    removed: Set[str] = set()
    for operation in operations:
        if operation.op == INTRODUCE_INTERFACE:
            removed.update(graph.interfaces[operation.interface_id].replaces)
    return removed


def _changed_edge_set(operations: List[EdgeOperation]) -> Set[str]:
    changed: Set[str] = set()
    for operation in operations:
        if operation.edge_id:
            changed.add(operation.edge_id)
    return changed


def _explain_scc(
    graph: Graph,
    info: SCCInfo,
    candidate_actions: List[Action],
    operations: List[EdgeOperation],
    include_types: List[str],
) -> dict:
    removed = _removed_edge_set(graph, operations)
    changed = _changed_edge_set(operations)
    scc_id = "scc%d" % info.index

    broken_cycles: List[dict] = []
    surviving_cycles: List[dict] = []
    for cycle_edges in info.cycles:
        edge_set = set(cycle_edges)
        is_broken = bool(edge_set & removed) or bool(edge_set & changed)
        record = {
            "edges": cycle_edges,
            "nodes": [graph.edges[e].source for e in cycle_edges],
        }
        if is_broken:
            broken_cycles.append(record)
        else:
            surviving_cycles.append(record)

    # Still-reachable ordered node pairs across changed/removed edges.
    reachable_paths: List[dict] = []
    candidate_edge_ids = sorted(changed | removed)
    for edge_id in candidate_edge_ids:
        edge = graph.edges.get(edge_id)
        if edge is None:
            continue
        from .operations import apply_operations

        projected, _ = apply_operations(graph, operations)
        path = shortest_path_edges(projected, edge.source, edge.target, include_types)
        if path is not None:
            reachable_paths.append(
                {
                    "from": edge.source,
                    "to": edge.target,
                    "original_edge": edge_id,
                    "path_edges": path,
                }
            )

    owners = sorted(
        {
            graph.owner_of(node)
            for action in candidate_actions
            for node in action.nodes
            if node in graph.components and graph.owner_of(node)
        }
    )
    changed_nodes = sorted(
        {node for action in candidate_actions for node in action.nodes if node in graph.components}
    )
    return {
        "scc": scc_id,
        "members": info.members,
        "broken_cycles": broken_cycles,
        "surviving_cycles": surviving_cycles,
        "reachable_paths": reachable_paths,
        "changed_nodes": changed_nodes,
        "owners_required": owners,
        "sufficiency": (
            "All cycles in %s are cut: applying the edge operations to a graph "
            "copy projected to %s leaves this component acyclic, and a global "
            "verification confirms a DAG (no edges left participating in a cycle)."
            % (scc_id, include_types)
        ),
    }


def analyze(graph: Graph, include_types: Sequence[str]) -> AnalysisResult:
    include_types = list(include_types)
    cyclic = cyclic_components(graph, include_types)

    scc_infos: List[SCCInfo] = []
    per_scc_solutions: List[List[Tuple[Tuple[Action, ...], float]]] = []

    for index, (members, self_loop) in enumerate(cyclic):
        cycles = enumerate_simple_cycles(graph, members, include_types)
        info = SCCInfo(
            index=index,
            members=members,
            self_loop=self_loop,
            cycles=cycles,
            splittable=True,
        )
        actions = _scc_actions(graph, info, include_types)
        solutions = _enumerate_local_solutions(graph, info, actions, include_types)
        if not solutions:
            info.splittable = False
            locked = [
                edge.id
                for edge in graph.edges_for_view(include_types)
                if edge.source in set(members)
                and edge.target in set(members)
                and edge.locked
            ]
            info.unsplittable_reason = (
                "No legal change set breaks this component: every applicable "
                "edge is locked or absent, and no registered interface replaces "
                "an edge on a cycle (locked edges: %s)." % sorted(locked)
            )
        scc_infos.append(info)
        per_scc_solutions.append(solutions)

    if not scc_infos or any(not info.splittable for info in scc_infos):
        return AnalysisResult(
            include_types=include_types,
            sccs=scc_infos,
            candidates=[],
            cyclic=bool(scc_infos),
        )

    # Combine one solution per SCC. Deduplicate operations (an interface may
    # replace edges in several SCCs) and globally verify on a graph copy.
    candidates: List[Candidate] = []
    seen_keys: Set[str] = set()
    combos = 0
    choices = [solutions for solutions in per_scc_solutions]
    for combo in itertools.product(*choices):
        combos += 1
        if combos > COMBO_CAP:
            break
        merged: Dict[str, Action] = {}
        ordered_actions: List[Action] = []
        for action_tuple, _ in combo:
            for action in action_tuple:
                signature = action.signature()
                if signature not in merged:
                    merged[signature] = action
                    ordered_actions.append(action)
        ordered_actions.sort(key=lambda a: a.signature())
        operations = [action.operation for action in ordered_actions]
        report = verify(graph, operations, include_types)
        if not report.ok:
            continue
        key = candidate_key(operations)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        cost, detail = _global_cost(graph, ordered_actions)
        owners = sorted(
            {
                graph.owner_of(node)
                for action in ordered_actions
                for node in action.nodes
                if node in graph.components and graph.owner_of(node)
            }
        )
        affected = ["scc%d" % idx for idx, _ in enumerate(cyclic)]
        explanations = [
            _explain_scc(graph, scc_infos[idx], list(combo[idx][0]), operations, include_types)
            for idx in range(len(scc_infos))
        ]
        candidates.append(
            Candidate(
                key=key,
                operations=operations,
                cost=cost,
                cost_detail=detail,
                actions=ordered_actions,
                explanations=explanations,
                affected_sccs=affected,
                owners=owners,
                breaks_all_cycles=True,
            )
        )

    candidates.sort(
        key=lambda c: (
            c.cost,
            len(c.operations),
            tuple(sorted(op.signature() for op in c.operations)),
        )
    )
    return AnalysisResult(
        include_types=include_types,
        sccs=scc_infos,
        candidates=candidates[:GLOBAL_CANDIDATE_CAP],
        cyclic=bool(scc_infos),
    )


def candidate_to_dict(candidate: Candidate) -> dict:
    return {
        "key": candidate.key,
        "cost": candidate.cost,
        "cost_detail": candidate.cost_detail,
        "operations": [op.to_dict() for op in candidate.operations],
        "actions": [
            {
                "kind": action.kind,
                "summary": action.summary,
                "edge_id": action.edge_id,
                "nodes": list(action.nodes),
                "base_cost": action.base_cost,
            }
            for action in candidate.actions
        ],
        "affected_sccs": candidate.affected_sccs,
        "owners_required": candidate.owners,
        "explanations": candidate.explanations,
        "breaks_all_cycles": candidate.breaks_all_cycles,
    }
