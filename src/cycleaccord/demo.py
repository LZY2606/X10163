"""演示用内置图：包含相交环、自环、平行边、多类型与已登记接口。"""
from __future__ import annotations

from .graphio import parse_graph

DEMO_GRAPH = {
    "components": [
        {"id": "web", "name": "Web 网关", "owner": "alice", "weight": 3},
        {"id": "orders", "name": "订单服务", "owner": "bob", "weight": 3},
        {"id": "payments", "name": "支付服务", "owner": "carol", "weight": 4},
        {"id": "ledger", "name": "账务核心", "owner": "dave", "weight": 5},
        {"id": "inventory", "name": "库存服务", "owner": "bob", "weight": 2},
        {"id": "notify", "name": "通知服务", "owner": "erin", "weight": 1},
        {"id": "codegen", "name": "代码生成器", "owner": "platform", "weight": 1},
        {"id": "testkit", "name": "测试工具包", "owner": "qa", "weight": 1},
        {"id": "LedgerPort", "name": "账务接口 LedgerPort",
         "owner": "platform", "weight": 0, "kind": "interface"},
        {"id": "LedgerSPI", "name": "账务回调 SPI",
         "owner": "platform", "weight": 0, "kind": "interface"},
    ],
    "edges": [
        # 主环 1：web -> orders -> payments -> ledger -> web
        {"id": "web-orders", "src": "web", "target": "orders", "type": "hard"},
        {"id": "orders-payments", "src": "orders", "target": "payments",
         "type": "hard",
         "interface_ids": ["if-ledger-port"]},
        {"id": "payments-ledger", "src": "payments", "target": "ledger",
         "type": "hard",
         "interface_ids": ["if-ledger-spi"]},
        {"id": "ledger-web-callback", "src": "ledger", "target": "web",
         "type": "runtime", "note": "回调 Web 网关更新页面"},

        # 相交环 2：orders -> inventory -> notify -> orders
        {"id": "orders-inventory", "src": "orders", "target": "inventory",
         "type": "hard"},
        {"id": "inventory-notify", "src": "inventory", "target": "notify",
         "type": "runtime"},
        {"id": "notify-orders", "src": "notify", "target": "orders",
         "type": "hard", "note": "通知回执回写订单"},

        # 两环之间的相交边（让环在 payments/notify 处相交）
        {"id": "payments-notify", "src": "payments", "target": "notify",
         "type": "runtime"},

        # 自环：仅 generated / test 视图下出现
        {"id": "gen-selfloop", "src": "codegen", "target": "codegen",
         "type": "generated",
         "note": "生成器模板引用自身产物，偶尔成环"},
        {"id": "test-selfloop", "src": "testkit", "target": "testkit",
         "type": "test", "note": "测试夹具自引用"},

        # 平行边：web -> orders 同时存在 hard 与 runtime（灰度通道）
        {"id": "web-orders-runtime", "src": "web", "target": "orders",
         "type": "runtime", "note": "灰度运行时通道（平行边）"},

        # 弱依赖
        {"id": "inventory-codegen", "src": "inventory", "target": "codegen",
         "type": "generated"},
        {"id": "orders-testkit", "src": "orders", "target": "testkit",
         "type": "test"},
    ],
    "interfaces": [
        {"id": "if-ledger-port", "component": "LedgerPort",
         "consumer": "orders", "provider": "payments",
         "replaces_edge": "orders-payments", "added_type": "runtime",
         "note": "订单只依赖账务端口，支付侧提供实现"},
        {"id": "if-ledger-spi", "component": "LedgerSPI",
         "consumer": "payments", "provider": "ledger",
         "replaces_edge": "payments-ledger", "added_type": "runtime",
         "note": "支付通过 SPI 调账务，切断与账务核心的硬耦合"},
    ],
}


def load_demo_graph():
    return parse_graph(DEMO_GRAPH)
