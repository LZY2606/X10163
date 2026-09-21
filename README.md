# cycleaccord · 依赖环协商器

导入带组件、依赖类型、负责人和可替代接口说明的依赖图，找出强连通分量，
生成若干打破全部环的候选变更集，并支持负责人协商、批准与最终选择。

## 安装与运行

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/python -m cycleaccord --host 127.0.0.1 --port 5236
```

访问 http://127.0.0.1:5236 。首次启动会载入演示图；状态持久化在
`.cycleaccord-state.json`（可用 `--state` 指定）。

## 模型

- 边类型：`hard` / `runtime` / `test` / `generated`。分析视图可勾选包含
  哪些类型，原始图不变。
- 候选变更操作（均为可机器校验的边操作，应用到图副本后必须无环）：
  - `reverse`：反转一条边；
  - `downgrade`：把边降为更弱的类型（降幅越小成本越低）；
  - `interface`：引入目标组件已登记的接口边（系统不会凭空发明接口）。
- 成本 = 操作基础成本 + 涉及组件权重 + 跨负责人惩罚；同成本按稳定顺序排列。
- 意见（接受/拒绝/提出替代边）与最终选择均为追加式版本化记录；图或分析
  版本变化后，旧批准自动转为历史，不会被后写覆盖。

## API

- `GET  /api/state` — 图、当前分析（SCC/环/候选）、意见、最终选择
- `POST /api/graph` — 导入新图（graph_version +1）
- `POST /api/view` — 设置分析视图包含的类型
- `POST /api/opinion` — 提交负责人意见
- `POST /api/select` — 记录最终选择
