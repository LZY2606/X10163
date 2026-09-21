from cycleaccord.candidates import generate_candidates
from cycleaccord.graphio import parse_graph

from conftest import ALL_TYPES


def _diamond_cycle_graph():
    return parse_graph({
        "nodes": [
            {"id": "a", "owner": "alice", "weight": 1},
            {"id": "b", "owner": "alice", "weight": 1},
            {"id": "c", "owner": "carol", "weight": 1},
        ],
        "edges": [
            {"id": "e1", "src": "a", "dst": "b", "owner": "alice"},
            {"id": "e2", "src": "b", "dst": "c", "owner": "carol"},
            {"id": "e3", "src": "c", "dst": "a", "owner": "alice",
             "downgrade": ["generated"]},
            {"id": "e4", "src": "c", "dst": "b", "owner": "carol"},
        ],
    })


def test_type_filter_removes_generated_cycle():
    g = parse_graph({
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"id": "h", "src": "a", "dst": "b", "type": "hard"},
            {"id": "g", "src": "b", "dst": "a", "type": "generated"},
        ],
    })
    # 原始图不变：全类型有环
    assert bool(generate_candidates(g, ALL_TYPES).sccs)
    # 只看 hard：generated 回边被视图过滤，图无环
    result = generate_candidates(g, ("hard",))
    assert result.sccs == []
    assert result.candidates == []
    # 原始图仍保留 generated 边
    assert g.edges["g"].dep_type == "generated"


def test_downgrade_only_valid_when_target_type_excluded():
    g = _diamond_cycle_graph()
    # 包含 generated 时，e3 降级到 generated 不能切断该视图下的环，故不提供
    full = generate_candidates(g, ALL_TYPES)
    kinds = [(op.kind, op.target_type) for c in full.candidates for op in c.ops]
    assert ("downgrade", "generated") not in kinds

    # 排除 generated 时降级成为合法操作
    filtered = generate_candidates(g, ("hard", "runtime", "test"))
    kinds2 = [(op.kind, op.target_type) for c in filtered.candidates for op in c.ops]
    assert ("downgrade", "generated") in kinds2
    assert all(c.verified for c in filtered.candidates)


def test_stable_minimum_candidate_is_cheapest_and_deterministic():
    g = _diamond_cycle_graph()
    cs1 = generate_candidates(g, ("hard", "runtime", "test"))
    cs2 = generate_candidates(g, ("hard", "runtime", "test"))
    ids1 = [c.id for c in cs1.candidates]
    ids2 = [c.id for c in cs2.candidates]
    assert ids1 == ids2  # 稳定顺序
    costs = [c.cost for c in cs1.candidates]
    assert costs == sorted(costs)  # 成本升序
    # 最小候选操作数最少且成本最低
    best = cs1.candidates[0]
    assert len(best.ops) == min(len(c.ops) for c in cs1.candidates)
    # 最小候选是反转同时位于两条环上的 e2（b->c）
    assert [(o.kind, o.edge_id) for o in best.ops] == [("reverse", "e2")]
    assert best.verified


def test_intersecting_cycles_minimal_solution_hits_both():
    g = _diamond_cycle_graph()
    cs = generate_candidates(g, ("hard", "runtime", "test"))
    best = cs.candidates[0]
    # e2 在 (a,b,c) 和 (b,c) 两条环上
    assert best.cut_edge_ids == ["e2"]
    assert len(best.broken_cycles) == 2


def test_no_legal_split_when_all_edges_locked():
    g = parse_graph({
        "nodes": [{"id": "x"}, {"id": "y"}],
        "edges": [
            {"id": "p", "src": "x", "dst": "y", "locked": True},
            {"id": "q", "src": "y", "dst": "x", "locked": True},
        ],
    })
    cs = generate_candidates(g, ALL_TYPES)
    assert cs.infeasible_sccs == [["x", "y"]]
    assert cs.complete is False
    assert cs.candidates == []


def test_registered_interface_allowed_even_on_locked_edge():
    g = parse_graph({
        "nodes": [{"id": "x"}, {"id": "y"}, {"id": "z"}],
        "edges": [
            {"id": "p", "src": "x", "dst": "y", "locked": True},
            {"id": "q", "src": "y", "dst": "x", "locked": True},
        ],
        "interfaces": [
            {"id": "i1", "replaces_edge": "q", "new_src": "y",
             "new_dst": "z", "new_type": "hard"},
        ],
    })
    cs = generate_candidates(g, ALL_TYPES)
    assert cs.infeasible_sccs == []
    assert cs.candidates
    best = cs.candidates[0]
    assert any(op.kind == "interface" and op.interface_id == "i1" for op in best.ops)
    assert best.verified


def test_system_does_not_invent_interfaces():
    # 没有登记任何接口时，候选中不允许出现 interface 操作
    g = _diamond_cycle_graph()
    cs = generate_candidates(g, ALL_TYPES)
    for cand in cs.candidates:
        assert all(op.kind != "interface" for op in cand.ops)


def test_cost_reflects_critical_weight_and_cross_owner():
    g = parse_graph({
        "nodes": [
            {"id": "a", "owner": "alice", "critical": True, "weight": 5},
            {"id": "b", "owner": "bob", "weight": 1},
        ],
        "edges": [
            {"id": "p", "src": "a", "dst": "b", "owner": "bob"},
            {"id": "q", "src": "b", "dst": "a", "owner": "alice"},
        ],
    })
    cs = generate_candidates(g, ALL_TYPES)
    assert cs.candidates
    # 两条候选都触及相同端点，因此端点成本一致；用降级制造更便宜的同环方案，
    # 验证变更类型基础成本（降级 4 < 反转 10）决定排序，且跨负责人计入。
    g2 = parse_graph({
        "nodes": [
            {"id": "a", "owner": "alice", "critical": True, "weight": 5},
            {"id": "b", "owner": "bob", "weight": 1},
        ],
        "edges": [
            {"id": "p", "src": "a", "dst": "b", "owner": "bob"},
            {"id": "q", "src": "b", "dst": "a", "owner": "alice",
             "downgrade": ["generated"]},
        ],
    })
    cs2 = generate_candidates(g2, ("hard", "runtime", "test"))
    by_kind = {}
    for c in cs2.candidates:
        key = tuple(sorted((o.kind for o in c.ops)))
        by_kind.setdefault(key, c.cost)
    # 降级方案比反转方案便宜
    assert by_kind[("downgrade",)] < by_kind[("reverse",)]
    costs = [c.cost for c in cs.candidates]
    assert costs == sorted(costs)
    # 任何候选都需两位负责人确认
    assert all(set(c.owners) == {"alice", "bob"} for c in cs.candidates)
