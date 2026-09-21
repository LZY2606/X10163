"use strict";

const state = {
  state: null,
  analysis: null,
  selectedKey: null,
  discussion: null,
};

const SCC_COLORS = ["#2f7ed8", "#e8743b", "#8e44ad", "#16a085", "#c0392b"];
const TYPE_COLOR = {
  hard: "#33495f",
  runtime: "#2e7d8f",
  test: "#8a7f2c",
  generated: "#7a4fb0",
};

function $(id) { return document.getElementById(id); }

function selectedTypes() {
  return Array.from(document.querySelectorAll(".type-toggle:checked")).map(c => c.value);
}

async function api(path, options) {
  const resp = await fetch(path, options || {});
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || ("HTTP " + resp.status));
  }
  return data;
}

function showBanner(message, kind) {
  const banner = $("banner");
  banner.textContent = message;
  banner.className = "banner " + (kind || "err");
  setTimeout(() => banner.classList.add("hidden"), 6000);
}

// ---------------------------------------------------------------------------
// Load + render
// ---------------------------------------------------------------------------
async function loadState() {
  state.state = await api("/api/state");
  $("graph-version").textContent = state.state.graph_version;
}

async function runAnalysis() {
  const types = selectedTypes();
  if (types.length === 0) {
    showBanner("至少选择一种依赖类型", "warn");
    return;
  }
  try {
    state.analysis = await api("/api/analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_types: types }),
    });
    state.selectedKey = null;
    state.discussion = null;
    renderAll();
  } catch (err) {
    showBanner(err.message, "err");
  }
}

function renderAll() {
  renderSccSummary();
  renderCandidates();
  renderGraph();
  $("detail-panel").hidden = !state.selectedKey;
  if (state.selectedKey) renderDetail();
}

function renderSccSummary() {
  const box = $("scc-summary");
  const analysis = state.analysis;
  if (!analysis || !analysis.cyclic) {
    box.innerHTML = '<span class="pill">当前视图下没有依赖环，图是无环的（DAG）。</span>';
    return;
  }
  let html = "";
  analysis.sccs.forEach((scc) => {
    const color = SCC_COLORS[scc.index % SCC_COLORS.length];
    const label = scc.self_loop
      ? `SCC ${scc.index}（自环）`
      : `SCC ${scc.index}（${scc.members.length} 节点，${scc.cycles.length}+ 条环）`;
    html += `<span class="scc-chip" style="background:${color}">${label}: ${scc.members.join(", ")}</span>`;
    if (!scc.splittable) {
      html += `<div class="unsplittable">⚠ 无合法拆分：${scc.unsplittable_reason}</div>`;
    }
  });
  box.innerHTML = html;
}

function renderCandidates() {
  const box = $("candidate-list");
  const analysis = state.analysis;
  if (!analysis) {
    box.innerHTML = "<p>点击「重新计算强连通分量」生成分析。</p>";
    return;
  }
  if (!analysis.cyclic) {
    box.innerHTML = "<p>没有需要打破的环。</p>";
    return;
  }
  if (analysis.candidates.length === 0) {
    box.innerHTML = '<p class="unsplittable">不存在可打破全部环的合法候选变更集（边被锁定且没有登记接口可用）。</p>';
    return;
  }
  box.innerHTML = analysis.candidates.map((cand) => {
    const selected = cand.key === state.selectedKey ? " selected" : "";
    const ops = cand.actions.map(a =>
      `<span class="pill">${escapeHtml(a.summary)}</span>`
    ).join("");
    return `<div class="candidate-card${selected}" data-key="${cand.key}">
      <div><span class="cost">成本 ${cand.cost}</span>
      （基础 ${cand.cost_detail.base_change_cost} + 权重 ${cand.cost_detail.key_component_weight}
      + 跨负责人 ${cand.cost_detail.cross_owner_penalty}）</div>
      <div class="ops">${ops}</div>
      <div class="owners">需要确认的负责人：${cand.owners_required.length ? cand.owners_required.join(", ") : "（无）"}
      ｜ 影响 ${cand.affected_sccs.join(", ")}</div>
    </div>`;
  }).join("");
  box.querySelectorAll(".candidate-card").forEach(card => {
    card.addEventListener("click", () => selectCandidate(card.dataset.key));
  });
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

// ---------------------------------------------------------------------------
// SVG graph with deterministic force-directed layout
// ---------------------------------------------------------------------------
function buildDisplayGraph() {
  const graph = state.state.graph;
  const analysis = state.analysis;
  const types = analysis ? analysis.include_types : selectedTypes();
  const nodeById = {};
  graph.components.forEach(c => { nodeById[c.id] = c; });

  let edges = graph.edges.filter(e => types.includes(e.type));

  // When a candidate is selected, project its operations onto a client copy.
  let cutEdges = new Set();   // removed (interface replace)
  let changedEdges = new Set(); // reversed/weakened (leaves view)
  let newEdges = [];
  let interfaceNodes = new Set();
  let reachPaths = [];
  if (state.selectedKey) {
    const cand = analysis.candidates.find(c => c.key === state.selectedKey);
    edges = edges.filter(e => {
      let keep = true;
      cand.operations.forEach(op => {
        if ((op.op === "reverse" || op.op === "weaken") && op.edge_id === e.id) {
          changedEdges.add(e.id);
          if (op.op === "weaken") keep = false;
        }
        if (op.op === "introduce_interface") {
          const spec = graph.interfaces.find(i => i.id === op.interface_id);
          if (spec && spec.replaces.includes(e.id)) {
            cutEdges.add(e.id);
            keep = false;
          }
        }
      });
      return keep;
    });
    cand.operations.forEach(op => {
      if (op.op === "reverse") {
        const original = graph.edges.find(e => e.id === op.edge_id);
        if (original) {
          edges = edges.filter(e => e.id !== original.id);
          edges.push({
            id: original.id + ":rev",
            source: original.target,
            target: original.source,
            type: original.type,
            _reversedOf: original.id,
          });
        }
      }
      if (op.op === "introduce_interface") {
        const spec = graph.interfaces.find(i => i.id === op.interface_id);
        if (spec) {
          interfaceNodes.add(spec.id);
          spec.consumers.forEach(c => newEdges.push({
            id: `${spec.id}:if${c}`, source: c, target: spec.id, type: "hard", _interface: true,
          }));
        }
      }
    });
    edges = edges.concat(newEdges);
    cand.explanations.forEach(ex => {
      reachPaths = reachPaths.concat(ex.reachable_paths.map(p => p.path_edges));
    });
  }

  // SCC membership coloring (pre-operation membership from analysis).
  const sccOf = {};
  if (analysis) {
    analysis.sccs.forEach(scc => scc.members.forEach(m => { sccOf[m] = scc.index; }));
  }
  const cycleEdgeIds = new Set();
  if (analysis && !state.selectedKey) {
    analysis.sccs.forEach(scc => scc.cycles.forEach(c => c.edges.forEach(id => cycleEdgeIds.add(id))));
  }
  return {
    graph, edges, sccOf, cycleEdgeIds, cutEdges, changedEdges,
    interfaceNodes,
    reachEdgeIds: new Set(reachPaths.flat()),
    changedOriginalIds: changedEdges,
    cutOriginalIds: cutEdges,
  };
}

const SVG_NS = "http://www.w3.org/2000/svg";

function layout(graph, edges, width, height) {
  const ids = graph.components.map(c => c.id);
  edges.forEach(e => { if (!ids.includes(e.source)) ids.push(e.source); });
  edges.forEach(e => { if (!ids.includes(e.target)) ids.push(e.target); });
  // Deterministic circular seed positions.
  const positions = {};
  const n = Math.max(ids.length, 1);
  ids.forEach((id, i) => {
    const angle = (2 * Math.PI * i) / n;
    positions[id] = {
      x: width / 2 + Math.cos(angle) * Math.min(width, height) * 0.36,
      y: height / 2 + Math.sin(angle) * Math.min(width, height) * 0.36,
    };
  });
  const velocities = {};
  ids.forEach(id => { velocities[id] = { x: 0, y: 0 }; });
  const indexOf = {};
  ids.forEach((id, i) => { indexOf[id] = i; });

  for (let iter = 0; iter < 320; iter++) {
    const force = {};
    ids.forEach(id => { force[id] = { x: 0, y: 0 }; });
    // Repulsion
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = positions[ids[i]], b = positions[ids[j]];
        let dx = a.x - b.x, dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy;
        if (dist2 < 1) { dx = (i % 2 ? 1 : -1); dy = (j % 2 ? -1 : 1); dist2 = 2; }
        const rep = 9000 / dist2;
        const dist = Math.sqrt(dist2);
        force[ids[i]].x += (dx / dist) * rep;
        force[ids[i]].y += (dy / dist) * rep;
        force[ids[j]].x -= (dx / dist) * rep;
        force[ids[j]].y -= (dy / dist) * rep;
      }
    }
    // Springs
    edges.forEach(e => {
      const a = positions[e.source], b = positions[e.target];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const pull = (dist - 110) * 0.02;
      force[e.source].x += (dx / dist) * pull * 10;
      force[e.source].y += (dy / dist) * pull * 10;
      force[e.target].x -= (dx / dist) * pull * 10;
      force[e.target].y -= (dy / dist) * pull * 10;
    });
    // Gravity toward center
    ids.forEach(id => {
      force[id].x += (width / 2 - positions[id].x) * 0.01;
      force[id].y += (height / 2 - positions[id].y) * 0.01;
    });
    ids.forEach(id => {
      velocities[id].x = (velocities[id].x + force[id].x) * 0.82;
      velocities[id].y = (velocities[id].y + force[id].y) * 0.82;
      positions[id].x += velocities[id].x * 0.15;
      positions[id].y += velocities[id].y * 0.15;
      positions[id].x = Math.max(50, Math.min(width - 50, positions[id].x));
      positions[id].y = Math.max(34, Math.min(height - 34, positions[id].y));
    });
  }
  return positions;
}

function renderGraph() {
  const svg = $("graph");
  svg.innerHTML = "";
  if (!state.state) return;
  const display = buildDisplayGraph();
  const W = 900, H = 560;
  const positions = layout(display.graph, display.edges, W, H);

  const defs = document.createElementNS(SVG_NS, "defs");
  ["#9fb0c0", "#d98324", "#c0392b", "#1a7f4b", "#7a4fb0", "#33495f", "#2e7d8f", "#8a7f2c"].forEach((color, idx) => {
    const marker = document.createElementNS(SVG_NS, "marker");
    marker.setAttribute("id", `arrow${idx}`);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("orient", "auto-start-reverse");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    path.setAttribute("fill", color);
    marker.appendChild(path);
    defs.appendChild(marker);
  });
  svg.appendChild(defs);

  const edgeLayer = document.createElementNS(SVG_NS, "g");
  const labelLayer = document.createElementNS(SVG_NS, "g");
  svg.appendChild(edgeLayer);
  svg.appendChild(labelLayer);

  const arrowIndex = {
    normal: 0, cycle: 1, cut: 2, reach: 3, interface: 4,
  };

  display.edges.forEach(edge => {
    const p1 = positions[edge.source], p2 = positions[edge.target];
    if (!p1 || !p2) return;
    const path = document.createElementNS(SVG_NS, "path");
    let d, midX, midY;
    if (edge.source === edge.target) {
      d = `M ${p1.x} ${p1.y - 14} C ${p1.x + 34} ${p1.y - 52}, ${p1.x + 34} ${p1.y + 20}, ${p1.x} ${p1.y + 14}`;
      midX = p1.x + 38; midY = p1.y - 16;
    } else {
      // slight curvature for parallel edges, deterministic by edge id
      const parallels = display.edges.filter(
        other => (other.source === edge.source && other.target === edge.target)
          || (other.target === edge.source && other.source === edge.target)
      );
      const sameDirection = parallels.filter(o => o.source === edge.source && o.target === edge.target);
      let bend = 0;
      if (sameDirection.length > 1) {
        const order = sameDirection.map(e => e.id).sort().indexOf(edge.id);
        bend = (order - (sameDirection.length - 1) / 2) * 26;
      }
      const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
      const dx = p2.x - p1.x, dy = p2.y - p1.y;
      const len = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
      d = `M ${p1.x} ${p1.y} Q ${cx} ${cy} ${p2.x} ${p2.y}`;
      midX = cx; midY = cy;
    }
    path.setAttribute("d", d);
    let cssClass = "edge";
    let marker = arrowIndex.normal;
    const originalId = edge._reversedOf || edge.id;
    if (display.cutOriginalIds.has(edge.id) || display.cutOriginalIds.has(originalId)) {
      cssClass += " cut"; marker = arrowIndex.cut;
    } else if (display.changedOriginalIds.has(originalId)) {
      cssClass += " changed"; marker = arrowIndex.cut;
    } else if (display.reachEdgeIds.has(edge.id) || display.reachEdgeIds.has(originalId)) {
      cssClass += " reach"; marker = arrowIndex.reach;
    } else if (edge._interface) {
      cssClass += " interface"; marker = arrowIndex.interface;
    } else if (display.cycleEdgeIds.has(edge.id)) {
      cssClass += " cycle"; marker = arrowIndex.cycle;
    }
    path.setAttribute("class", cssClass);
    path.setAttribute("marker-end", `url(#arrow${marker})`);
    edgeLayer.appendChild(path);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", midX);
    label.setAttribute("y", midY - 2);
    label.setAttribute("class", "edge-label");
    label.setAttribute("text-anchor", "middle");
    label.textContent = `${edge._reversedOf || edge.id}:${edge.type}`;
    labelLayer.appendChild(label);
  });

  // Ghost lines for edges removed/weakened by the candidate (cut cycles).
  if (state.selectedKey) {
    const analysis = state.analysis;
    const cand = analysis.candidates.find(c => c.key === state.selectedKey);
    const affected = new Set();
    cand.explanations.forEach(ex => ex.broken_cycles.forEach(c => c.edges.forEach(id => affected.add(id))));
    display.graph.edges.forEach(edge => {
      if (!analysis.include_types.includes(edge.type)) return;
      if (!(affected.has(edge.id) || display.changedOriginalIds.has(edge.id))) return;
      if (display.changedOriginalIds.has(edge.id) &&
          cand.operations.some(o => o.op === "reverse" && o.edge_id === edge.id)) return;
      const p1 = positions[edge.source], p2 = positions[edge.target];
      if (!p1 || !p2) return;
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", `M ${p1.x} ${p1.y} L ${p2.x} ${p2.y}`);
      path.setAttribute("class", "edge cut");
      path.setAttribute("stroke-dasharray", "2 6");
      path.setAttribute("marker-end", `url(#arrow${arrowIndex.cut})`);
      edgeLayer.appendChild(path);
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", (p1.x + p2.x) / 2);
      label.setAttribute("y", (p1.y + p2.y) / 2 - 3);
      label.setAttribute("class", "edge-label");
      label.textContent = `✗${edge.id}`;
      labelLayer.appendChild(label);
    });
  }

  // Nodes
  display.graph.components.forEach(comp => {
    const p = positions[comp.id];
    if (!p) return;
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "node");
    g.setAttribute("transform", `translate(${p.x - 44},${p.y - 18})`);
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("width", "88");
    rect.setAttribute("height", "36");
    rect.setAttribute("rx", "6");
    let fill = "#e9eef4";
    if (display.sccOf[comp.id] !== undefined) {
      fill = SCC_COLORS[display.sccOf[comp.id] % SCC_COLORS.length] + "33";
    }
    rect.setAttribute("fill", fill);
    g.appendChild(rect);
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${comp.id} / ${comp.name} / 负责人:${comp.owner || "?"} / 权重:${comp.weight}`;
    g.appendChild(title);
    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("x", "44");
    text.setAttribute("y", "16");
    text.setAttribute("text-anchor", "middle");
    text.textContent = comp.name || comp.id;
    g.appendChild(text);
    const sub = document.createElementNS(SVG_NS, "text");
    sub.setAttribute("x", "44");
    sub.setAttribute("y", "30");
    sub.setAttribute("class", "owner");
    sub.setAttribute("text-anchor", "middle");
    sub.textContent = `${comp.id} · ${comp.owner || "无主"} · w${comp.weight}`;
    g.appendChild(sub);
    svg.appendChild(g);
  });

  // Interface nodes introduced by the selected candidate
  display.interfaceNodes.forEach(id => {
    const p = positions[id];
    if (!p) return;
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("transform", `translate(${p.x - 44},${p.y - 18})`);
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("width", "88");
    rect.setAttribute("height", "36");
    rect.setAttribute("rx", "18");
    rect.setAttribute("fill", "#efe7f8");
    rect.setAttribute("stroke", "#7a4fb0");
    g.appendChild(rect);
    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("x", "44");
    text.setAttribute("y", "22");
    text.setAttribute("text-anchor", "middle");
    text.textContent = "接口:" + id;
    g.appendChild(text);
    svg.appendChild(g);
  });
}

// ---------------------------------------------------------------------------
// Candidate detail + negotiation
// ---------------------------------------------------------------------------
async function selectCandidate(key) {
  state.selectedKey = key;
  renderCandidates();
  renderGraph();
  $("detail-panel").hidden = false;
  await loadDiscussion();
  renderDetail();
}

function currentCandidate() {
  return state.analysis.candidates.find(c => c.key === state.selectedKey);
}

function renderDetail() {
  const cand = currentCandidate();
  if (!cand) return;
  const box = $("candidate-detail");
  let html = `
    <div class="explain">
      <p><b>候选 ${cand.key}</b>，总成本 <b>${cand.cost}</b>：
      基础变更成本 ${cand.cost_detail.base_change_cost}，关键组件权重 ${cand.cost_detail.key_component_weight}，
      跨负责人 ${cand.cost_detail.cross_owner_penalty}（${cand.owners_required.join("、") || "单一负责人"}）。</p>
      <p><b>为什么足够：</b>以下每条边操作都可机器校验；把这组操作应用到图副本并按当前类型投影后，全图是 DAG（强连通分量校验，自环也算环），因此所有环都被切断。</p>
      <pre class="json">${escapeHtml(JSON.stringify(cand.operations, null, 2))}</pre>
  `;
  cand.explanations.forEach(ex => {
    html += `<h3>${ex.scc}：${ex.members.join(", ")}</h3>`;
    html += `<p>${escapeHtml(ex.sufficiency)}</p>`;
    html += `<p><b>被切断的环路（${ex.broken_cycles.length} 条）：</b></p><table>
      <tr><th>环路边</th><th>节点路径</th></tr>`;
    ex.broken_cycles.forEach(c => {
      html += `<tr><td>${c.edges.map(escapeHtml).join(" → ")}</td>
        <td>${c.nodes.map(escapeHtml).join(" → ")}</td></tr>`;
    });
    html += `</table>`;
    if (ex.reachable_paths.length) {
      html += `<p><b>仍可达路径（依赖关系通过其他路径保留）：</b></p><table>
        <tr><th>原边</th><th>新路径</th></tr>`;
      ex.reachable_paths.forEach(p => {
        html += `<tr><td>${escapeHtml(p.original_edge)}（${escapeHtml(p.from)}→${escapeHtml(p.to)}）</td>
          <td>${p.path_edges.map(escapeHtml).join(" → ")}</td></tr>`;
      });
      html += `</table>`;
    } else {
      html += `<p><b>仍可达路径：</b>被切断边的端点之间在当前视图中不再有向可达路径。</p>`;
    }
    html += `<p><b>需要确认的负责人：</b>${ex.owners_required.map(o => `<span class="pill">${escapeHtml(o)}</span>`).join("") || "（无）"}</p>`;
  });
  html += `</div>`;
  box.innerHTML = html;
  renderOpinions();
  renderDecision();
  renderVerify(cand);
}

function renderOpinions() {
  const box = $("opinion-list");
  if (!state.discussion) { box.innerHTML = ""; return; }
  if (state.discussion.opinions.length === 0) {
    box.innerHTML = "<p>暂无意见。</p>";
    return;
  }
  box.innerHTML = state.discussion.opinions.map(op => `
    <div class="opinion-item ${op.kind}">
      <div><b>#${op.version}</b> ${escapeHtml(op.author)} · ${
        { accept: "接受", reject: "拒绝", alternative: "提出替代边" }[op.kind]
      } · ${escapeHtml(op.created_at)}
      ${op.stale ? '<span class="stale">（历史：基于旧图版本 ' + op.graph_version + '）</span>' : ""}</div>
      ${op.comment ? `<div>${escapeHtml(op.comment)}</div>` : ""}
      ${op.alternative_ops && op.alternative_ops.length
        ? `<pre class="json">${escapeHtml(JSON.stringify(op.alternative_ops, null, 2))}</pre>` : ""}
    </div>`).join("");
}

function renderDecision() {
  const box = $("decision-box");
  const decision = state.discussion && state.discussion.latest_decision;
  if (!decision) {
    box.innerHTML = "<p>尚未批准。</p>";
    return;
  }
  box.innerHTML = `<div class="opinion-item accept">
    <div><b>${decision.stale ? "历史批准（图已变更，仅作历史）" : "当前有效批准"}</b></div>
    <div>批准人：${escapeHtml(decision.decided_by)} ｜ 图版本：${decision.graph_version}
    ｜ 时间：${escapeHtml(decision.created_at)}</div>
    ${decision.note ? `<div>${escapeHtml(decision.note)}</div>` : ""}
  </div>`;
}

function renderVerify(cand) {
  const types = state.analysis.include_types;
  api("/api/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include_types: types, operations: cand.operations }),
  }).then(report => {
    $("verify-box").innerHTML = report.acyclic
      ? `<div class="banner ok">✔ 校验通过：应用 ${cand.operations.length} 条边操作到图副本后无环（候选键 ${report.candidate_key}）。</div>`
      : `<div class="banner err">✗ ${escapeHtml(report.errors.join("; ") || JSON.stringify(report.cyclic_components))}</div>`;
  }).catch(err => {
    $("verify-box").innerHTML = `<div class="banner err">${escapeHtml(err.message)}</div>`;
  });
}

async function loadDiscussion() {
  const types = state.analysis.include_types;
  const qs = types.map(t => "types=" + encodeURIComponent(t)).join("&");
  state.discussion = await api(
    `/api/discussion?candidate=${encodeURIComponent(state.selectedKey)}&${qs}`
  );
}

async function submitOpinion(kind) {
  const author = $("opinion-author").value.trim();
  const comment = $("opinion-comment").value.trim();
  if (!author) { showBanner("请填写负责人名字", "warn"); return; }
  let alternativeOps = [];
  if (kind === "alternative") {
    const raw = $("alternative-ops").value.trim();
    if (!raw) { showBanner("提出替代边时必须提供边操作 JSON", "warn"); return; }
    try {
      alternativeOps = JSON.parse(raw);
    } catch (err) {
      showBanner("替代边 JSON 解析失败：" + err.message, "err");
      return;
    }
  }
  try {
    await api("/api/opinion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        include_types: state.analysis.include_types,
        candidate_key: state.selectedKey,
        author, kind, comment,
        alternative_ops: alternativeOps,
      }),
    });
    showBanner("意见已作为新版本保存（并发提交各自保留版本，不会互相覆盖）", "ok");
    await loadDiscussion();
    renderOpinions();
    renderDecision();
  } catch (err) {
    showBanner(err.message, "err");
  }
}

async function decide() {
  const decidedBy = $("decide-by").value.trim();
  if (!decidedBy) { showBanner("请填写批准人", "warn"); return; }
  try {
    await api("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        include_types: state.analysis.include_types,
        candidate_key: state.selectedKey,
        decided_by: decidedBy,
        note: "页面批准",
      }),
    });
    showBanner("已记录版本化批准；若之后导入新图，该批准仅保留为历史。", "ok");
    await loadDiscussion();
    renderDecision();
  } catch (err) {
    showBanner(err.message, "err");
  }
}

async function importGraph() {
  const raw = $("import-json").value.trim();
  if (!raw) { showBanner("请粘贴图 JSON", "warn"); return; }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    showBanner("JSON 解析失败：" + err.message, "err");
    return;
  }
  try {
    const result = await api("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph: payload, note: "页面导入" }),
    });
    showBanner("已保存为图版本 " + result.graph_version + "，旧批准变为历史。", "ok");
    await loadState();
    await runAnalysis();
  } catch (err) {
    showBanner(err.message, "err");
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".opinion").forEach(btn => {
    btn.addEventListener("click", () => submitOpinion(btn.dataset.kind));
  });
  $("btn-decide").addEventListener("click", decide);
  $("btn-analyze").addEventListener("click", runAnalysis);
  $("btn-import").addEventListener("click", importGraph);
  await loadState();
  await runAnalysis();
});
