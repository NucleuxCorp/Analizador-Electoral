"""
scripts/build_gallery.py

Generates galeria.html inside E14D_3_totales_blancos/ with all 24 mesas.
Layout per mesa: pages grouped as rows, each row shows E14T | E14D | E14C.
Missing source shows a placeholder instead of a broken image.

Usage:
    python scripts/build_gallery.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESAS_DIR = ROOT / "Laboratorio/analisis_transversal/analisis_visual/E14D_3_totales_blancos/mesas"
MANIFEST = ROOT / "Laboratorio/analisis_transversal/analisis_visual/E14D_3_totales_blancos/manifest.json"
OUT_HTML = ROOT / "Laboratorio/analisis_transversal/analisis_visual/E14D_3_totales_blancos/galeria.html"

SOURCES = ("e14t", "e14d", "e14c")
SOURCE_LABELS = {"e14t": "E14T (Testigo)", "e14d": "E14D (Delegado)", "e14c": "E14C (Oficial)"}
SOURCE_COLORS = {"e14t": "#2563eb", "e14d": "#dc2626", "e14c": "#16a34a"}


def get_pages(mesa_dir: Path, src: str) -> list[Path]:
    return sorted(mesa_dir.glob(f"{src}_p*.jpg"))


def rel(path: Path) -> str:
    return "mesas/" + path.parent.name + "/" + path.name


def mesa_header(key: tuple, manifest_entry: dict) -> str:
    dept, mpio, zona, puesto, mesa = key
    return f"{dept} / {mpio} / zona_{zona} / puesto_{puesto} / mesa_{mesa}"


DEPT_NAMES = {
    "01": "ANTIOQUIA", "05": "BOLIVAR", "16": "BOGOTA D.C", "17": "CHOCO",
    "19": "HUILA", "21": "MAGDALENA", "23": "NARIÑO", "28": "SUCRE",
    "48": "LA GUAJIRA", "88": "CONSULADOS",
}


def build_html(manifest: list[dict]) -> str:
    # Build sidebar items and sections
    sidebar_items = []
    mesa_sections = []
    depts_seen: list[str] = []

    for idx, entry in enumerate(manifest):
        key = (entry["dept"], entry["mpio"], entry["zona"], entry["puesto"], entry["mesa"])
        folder = "_".join(key)
        mesa_dir = MESAS_DIR / folder
        dept = entry["dept"]

        if not mesa_dir.exists():
            continue

        pages_by_src = {src: get_pages(mesa_dir, src) for src in SOURCES}
        max_pages = max((len(v) for v in pages_by_src.values()), default=0)
        if max_pages == 0:
            continue

        dept_name = DEPT_NAMES.get(dept, dept)
        anchor = f"mesa-{folder}"
        short = f"{dept_name} / z{key[2]} p{key[3]} m{key[4]}"

        if dept not in depts_seen:
            depts_seen.append(dept)

        sidebar_items.append(
            f'<a href="#" onclick="goMesa({idx});return false;" class="nav-item" data-dept="{dept}" data-idx="{idx}">'
            f'<span class="nav-num">{idx+1:02d}</span>'
            f'<span class="nav-label">{short}</span></a>'
        )

        rows = []
        for p in range(1, max_pages + 1):
            cells = []
            for src in SOURCES:
                pages = pages_by_src[src]
                img_path = next((pg for pg in pages if f"_p{p:02d}." in pg.name), None)
                color = SOURCE_COLORS[src]
                label = SOURCE_LABELS[src]
                if img_path:
                    cells.append(
                        f'<div class="cell">'
                        f'<div class="src-label" style="background:{color}">{label}</div>'
                        f'<img src="{rel(img_path)}" loading="lazy" alt="{label} p{p}"></div>'
                    )
                else:
                    cells.append(
                        f'<div class="cell missing">'
                        f'<div class="src-label" style="background:{color}">{label}</div>'
                        f'<div class="no-pdf">PDF no disponible</div></div>'
                    )

            rows.append(
                f'<div class="page-row">'
                f'<div class="page-badge">Pág. {p}</div>'
                f'<div class="page-cols">{"".join(cells)}</div></div>'
            )

        dept_badge = f'<span class="dept-badge">{dept_name}</span>'
        coord = f'zona {key[2]} · puesto {key[3]} · mesa {key[4]}'
        mesa_sections.append(
            f'<section class="mesa" id="{anchor}" data-dept="{dept}">'
            f'<div class="mesa-header">'
            f'<div class="mesa-title">{dept_badge} {coord}</div>'
            f'<div class="mesa-nav">'
            f'<button onclick="goPrev()">&#8592; Anterior</button>'
            f'<span id="nav-counter">{idx+1} / {len(manifest)}</span>'
            f'<button onclick="goNext()">Siguiente &#8594;</button>'
            f'</div></div>'
            f'{"".join(rows)}</section>'
        )

    dept_options = "".join(
        f'<option value="{d}">{DEPT_NAMES.get(d, d)}</option>'
        for d in depts_seen
    )

    anchors_js = "[" + ",".join(
        '"mesa-' + "_".join((e["dept"], e["mpio"], e["zona"], e["puesto"], e["mesa"])) + '"'
        for e in manifest
    ) + "]"

    # Build alert data per mesa: one alert per available source
    alerts_per_mesa = {}
    for entry in manifest:
        key = (entry["dept"], entry["mpio"], entry["zona"], entry["puesto"], entry["mesa"])
        folder = "_".join(key)
        mesa_dir = MESAS_DIR / folder
        mesa_alerts = []
        for src in SOURCES:
            pages = get_pages(mesa_dir, src) if mesa_dir.exists() else []
            if pages:
                mesa_alerts.append({
                    "src": src,
                    "label": SOURCE_LABELS[src],
                    "color": SOURCE_COLORS[src],
                    "msg": "3 campos de totales sin rellenar: VOTANTES, URNA, SUMA_TOTAL (con C1+C2 > 0)"
                })
        alerts_per_mesa[folder] = mesa_alerts

    import json as _json
    alerts_js = _json.dumps(alerts_per_mesa, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Galería — E14D Mesas Sospechosas</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden}}
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column}}

/* TOP BAR */
.topbar{{display:flex;align-items:center;gap:10px;padding:8px 16px;background:#1e293b;border-bottom:1px solid #334155;flex-shrink:0;height:52px}}
.topbar h1{{font-size:0.88rem;font-weight:700;color:#f8fafc;white-space:nowrap}}
.topbar select{{background:#0f172a;color:#e2e8f0;border:1px solid #475569;border-radius:6px;padding:5px 8px;font-size:0.8rem}}
.topbar input{{background:#0f172a;color:#e2e8f0;border:1px solid #475569;border-radius:6px;padding:5px 8px;font-size:0.8rem;width:180px}}
.topbar .hint{{font-size:0.7rem;color:#475569;margin-left:auto;white-space:nowrap}}
.btn-sidebar-toggle{{display:none;background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:5px 10px;font-size:0.8rem;cursor:pointer}}

/* LAYOUT */
.layout{{display:flex;flex:1;overflow:hidden;position:relative}}

/* SIDEBAR — fixed, independent scroll */
.sidebar{{width:375px;flex-shrink:0;background:#1e293b;border-right:1px solid #334155;overflow-y:auto;padding:8px 0}}
.nav-item{{display:flex;align-items:flex-start;gap:8px;padding:8px 14px;cursor:pointer;text-decoration:none;color:#cbd5e1;border-left:3px solid transparent;transition:background .1s,color .1s}}
.nav-item:hover{{background:#0f172a;color:#f8fafc}}
.nav-item.active{{background:#0f172a;border-left-color:#fbbf24;color:#fbbf24}}
.nav-item.nav-hidden{{display:none}}
.nav-num{{font-size:0.68rem;color:#475569;font-family:monospace;min-width:24px;padding-top:2px}}
.nav-label{{font-size:0.8rem;line-height:1.4}}

/* MAIN */
.main{{flex:1;overflow-y:auto;padding:0}}

/* MESA SECTION — only one visible at a time */
.mesa{{display:none}}
.mesa.active{{display:block}}
.mesa.hidden{{display:none}}

/* Sticky mesa header */
.mesa-header{{
  position:sticky;top:0;z-index:10;
  display:flex;align-items:center;justify-content:space-between;
  padding:6px 14px;gap:8px;flex-wrap:wrap;
  background:#0f172a;border-bottom:1px solid #1e293b;
  height:40px;flex-shrink:0;
}}
.mesa-title{{display:flex;align-items:center;gap:8px;font-family:monospace;font-size:0.8rem;color:#94a3b8}}
.dept-badge{{background:#fbbf24;color:#0f172a;font-size:0.65rem;font-weight:700;padding:2px 8px;border-radius:4px;text-transform:uppercase;white-space:nowrap}}
.mesa-nav{{display:flex;align-items:center;gap:8px;flex-shrink:0}}
.mesa-nav button{{background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:3px 10px;font-size:0.75rem;cursor:pointer;white-space:nowrap}}
.mesa-nav button:hover:not(:disabled){{background:#475569}}
.mesa-nav button:disabled{{opacity:0.3;cursor:default}}
.mesa-nav span{{font-size:0.7rem;color:#64748b;white-space:nowrap}}

/* PAGE ROWS */
.page-row{{padding:6px 12px 12px}}
.page-badge{{font-size:0.65rem;color:#475569;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
.page-cols{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;align-items:start}}

/* CELLS */
.cell{{border-radius:6px;border:1px solid #334155;overflow:visible}}
.src-label{{
  position:sticky;top:40px;z-index:5;
  font-size:0.68rem;font-weight:700;color:#fff;
  padding:3px 8px;text-transform:uppercase;letter-spacing:.05em;display:block;
}}
.cell img{{width:100%;height:auto;display:block;border-radius:0 0 6px 6px}}
.cell.missing{{background:#0f172a}}
.no-pdf{{display:flex;align-items:center;justify-content:center;height:calc(100vh - 160px);font-size:0.8rem;color:#334155;font-style:italic}}

/* FLOATING ALERT PANEL */
.alert-panel{{
  position:fixed;bottom:20px;right:20px;z-index:50;
  width:320px;background:#1e293b;border:1px solid #334155;
  border-radius:12px;box-shadow:0 8px 32px #0008;
  overflow:hidden;
}}
.alert-panel-header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;background:#0f172a;cursor:pointer;user-select:none;
}}
.alert-panel-title{{font-size:0.8rem;font-weight:700;color:#f8fafc;display:flex;align-items:center;gap:8px}}
.alert-badge{{
  background:#ef4444;color:#fff;font-size:0.65rem;font-weight:700;
  padding:2px 7px;border-radius:999px;
}}
.alert-panel-toggle{{font-size:0.75rem;color:#64748b}}
.alert-panel-body{{padding:10px 12px;display:flex;flex-direction:column;gap:8px}}
.alert-panel.collapsed .alert-panel-body{{display:none}}

.alert-item{{
  border-radius:8px;overflow:hidden;border:1px solid #334155;
}}
.alert-item-header{{
  display:flex;align-items:center;gap:8px;padding:6px 10px;
}}
.alert-src-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.alert-src-name{{font-size:0.72rem;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:.04em}}
.alert-msg{{font-size:0.72rem;color:#94a3b8;padding:4px 10px 6px;line-height:1.4}}
.alert-actions{{display:flex;gap:6px;padding:0 10px 8px}}
.btn-accept{{
  flex:1;background:#16a34a;color:#fff;border:none;border-radius:6px;
  padding:5px 0;font-size:0.72rem;font-weight:600;cursor:pointer;
  transition:background .15s;
}}
.btn-accept:hover{{background:#15803d}}
.btn-accept.selected{{background:#15803d;box-shadow:0 0 0 2px #4ade80}}
.btn-reject{{
  flex:1;background:#dc2626;color:#fff;border:none;border-radius:6px;
  padding:5px 0;font-size:0.72rem;font-weight:600;cursor:pointer;
  transition:background .15s;
}}
.btn-reject:hover{{background:#b91c1c}}
.btn-reject.selected{{background:#b91c1c;box-shadow:0 0 0 2px #f87171}}
.alert-decided{{font-size:0.68rem;text-align:center;padding:2px 0 6px;color:#64748b}}

/* MOBILE */
@media(max-width:768px){{
  .sidebar{{display:none}}
  .sidebar.open{{display:flex;flex-direction:column;position:fixed;top:52px;left:0;bottom:0;z-index:100;width:85vw;max-width:320px;box-shadow:4px 0 24px #000a}}
  .btn-sidebar-toggle{{display:inline-block}}
  .topbar .hint{{display:none}}
  .topbar input{{width:110px}}
  .page-cols{{grid-template-columns:1fr}}
  .cell img{{height:100vh;object-fit:contain;background:#0a0f1a}}
  .no-pdf{{height:100vh}}
}}
</style>
</head>
<body>

<div class="topbar">
  <button class="btn-sidebar-toggle" onclick="toggleSidebar()">&#9776; Mesas</button>
  <h1>E14D · Mesas Sospechosas</h1>
  <select id="dept-filter" onchange="filterDept(this.value)">
    <option value="">Todos los depts.</option>
    {dept_options}
  </select>
  <input id="search" placeholder="Buscar mesa..." oninput="filterSearch(this.value)">
  <span class="hint">&#8592; &#8594; navegar &nbsp;·&nbsp; Esc limpiar filtro</span>
  <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
    <span id="unsaved-dot" style="display:none;width:8px;height:8px;border-radius:50%;background:#f59e0b" title="Cambios sin guardar"></span>
    <button onclick="loadFromFile()" style="background:#334155;color:#e2e8f0;border:none;border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer">&#128194; Cargar</button>
    <button onclick="saveToFile()" style="background:#16a34a;color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:0.75rem;cursor:pointer">&#128190; Guardar</button>
  </div>
</div>
<input type="file" id="file-input" accept=".json" style="display:none" onchange="onFileLoaded(event)">

<div class="layout">
  <nav class="sidebar" id="sidebar">
    {"".join(sidebar_items)}
  </nav>
  <main class="main" id="main">
    {"".join(mesa_sections)}
  </main>
</div>

<!-- Floating alert panel -->
<div class="alert-panel" id="alert-panel">
  <div class="alert-panel-header" onclick="toggleAlertPanel()">
    <div class="alert-panel-title">
      Alertas <span class="alert-badge" id="alert-count">0</span>
    </div>
    <span class="alert-panel-toggle" id="alert-chevron">&#9650;</span>
  </div>
  <div class="alert-panel-body" id="alert-body"></div>
</div>

<script>
const anchors = {anchors_js};
const ALERTS = {alerts_js};
let currentIdx = 0;

function getMesaKey(idx) {{
  return anchors[idx].replace('mesa-', '');
}}

function goMesa(idx) {{
  if (idx < 0 || idx >= anchors.length) return;
  const el = document.getElementById(anchors[idx]);
  if (!el || el.classList.contains('nav-hidden')) return;
  document.querySelectorAll('.mesa.active').forEach(m => m.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('main').scrollTop = 0;
  currentIdx = idx;
  updateActive();
  renderAlerts(getMesaKey(idx));
}}

function updateActive() {{
  document.querySelectorAll('.nav-item').forEach(a => a.classList.remove('active'));
  const a = document.querySelector(`.nav-item[data-idx="${{currentIdx}}"]`);
  if (a) {{ a.classList.add('active'); a.scrollIntoView({{block:'nearest'}}); }}
}}

function getVisibleIdxs() {{
  return [...document.querySelectorAll('.nav-item:not(.nav-hidden)')]
    .map(a => parseInt(a.dataset.idx));
}}

function filterDept(val) {{
  document.querySelectorAll('.nav-item').forEach(a => {{
    a.classList.toggle('nav-hidden', !!(val && a.dataset.dept !== val));
  }});
  // jump to first visible if current is now hidden
  const vis = getVisibleIdxs();
  if (vis.length && !vis.includes(currentIdx)) goMesa(vis[0]);
}}

function filterSearch(val) {{
  const q = val.toLowerCase();
  document.querySelectorAll('.nav-item').forEach(a => {{
    a.classList.toggle('nav-hidden', !!(q && !a.textContent.toLowerCase().includes(q)));
  }});
  const vis = getVisibleIdxs();
  if (vis.length && !vis.includes(currentIdx)) goMesa(vis[0]);
}}

function goPrev() {{
  const vis = getVisibleIdxs();
  const pos = vis.indexOf(currentIdx);
  if (pos > 0) goMesa(vis[pos - 1]);
}}

function goNext() {{
  const vis = getVisibleIdxs();
  const pos = vis.indexOf(currentIdx);
  if (pos < vis.length - 1) goMesa(vis[pos + 1]);
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight') goNext();
  if (e.key === 'ArrowLeft')  goPrev();
  if (e.key === 'Escape') {{
    document.getElementById('dept-filter').value = '';
    document.getElementById('search').value = '';
    filterDept(''); filterSearch('');
  }}
}});

function toggleSidebar() {{
  document.getElementById('sidebar').classList.toggle('open');
}}

// ---- DECISIONS STORAGE ----
let decisions = {{}};
let fileHandle = null;
let hasUnsaved = false;

function markUnsaved() {{
  hasUnsaved = true;
  document.getElementById('unsaved-dot').style.display = 'inline-block';
}}

function markSaved() {{
  hasUnsaved = false;
  document.getElementById('unsaved-dot').style.display = 'none';
}}

function decisionsToJson() {{
  return JSON.stringify({{
    generated: new Date().toISOString(),
    project: "E14D_3_totales_blancos",
    decisions
  }}, null, 2);
}}

async function saveToFile() {{
  const json = decisionsToJson();
  // Try File System Access API (Chrome/Edge)
  if (window.showSaveFilePicker) {{
    try {{
      if (!fileHandle) {{
        fileHandle = await window.showSaveFilePicker({{
          suggestedName: 'decisiones_e14d.json',
          types: [{{ description: 'JSON', accept: {{ 'application/json': ['.json'] }} }}]
        }});
      }}
      const writable = await fileHandle.createWritable();
      await writable.write(json);
      await writable.close();
      markSaved();
      return;
    }} catch(e) {{ if (e.name === 'AbortError') return; }}
  }}
  // Fallback: download
  const a = document.createElement('a');
  a.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(json);
  a.download = 'decisiones_e14d.json';
  a.click();
  markSaved();
}}

function loadFromFile() {{
  document.getElementById('file-input').click();
}}

function onFileLoaded(event) {{
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {{
    try {{
      const data = JSON.parse(e.target.result);
      decisions = data.decisions || {{}};
      markSaved();
      renderAlerts(getMesaKey(currentIdx));
      alert('Decisiones cargadas: ' + Object.keys(decisions).length + ' mesas.');
    }} catch {{ alert('Archivo inválido.'); }}
  }};
  reader.readAsText(file);
  event.target.value = '';
}}

// ---- ALERT PANEL ----
function toggleAlertPanel() {{
  document.getElementById('alert-panel').classList.toggle('collapsed');
  document.getElementById('alert-chevron').textContent =
    document.getElementById('alert-panel').classList.contains('collapsed') ? '▼' : '▲';
}}

function saveDecision(mesaKey, src, decision) {{
  if (!decisions[mesaKey]) decisions[mesaKey] = {{}};
  decisions[mesaKey][src] = decision;
  renderAlerts(mesaKey);
  autoSave();
}}

function clearDecisions(mesaKey) {{
  delete decisions[mesaKey];
  renderAlerts(mesaKey);
  autoSave();
}}

async function autoSave() {{
  if (!fileHandle) {{
    markUnsaved();
    return;
  }}
  try {{
    const writable = await fileHandle.createWritable();
    await writable.write(decisionsToJson());
    await writable.close();
    markSaved();
  }} catch(e) {{
    markUnsaved();
  }}
}}

function renderAlerts(mesaKey) {{
  const alerts = ALERTS[mesaKey] || [];
  const mesa_decisions = decisions[mesaKey] || {{}};
  const pending = alerts.filter(a => !mesa_decisions[a.src]).length;

  const badge = document.getElementById('alert-count');
  badge.textContent = pending;
  badge.style.background = pending === 0 ? '#16a34a' : '#ef4444';

  const body = document.getElementById('alert-body');
  const undecided = alerts.filter(a => !mesa_decisions[a.src]);
  if (undecided.length === 0) {{
    const decided = alerts.map(a => {{
      const dec = mesa_decisions[a.src];
      const icon = dec === 'accepted' ? '✓' : '✗';
      const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
      return `<div style="display:flex;align-items:center;gap:6px;padding:2px 0">
        <span style="color:${{color}};font-weight:700">${{icon}}</span>
        <span style="font-size:0.72rem;color:#94a3b8">${{a.label}}</span>
        <span style="margin-left:auto;font-size:0.68rem;color:${{color}}">${{dec === 'accepted' ? 'Aceptado' : 'Rechazado'}}</span>
      </div>`;
    }}).join('');
    body.innerHTML = `<div style="padding:4px 2px">
      ${{decided}}
      <div style="margin-top:8px;font-size:0.72rem;color:#475569;text-align:center;cursor:pointer" onclick="clearDecisions('${{mesaKey}}')">↺ Reiniciar decisiones</div>
    </div>`;
  }} else {{
    body.innerHTML = undecided.map(a => `
      <div class="alert-item">
        <div class="alert-item-header" style="background:${{a.color}}22;border-bottom:1px solid ${{a.color}}44">
          <span class="alert-src-dot" style="background:${{a.color}}"></span>
          <span class="alert-src-name" style="color:${{a.color}}">${{a.label}}</span>
        </div>
        <div class="alert-msg">${{a.msg}}</div>
        <div class="alert-actions">
          <button class="btn-accept" onclick="saveDecision('${{mesaKey}}','${{a.src}}','accepted')">✓ Aceptar</button>
          <button class="btn-reject" onclick="saveDecision('${{mesaKey}}','${{a.src}}','rejected')">✗ Rechazar</button>
        </div>
      </div>`).join('');
  }}
}}

// init
goMesa(0);
</script>
</body>
</html>"""


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    html = build_html(manifest)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Generated: {OUT_HTML}")
    print(f"Open in browser: file:///{OUT_HTML.as_posix()}")


if __name__ == "__main__":
    main()
