"""Sample graph used to bootstrap the demo. Contains intersecting cycles,
parallel edges, a filtered-out self-loop and a pre-registered interface."""

SAMPLE_GRAPH = {
    "components": [
        {"id": "auth", "name": "认证服务", "owner": "alice", "weight": 3},
        {"id": "orders", "name": "订单服务", "owner": "bob", "weight": 4},
        {"id": "payments", "name": "支付服务", "owner": "carol", "weight": 5},
        {"id": "inventory", "name": "库存服务", "owner": "bob", "weight": 2},
        {"id": "notify", "name": "通知服务", "owner": "dave", "weight": 1},
        {"id": "billing", "name": "计费服务", "owner": "erin", "weight": 2},
        {"id": "report", "name": "报表服务", "owner": "frank", "weight": 2},
    ],
    "edges": [
        {"id": "e1", "source": "orders", "target": "auth", "type": "hard"},
        {"id": "e2", "source": "auth", "target": "orders", "type": "hard"},
        {"id": "e3", "source": "orders", "target": "payments", "type": "hard"},
        {"id": "e3b", "source": "orders", "target": "payments", "type": "runtime"},
        {"id": "e4", "source": "payments", "target": "orders", "type": "runtime"},
        {"id": "e5", "source": "payments", "target": "inventory", "type": "hard"},
        {"id": "e6", "source": "inventory", "target": "orders", "type": "hard"},
        {"id": "e7", "source": "notify", "target": "orders", "type": "test"},
        {"id": "e8", "source": "inventory", "target": "inventory", "type": "generated"},
        {"id": "e20", "source": "billing", "target": "report", "type": "hard"},
        {"id": "e21", "source": "billing", "target": "report", "type": "runtime"},
        {"id": "e22", "source": "report", "target": "billing", "type": "hard"},
    ],
    "interfaces": [
        {
            "id": "pay_gw",
            "provider": "payments",
            "consumers": ["orders"],
            "replaces": ["e3", "e3b"],
            "description": "支付网关接口",
        }
    ],
}
