"""Demo graph used to seed a fresh store."""
from __future__ import annotations


def demo_graph():
    return {
        "components": [
            {"id": "web", "owner": "alice", "weight": 2},
            {"id": "orders", "owner": "bob", "weight": 3},
            {"id": "payments", "owner": "carol", "weight": 3,
             "interfaces": ["I-Payments"]},
            {"id": "billing", "owner": "carol", "weight": 1},
            {"id": "inventory", "owner": "dave", "weight": 2},
            {"id": "shipping", "owner": "dave", "weight": 1},
            {"id": "notify", "owner": "erin", "weight": 1},
            {"id": "report", "owner": "erin", "weight": 1},
        ],
        "edges": [
            {"id": "e1", "source": "web", "target": "orders", "type": "hard"},
            {"id": "e2", "source": "orders", "target": "payments", "type": "hard"},
            {"id": "e3", "source": "payments", "target": "billing", "type": "runtime"},
            {"id": "e4", "source": "billing", "target": "orders", "type": "hard"},
            {"id": "e5", "source": "orders", "target": "inventory", "type": "runtime"},
            {"id": "e6", "source": "inventory", "target": "shipping", "type": "hard"},
            {"id": "e7", "source": "shipping", "target": "inventory", "type": "hard"},
            {"id": "e8", "source": "shipping", "target": "orders", "type": "test"},
            {"id": "e9", "source": "billing", "target": "notify", "type": "generated"},
            {"id": "e10", "source": "notify", "target": "report", "type": "runtime"},
            {"id": "e11", "source": "report", "target": "billing", "type": "generated"},
        ],
    }
