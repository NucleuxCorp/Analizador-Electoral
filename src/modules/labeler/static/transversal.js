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

const PAGE_SIZE = 50;

let keyOrder = [];
let itemByKey = {};
let currentMesaKey = null;
let mesaDetail = null;
let searchTimer = null;
let sidebarFilter = 'all';
let queueObserver = null;
let queueMeta = {
  page: 0,
  total: 0,
  queueMesas: 0,
  pending: 0,
  done: 0,
  hasMore: false,
  loading: false,
};

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

function buildQueueParams(page) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PAGE_SIZE),
  });
  const dept = document.getElementById('dept-filter').value;
  const q = document.getElementById('search').value.trim();
  if (dept) params.set('dept', dept);
  if (q) params.set('q', q);
  if (sidebarFilter === 'pending') params.set('status', 'pending');
  else if (sidebarFilter === 'done') params.set('status', 'done');
  return params;
}

function navItemHtml(item, index) {
  const deptName = DEPT_NAMES[item.dept] || item.dept;
  const short = `${deptName} / z${item.zona} p${item.puesto} m${item.mesa}`;
  const status = getMesaStatus(item);
  return `<a href="#" class="nav-item" data-mesa-key="${item.mesa_key}" data-dept="${item.dept}" onclick="selectMesaByKey('${item.mesa_key}');return false;">
    <span class="nav-num">${String(index + 1).padStart(2, '0')}</span>
    <span class="nav-label">${short}</span>
    <span class="nav-status ${status}" title="${status === 'done' ? 'Revisada' : 'Pendiente'}"></span>
  </a>`;
}

function disconnectQueueObserver() {
  if (queueObserver) {
    queueObserver.disconnect();
    queueObserver = null;
  }
}

function ensureQueueSentinel() {
  const list = document.getElementById('sidebar-list');
  if (!list) return null;
  let sentinel = document.getElementById('queue-sentinel');
  if (!sentinel) {
    sentinel = document.createElement('div');
    sentinel.id = 'queue-sentinel';
    sentinel.className = 'sidebar-loading';
    sentinel.style.minHeight = '1px';
    list.appendChild(sentinel);
  }
  return sentinel;
}

function setupQueueObserver() {
  disconnectQueueObserver();
  const list = document.getElementById('sidebar-list');
  const sentinel = ensureQueueSentinel();
  if (!list || !sentinel || !queueMeta.hasMore) return;

  queueObserver = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) {
      loadNextQueuePage().catch((err) => console.error(err));
    }
  }, { root: list, rootMargin: '120px', threshold: 0 });

  queueObserver.observe(sentinel);
}

function appendQueueItems(items) {
  const list = document.getElementById('sidebar-list');
  if (!list || !items.length) return;

  const startIndex = keyOrder.length;
  const fragment = items.map((item, offset) => {
    itemByKey[item.mesa_key] = item;
    keyOrder.push(item.mesa_key);
    return navItemHtml(item, startIndex + offset);
  }).join('');

  const sentinel = document.getElementById('queue-sentinel');
  if (sentinel) {
    sentinel.insertAdjacentHTML('beforebegin', fragment);
  } else {
    list.insertAdjacentHTML('beforeend', fragment);
  }
}

async function loadNextQueuePage(reset = false) {
  if (queueMeta.loading) return;
  if (!reset && !queueMeta.hasMore) return;

  queueMeta.loading = true;
  const page = reset ? 1 : queueMeta.page + 1;

  try {
    const resp = await fetch(`/api/transversal/queue?${buildQueueParams(page)}`);
    if (!resp.ok) {
      const detail = await resp.text().catch(() => '');
      throw new Error(`queue ${resp.status}${detail ? `: ${detail.slice(0, 120)}` : ''}`);
    }
    const data = await resp.json();

    queueMeta.page = page;
    queueMeta.total = data.total || 0;
    queueMeta.queueMesas = data.queue_mesas ?? data.stats?.queue_mesas ?? queueMeta.total;
    queueMeta.pending = data.pending ?? data.stats?.pending_human ?? 0;
    queueMeta.done = data.done ?? Math.max(0, queueMeta.queueMesas - queueMeta.pending);
    queueMeta.hasMore = Boolean(
      data.has_more ?? (page * PAGE_SIZE < queueMeta.total),
    );

    if (reset) {
      keyOrder = [];
      itemByKey = {};
      const list = document.getElementById('sidebar-list');
      if (list) list.innerHTML = '';
    }

    appendQueueItems(data.items || []);
    ensureQueueSentinel();
    const sentinel = document.getElementById('queue-sentinel');
    if (sentinel) {
      sentinel.textContent = queueMeta.hasMore ? 'Cargando más…' : '';
      sentinel.style.display = queueMeta.hasMore ? 'block' : 'none';
    }

    updateStatsBar();
    updateFilterButtons();
    setupQueueObserver();

    if (reset) {
      if (!keyOrder.length) {
        document.getElementById('main').innerHTML = '<p class="empty-msg">Sin mesas en cola.</p>';
        currentMesaKey = null;
        mesaDetail = null;
        renderAlerts(null);
        return;
      }
      const keepKey = currentMesaKey && itemByKey[currentMesaKey]
        ? currentMesaKey
        : keyOrder[0];
      await selectMesaByKey(keepKey);
    }
  } finally {
    queueMeta.loading = false;
  }
}

async function resetQueue() {
  disconnectQueueObserver();
  const list = document.getElementById('sidebar-list');
  if (list) list.innerHTML = '<p class="sidebar-loading">Cargando cola…</p>';
  queueMeta.hasMore = true;
  await loadNextQueuePage(true);
}

function updateStatsBar() {
  const total = queueMeta.queueMesas;
  const pending = queueMeta.pending;
  const done = queueMeta.done;

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

function setSidebarFilter(filter) {
  if (sidebarFilter === filter) return;
  sidebarFilter = filter;
  currentMesaKey = null;
  updateFilterButtons();
  resetQueue().catch((err) => console.error(err));
}

function updateNavCounter() {
  const el = document.getElementById('nav-counter');
  if (!el) return;
  const pos = keyOrder.indexOf(currentMesaKey);
  el.textContent = keyOrder.length
    ? `${pos >= 0 ? pos + 1 : 0} / ${keyOrder.length}`
    : '0 / 0';
}

function updateActiveNav() {
  document.querySelectorAll('.nav-item').forEach((a) => a.classList.remove('active'));
  const active = document.querySelector(`.nav-item[data-mesa-key="${currentMesaKey}"]`);
  if (active) {
    active.classList.add('active');
    active.scrollIntoView({ block: 'nearest' });
  }
}

async function selectMesaByKey(mesaKey) {
  const item = itemByKey[mesaKey];
  if (!item) return;
  currentMesaKey = mesaKey;
  updateActiveNav();

  const main = document.getElementById('main');
  main.innerHTML = '<div class="loading">Cargando mesa…</div>';

  const resp = await fetch(`/api/transversal/mesa/${encodeURIComponent(mesaKey)}`);
  if (!resp.ok) {
    main.innerHTML = '<p class="empty-msg">Error al cargar mesa.</p>';
    return;
  }
  mesaDetail = await resp.json();
  renderMesaMain();
  renderAlerts(mesaKey);
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
          <button type="button" class="btn-report" onclick="openReportPanel('${d.mesa_key}')">📝 Reporte</button>
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
    scope: 'field',
    fields: {},
    decision_count: 0,
  };
}

/** Per-field 3 h window (from first decision on that field only). */
function fieldEditMeta(field) {
  const pkg = decisionEditMeta();
  const fields = pkg.fields || {};
  return fields[field] || {
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

  const blankConfirm = human.every((h) => h.tier === 'blank_confirm' || h.class === 'confirmed_blank');
  const sectionLabel = blankConfirm
    ? `Blancos reales — confirmar (${pending} pendiente(s))`
    : `Revisión humana (${human.length} campo(s))`;
  html += `<div class="alert-section-label human">${sectionLabel}</div>`;
  html += '<div class="edit-locked">La ventana de 3 h aplica por campo (no por mesa).</div>';

  html += human.map((h) => {
    const fd = mesaDecisions[h.field] || {};
    const fw = fieldEditMeta(h.field);
    const locked = fw.decision_count > 0 && !fw.editable;
    let fieldNote = '';
    if (locked) {
      fieldNote = '<div class="edit-locked">Campo cerrado (3 h desde su primera decisión).</div>';
    } else if (fw.editable_until) {
      fieldNote = `<div class="edit-locked">Este campo editable hasta ${formatEditDeadline(fw.editable_until)}</div>`;
    }
    const srcRows = (h.sources || []).map((s) => {
      const dec = fd[s.src];
      const disabled = locked ? ' disabled' : '';
      const acceptCls = dec === 'accepted' ? ' is-current' : '';
      const rejectCls = dec === 'rejected' ? ' is-current' : '';
      const missing = s.available === false
        ? '<span class="src-missing" title="Sin página renderizada; igual puedes aceptar o rechazar">sin imagen</span>'
        : '';
      return `<div class="alert-actions">
        <span class="alert-src-dot" style="background:${s.color}"></span>
        <span class="src-name">${s.label}${missing ? ' ' + missing : ''}</span>
        <button type="button" class="btn-accept${acceptCls}"${disabled}
          onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','accepted')"
          title="${dec === 'accepted' ? 'Ya aceptado' : 'Aceptar'}">✓</button>
        <button type="button" class="btn-reject${rejectCls}"${disabled}
          onclick="saveDecision('${mesaKey}','${h.field}','${s.src}','rejected')"
          title="${dec === 'rejected' ? 'Ya rechazado' : 'Rechazar'}">✗</button>
      </div>`;
    }).join('');
    const tierBadge = h.tier === 'blank_confirm'
      ? '<span class="alert-auto-badge">BLANCO</span>'
      : `<span class="alert-class">${h.class}</span>`;
    const reopenBtn = (fw.decision_count > 0 && fw.editable)
      ? `<button type="button" class="btn-reopen" onclick="reopenField('${mesaKey}','${h.field}')">Reabrir ${h.field_label}</button>`
      : '';
    return `<div class="alert-item">
      <div class="alert-item-header human">
        <span class="alert-src-name">${h.field_label}</span>
        ${tierBadge}
      </div>
      <div class="alert-msg">${h.msg}</div>
      ${fieldNote}
      ${srcRows}
      ${reopenBtn}
    </div>`;
  }).join('');
  body.innerHTML = html;
}

async function saveDecision(mesaKey, field, src, decision) {
  const fw = fieldEditMeta(field);
  if (fw.decision_count > 0 && !fw.editable) {
    alert('Ventana de edición cerrada para este campo (3 h desde su primera decisión).');
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
      alert('Ventana de edición cerrada para este campo (3 h desde su primera decisión).');
    } else if (err.error === 'invalid_or_failed') {
      alert('No se pudo guardar la decisión (campo/fuente inválidos o error de base de datos).');
    } else {
      alert(`Error al guardar decisión${err.error ? ': ' + err.error : ''}`);
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

  const item = itemByKey[mesaKey];
  if (item && mesaDetail.alerts) {
    item.decision_progress = computeProgress(mesaDetail.alerts, mesaDetail.decisions);
    item.pending_human_count = countPending(mesaDetail.alerts, mesaDetail.decisions);
    if (item.pending_human_count === 0) {
      queueMeta.pending = Math.max(0, queueMeta.pending - 1);
      queueMeta.done = Math.min(queueMeta.queueMesas, queueMeta.done + 1);
    }
  }
  renderAlerts(mesaKey);
  updateStatsBar();
  updateNavStatus();
  updateActiveNav();
}

function updateNavStatus() {
  document.querySelectorAll('.nav-item').forEach((a) => {
    const mesaKey = a.dataset.mesaKey;
    const item = itemByKey[mesaKey];
    const dot = a.querySelector('.nav-status');
    if (!dot || !item) return;
    const status = getMesaStatus(item);
    dot.className = `nav-status ${status}`;
    dot.title = status === 'done' ? 'Revisada' : 'Pendiente';
  });
}

async function reopenField(mesaKey, field) {
  const fw = fieldEditMeta(field);
  if (fw.decision_count > 0 && !fw.editable) {
    alert('Ya no puedes reabrir este campo (pasaron más de 3 h desde su primera decisión).');
    return;
  }
  if (!confirm(`¿Borrar decisiones de ${field} en esta mesa y volver a revisar ese campo?`)) return;
  const resp = await fetch('/api/transversal/decisions/reopen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: mesaKey, field }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert(err.error === 'edit_window_expired'
      ? 'Ventana de edición cerrada para este campo (3 h).'
      : 'No se pudo reabrir el campo.');
    return;
  }
  if (mesaDetail.decisions && mesaDetail.decisions[field]) {
    delete mesaDetail.decisions[field];
  }
  const refresh = await fetch(`/api/transversal/mesa/${encodeURIComponent(mesaKey)}`);
  if (refresh.ok) {
    const data = await refresh.json();
    mesaDetail.decisions = data.decisions || {};
    mesaDetail.decision_edit = data.decision_edit || decisionEditMeta();
  }
  const item = itemByKey[mesaKey];
  if (item && mesaDetail.alerts) {
    const wasDone = (item.pending_human_count || 0) === 0;
    item.decision_progress = computeProgress(mesaDetail.alerts, mesaDetail.decisions || {});
    item.pending_human_count = countPending(mesaDetail.alerts, mesaDetail.decisions || {});
    if (wasDone && item.pending_human_count > 0) {
      queueMeta.pending += 1;
      queueMeta.done = Math.max(0, queueMeta.done - 1);
    }
  }
  renderAlerts(mesaKey);
  updateNavStatus();
  updateActiveNav();
  updateStatsBar();
}

/** @deprecated use reopenField — kept for any leftover callers */
async function reopenMesa(mesaKey) {
  if (!confirm('¿Borrar las decisiones de campos aún editables en esta mesa?')) return;
  const resp = await fetch('/api/transversal/decisions/reopen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: mesaKey }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert(err.error === 'edit_window_expired'
      ? 'Ningún campo tiene ventana de edición abierta (3 h).'
      : 'No se pudo reabrir.');
    return;
  }
  const refresh = await fetch(`/api/transversal/mesa/${encodeURIComponent(mesaKey)}`);
  if (refresh.ok) {
    const data = await refresh.json();
    mesaDetail.decisions = data.decisions || {};
    mesaDetail.decision_edit = data.decision_edit || decisionEditMeta();
  } else {
    mesaDetail.decisions = {};
  }
  const item = itemByKey[mesaKey];
  if (item && mesaDetail.alerts) {
    const wasDone = (item.pending_human_count || 0) === 0;
    item.decision_progress = computeProgress(mesaDetail.alerts, mesaDetail.decisions || {});
    item.pending_human_count = countPending(mesaDetail.alerts, mesaDetail.decisions || {});
    if (wasDone && item.pending_human_count > 0) {
      queueMeta.pending += 1;
      queueMeta.done = Math.max(0, queueMeta.done - 1);
    }
  }
  renderAlerts(mesaKey);
  updateNavStatus();
  updateActiveNav();
  updateStatsBar();
}

function goPrev() {
  const pos = keyOrder.indexOf(currentMesaKey);
  if (pos > 0) selectMesaByKey(keyOrder[pos - 1]);
}

function goNext() {
  const pos = keyOrder.indexOf(currentMesaKey);
  if (pos >= 0 && pos < keyOrder.length - 1) {
    selectMesaByKey(keyOrder[pos + 1]);
  }
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

function toggleAlertPanel() {
  document.getElementById('alert-panel').classList.toggle('collapsed');
  const chevron = document.getElementById('alert-chevron');
  chevron.textContent = document.getElementById('alert-panel').classList.contains('collapsed') ? '▼' : '▲';
}

const BLANK_FIELDS = ['VOTANTES', 'URNA', 'SUMA_TOTAL'];
const RTYPE_LABELS = {
  campos_vacios: 'Campos críticos vacíos',
  enmienda: 'Enmienda / Tachón',
  otro: 'Otros',
};

let _reportMesaKey = null;
let _selectedRtype = null;

function openReportPanel(mesaKey) {
  _reportMesaKey = mesaKey;
  _selectedRtype = null;
  document.querySelectorAll('.src-check').forEach((c) => { c.checked = false; });
  document.querySelectorAll('.rtype-btn').forEach((b) => b.classList.remove('selected'));
  document.getElementById('report-notes').value = '';
  document.getElementById('campos-detail').style.display = 'none';
  document.getElementById('campos-per-src').innerHTML = '';
  renderModalSaved(mesaKey);
  document.getElementById('report-modal').classList.add('open');
}

function closeReportModal(e) {
  if (e && e.target !== document.getElementById('report-modal')) return;
  document.getElementById('report-modal').classList.remove('open');
  _reportMesaKey = null;
  _selectedRtype = null;
}

function selectReportType(btn) {
  document.querySelectorAll('.rtype-btn').forEach((b) => b.classList.remove('selected'));
  btn.classList.add('selected');
  _selectedRtype = btn.dataset.type;
  updateCamposDetail();
}

function onReportSrcChange() {
  updateCamposDetail();
}

function updateCamposDetail() {
  const detail = document.getElementById('campos-detail');
  const perSrc = document.getElementById('campos-per-src');
  if (_selectedRtype !== 'campos_vacios') {
    detail.style.display = 'none';
    perSrc.innerHTML = '';
    return;
  }
  const selected = [...document.querySelectorAll('.src-check:checked')].map((c) => c.value);
  if (!selected.length) {
    detail.style.display = 'none';
    perSrc.innerHTML = '';
    return;
  }
  detail.style.display = '';
  perSrc.innerHTML = selected.map((src) => {
    const color = SOURCE_COLORS[src];
    const label = SOURCE_LABELS[src];
    const fields = BLANK_FIELDS.map((f) =>
      `<label><input type="checkbox" id="campo-${src}-${f}" checked> ${f}</label>`,
    ).join('');
    return `<div class="campos-src-block">
      <div class="campos-src-header" style="background:${color}22;border-bottom:1px solid ${color}44">
        <span class="src-dot" style="background:${color}"></span>
        <span style="color:${color}">${label}</span>
      </div>
      <div class="campos-src-fields">${fields}</div>
    </div>`;
  }).join('');
}

function renderModalSaved(mesaKey) {
  const reports = (mesaKey === currentMesaKey && mesaDetail && mesaDetail.reports) || [];
  const sec = document.getElementById('modal-saved-section');
  const list = document.getElementById('modal-saved-list');
  if (!reports.length) {
    sec.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  sec.style.display = '';
  list.innerHTML = reports.map((r) => {
    const color = SOURCE_COLORS[r.source] || '#475569';
    const srcLabel = SOURCE_LABELS[r.source] || r.source;
    const typeLabel = RTYPE_LABELS[r.report_type] || r.report_type;
    let fieldsHtml = '';
    if (r.fields) {
      fieldsHtml = `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px">${
        Object.entries(r.fields).map(([f, v]) =>
          `<span class="field-tag ${v ? 'on' : 'off'}">${f}</span>`,
        ).join('')
      }</div>`;
    }
    return `<div class="modal-saved-item">
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span class="src-dot" style="background:${color}"></span>
          <span style="font-size:0.7rem;color:${color};font-weight:700">${srcLabel}</span>
          <span class="rtype-badge ${r.report_type}">${typeLabel}</span>
        </div>
        ${fieldsHtml}
        <div style="font-size:0.7rem;color:#64748b;margin-top:3px">${r.notes}</div>
      </div>
      <button type="button" class="modal-del-btn" onclick="deleteModalReport('${r.id}')">✕</button>
    </div>`;
  }).join('');
}

async function saveModalReport() {
  const sources = [...document.querySelectorAll('.src-check:checked')].map((c) => c.value);
  const notes = document.getElementById('report-notes').value.trim();
  if (!sources.length) { alert('Selecciona al menos una fuente.'); return; }
  if (!_selectedRtype) { alert('Selecciona el tipo de reporte.'); return; }
  if (!notes) { alert('Las notas son obligatorias.'); return; }

  const entries = sources.map((src) => {
    const entry = { source: src, report_type: _selectedRtype, notes };
    if (_selectedRtype === 'campos_vacios') {
      const fields = {};
      BLANK_FIELDS.forEach((f) => {
        const el = document.getElementById(`campo-${src}-${f}`);
        if (el) fields[f] = el.checked;
      });
      entry.fields = fields;
    }
    return entry;
  });

  const resp = await fetch('/api/transversal/reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mesa_key: _reportMesaKey, entries }),
  });
  if (!resp.ok) {
    alert('Error al guardar reporte.');
    return;
  }
  const data = await resp.json();
  if (mesaDetail && _reportMesaKey === currentMesaKey) {
    mesaDetail.reports = [...(mesaDetail.reports || []), ...(data.reports || [])];
  }
  renderModalSaved(_reportMesaKey);
  document.getElementById('report-notes').value = '';
  document.querySelectorAll('.rtype-btn').forEach((b) => b.classList.remove('selected'));
  document.querySelectorAll('.src-check').forEach((c) => { c.checked = false; });
  document.getElementById('campos-detail').style.display = 'none';
  document.getElementById('campos-per-src').innerHTML = '';
  _selectedRtype = null;
}

async function deleteModalReport(reportId) {
  const resp = await fetch(`/api/transversal/reports/${encodeURIComponent(reportId)}`, {
    method: 'DELETE',
  });
  if (!resp.ok) {
    alert('No se pudo borrar el reporte.');
    return;
  }
  if (mesaDetail && mesaDetail.reports) {
    mesaDetail.reports = mesaDetail.reports.filter((r) => r.id !== reportId);
  }
  renderModalSaved(_reportMesaKey || currentMesaKey);
}

function exportDecisions() {
  window.location.href = '/api/transversal/decisions/export';
}

function onFilterChange() {
  currentMesaKey = null;
  resetQueue().catch((err) => console.error(err));
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

window.selectMesaByKey = selectMesaByKey;
window.goPrev = goPrev;
window.goNext = goNext;
window.saveDecision = saveDecision;
window.reopenField = reopenField;
window.reopenMesa = reopenMesa;
window.toggleSidebar = toggleSidebar;
window.toggleAlertPanel = toggleAlertPanel;
window.exportDecisions = exportDecisions;
window.onFilterChange = onFilterChange;
window.onSearchInput = onSearchInput;
window.setSidebarFilter = setSidebarFilter;
window.resetQueue = resetQueue;
window.openReportPanel = openReportPanel;
window.closeReportModal = closeReportModal;
window.selectReportType = selectReportType;
window.onReportSrcChange = onReportSrcChange;
window.saveModalReport = saveModalReport;
window.deleteModalReport = deleteModalReport;

document.addEventListener('DOMContentLoaded', () => {
  resetQueue().catch((err) => {
    console.error(err);
    const list = document.getElementById('sidebar-list');
    if (list) list.innerHTML = '<p class="empty-msg">Error cargando cola.</p>';
  });
});