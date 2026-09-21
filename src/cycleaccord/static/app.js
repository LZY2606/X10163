"use strict";

const state = {
  graph: null,
  latestVersion: null,
  analyses: [],
  activeAnalysis: null,
  selectedCandidateId: null,
  includeTypes: ["hard", "runtime", "test"],
  geometry: { nodes: {}, edges: [] }
};

const TYPE_LABELS = { hard: "hard", runtime: "runtime", test: "test", generated: "generated" };

async function api(path, options) {
  const res = await fetch(path, options || {});
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || ("HTTP " + res.status));
  }
  return data;
}

function post(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

function edgeMap() {
  const m = new Map();
  (state.graph.edges || []).forEach(e => m.set(e.id, e));
  return m;
}

function nodeMap() {
  const m = new Map();
  (state.graph.nodes || []).forEach(n => m.set(n.id, n));
  return m;
}

function activeRecord() {
  return state.analyses.find(a => a.id === state.activeAnalysis);
}

function activePayload() {
  const rec = activeRecord();
  return rec ? rec.payload : null;
}

function selectedCandidate() {
  const payload = activePayload();
  if (!payload) return null;
  return payload.candidates.find(c => c.id === state.selectedCandidateId) || null;
}

// ---------------------------------------------------------------------------
// 布局：按 SCC 分簇，每簇圆周排点；非环节点放在右侧。
// ---------------------------------------------------------------------------

function computeLayout() {
  const nodes = nodeMap();
  const edges = edgeMap();
  const payload = activePayload();
  const sccs = payload ? payload.sccs : [];
  const inScc = new Set();
  sccs.flat().forEach(id => inScc.add(id));

  const positions = {};
  const clusterBoxes = [];
  const clusterOf = new Map();
  const clusterIndexOf = new Map();
  const margin = 70;
  const clusterGapX = 40;
  const clusterGapY = 40;
  let cursorY = margin;

  sccs.forEach((comp, ci) => {
    const n = comp.length;
    const r = Math.max(62, 34 + n * 13);
    const size = 2 * r + 70;
    const cx = margin + r + 35;
    const cy = cursorY + r + 35;
    comp.forEach((id, i) => {
      const angle = (Math.PI * 2 * i) / Math.max(n, 1) - Math.PI / 2;
      positions[id] = {
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle)
      };
      clusterOf.set(id, ci);
      clusterIndexOf.set(id, i);
    });
    clusterBoxes.push({ x: cx - r - 30, y: cy - r - 30, w: size, h: size, index: ci, nodes: comp });
    cursorY += size + clusterGapY;
  });

  // 非环节点纵向排在右侧
  const outsiders = [...nodes.keys()].filter(id => !inScc.has(id));
  const colX = Math.max(...(clusterBoxes.length ? clusterBoxes.map(b => b.x + b.w) : [300])) + 120;
  outsiders.forEach((id, i) => {
    positions[id] = { x: colX, y: margin + 30 + i * 95 };
  });

  const edgeGeoms = [];
  edges.forEach(e => {
    const p1 = positions[e.src];
    const p2 = positions[e.dst];
    if (p1 && p2) edgeGeoms.push({ edge: e, p1, p2 });
  });

  state.geometry = { nodes: positions, edges: edgeGeoms, clusterBoxes };
}

// ---------------------------------------------------------------------------
// SVG 绘制
// ---------------------------------------------------------------------------

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

function edgeClassification(edgeId, candidate) {
  if (!candidate) return "normal";
  if (candidate.cut_edge_ids.includes(edgeId)) return "cut";
  if (candidate.reachable_edge_ids.includes(edgeId)) return "reach";
  return "dim";
}

function renderGraph() {
  const svg = document.getElementById("graphSvg");
  svg.innerHTML = "";
  if (!state.graph) return;
  const nodes = nodeMap();
  computeLayout();

  const included = new Set(state.includeTypes);

  // 簇背景
  state.geometry.clusterBoxes.forEach(box => {
    svg.appendChild(svgEl("rect", {
      class: "scc-cluster",
      x: box.x, y: box.y, width: box.w, height: box.h, rx: 14
    }));
    const label = svgEl("text", { class: "scc-label", x: box.x + 12, y: box.y + 18 });
    label.textContent = "SCC #" + (box.index + 1) + "（" + box.nodes.length + " 节点）";
    svg.appendChild(label);
  });

  const candidate = selectedCandidate();

  defineMarkers(svg);

  // 先画边
  state.geometry.edges.forEach(g => {
    const e = g.edge;
    const excluded = !included.has(e.type);
    let cls = "edge";
    let markerKind = "normal";
    if (excluded) {
      cls += " excluded";
      markerKind = "excluded";
    } else {
      const kind = edgeClassification(e.id, candidate);
      if (kind === "cut") { cls += " cut"; markerKind = "cut"; }
      else if (kind === "reach") { cls += " reach"; markerKind = "reach"; }
      else if (kind === "dim") { cls += " dim"; markerKind = "dim"; }
    }
    let d;
    if (e.src === e.dst) {
      const p = g.p1;
      d = "M " + (p.x - 18) + " " + (p.y - 6) +
          " C " + (p.x - 46) + " " + (p.y - 40) + ", " +
                   (p.x + 46) + " " + (p.y - 40) + ", " +
                   (p.x + 18) + " " + (p.y - 6);
    } else {
      const dx = g.p2.x - g.p1.x;
      const dy = g.p2.y - g.p1.y;
      const len = Math.max(Math.hypot(dx, dy), 1);
      const ux = dx / len, uy = dy / len;
      const r = 30;
      const sx = g.p1.x + ux * r, sy = g.p1.y + uy * r;
      const tx = g.p2.x - ux * r, ty = g.p2.y - uy * r;
      const curve = 14;
      const cx1 = sx + (-uy) * curve;
      const cy1 = sy + (ux) * curve;
      const cx2 = tx + (-uy) * curve;
      const cy2 = ty + (ux) * curve;
      d = "M " + sx + " " + sy + " C " + cx1 + " " + cy1 + ", " + cx2 + " " + cy2 + ", " + tx + " " + ty;
    }
    const path = svgEl("path", { class: cls, d: d, "marker-end": "url(#arrow-" + markerKind + ")" });
    svg.appendChild(path);

    const lx = (g.p1.x + g.p2.x) / 2;
    const ly = (g.p1.y + g.p2.y) / 2 - 6;
    const label = svgEl("text", {
      class: "edge-label" + (edgeClassification(e.id, candidate) === "cut" && !excluded ? " cut"
              : edgeClassification(e.id, candidate) === "reach" && !excluded ? " reach" : ""),
      x: lx, y: ly, "text-anchor": "middle"
    });
    label.textContent = e.id + "·" + e.type;
    svg.appendChild(label);
  });

  // 候选新增的接口边（虚线黄）
  if (candidate) {
    candidate.ops.filter(op => op.kind === "interface").forEach(op => {
      const a = state.geometry.nodes[op.new_src];
      const b = state.geometry.nodes[op.new_dst];
      if (!a || !b) return;
      svg.appendChild(svgEl("path", {
        class: "edge iface-new",
        d: "M " + a.x + " " + a.y + " L " + b.x + " " + b.y
      }));
    });
  }

  // 节点
  nodes.forEach((n, id) => {
    const p = state.geometry.nodes[id];
    if (!p) return;
    const g = svgEl("g", { class: "node" + (n.critical ? " critical" : "") });
    g.appendChild(svgEl("rect", { x: p.x - 30, y: p.y - 17, width: 60, height: 34, rx: 7 }));
    const title = svgEl("text", { x: p.x, y: p.y - 1, "text-anchor": "middle" });
    title.textContent = n.id.length > 8 ? n.id.slice(0, 7) + "…" : n.id;
    g.appendChild(title);
    if (n.owner) {
      const own = svgEl("text", { class: "owner", x: p.x, y: p.y + 12, "text-anchor": "middle" });
      own.textContent = "@" + n.owner;
      g.appendChild(own);
    }
    svg.appendChild(g);
  });
}

function defineMarkers(svg) {
  const defs = svgEl("defs");
  const variants = [
    ["normal", "#68789a"], ["excluded", "#3a4459"],
    ["reach", "#46d39a"], ["cut", "#ff5d6c"], ["dim", "#3a4459"]
  ];
  variants.forEach(([name, color]) => {
    const marker = svgEl("marker", {
      id: "arrow-" + name, viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"
    });
    const path = svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color });
    marker.appendChild(path);
    defs.appendChild(marker);
  });
  svg.insertBefore(defs, svg.firstChild);
}

// ---------------------------------------------------------------------------
// 候选列表 / 详情
// ---------------------------------------------------------------------------

function renderTypeFilters() {
  const box = document.getElementById("typeFilters");
  box.innerHTML = "";
  ["hard", "runtime", "test", "generated"].forEach(t => {
    const id = "chk-" + t;
    const lbl = document.createElement("label");
    lbl.style.marginRight = "8px";
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.id = id;
    chk.checked = state.includeTypes.includes(t);
    chk.addEventListener("change", () => {
      state.includeTypes = ["hard", "runtime", "test", "generated"]
        .filter(x => document.getElementById("chk-" + x).checked);
    });
    lbl.appendChild(chk);
    lbl.appendChild(document.createTextNode(" " + TYPE_LABELS[t]));
    box.appendChild(lbl);
  });
}

function renderBanner() {
  const banner = document.getElementById("statusBanner");
  const payload = activePayload();
  const rec = activeRecord();
  if (!payload) {
    banner.className = "banner hidden";
    return;
  }
  const parts = [];
  if (rec && rec.stale) {
    parts.push("该分析基于图版本 v" + rec.graph_version +
      "，当前最新为 v" + state.latestVersion + "：旧批准仅作历史，已失效。");
    banner.className = "banner stale";
  } else if (payload.infeasible_sccs && payload.infeasible_sccs.length) {
    parts.push("存在无法合法拆分的 SCC：" +
      payload.infeasible_sccs.map(c => "{" + c.join(", ") + "}").join("、") +
      "。以下候选只覆盖可行动部分，未打破全部环。");
    banner.className = "banner warn";
  } else if (!payload.sccs.length) {
    parts.push("当前视图下没有强连通分量，图无环。");
    banner.className = "banner info";
  } else {
    parts.push("发现 " + payload.sccs.length + " 个强连通分量，已生成 " +
      payload.candidates.length + " 个候选（按成本与稳定顺序排序）。");
    banner.className = "banner info";
  }
  banner.textContent = parts.join(" ");
  banner.classList.remove("hidden");
}

function renderCandidateList() {
  const box = document.getElementById("candidateList");
  box.innerHTML = "";
  const payload = activePayload();
  if (!payload) return;
  if (!payload.candidates.length) {
    const p = document.createElement("div");
    p.className = "cand-card";
    p.textContent = payload.sccs.length ? "无合法拆分候选。" : "当前视图无环，无需候选。";
    box.appendChild(p);
    return;
  }
  payload.candidates.forEach(c => {
    const card = document.createElement("div");
    card.className = "cand-card" + (c.id === state.selectedCandidateId ? " selected" : "");
    const top = document.createElement("div");
    top.className = "cand-top";
    const left = document.createElement("span");
    left.textContent = c.id + " · " + c.ops.length + " 条操作";
    const cost = document.createElement("span");
    cost.className = "cand-cost";
    cost.textContent = "成本 " + c.cost;
    top.append(left, cost);
    card.appendChild(top);

    const desc = document.createElement("div");
    desc.className = "cand-ops";
    desc.textContent = c.ops.map(o =>
      o.kind === "reverse" ? "反转 " + o.edge_id
      : o.kind === "downgrade" ? "降级 " + o.edge_id + "→" + o.target_type
      : "接口 " + o.edge_id + "→" + o.interface_id
    ).join("；");
    card.appendChild(desc);

    const badges = document.createElement("div");
    badges.className = "cand-badges";
    c.owners.forEach(o => {
      const b = document.createElement("span");
      b.className = "badge owner";
      b.textContent = "@" + o;
      badges.appendChild(b);
    });
    if (!c.verified) {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = "未通过校验";
      badges.appendChild(b);
    }
    card.appendChild(badges);

    card.addEventListener("click", () => selectCandidate(c.id));
    box.appendChild(card);
  });
}

function selectCandidate(candidateId) {
  state.selectedCandidateId = candidateId;
  renderCandidateList();
  renderGraph();
  renderDetail();
}

function renderDetail() {
  const detail = document.getElementById("candidateDetail");
  const c = selectedCandidate();
  if (!c) {
    detail.classList.add("hidden");
    document.getElementById("cycleInfo").textContent =
      "选择一个候选以高亮被切断的环路与仍可达路径。";
    return;
  }
  detail.classList.remove("hidden");
  document.getElementById("detailMeta").innerHTML =
    "<strong>" + c.id + "</strong> · 成本 <strong>" + c.cost +
    "</strong> · 负责人：" + c.owners.map(o => "@" + o).join("、");

  document.getElementById("detailOps").textContent = JSON.stringify(
    c.ops.map(o => ({
      op: o.kind, edge: o.edge_id,
      target_type: o.target_type || undefined,
      interface: o.interface_id || undefined,
      new_edge: o.interface_id ? { src: o.new_src, dst: o.new_dst, type: o.new_type } : undefined
    })), null, 2);

  const rationale = document.getElementById("detailRationale");
  rationale.innerHTML = "";
  c.rationale.forEach(line => {
    const li = document.createElement("li");
    li.textContent = line;
    rationale.appendChild(li);
  });

  const cycles = c.broken_cycles.map(path => path.join(" → ")).join("\n");
  const reach = c.reachable_edge_ids.length;
  document.getElementById("cycleInfo").textContent =
    "被切断的环（边序列）：\n" + (cycles || "（无枚举环）") +
    "\n\n应用后仍在当前视图中的边：" + reach + " 条；" +
    "新增接口边：" + (c.added_edge_ids.join(", ") || "无") + "。";

  document.getElementById("verifyResult").textContent = "";
  renderOpinions();
}

function renderOpinions() {
  const box = document.getElementById("opinionList");
  box.innerHTML = "";
  const rec = activeRecord();
  const c = selectedCandidate();
  if (!rec || !c) return;
  const opinions = rec.opinions.filter(o => o.candidate_id === c.id);
  if (!opinions.length) {
    box.textContent = "暂无意见。";
    return;
  }
  opinions.forEach(o => {
    const item = document.createElement("div");
    item.className = "opinion-item " + o.decision + (o.graph_version !== state.latestVersion ? " stale" : "");
    const meta = document.createElement("div");
    meta.className = "opinion-meta";
    meta.textContent = "#" + o.seq + " @" + o.owner + " · " + o.decision +
      " · 基于图 v" + o.graph_version +
      (o.graph_version !== state.latestVersion ? "（已失效，仅历史）" : "");
    const body = document.createElement("div");
    body.textContent = o.comment || (o.alternative ? "替代边：" + o.alternative : "");
    item.append(meta, body);
    box.appendChild(item);
  });
}

// ---------------------------------------------------------------------------
// 交互
// ---------------------------------------------------------------------------

async function submitOpinion(decision) {
  const c = selectedCandidate();
  const rec = activeRecord();
  if (!c || !rec) return;
  const owner = document.getElementById("ownerName").value.trim();
  const comment = document.getElementById("commentText").value.trim();
  const alternative = document.getElementById("alternativeText").value.trim();
  try {
    await post("/api/opinions", {
      analysis_id: rec.id, candidate_id: c.id,
      owner, decision, comment, alternative
    });
    document.getElementById("commentText").value = "";
    document.getElementById("alternativeText").value = "";
    await refreshState();
    selectCandidate(c.id);
  } catch (err) {
    alert(err.message);
  }
}

async function approveCandidate() {
  const c = selectedCandidate();
  const rec = activeRecord();
  if (!c || !rec) return;
  const owner = document.getElementById("ownerName").value.trim();
  try {
    const result = await post("/api/approve", {
      analysis_id: rec.id, candidate_id: c.id, owner
    });
    alert(result.selection.current
      ? "已记录为当前最终选择（版本 #" + result.selection.seq + "）。"
      : "已记录为历史批准，但图已变更（最新 v" + result.selection.latest_graph_version +
        "），该批准对当前图失效。");
    await refreshState();
    selectCandidate(c.id);
  } catch (err) {
    alert(err.message);
  }
}

async function runAnalysis() {
  try {
    const result = await post("/api/analyses", { include_types: state.includeTypes });
    await refreshState();
    state.activeAnalysis = result.analysis.id;
    state.selectedCandidateId = null;
    renderAll();
  } catch (err) {
    alert(err.message);
  }
}

async function verifySelected() {
  const c = selectedCandidate();
  const rec = activeRecord();
  const out = document.getElementById("verifyResult");
  try {
    const result = await api("/api/verify?analysis_id=" + encodeURIComponent(rec.id) +
      "&candidate_id=" + encodeURIComponent(c.id));
    out.textContent = result.acyclic ? "✓ 应用到图副本后无环" : "✗ 仍存在环";
    out.className = result.acyclic ? "ok" : "bad";
  } catch (err) {
    out.textContent = err.message;
    out.className = "bad";
  }
}

async function importGraph(payload) {
  const result = await post("/api/import", { graph: payload });
  const analysis = await post("/api/analyses", {
    include_types: state.includeTypes, graph_version: result.graph_version
  });
  document.getElementById("importDialog").close();
  await refreshState();
  state.activeAnalysis = analysis.analysis.id;
  state.selectedCandidateId = null;
  renderAll();
}

// ---------------------------------------------------------------------------
// 状态与初始化
// ---------------------------------------------------------------------------

async function refreshState() {
  const data = await api("/api/state");
  state.graph = data.graph;
  state.latestVersion = data.latest_graph_version;
  state.analyses = data.analyses;
  if (!state.activeAnalysis || !state.analyses.some(a => a.id === state.activeAnalysis)) {
    const latest = state.analyses.filter(a => !a.stale).pop() || state.analyses[0];
    state.activeAnalysis = latest ? latest.id : null;
  }
  document.getElementById("graphVersion").textContent =
    state.latestVersion ? "图版本 v" + state.latestVersion : "无图";
}

function renderAll() {
  renderBanner();
  renderCandidateList();
  if (selectedCandidate()) {
    renderDetail();
  } else {
    document.getElementById("candidateDetail").classList.add("hidden");
  }
  renderGraph();
}

function bindEvents() {
  document.getElementById("analyzeBtn").addEventListener("click", runAnalysis);
  document.getElementById("verifyBtn").addEventListener("click", verifySelected);
  document.getElementById("approveBtn").addEventListener("click", approveCandidate);
  document.querySelectorAll(".opinion-buttons button[data-decision]").forEach(btn => {
    btn.addEventListener("click", () => submitOpinion(btn.dataset.decision));
  });

  const dialog = document.getElementById("importDialog");
  document.getElementById("importBtn").addEventListener("click", () => {
    document.getElementById("importError").textContent = "";
    document.getElementById("importText").value = JSON.stringify(state.graph, null, 2);
    dialog.showModal();
  });
  document.getElementById("importConfirm").addEventListener("click", async () => {
    const errBox = document.getElementById("importError");
    try {
      const parsed = JSON.parse(document.getElementById("importText").value);
      await importGraph(parsed);
    } catch (err) {
      errBox.textContent = "导入失败：" + err.message;
    }
  });
}

(async function init() {
  renderTypeFilters();
  bindEvents();
  await refreshState();
  renderAll();
})();
