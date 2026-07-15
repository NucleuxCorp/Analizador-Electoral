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

async function fetchQueueAll() {
  const items = [];
  let page = 1;
  const pageSize = 200;
  const dept = document.getElementById('dept-filter').value;
  const q = document.getElementById('search').value.trim();
  const pendingOnly = document.getElementById('pending-only').checked;

  while (true) {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (dept) params.set('dept', dept);
    if (q) params.set('q', q);
    if (pendingOnly) params.set('pending_only', '1');

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
  const pending = queueItems.filter((i) => i.pending_human_count > 0).length;
  const avgProgress = total
    ? queueItems.reduce((s, i) => s + (i.decision_progress || 0), 0) / total
    : 0;

  document.getElementById('stat-total').textContent = String(total);
  document.getElementById('stat-pending').textContent = String(pending);
  document.getElementById('stat-decided').textContent = String(Math.round(avgProgress * 100));
}

function renderSidebar() {
  const nav = document.getElementById('sidebar');
  nav.innerHTML = queueItems.map((item, idx) => {
    const deptName = DEPT_NAMES[item.dept] || item.dept;
    const short = `${deptName} / z${item.zona} p${item.puesto} m${item.mesa}`;
    const badge = item.pending_human_count > 0
      ? ' <span class="pending-dot" title="Pendiente"></span>'
      : '';
    return `<a href="#" class="nav-item" data-idx="${idx}" data-dept="${item.dept}" onclick="selectMesa(${idx});return false;">
      <span class="nav-num">${String(idx + 1).padStart(2, '0')}</span>
      <span class="nav-label">${short}${badge}</span>
    </a>`;
  }).join('');
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
  document.getElementById('sidebar').innerHTML = '<p class="sidebar-loading">Cargando cola…</p>';
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

function pageImageUrl(mesaKey, src, pageNum) {
  if (mesaDetail && mesaDetail.storage_base) {
    const p = String(pageNum).padStart(2, '0');
    return `${mesaDetail.storage_base}/${mesaKey}/${src}_p${p}.jpg`;
  }
  return `/api/transversal/mesa/${encodeURIComponent(mesaKey)}/page/${src}/${pageNum}`;
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
        const url = pageImageUrl(d.mesa_key, src, p);
        return `<div class="cell"><div class="src-label" style="background:${color}">${label}</div>
          <img src="${url}" loading="lazy" alt="${label} p${p}"></div>`;
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
          <span id="nav-counter">${currentIdx + 1} / ${queueItems.length}</span>
          <button type="button" onclick="goNext()">Siguiente →</button>
        </div>
      </div>
      ${rows.join('') || '<p class="empty-msg">Sin páginas disponibles.</p>'}
    </section>`;
  document.getElementById('main').scrollTop = 0;
}

function renderAlerts(mesaKey) {
  const pkg = (mesaKey && mesaDetail && mesaDetail.alerts) || { human: [], auto: [] };
  const human = pkg.human || [];
  const auto = pkg.auto || [];
  const mesaDecisions = (mesaDetail && mesaDetail.decisions) || {};

  function isPending(h) {
    const fd = mesaDecisions[h.field] || {};
    return (h.sources || []).some((s) => !fd[s.src]);
  }

  const pending = human.filter(isPending).length;
  const badge = document.getElementById('alert-count');
  badge.textContent = String(pending);
  badge.style.background = pending === 0 ? '#16a34a' : '#ef4444';

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

  if (pending === 0) {
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
    body.innerHTML = html;
    return;
  }

  html += `<div class="alert-section-label human">Revisión humana (${human.length} campo(s))</div>`;
  html += human.map((h) => {
    const fd = mesaDecisions[h.field] || {};
    const srcRows = (h.sources || []).map((s) => {
      const dec = fd[s.src];
      if (dec) {
        const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
        return `<div class="decided-inline" style="color:${color}">${s.label}: ${dec === 'accepted' ? 'Aceptado' : 'Rechazado'}</div>`;
      }
      return `<div class="alert-actions">
        <span class="alert-src-dot" style="background:${s.color}"></span>
        <span class="src-name">${s.label}</span>
        <button type="button" class="btn-accept" onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','accepted')">✓</button>
        <button type="button" class="btn-reject" onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','rejected')">✗</button>
      </div>`;
    }).join('');
    return `<div class="alert-item">
      <div class="alert-item-header human">
        <span class="alert-src-name">${h.field_label}</span>
        <span class="alert-class">${h.class}</span>
      </div>
      <div class="alert-msg">${h.msg}</div>
      ${srcRows}
    </div>`;
  }).join('');
  body.innerHTML = html;
}

async function saveDecision(mesaKey, field, src, decision) {
  const resp = await fetch('/api/transversal/decisions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: mesaKey, field, source: src, decision }),
  });
  if (!resp.ok) {
    alert('Error al guardar decisión');
    return;
  }
  if (!mesaDetail.decisions) mesaDetail.decisions = {};
  if (!mesaDetail.decisions[field]) mesaDetail.decisions[field] = {};
  mesaDetail.decisions[field][src] = decision;

  const item = queueItems.find((i) => i.mesa_key === mesaKey);
  if (item && mesaDetail.alerts) {
    item.decision_progress = computeProgress(mesaDetail.alerts, mesaDetail.decisions);
    item.pending_human_count = countPending(mesaDetail.alerts, mesaDetail.decisions);
  }
  renderAlerts(mesaKey);
  updateStatsBar();
  renderSidebar();
  updateActiveNav();
}

function goPrev() {
  if (currentIdx > 0) selectMesa(currentIdx - 1);
}

function goNext() {
  if (currentIdx < queueItems.length - 1) selectMesa(currentIdx + 1);
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
    document.getElementById('pending-only').checked = false;
    onFilterChange();
  }
});

window.selectMesa = selectMesa;
window.goPrev = goPrev;
window.goNext = goNext;
window.saveDecision = saveDecision;
window.toggleSidebar = toggleSidebar;
window.toggleAlertPanel = toggleAlertPanel;
window.exportDecisions = exportDecisions;
window.onFilterChange = onFilterChange;
window.onSearchInput = onSearchInput;

document.addEventListener('DOMContentLoaded', () => {
  refreshQueue().catch((err) => {
    console.error(err);
    document.getElementById('sidebar').innerHTML = '<p class="empty-msg">Error cargando cola.</p>';
  });
});