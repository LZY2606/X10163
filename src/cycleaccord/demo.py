"""内置演示图：覆盖自环、平行边、相交环、多类型、锁定边与登记接口。"""

from __future__ import annotations

DEMO_GRAPH = {
    "nodes": [
        {"id": "gateway", "name": "网关", "owner": "lin", "weight": 3, "critical": True},
        {"id": "core", "name": "核心域", "owner": "aziz", "weight": 4, "critical": True},
        {"id": "store", "name": "存储层", "owner": "mei", "weight": 2},
        {"id": "audit", "name": "审计", "owner": "lin", "weight": 1},
        {"id": "billing", "name": "计费", "owner": "noa", "weight": 2},
        {"id": "notify", "name": "通知", "owner": "noa", "weight": 1},
        {"id": "legacy", "name": "遗留模块", "owner": "mei", "weight": 1},
        {"id": "featureflag", "name": "开关服务", "owner": "aziz", "weight": 1},
    ],
    "edges": [
        # 主环 gateway -> core -> store -> gateway
        {"id": "d1", "src": "gateway", "dst": "core", "type": "hard", "owner": "aziz"},
        {"id": "d2", "src": "core", "dst": "store", "type": "hard", "owner": "mei"},
        {"id": "d3", "src": "store", "dst": "gateway", "type": "hard", "owner": "lin"},
        # 与主环相交的环 core -> audit -> core（平行方向之一）
        {"id": "d4", "src": "core", "dst": "audit", "type": "runtime", "owner": "lin"},
        {"id": "d5", "src": "audit", "dst": "core", "type": "runtime", "owner": "aziz"},
        # core 与 store 之间的平行边（弱类型）
        {"id": "d6", "src": "core", "dst": "store", "type": "test", "owner": "mei"},
        # 计费 -> 核心 -> 计费 的环，其中一条为 generated 代码生成依赖
        {"id": "d7", "src": "billing", "dst": "core", "type": "hard", "owner": "aziz"},
        {"id": "d8", "src": "core", "dst": "billing", "type": "generated", "owner": "noa"},
        # 通知的自环（故障注入配置导致）
        {"id": "d9", "src": "notify", "dst": "notify", "type": "hard",
         "owner": "noa", "downgrade": ["generated"]},
        # 通知 -> 计费（跨负责人），不构成环
        {"id": "d10", "src": "notify", "dst": "billing", "type": "runtime", "owner": "noa"},
        # 遗留模块锁定环：两条边均锁定，唯一出路是登记接口
        {"id": "d11", "src": "legacy", "dst": "featureflag", "type": "hard",
         "owner": "mei", "locked": True},
        {"id": "d12", "src": "featureflag", "dst": "legacy", "type": "hard",
         "owner": "aziz", "locked": True},
        # d2 允许降级到 generated（已登记）
        {"id": "d13", "src": "audit", "dst": "store", "type": "runtime", "owner": "mei"},
    ],
    "interfaces": [
        # 显式登记：把 core->store 的直接硬依赖替换为 core->gateway 的接口回调边。
        {"id": "if_store_port",
         "replaces_edge": "d2",
         "new_src": "core",
         "new_dst": "gateway",
         "new_type": "runtime",
         "owner": "lin",
         "note": "用 StorePort 接口回调替代 core 对 store 的直接 hard 依赖"},
        # 显式登记：legacy 改为依赖开关服务暴露的只读接口（指向 audit，脱离环）。
        {"id": "if_flag_ro",
         "replaces_edge": "d12",
         "new_src": "featureflag",
         "new_dst": "audit",
         "new_type": "runtime",
         "owner": "lin",
         "note": "featureflag 通过只读 FlagView 接口依赖 audit，脱离 legacy 环"},
    ],
}
