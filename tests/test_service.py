import json
import sqlite3
import threading

import pytest

from conftest import ALL_TYPES


SIMPLE_GRAPH = {
    "nodes": [
        {"id": "a", "owner": "alice"},
        {"id": "b", "owner": "bob"},
        {"id": "c", "owner": "carol"},
    ],
    "edges": [
        {"id": "e1", "src": "a", "dst": "b", "owner": "bob"},
        {"id": "e2", "src": "b", "dst": "c", "owner": "carol"},
        {"id": "e3", "src": "c", "dst": "a", "owner": "alice",
         "downgrade": ["generated"]},
    ],
}


@pytest.fixture()
def analyzed(service):
    v = service.import_graph(SIMPLE_GRAPH)
    analysis = service.ensure_analysis(list(ALL_TYPES), graph_version=v)
    return v, analysis


def test_candidate_persisted_and_machine_verifies(service, analyzed):
    _, analysis = analyzed
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]
    result = service.machine_verify(aid, cid)
    assert result["acyclic"] is True
    # 重新从存储读出的候选也通过校验（边操作可独立重放）
    stored = service.storage.get_candidate(aid, cid)
    assert stored["verified"] is True


def test_concurrent_opinions_keep_separate_versions(service, analyzed):
    _, analysis = analyzed
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]

    errors = []

    def submit(owner):
        try:
            service.add_opinion(aid, cid, owner, "accept", "from " + owner)
        except Exception as exc:  # pragma: no cover - 并发不应抛错
            errors.append(exc)

    owners = ["o%02d" % i for i in range(20)]
    threads = [threading.Thread(target=submit, args=(o,)) for o in owners]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    opinions = service.storage.list_opinions(aid, cid)
    assert len(opinions) == 20
    # 并发提交顺序不保证，但每位提交者的意见都作为独立版本保留，
    # 后写不能覆盖先写（每个 owner 恰好一条、seq 唯一）。
    assert sorted(o["owner"] for o in opinions) == sorted(owners)
    assert sorted(o["seq"] for o in opinions) == list(range(1, 21))
    assert all(o["comment"] == "from " + o["owner"] for o in opinions)


def test_later_writer_cannot_overwrite_earlier(service, analyzed, tmp_path):
    _, analysis = analyzed
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]
    service.add_opinion(aid, cid, "first", "accept", "original")

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    # UPDATE 与 DELETE 都必须被触发器拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE opinions SET comment='tampered' WHERE seq=1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM opinions WHERE seq=1")
    conn.commit()
    conn.close()

    stored = service.storage.list_opinions(aid, cid)[0]
    assert stored["comment"] == "original"


def test_approval_invalidated_after_graph_change(service, analyzed):
    v1, analysis = analyzed
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]

    approved_v1 = service.approve(aid, cid, "alice")
    assert approved_v1["current"] is True

    # 导入变化后的图 -> 新版本
    changed = json.loads(json.dumps(SIMPLE_GRAPH))
    changed["edges"].append({"id": "e4", "src": "a", "dst": "c", "owner": "carol"})
    v2 = service.import_graph(changed)
    assert v2 == v1 + 1

    # 旧分析标记为 stale；再次批准旧候选只作为历史，不对当前图生效
    record = service.storage.get_analysis(aid)
    assert service.is_stale(record) is True
    approved_again = service.approve(aid, cid, "bob")
    assert approved_again["current"] is False
    assert approved_again["graph_version"] == v1

    # 历史两条选择都保留
    selections = service.storage.list_selections(aid)
    assert [s["owner"] for s in selections] == ["alice", "bob"]

    # 新版本上重新分析得到的新分析是当前的
    fresh = service.ensure_analysis(list(ALL_TYPES), graph_version=v2)
    assert service.is_stale(service.storage.get_analysis(fresh["id"])) is False


def test_propose_requires_alternative_edge(service, analyzed):
    _, analysis = analyzed
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]
    with pytest.raises(ValueError):
        service.add_opinion(aid, cid, "bob", "propose", comment="no alt")
    saved = service.add_opinion(aid, cid, "bob", "propose",
                                alternative="e2: b->a runtime")
    assert saved["decision"] == "propose"


def test_unverified_candidate_cannot_be_approved(service):
    # 业务层必须拒绝批准未通过无环校验的候选。
    g = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"id": "p", "src": "a", "dst": "b"},
                  {"id": "q", "src": "b", "dst": "a"}],
    }
    v = service.import_graph(g)
    analysis = service.ensure_analysis(list(ALL_TYPES), graph_version=v)
    aid = analysis["id"]
    cid = analysis["payload"]["candidates"][0]["id"]
    # monkeypatch：候选被标记为未校验时，批准必须被业务规则拒绝
    original = service.storage.get_candidate
    service.storage.get_candidate = lambda a, c: {**original(a, c), "verified": False}
    with pytest.raises(ValueError):
        service.approve(aid, cid, "alice")
    service.storage.get_candidate = original


def test_analysis_reused_and_original_graph_unchanged(service, analyzed):
    v, analysis = analyzed
    again = service.ensure_analysis(list(ALL_TYPES), graph_version=v)
    assert again["id"] == analysis["id"]
    assert again["reused"] is True
    # 分析视图不修改原始图
    raw = service.storage.get_graph_json(v)
    assert {e["id"] for e in raw["edges"]} == {"e1", "e2", "e3"}
