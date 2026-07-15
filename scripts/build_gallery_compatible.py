"""
build_gallery_compatible.py

Prototype gallery for SDD transversal review panel.
Uses index.jsonl (stored cross data, NO reprocess) + compatible alert tiers.

Usage:
    python scripts/build_gallery_compatible.py
    python scripts/build_gallery_compatible.py --lab-dir Laboratorio/analisis_transversal/E14C_conflictivas_pendientes
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAB = ROOT / "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"
SHARED_MESAS_DIR = ROOT / "Laboratorio/analisis_transversal/mesas_compartidas"

from scripts.build_gallery import build_html, _mesas_url_prefix  # noqa: E402
from scripts.gallery_alert_compat import build_alert_index  # noqa: E402


def _patch_compatible_script(html: str, alert_index: dict) -> str:
    alerts_js = json.dumps(alert_index, ensure_ascii=False)
    marker = "function saveDecision(mesaKey, src, decision)"
    if marker not in html:
        raise RuntimeError("Could not patch gallery HTML: legacy saveDecision not found")

    head, _tail = html.split(marker, 1)
    a0 = head.index("const ALERTS = ")
    a1 = head.index(";", a0) + 1
    head = head[:a0] + f"const ALERTS = {alerts_js};" + head[a1:]
    compat_tail = f"""function saveDecision(mesaKey, field, src, decision) {{
  if (!decisions[mesaKey]) decisions[mesaKey] = {{}};
  if (!decisions[mesaKey][field]) decisions[mesaKey][field] = {{}};
  decisions[mesaKey][field][src] = decision;
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
  const pkg = ALERTS[mesaKey] || {{human: [], auto: []}};
  const human = pkg.human || [];
  const auto = pkg.auto || [];
  const mesa_decisions = decisions[mesaKey] || {{}};

  function isPending(h) {{
    const fd = mesa_decisions[h.field] || {{}};
    return (h.sources || []).some(s => !fd[s.src]);
  }}

  const pending = human.filter(isPending).length;
  const badge = document.getElementById('alert-count');
  badge.textContent = pending;
  badge.style.background = pending === 0 ? '#16a34a' : '#ef4444';

  const body = document.getElementById('alert-body');
  let html = '';

  if (auto.length) {{
    html += `<div style="font-size:0.68rem;color:#64748b;margin-bottom:8px;font-weight:600">Auto-resueltas (sin tinta)</div>`;
    html += auto.map(a => `
      <div class="alert-item" style="opacity:0.75;border-color:#1e3a2f">
        <div class="alert-item-header" style="background:#16a34a18">
          <span class="alert-src-name" style="color:#86efac">${{a.field_label}}</span>
          <span style="margin-left:auto;font-size:0.65rem;color:#4ade80">AUTO</span>
        </div>
        <div class="alert-msg">${{a.msg}}</div>
      </div>`).join('');
  }}

  if (!human.length) {{
    if (!auto.length) html += `<div class="muted" style="font-size:0.72rem;padding:4px">Sin alertas.</div>`;
    body.innerHTML = html;
    return;
  }}

  if (pending === 0) {{
    html += human.map(h => {{
      const fd = mesa_decisions[h.field] || {{}};
      const rows = (h.sources || []).map(s => {{
        const dec = fd[s.src];
        if (!dec) return '';
        const icon = dec === 'accepted' ? '✓' : '✗';
        const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
        return `<div style="display:flex;gap:6px;padding:2px 0">
          <span style="color:${{color}}">${{icon}}</span>
          <span style="font-size:0.72rem;color:#94a3b8">${{h.field_label}} · ${{s.label}}</span>
        </div>`;
      }}).join('');
      return rows;
    }}).join('');
    html += `<div style="margin-top:8px;font-size:0.72rem;color:#475569;text-align:center;cursor:pointer" onclick="clearDecisions('${{mesaKey}}')">↺ Reiniciar decisiones</div>`;
    body.innerHTML = html;
    return;
  }}

  html += `<div style="font-size:0.68rem;color:#fbbf24;margin:8px 0 6px;font-weight:600">Revisión humana (${{human.length}} campo(s))</div>`;
  html += human.map(h => {{
    const fd = mesa_decisions[h.field] || {{}};
    const srcRows = (h.sources || []).map(s => {{
      const dec = fd[s.src];
      if (dec) {{
        const color = dec === 'accepted' ? '#16a34a' : '#dc2626';
        return `<div style="font-size:0.68rem;color:${{color}};padding:2px 0">${{s.label}}: ${{dec === 'accepted' ? 'Aceptado' : 'Rechazado'}}</div>`;
      }}
      return `<div class="alert-actions" style="padding:4px 0 6px">
        <span class="alert-src-dot" style="background:${{s.color}};display:inline-block;margin-right:6px"></span>
        <span style="font-size:0.68rem;color:#94a3b8;margin-right:8px">${{s.label}}</span>
        <button class="btn-accept" style="flex:0 0 auto;padding:3px 8px" onclick="saveDecision('${{mesaKey}}','${{h.field}}','${{s.src}}','accepted')">✓</button>
        <button class="btn-reject" style="flex:0 0 auto;padding:3px 8px" onclick="saveDecision('${{mesaKey}}','${{h.field}}','${{s.src}}','rejected')">✗</button>
      </div>`;
    }}).join('');
    return `<div class="alert-item">
      <div class="alert-item-header" style="background:#fbbf2422;border-bottom:1px solid #fbbf2444">
        <span class="alert-src-name" style="color:#fbbf24">${{h.field_label}}</span>
        <span style="margin-left:auto;font-size:0.65rem;color:#94a3b8">${{h.class}}</span>
      </div>
      <div class="alert-msg">${{h.msg}}</div>
      ${{srcRows}}
    </div>`;
  }}).join('');
  body.innerHTML = html;
}}

// init
goMesa(0);
</script>
</body>
</html>"""

    return head + compat_tail


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compatible prototype gallery (no reprocess)")
    parser.add_argument("--lab-dir", type=Path, default=DEFAULT_LAB)
    parser.add_argument("--mesas-dir", type=Path, default=None,
                        help="Shared mesas dir (default: SHARED_MESAS_DIR)")
    args = parser.parse_args()

    lab_dir = args.lab_dir if args.lab_dir.is_absolute() else ROOT / args.lab_dir
    mesas_dir = args.mesas_dir
    if mesas_dir is None:
        mesas_dir = SHARED_MESAS_DIR
    elif not mesas_dir.is_absolute():
        mesas_dir = ROOT / mesas_dir
    manifest_path = lab_dir / "manifest.json"
    index_path = lab_dir / "index.jsonl"
    out_html = lab_dir / "galeria_prototype.html"
    alert_index_path = lab_dir / "alert_index.json"
    meta_path = lab_dir / "prototype_meta.json"

    if not manifest_path.exists() or not index_path.exists():
        raise SystemExit(f"Missing manifest or index in {lab_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    index_rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    index_by = {r["mesa_key"]: r for r in index_rows}

    alert_index, stats = build_alert_index(index_rows, mesas_dir)
    alert_index_path.write_text(json.dumps(alert_index, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "SDD prototype — transversal visual review (moderator/admin)",
        "data_source": "index.jsonl from cross_mesa_validation stored arrays — NO reprocess",
        "local_gallery": {
            "production": str(lab_dir / "galeria.html"),
            "prototype_compatible": str(out_html),
            "manifest": str(manifest_path),
            "alert_index": str(alert_index_path),
            "mesas_dir": str(mesas_dir),
        },
        "alert_rules": {
            "auto_resolved_class": "confirmed_blank",
            "human_review_classes": ["partial", "unreadable", "missing"],
            "external_groups": list(stats["human_alerts_by_field"].keys()),
            "internal_sources": ["e14c", "e14d", "e14t"],
            "note": "confirmed_blank skips accept/reject; only uncertain empties prompt human per field × E14",
        },
        "sdd_target": {
            "roles": ["moderator", "admin"],
            "skip_reprocess": True,
            "compatible_with_labeler_reports": True,
        },
        "stats": stats,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    from scripts.prepare_gallery import load_dept_map

    base_html = build_html(
        manifest,
        mesas_dir=mesas_dir,
        mesas_url_prefix=_mesas_url_prefix(mesas_dir, out_html.parent),
        dept_names=load_dept_map(),
        title="[PROTOTIPO SDD] E14C Conflictivas — alertas compatibles",
        project_id="E14C_conflictivas_prototype",
        decisions_filename="decisiones_prototype_compatible.json",
        alerts_per_mesa={},
    )
    html = _patch_compatible_script(base_html, alert_index)
    out_html.write_text(html, encoding="utf-8")

    print(f"alert_index: {alert_index_path} ({len(alert_index):,} mesas)")
    print(f"prototype_meta: {meta_path}")
    print(f"prototype gallery: {out_html}")
    print(f"  human review mesas: {stats['mesas_human_review']:,} / {stats['mesas']:,}")
    print(f"  auto-only mesas: {stats['mesas_auto_only']:,}")
    print(f"Open: file:///{out_html.as_posix()}")


if __name__ == "__main__":
    main()