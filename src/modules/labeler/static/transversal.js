'use strict';

const SOURCE_ORDER = ['e14t', 'e14d', 'e14c'];
const SOURCE_LABELS = {
  e14t: 'E14T (Testigo)',
  e14d: 'E14D (Delegado)',
  e14c: 'E14C (Oficial)',
};
const SOURCE_COLORS = {
  e14t: '#2563eb',
  e14d: '#dc2626',
  e14c: '#16a34a',
};

const DEPT_NAMES = {
  '01': 'ANTIOQUIA', '03': 'ATLANTICO', '05': 'BOLIVAR', '07': 'BOYACA',
  '09': 'CALDAS', '11': 'CAUCA', '12': 'CESAR', '13': 'CORDOBA',
  '15': 'CUNDINAMARCA', '16': 'BOGOTA D.C', '17': 'CHOCO', '19': 'HUILA',
  '21': 'MAGDALENA', '23': 'NARIÑO', '24': 'RISARALDA', '25': 'NORTE DE SAN',
  '26': 'QUINDIO', '27': 'SANTANDER', '28': 'SUCRE', '29': 'TOLIMA',
  '31': 'VALLE', '40': 'ARAUCA', '44': 'CAQUETA', '46': 'CASANARE',
  '48': 'LA GUAJIRA', '50': 'GUAINIA', '52': 'META', '54': 'GUAVIARE',
  '56': 'SAN ANDRES', '60': 'AMAZONAS', '64': 'PUTUMAYO', '68': 'VAUPES',
  '72': 'VICHADA', '88': 'CONSULADOS',
};

let queueItems = [];
let currentIdx = 0;
let currentMesaKey = null;
let mesaDetail = null;
let searchTimer = null;
let sidebarFilter = 'all';

function countPending(alerts, decisions) {
  const human = (alerts && alerts.human) || [];
  return human.filter((h) => {
    const fd = (decisions && decisions[h.field]) || {};
    return (h.sources || []).some((s) => !fd[s.src]);
  }).length;
}

function computeProgress(alerts, decisions) {
  const human = (alerts && alerts.human) || [];
  let total = 0;
  let decided = 0;
  for (const h of human) {
    const fd = (decisions && decisions[h.field]) || {};
    for (const s of (h.sources || [])) {
      total += 1;
      if (fd[s.src]) decided += 1;
    }
  }
  return total ? decided / total : 1;
}

function getMesaStatus(item) {
  if (!item) return 'pending';
  if ((item.pending_human_count || 0) > 0) return 'pending';
  if ((item.decision_progress || 0) >= 1) return 'done';
  return 'pending';
}

function getVisibleIndices() {
  return queueItems
    .map((item, idx) => ({ idx, status: getMesaStatus(item) }))
    .filter((x) => sidebarFilter === 'all' || x.status === sidebarFilter)
    .map((x) => x.idx);
}

async function fetchQueueAll() {
  const items = [];
  let page = 1;
  const pageSize = 200;
  const dept = document.getElementById('dept-filter').value;
  const q = document.getElementById('search').value.trim();

  while (true) {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (dept) params.set('dept', dept);
    if (q) params.set('q', q);

    const resp = await fetch(`/api/transversal/queue?${params}`);
    if (!resp.ok) {
      const detail = await resp.text().catch(() => '');
      throw new Error(`queue ${resp.status}${detail ? `: ${detail.slice(0, 120)}` : ''}`);
    }
    const data = await resp.json();
    items.push(...(data.items || []));
    if (items.length >= data.total || !(data.items || []).length) break;
    page += 1;
  }
  return items;
}

function updateStatsBar() {
  const total = queueItems.length;
  const pending = queueItems.filter((i) => getMesaStatus(i) === 'pending').length;
  const done = queueItems.filter((i) => getMesaStatus(i) === 'done').length;

  document.getElementById('stat-total').textContent = String(total);
  document.getElementById('stat-pending').textContent = String(pending);
  document.getElementById('stat-done').textContent = String(done);

  const allN = document.getElementById('sf-all-n');
  const pendingN = document.getElementById('sf-pending-n');
  const doneN = document.getElementById('sf-done-n');
  if (allN) allN.textContent = String(total);
  if (pendingN) pendingN.textContent = String(pending);
  if (doneN) doneN.textContent = String(done);
}

function updateFilterButtons() {
  ['all', 'pending', 'done'].forEach((f) => {
    const btn = document.getElementById(`sf-${f}`);
    if (btn) btn.classList.toggle('active', f === sidebarFilter);
  });
}

function applySidebarFilter() {
  document.querySelectorAll('.nav-item').forEach((a) => {
    const idx = Number(a.dataset.idx);
    const status = getMesaStatus(queueItems[idx]);
    const hidden = sidebarFilter !== 'all' && status !== sidebarFilter;
    a.classList.toggle('nav-hidden', hidden);
  });
  const vis = getVisibleIndices();
  if (vis.length && !vis.includes(currentIdx)) {
    selectMesa(vis[0]);
  } else if (!vis.length) {
    document.getElementById('main').innerHTML = '<p class="empty-msg">Sin mesas en este filtro.</p>';
  }
  updateNavCounter();
}

function setSidebarFilter(filter) {
  sidebarFilter = filter;
  updateFilterButtons();
  applySidebarFilter();
}

function updateNavCounter() {
  const el = document.getElementById('nav-counter');
  if (!el) return;
  const vis = getVisibleIndices();
  const pos = vis.indexOf(currentIdx);
  el.textContent = vis.length
    ? `${pos >= 0 ? pos + 1 : 0} / ${vis.length}`
    : '0 / 0';
}

function renderSidebar() {
  const list = document.getElementById('sidebar-list');
  if (!list) return;
  if (!queueItems.length) {
    list.innerHTML = '<p class="empty-msg">Sin mesas en cola.</p>';
    return;
  }
  list.innerHTML = queueItems.map((item, idx) => {
    const deptName = DEPT_NAMES[item.dept] || item.dept;
    const short = `${deptName} / z${item.zona} p${item.puesto} m${item.mesa}`;
    const status = getMesaStatus(item);
    return `<a href="#" class="nav-item" data-idx="${idx}" data-mesa-key="${item.mesa_key}" data-dept="${item.dept}" onclick="selectMesa(${idx});return false;">
      <span class="nav-num">${String(idx + 1).padStart(2, '0')}</span>
      <span class="nav-label">${short}</span>
      <span class="nav-status ${status}" title="${status === 'done' ? 'Revisada' : 'Pendiente'}"></span>
    </a>`;
  }).join('');
  updateFilterButtons();
  applySidebarFilter();
}

function updateActiveNav() {
  document.querySelectorAll('.nav-item').forEach((a) => a.classList.remove('active'));
  const active = document.querySelector(`.nav-item[data-idx="${currentIdx}"]`);
  if (active) {
    active.classList.add('active');
    active.scrollIntoView({ block: 'nearest' });
  }
}

async function refreshQueue() {
  const list = document.getElementById('sidebar-list');
  if (list) list.innerHTML = '<p class="sidebar-loading">Cargando cola…</p>';
  queueItems = await fetchQueueAll();
  renderSidebar();
  updateStatsBar();
  if (!queueItems.length) {
    document.getElementById('main').innerHTML = '<p class="empty-msg">Sin mesas en cola.</p>';
    currentMesaKey = null;
    mesaDetail = null;
    renderAlerts(null);
    return;
  }
  const idx = Math.min(currentIdx, queueItems.length - 1);
  await selectMesa(idx);
}

async function selectMesa(idx) {
  if (idx < 0 || idx >= queueItems.length) return;
  currentIdx = idx;
  currentMesaKey = queueItems[idx].mesa_key;
  updateActiveNav();

  const main = document.getElementById('main');
  main.innerHTML = '<div class="loading">Cargando mesa…</div>';

  const resp = await fetch(`/api/transversal/mesa/${encodeURIComponent(currentMesaKey)}`);
  if (!resp.ok) {
    main.innerHTML = '<p class="empty-msg">Error al cargar mesa.</p>';
    return;
  }
  mesaDetail = await resp.json();
  renderMesaMain();
  renderAlerts(currentMesaKey);
}

function pageImageUrl(mesaKey, src, pageNum, ext) {
  const p = String(pageNum).padStart(2, '0');
  if (mesaDetail && mesaDetail.storage_base) {
    return `${mesaDetail.storage_base}/${mesaKey}/${src}_p${p}.${ext}`;
  }
  return `/api/transversal/mesa/${encodeURIComponent(mesaKey)}/page/${src}/${pageNum}?fmt=${ext}`;
}

function pageImageHtml(mesaKey, src, pageNum, label) {
  const webp = pageImageUrl(mesaKey, src, pageNum, 'webp');
  const jpg = pageImageUrl(mesaKey, src, pageNum, 'jpg');
  const alt = `${label} p${pageNum}`;
  return `<img src="${webp}" data-fallback="${jpg}" loading="lazy" alt="${alt}"
    onerror="if(this.src!==this.dataset.fallback){this.src=this.dataset.fallback}">`;
}

function renderMesaMain() {
  const d = mesaDetail;
  const deptName = DEPT_NAMES[d.dept] || d.dept;
  const pages = d.pages || {};
  const maxPages = Math.max(0, ...Object.values(pages));
  const rows = [];

  for (let p = 1; p <= maxPages; p += 1) {
    const cells = SOURCE_ORDER.map((src) => {
      const color = SOURCE_COLORS[src];
      const label = SOURCE_LABELS[src];
      const count = pages[src] || 0;
      if (p <= count) {
        return `<div class="cell"><div class="src-label" style="background:${color}">${label}</div>
          ${pageImageHtml(d.mesa_key, src, p, label)}</div>`;
      }
      if (p === 1 && !count) {
        return `<div class="cell missing"><div class="src-label" style="background:${color}">${label}</div>
          <div class="no-pdf">PDF no disponible</div></div>`;
      }
      return '';
    }).filter(Boolean).join('');

    if (cells) {
      rows.push(`<div class="page-row"><div class="page-badge">Pág. ${p}</div><div class="page-cols">${cells}</div></div>`);
    }
  }

  document.getElementById('main').innerHTML = `
    <section class="mesa active">
      <div class="mesa-header">
        <div class="mesa-title">
          <span class="dept-badge">${deptName}</span>
          zona ${d.zona} · puesto ${d.puesto} · mesa ${d.mesa}
          <span class="mesa-meta">C1+C2=${d.candidate_votes ?? '—'}</span>
        </div>
        <div class="mesa-nav">
          <button type="button" onclick="goPrev()">← Anterior</button>
          <span id="nav-counter">—</span>
          <button type="button" onclick="goNext()">Siguiente →</button>
        </div>
      </div>
      ${rows.join('') || '<p class="empty-msg">Sin páginas disponibles.</p>'}
    </section>`;
  document.getElementById('main').scrollTop = 0;
  updateNavCounter();
}

function decisionEditMeta() {
  return (mesaDetail && mesaDetail.decision_edit) || {
    editable: true,
    editable_until: null,
    first_decision_at: null,
    decision_count: 0,
  };
}

function formatEditDeadline(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' });
  } catch (_e) {
    return iso;
  }
}

function renderAlerts(mesaKey) {
  const pkg = (mesaKey && mesaDetail && mesaDetail.alerts) || { human: [], auto: [] };
  const human = pkg.human || [];
  const auto = pkg.auto || [];
  const mesaDecisions = (mesaDetail && mesaDetail.decisions) || {};
  const editMeta = decisionEditMeta();

  function isPending(h) {
    const fd = mesaDecisions[h.field] || {};
    return (h.sources || []).some((s) => !fd[s.src]);
  }

  const pending = human.filter(isPending).length;
  const badge = document.getElementById('alert-count');
  badge.textContent = String(pending);
  badge.style.background = pending === 0 ? '#16a34a' : '#f59e0b';

  const body = document.getElementById('alert-body');
  let html = '';

  if (auto.length) {
    html += '<div class="alert-section-label">Auto-resueltas (sin tinta)</div>';
    html += auto.map((a) => `
      <div class="alert-item alert-auto">
        <div class="alert-item-header">
          <span class="alert-src-name">${a.field_label}</span>
          <span class="alert-auto-badge">AUTO</span>
        </div>
        <div class="alert-msg">${a.msg}</div>
      </div>`).join('');
  }

  if (!human.length) {
    if (!auto.length) html += '<div class="empty-msg">Sin alertas.</div>';
    body.innerHTML = html;
    return;
  }

  if (pending === 0 && human.length) {
    html += human.map((h) => {
      const fd = mesaDecisions[h.field] || {};
      return (h.sources || []).map((s) => {
        const dec = fd[s.src];
        if (!dec) return '';
        const icon = dec === 'accepted' ? '✓' : '✗';
        const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
        return `<div class="decided-row">
          <span style="color:${color}">${icon}</span>
          <span>${h.field_label} · ${s.label}</span>
        </div>`;
      }).join('');
    }).join('');
    if (editMeta.decision_count > 0 && editMeta.editable) {
      html += `<button type="button" class="btn-reopen" onclick="reopenMesa('${mesaKey}')">Reabrir (editar de nuevo)</button>`;
      html += `<div class="edit-locked">Editable hasta ${formatEditDeadline(editMeta.editable_until)}</div>`;
    } else if (editMeta.decision_count > 0) {
      html += '<div class="edit-locked">Cerrado — pasaron más de 3 h desde la primera decisión.</div>';
    }
    body.innerHTML = html;
    return;
  }

  const blankConfirm = human.every((h) => h.tier === 'blank_confirm' || h.class === 'confirmed_blank');
  const sectionLabel = blankConfirm
    ? `Blancos reales — confirmar (${pending} pendiente(s))`
    : `Revisión humana (${human.length} campo(s))`;
  html += `<div class="alert-section-label human">${sectionLabel}</div>`;
  if (!editMeta.editable && editMeta.decision_count > 0) {
    html += '<div class="edit-locked">Ventana de edición cerrada (3 h). No puedes guardar más cambios.</div>';
  } else if (editMeta.editable_until) {
    html += `<div class="edit-locked">Editable hasta ${formatEditDeadline(editMeta.editable_until)}</div>`;
  }
  html += human.map((h) => {
    const fd = mesaDecisions[h.field] || {};
    const srcRows = (h.sources || []).map((s) => {
      const dec = fd[s.src];
      if (dec) {
        const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
        return `<div class="decided-inline" style="color:${color}">${s.label}: ${dec === 'accepted' ? 'Aceptado' : 'Rechazado'}</div>`;
      }
      const disabled = (!editMeta.editable && editMeta.decision_count > 0) ? ' disabled' : '';
      return `<div class="alert-actions">
        <span class="alert-src-dot" style="background:${s.color}"></span>
        <span class="src-name">${s.label}</span>
        <button type="button" class="btn-accept"${disabled} onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','accepted')">✓</button>
        <button type="button" class="btn-reject"${disabled} onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','rejected')">✗</button>
      </div>`;
    }).join('');
    const tierBadge = h.tier === 'blank_confirm'
      ? '<span class="alert-auto-badge">BLANCO</span>'
      : `<span class="alert-class">${h.class}</span>`;
    return `<div class="alert-item">
      <div class="alert-item-header human">
        <span class="alert-src-name">${h.field_label}</span>
        ${tierBadge}
      </div>
      <div class="alert-msg">${h.msg}</div>
      ${srcRows}
    </div>`;
  }).join('');
  body.innerHTML = html;
}

async function saveDecision(mesaKey, field, src, decision) {
  const editMeta = decisionEditMeta();
  if (!editMeta.editable && editMeta.decision_count > 0) {
    alert('Ventana de edición cerrada (3 h desde la primera decisión).');
    return;
  }
  const resp = await fetch('/api/transversal/decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: mesaKey, field, source: src, decision }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    if (err.error === 'edit_window_expired') {
      alert('Ventana de edición cerrada (3 h desde la primera decisión).');
    } else {
      alert('Error al guardar decisión');
    }
    return;
  }
  const refresh = await fetch(`/api/transversal/mesa/${encodeURIComponent(mesaKey)}`);
  if (refresh.ok) {
    const data = await refresh.json();
    mesaDetail.decisions = data.decisions || {};
    mesaDetail.decision_edit = data.decision_edit || decisionEditMeta();
  } else {
    if (!mesaDetail.decisions) mesaDetail.decisions = {};
    if (!mesaDetail.decisions[field]) mesaDetail.decisions[field] = {};
    mesaDetail.decisions[field][src] = decision;
  }

  const item = queueItems.find((i) => i.mesa_key === mesaKey);
  if (item && mesaDetail.alerts) {
    item.decision_progress = computeProgress(mesaDetail.alerts, mesaDetail.decisions);
    item.pending_human_count = countPending(mesaDetail.alerts, mesaDetail.decisions);
  }
  renderAlerts(mesaKey);
  updateStatsBar();
  updateNavStatus();
  updateActiveNav();
}

function updateNavStatus() {
  document.querySelectorAll('.nav-item').forEach((a) => {
    const idx = Number(a.dataset.idx);
    const item = queueItems[idx];
    const dot = a.querySelector('.nav-status');
    if (!dot || !item) return;
    const status = getMesaStatus(item);
    dot.className = `nav-status ${status}`;
    dot.title = status === 'done' ? 'Revisada' : 'Pendiente';
  });
  updateStatsBar();
  applySidebarFilter();
}

async function reopenMesa(mesaKey) {
  const editMeta = decisionEditMeta();
  if (!editMeta.editable) {
    alert('Ya no puedes reabrir esta mesa (pasaron más de 3 h).');
    return;
  }
  if (!confirm('¿Borrar tus decisiones en esta mesa y volver a revisar?')) return;
  const resp = await fetch('/api/transversal/decisions/reopen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: mesaKey }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert(err.error === 'edit_window_expired'
      ? 'Ventana de edición cerrada (3 h).'
      : 'No se pudo reabrir la mesa.');
    return;
  }
  mesaDetail.decisions = {};
  mesaDetail.decision_edit = {
    editable: true,
    editable_until: null,
    first_decision_at: null,
    decision_count: 0,
  };
  const item = queueItems.find((i) => i.mesa_key === mesaKey);
  if (item && mesaDetail.alerts) {
    item.decision_progress = computeProgress(mesaDetail.alerts, {});
    item.pending_human_count = countPending(mesaDetail.alerts, {});
  }
  renderAlerts(mesaKey);
  updateNavStatus();
  updateActiveNav();
}

function goPrev() {
  const vis = getVisibleIndices();
  const pos = vis.indexOf(currentIdx);
  if (pos > 0) selectMesa(vis[pos - 1]);
}

function goNext() {
  const vis = getVisibleIndices();
  const pos = vis.indexOf(currentIdx);
  if (pos >= 0 && pos < vis.length - 1) selectMesa(vis[pos + 1]);
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

function toggleAlertPanel() {
  document.getElementById('alert-panel').classList.toggle('collapsed');
  const chevron = document.getElementById('alert-chevron');
  chevron.textContent = document.getElementById('alert-panel').classList.contains('collapsed') ? '▼' : '▲';
}

function exportDecisions() {
  window.location.href = '/api/transversal/decisions/export';
}

function onFilterChange() {
  currentIdx = 0;
  refreshQueue().catch((err) => console.error(err));
}

function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(onFilterChange, 350);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight') goNext();
  if (e.key === 'ArrowLeft') goPrev();
  if (e.key === 'Escape') {
    document.getElementById('dept-filter').value = '';
    document.getElementById('search').value = '';
    sidebarFilter = 'all';
    onFilterChange();
  }
});

window.selectMesa = selectMesa;
window.goPrev = goPrev;
window.goNext = goNext;
window.saveDecision = saveDecision;
window.reopenMesa = reopenMesa;
window.toggleSidebar = toggleSidebar;
window.toggleAlertPanel = toggleAlertPanel;
window.exportDecisions = exportDecisions;
window.onFilterChange = onFilterChange;
window.onSearchInput = onSearchInput;
window.setSidebarFilter = setSidebarFilter;

document.addEventListener('DOMContentLoaded', () => {
  refreshQueue().catch((err) => {
    console.error(err);
    const list = document.getElementById('sidebar-list');
    if (list) list.innerHTML = '<p class="empty-msg">Error cargando cola.</p>';
  });
});