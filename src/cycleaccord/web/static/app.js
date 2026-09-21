"use strict";

const EDGE_TYPES = ["hard", "runtime", "test", "generated"];
const TYPE_LABEL = {
  hard: "hard（硬依赖）",
  runtime: "runtime（运行时）",
  test: "test（测试）",
  generated: "generated（生成）",
};

const state = {
  graphData: null,
  analysis: null,
  analysisDetail: null,
  selectedCandidateId: null,
  candidateDetail: null,
  toggleReach: false,
  togglePaths: true,
};

function $(id) { return document.getElementById(id); }

async function api(method, url, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || ("HTTP " + resp.status));
  return data;
}

function personName() {
  const name = $("person").value.trim();
  localStorage.setItem("cycleaccord_person", name);
  return name;
}

// ---------- 类型过滤 ----------
function renderTypeFilters() {
  const box = $("typeFilters");
  box.innerHTML = "";
  EDGE_TYPES.forEach((t) => {
    const label = document.createElement("label");
    label.innerHTML =
      `<input type="checkbox" value="${t}" checked />
       <span class="dot dot-${t}"></span>${TYPE_LABEL[t]}`;
    box.appendChild(label);
  });
}

function selectedTypes() {
  return Array.from(
    document.querySelectorAll('#typeFilters input[type="checkbox"]:checked')
  ).map((el) => el.value);
}

// ---------- 图加载 ----------
async function loadState() {
  const data = await api("GET", "/api/state");
  state.graphData = data;
  $("versionBadge").textContent = "图版本：" + data.graph_version.slice(0, 12);
  $("importArea").value = JSON.stringify(data.graph, null, 2);
  renderGraph();
}

// ---------- 分析 ----------
async function runAnalyze() {
  const types = selectedTypes();
  if (!types.length) {
    alert("请至少选择一种依赖类型");
    return;
  }
  const data = await api("POST", "/api/analyze", {
    types,
    graph_version: state.graphData ? state.graphData.graph_version : null,
  });
  state.analysis = data;
  state.selectedCandidateId = null;
  state.candidateDetail = null;
  const detail = await api("GET", "/api/analyses/" + data.analysis_id);
  state.analysisDetail = detail;
  renderScc();
  renderCandidates();
  renderStale();
  renderGraph();
}

function renderStale() {
  const stale = state.analysis && !state.analysis.is_current;
  $("staleBadge").classList.toggle("hidden", !stale);
}

function renderScc() {
  const box = $("sccList");
  box.innerHTML = "";
  if (!state.analysis) return;
  const comps = state.analysis.cyclic_components;
  if (!comps.length) {
    box.innerHTML = '<div class="hint">当前视图无环。</div>';
    return;
  }
  comps.forEach((comp, i) => {
    const div = document.createElement("div");
    div.className = "scc-item";
    div.textContent = `SCC ${i + 1}（${comp.length}）：${comp.join(" → ")}`;
    box.appendChild(div);
  });
}

function renderCandidates() {
  const box = $("candidateList");
  const diagBox = $("diagBox");
  box.innerHTML = "";
  diagBox.innerHTML = "";
  const a = state.analysis;
  if (!a) return;

  if (a.diag && a.diag.reason) {
    const warn = document.createElement("div");
    warn.className = "warn";
    warn.textContent = a.diag.reason;
    diagBox.appendChild(warn);
    if (a.diag.stuck_cycles) {
      const ul = document.createElement("ul");
      a.diag.stuck_cycles.forEach((cyc) => {
        const li = document.createElement("li");
        li.textContent = "卡住的环（边）：" + cyc.join(" → ");
        ul.appendChild(li);
      });
      diagBox.appendChild(ul);
    }
  }
  if (!a.candidates.length && !a.diag.reason) {
    diagBox.innerHTML = '<div class="hint">当前视图无环，无需拆分。</div>';
    return;
  }
  const edgeById = edgeMap();
  a.candidates.forEach((cand) => {
    const card = document.createElement("div");
    card.className = "cand-card";
    if (cand.id === state.selectedCandidateId) card.classList.add("selected");
    const actionText = cand.actions
      .map((act) => describeAction(act, edgeById))
      .join("；");
    card.innerHTML =
      `<div class="cand-head"><span>#${cand.rank + 1} 成本 ${cand.cost}</span>
         <span class="cand-owners">${cand.owners_to_confirm.join(", ")}</span>
       </div>
       <div class="cand-actions">${actionText}</div>`;
    card.onclick = () => selectCandidate(cand.id);
    box.appendChild(card);
  });
}

function edgeMap() {
  const m = {};
  if (!state.graphData) return m;
  state.graphData.graph.edges.forEach((e) => { m[e.id] = e; });
  return m;
}

function describeAction(act, edgeById) {
  const e = edgeById[act.edge_id] || { src: "?", target: "?" };
  if (act.kind === "reverse")
    return `反转 ${act.edge_id}（${e.src}→${e.target}）`;
  if (act.kind === "downgrade")
    return `降级 ${act.edge_id} ${e.type}→${act.new_type}`;
  return `引入接口 ${act.interface_id} 替换 ${act.edge_id}`;
}

// ---------- 候选详情与意见 ----------
async function selectCandidate(cid) {
  if (!state.analysis) return;
  state.selectedCandidateId = cid;
  const aid = state.analysis.analysis_id;
  state.candidateDetail = await api(
    "GET", `/api/analyses/${aid}/candidates/${cid}`
  );
  renderCandidates();
  renderCandidateDetail();
  renderGraph();
}

function renderCandidateDetail() {
  const box = $("candidateDetail");
  const d = state.candidateDetail;
  if (!d) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const cand = d.candidate;
  $("candRationale").textContent = cand.rationale;
  $("candVerify").innerHTML = d.verified_acyclic
    ? '<span style="color:#15803d">通过 ✓ 应用到图副本后无环</span>'
    : '<span style="color:#b91c1c">未通过 ✗</span>';
  $("candOwners").textContent = cand.owners_to_confirm.join("、") || "（无）";
  fillList($("candBroken"), cand.broken_cycles);
  fillList($("candPaths"),
    d.affected_node_paths.map((p) => p.join(" → ")));
  $("candOps").textContent = JSON.stringify(cand.ops, null, 2);

  const ol = $("opinionList");
  ol.innerHTML = "";
  d.opinions.forEach((op) => {
    const div = document.createElement("div");
    div.className = "opinion " + op.role;
    const roleText = { accept: "接受", reject: "拒绝", alternate: "替代方案" }[op.role];
    let extra = "";
    if (op.role === "alternate") {
      const verdict = op.alternate_feasible
        ? "校验无环 ✓" : "校验仍有环 ✗";
      extra = `<div>替代操作：${verdict}</div>`;
    }
    div.innerHTML =
      `<span class="who">${escapeHtml(op.person)}</span> · ${roleText}` +
      (op.status === "superseded"
        ? '<span class="stale">[基于旧图版本·仅历史]</span>' : "") +
      (op.comment ? `<div>${escapeHtml(op.comment)}</div>` : "") + extra;
    ol.appendChild(div);
  });

  renderSelection();
}

function renderSelection() {
  const box = $("selectionBox");
  box.innerHTML = "";
  const detail = state.analysisDetail;
  if (!detail || !detail.selection) return;
  const sel = detail.selection;
  const cls = sel.status === "superseded" ? "stale" : "ok";
  const note = sel.status === "superseded"
    ? "（图已变化，此批准仅作历史）" : "（当前有效）";
  box.innerHTML =
    `<div class="${cls}">已选择候选 ${sel.candidate_id}，` +
    `由 ${escapeHtml(sel.person)} 于 ${sel.created_at} 批准 ${note}</div>`;
}

function fillList(ul, items) {
  ul.innerHTML = "";
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.textContent = "（无）";
    ul.appendChild(li);
    return;
  }
  items.forEach((t) => {
    const li = document.createElement("li");
    li.textContent = t;
    ul.appendChild(li);
  });
}

async function submitOpinion(role) {
  const person = personName();
  if (!person) { alert("请先填写你的名字"); $("person").focus(); return; }
  if (!state.analysis || !state.candidateDetail) return;
  const aid = state.analysis.analysis_id;
  const cid = state.selectedCandidateId;
  const body = {
    person, role, comment: $("opinionComment").value.trim(),
  };
  await api(
    "POST",
    `/api/analyses/${aid}/candidates/${cid}/opinions`,
    body
  );
  $("opinionComment").value = "";
  await refreshAfterOpinion();
}

async function submitAlternate() {
  const person = personName();
  if (!person) { alert("请先填写你的名字"); $("person").focus(); return; }
  let ops;
  try {
    ops = JSON.parse($("alternateOps").value || "[]");
  } catch (e) {
    alert("替代边操作不是合法 JSON：" + e.message);
    return;
  }
  const aid = state.analysis.analysis_id;
  const cid = state.selectedCandidateId;
  const result = await api(
    "POST",
    `/api/analyses/${aid}/candidates/${cid}/opinions`,
    { person, role: "alternate", alternate_ops: ops,
      comment: "提出替代边" }
  );
  alert(result.alternate_feasible
    ? "替代方案已记录，并通过无环机器校验。"
    : "替代方案已记录，但应用后仍有环，标记为不可行。");
  $("alternateOps").value = "";
  await refreshAfterOpinion();
}

async function selectFinal() {
  const person = personName();
  if (!person) { alert("请先填写你的名字"); $("person").focus(); return; }
  const aid = state.analysis.analysis_id;
  const cid = state.selectedCandidateId;
  const result = await api(
    "POST", `/api/analyses/${aid}/candidates/${cid}/select`,
    { person }
  );
  alert(result.status === "current"
    ? "已批准并选择（当前有效）。"
    : "已记录，但图已变化，该批准仅作历史。");
  state.analysisDetail = await api("GET", "/api/analyses/" + aid);
  renderSelection();
  await refreshAfterOpinion();
}

async function refreshAfterOpinion() {
  const aid = state.analysis.analysis_id;
  state.analysisDetail = await api("GET", "/api/analyses/" + aid);
  await selectCandidate(state.selectedCandidateId);
}

// ---------- 导入与历史 ----------
async function importGraph() {
  let payload;
  try {
    payload = JSON.parse($("importArea").value);
  } catch (e) {
    $("importMsg").style.color = "#b91c1c";
    $("importMsg").textContent = "JSON 解析失败：" + e.message;
    return;
  }
  try {
    const result = await api("POST", "/api/import", payload);
    $("importMsg").style.color = "#15803d";
    $("importMsg").textContent =
      (result.is_new ? "已创建新版本 " : "内容相同，复用版本 ") +
      result.graph_version.slice(0, 12);
    state.analysis = null;
    state.analysisDetail = null;
    state.selectedCandidateId = null;
    state.candidateDetail = null;
    await loadState();
    renderCandidates();
    renderScc();
    document.querySelectorAll('#sccList').forEach((e) => (e.innerHTML = ""));
    $("sccList").innerHTML = '<div class="hint">图已更新，请重新生成分析。</div>';
    $("candidateDetail").classList.add("hidden");
  } catch (e) {
    $("importMsg").style.color = "#b91c1c";
    $("importMsg").textContent = e.message;
  }
}

async function showHistory() {
  const data = await api("GET", "/api/history");
  const box = $("historyBox");
  box.classList.toggle("hidden");
  box.textContent = JSON.stringify(data, null, 2);
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ---------- SVG 绘图 ----------
const SVG_NS = "http://www.w3.org/2000/svg";

function computeLayout(graph, cyclicSet, width, height) {
  const comps = graph.components;
  const nodes = {};
  const cx = width / 2, cy = height / 2;
  const n = Math.max(comps.length, 1);
  const radius = Math.min(width, height) * 0.36;
  comps.forEach((c, i) => {
    const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
    nodes[c.id] = {
      id: c.id, x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      vx: 0, vy: 0, comp: c,
    };
  });
  // 确定性的小型力导向迭代
  const edges = graph.edges
    .filter((e) => nodes[e.src] && nodes[e.target]);
  for (let iter = 0; iter < 260; iter++) {
    Object.values(nodes).forEach((a) => {
      Object.values(nodes).forEach((b) => {
        if (a.id === b.id) return;
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy + 0.01;
        const force = 5200 / dist2;
        a.vx += (dx / Math.sqrt(dist2)) * force;
        a.vy += (dy / Math.sqrt(dist2)) * force;
      });
    });
    edges.forEach((e) => {
      const a = nodes[e.src], b = nodes[e.target];
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const target = 130;
      const force = (dist - target) * 0.02;
      a.vx += (dx / dist) * force;
      a.vy += (dy / dist) * force;
      b.vx -= (dx / dist) * force;
      b.vy -= (dy / dist) * force;
    });
    Object.values(nodes).forEach((nd) => {
      nd.vx *= 0.82; nd.vy *= 0.82;
      nd.x += nd.vx; nd.y += nd.vy;
      nd.x = Math.max(70, Math.min(width - 70, nd.x));
      nd.y = Math.max(40, Math.min(height - 40, nd.y));
    });
  }
  return nodes;
}

function currentViewEdges() {
  if (!state.graphData || !state.analysis) return [];
  const wanted = new Set(state.analysis.types);
  return state.graphData.graph.edges.filter((e) => wanted.has(e.type));
}

function renderGraph() {
  const canvas = $("graphCanvas");
  canvas.innerHTML = "";
  if (!state.graphData) return;
  const graph = state.graphData.graph;
  const width = Math.max(canvas.clientWidth || 700, 320);
  const height = Math.max(canvas.clientHeight || 500, 320);

  const cyclicSet = new Set();
  if (state.analysis) {
    state.analysis.cyclic_components.forEach((c) =>
      c.forEach((id) => cyclicSet.add(id)));
  }

  const nodes = computeLayout(graph, cyclicSet, width, height);

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);

  // SCC 背景圈
  if (state.analysis) {
    state.analysis.cyclic_components.forEach((comp) => {
      const pts = comp.map((id) => nodes[id]).filter(Boolean);
      if (pts.length < 2) return;
      const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
      const minX = Math.min(...xs) - 34, minY = Math.min(...ys) - 30;
      const w = Math.max(...xs) - minX + 34;
      const h = Math.max(...ys) - minY + 30;
      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("x", minX);
      rect.setAttribute("y", minY);
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.setAttribute("class", "scc-bg");
      svg.appendChild(rect);
    });
  }

  // 高亮集合
  const cutEdges = new Set();
  const pathHitEdges = new Set();
  const reachEdges = new Set();
  const addedSpecs = [];
  const d = state.candidateDetail;
  if (d) {
    d.removed_edge_ids.forEach((id) => cutEdges.add(id));
    if (state.togglePaths) {
      // affected_paths 以边 id 存储，转换节点路径时推导不到边 id，
      // 因此直接使用候选的 affected_paths（通过详情中的 ops 外信息不可得，
      // 这里改用节点路径与图边匹配）。
      markPathsOnGraph(graph, d, pathHitEdges);
    }
    d.added_edges.forEach((e) => addedSpecs.push(e));
    if (state.toggleReach && d.reachable_after) {
      const reach = d.reachable_after;
      currentViewEdges().forEach((e) => {
        if (cutEdges.has(e.id)) return;
        const targets = reach[e.src] || [];
        if (targets.includes(e.target)) reachEdges.add(e.id);
      });
    }
  }

  // 画边（当前分析类型视图；无分析时画全部边）
  const viewEdgeIds = new Set(currentViewEdges().map((e) => e.id));
  const offset = {};
  graph.edges.forEach((e) => {
    const key = e.src + "|" + e.target;
    offset[key] = (offset[key] || 0);
  });
  const seenPair = {};
  graph.edges.forEach((e) => {
    if (state.analysis && !viewEdgeIds.has(e.id)) return;
    const a = nodes[e.src], b = nodes[e.target];
    if (!a || !b) return;
    const pairKey = [e.src, e.target].sort().join("|");
    seenPair[pairKey] = seenPair[pairKey] || 0;
    const parallelIndex = seenPair[pairKey];
    seenPair[pairKey]++;
    const path = document.createElementNS(SVG_NS, "path");
    let cls = "edge " + e.type;
    if (cutEdges.has(e.id)) cls += " cut";
    else if (pathHitEdges.has(e.id)) cls += " path-hit";
    else if (state.toggleReach && reachEdges.has(e.id)) cls += " reach";
    path.setAttribute("class", cls);
    path.setAttribute("d", edgePathD(a, b, e.src === e.target, parallelIndex));

    const mid = edgeMid(a, b, e.src === e.target, parallelIndex);
    path.appendChild(document.createElementNS(SVG_NS, "title")).textContent =
      `${e.id} [${e.type}] ${e.src}→${e.target}${e.note ? "：" + e.note : ""}`;
    svg.appendChild(path);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", mid.x);
    label.setAttribute("y", mid.y);
    label.setAttribute("class", "edge-label");
    label.textContent = e.id;
    svg.appendChild(label);
  });

  // 新增边（候选引入/反转）紫色虚线
  addedSpecs.forEach((e) => {
    const a = nodes[e.src], b = nodes[e.target];
    if (!a || !b) return;
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "edge added");
    path.setAttribute("d", edgePathD(a, b, false, 0));
    path.appendChild(document.createElementNS(SVG_NS, "title")).textContent =
      `${e.id} [新增 ${e.type}] ${e.src}→${e.target}`;
    svg.appendChild(path);
  });

  // 画节点
  graph.components.forEach((c) => {
    const p = nodes[c.id];
    const g = document.createElementNS(SVG_NS, "g");
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", p.x - 44);
    rect.setAttribute("y", p.y - 18);
    rect.setAttribute("width", 88);
    rect.setAttribute("height", 36);
    rect.setAttribute("rx", 7);
    let cls = "node-rect";
    if (c.kind === "interface") cls += " interface";
    if (cyclicSet.has(c.id)) cls += " cyclic";
    rect.setAttribute("class", cls);
    g.appendChild(rect);

    const t1 = document.createElementNS(SVG_NS, "text");
    t1.setAttribute("x", p.x);
    t1.setAttribute("y", p.y - 1);
    t1.setAttribute("text-anchor", "middle");
    t1.setAttribute("class", "node-label");
    t1.textContent = c.id;
    g.appendChild(t1);

    const t2 = document.createElementNS(SVG_NS, "text");
    t2.setAttribute("x", p.x);
    t2.setAttribute("y", p.y + 12);
    t2.setAttribute("text-anchor", "middle");
    t2.setAttribute("class", "owner-label");
    t2.textContent = `${c.owner}·w${c.weight}`;
    g.appendChild(t2);
    svg.appendChild(g);
  });

  canvas.appendChild(svg);
  renderLegend();
}

function markPathsOnGraph(graph, detail, hitSet) {
  // 用节点路径在当前图里匹配边（平行边全部命中）
  const byPair = {};
  graph.edges.forEach((e) => {
    const k = e.src + ">" + e.target;
    (byPair[k] = byPair[k] || []).push(e.id);
  });
  detail.affected_node_paths.forEach((nodesPath) => {
    for (let i = 0; i < nodesPath.length - 1; i++) {
      const ids = byPair[nodesPath[i] + ">" + nodesPath[i + 1]] || [];
      ids.forEach((id) => hitSet.add(id));
    }
  });
}

function edgePathD(a, b, selfLoop, idx) {
  if (selfLoop) {
    return `M ${a.x - 8} ${a.y - 16} C ${a.x - 46} ${a.y - 60}, ` +
           `${a.x + 46} ${a.y - 60}, ${a.x + 8} ${a.y - 16}`;
  }
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / len, uy = dy / len;
  const bow = (idx % 2 === 0 ? 1 : -1) * (Math.floor(idx / 2) + 1) * 26;
  const mx = (a.x + b.x) / 2 - uy * bow;
  const my = (a.y + b.y) / 2 + ux * bow;
  return `M ${a.x + ux * 46} ${a.y + uy * 18} ` +
         `Q ${mx} ${my} ${b.x - ux * 46} ${b.y - uy * 18}`;
}

function edgeMid(a, b, selfLoop, idx) {
  if (selfLoop) return { x: a.x, y: a.y - 58 };
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const ux = dx / len, uy = dy / len;
  const bow = (idx % 2 === 0 ? 1 : -1) * (Math.floor(idx / 2) + 1) * 26;
  return {
    x: (a.x + b.x) / 2 - uy * bow * 0.45,
    y: (a.y + b.y) / 2 + ux * bow * 0.45,
  };
}

function renderLegend() {
  $("legend").innerHTML =
    EDGE_TYPES.map((t) =>
      `<span><span class="dot dot-${t}"></span> ${TYPE_LABEL[t]}</span>`
    ).join("") +
    `<span><span style="color:#dc2626">━</span> 被切断</span>` +
    `<span><span style="color:#d97706">━</span> 受影响路径</span>` +
    `<span><span style="color:#16a34a">━</span> 变更后仍可达</span>` +
    `<span><span style="color:#7e3af2">┅</span> 新增/反转边</span>`;
}

// ---------- 初始化 ----------
window.addEventListener("DOMContentLoaded", async () => {
  renderTypeFilters();
  const savedPerson = localStorage.getItem("cycleaccord_person");
  if (savedPerson) $("person").value = savedPerson;

  $("btnAnalyze").onclick = runAnalyze;
  $("btnImport").onclick = importGraph;
  $("btnHistory").onclick = showHistory;
  $("btnAccept").onclick = () => submitOpinion("accept");
  $("btnReject").onclick = () => submitOpinion("reject");
  $("btnAlternate").onclick = submitAlternate;
  $("btnSelect").onclick = selectFinal;
  $("toggleReach").onchange = (e) => {
    state.toggleReach = e.target.checked; renderGraph();
  };
  $("togglePaths").onchange = (e) => {
    state.togglePaths = e.target.checked; renderGraph();
  };
  window.addEventListener("resize", () => renderGraph());

  try {
    await loadState();
    // 默认视图 hard + runtime（自环的 generated/test 默认不参与）
    document.querySelectorAll('#typeFilters input').forEach((el) => {
      el.checked = (el.value === "hard" || el.value === "runtime");
    });
    await runAnalyze();
  } catch (e) {
    alert("初始化失败：" + e.message);
  }
});
