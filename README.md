# 依赖环协商器（cycleaccord）

面向大型构建图的依赖环分析与协商工具。导入带**组件、依赖类型、负责人和可替代接口说明**
的有向图后，系统会：

- 找出强连通分量（SCC），自环、平行边、多个相交环均支持；
- 生成若干**打破全部环**的候选变更集，并给出稳定排序；
- 为每条候选解释「为什么足够」「影响哪些路径」「需要哪些负责人确认」；
- 提供一组**可机器校验的边操作**，应用到图副本后确实无环；
- 支持负责人**接受 / 拒绝 / 提出替代边**，批准是版本化过程，图变化后旧批准只作历史。

不依赖任何图数据库，核心算法与 HTTP 服务全部基于 Python 标准库。

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/python -m cycleaccord --host 127.0.0.1 --port 5236
```

打开 http://127.0.0.1:5236 即可看到「依赖环协商器」。空数据库首次启动会自动导入内置演示图，
可用 `--no-seed` 关闭；数据库路径由 `--db` 或环境变量 `CYCLEACCORD_DB` 指定（默认
`cycleaccord.db`）。

## 概念与规则

**依赖类型与视图**：类型强度为 `hard > runtime > test > generated`。分析视图可勾选包含
哪些类型；视图选择只影响分析，**原始图永不改变**。

**候选变更（边操作）**：

| 操作 | 含义 | 约束 |
| --- | --- | --- |
| `reverse` | 反转一条边 | 自环不提供（反转后仍是自环）；`locked` 边不允许 |
| `downgrade` | 降为允许的较弱类型 | 只能降到该边 `downgrade` 中**已登记**、更弱、且被当前视图**排除**的类型，否则无法在该视图切断环 |
| `interface` | 引入已登记接口边 | 删除原边并新增接口边；**只能使用显式登记的接口，系统不会凭空发明**；`locked` 边也允许走接口 |

**成本**：由变更类型基础成本（反转 10 / 降级 4 / 接口 12）、边与端点权重、关键组件附加
成本，以及**跨负责人数量**（每个额外负责人 +8）共同决定。成本相同按操作签名做稳定字典序。

**充分性校验**：每个 SCC 求最小反馈边集（规模最小优先，数量不足时补次小规模），跨 SCC
组合后用 Tarjan/Kahn 在「应用操作后的图副本」上重新做无环校验——反转或接口新增的边若在
SCC 之外重新成环会被丢弃。`machine_verify` 与页面按钮可随时独立重放边操作校验。

**无合法拆分**：当某个 SCC 的所有边都不可行动（例如全部 `locked` 且无登记接口），该 SCC
被标记为 `infeasible_sccs`，候选集 `complete=false`，但仍会为其他可行动 SCC 生成部分候选。

**版本化与并发**：

- 每次导入创建新的 `graph_versions` 版本；分析（`analyses`）绑定图版本与类型集合，
  图变化后旧分析及其批准自动标记 `stale`，记录保留为历史；
- `opinions` 与 `selections` 只追加（SQLite 触发器拒绝 UPDATE/DELETE），多人并发提交
  各自获得独立自增版本，后写不能覆盖先写。

## 图 JSON 格式

```json
{
  "nodes": [
    {"id": "core", "name": "核心域", "owner": "aziz", "weight": 4, "critical": true}
  ],
  "edges": [
    {"id": "e1", "src": "core", "dst": "store", "type": "hard",
     "owner": "mei", "weight": 1, "locked": false, "downgrade": ["generated"]}
  ],
  "interfaces": [
    {"id": "if_store_port", "replaces_edge": "e1",
     "new_src": "core", "new_dst": "gateway", "new_type": "runtime",
     "owner": "lin", "note": "用 StorePort 接口回调替代直接 hard 依赖"}
  ]
}
```

## HTTP API

- `GET  /api/state`：当前图、所有分析（含 stale 标记）、意见与选择
- `POST /api/import`：导入新图版本 `{ "graph": { ... } }`
- `POST /api/analyses`：`{ "include_types": ["hard", ...] }`，幂等（同图同类型复用）
- `GET  /api/analyses?id=<analysis_id>`：分析详情
- `GET  /api/verify?analysis_id=...&candidate_id=...`：机器无环校验
- `POST /api/opinions`：`accept` / `reject` / `propose`（propose 必须带 `alternative`）
- `POST /api/approve`：记录最终选择；图已变化时返回 `current=false`

## 代码结构

- `src/cycleaccord/model.py`：节点 / 边 / 接口 / 边操作模型
- `src/cycleaccord/graphio.py`：图解析与校验
- `src/cycleaccord/algorithms.py`：迭代 Tarjan SCC、有环判定、基本环枚举、操作应用
- `src/cycleaccord/candidates.py`：可行操作、最小反馈边集、成本、候选生成与解释
- `src/cycleaccord/storage.py`：SQLite 版本化只追加存储
- `src/cycleaccord/service.py` / `server.py` / `cli.py`：业务服务、HTTP、入口
- `src/cycleaccord/static/`：可视化页面（原生 JS + SVG，无构建步骤）
- `tests/`：自环、平行边、类型过滤、相交环、稳定最小候选、无合法拆分、并发意见、
  图变化后批准失效、HTTP 端到端
