"""
HTML report of a run (<name>_report.html): one self-contained page (the plots embedded) with the model,
mesh, results, antenna / convergence sections when they apply, the run statistics, warnings, output
files, settings and the full log. Written from a finished pipeline.Job, also after --preview.
"""
import base64, datetime, html, os, sys
import numpy as np

CSS = """
:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#5f6670; --line:#d8dbe0; --card:#ffffff; --accent:#1f5f99;
        --warn-bg:#fff4e0; --warn:#8a5300; --ok:#1d7a46; --bad:#b42318; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa1ab; --line:#33373e; --card:#1e2126; --accent:#6aa7e0;
          --warn-bg:#3a2c12; --warn:#f0b75a; --ok:#5cc28a; --bad:#f07167; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:1180px; margin:0 auto; padding:24px 16px 64px; }
header h1 { margin:0 0 4px; font-size:26px; }
.meta { color:var(--muted); font-size:13px; }
nav { margin:16px 0 8px; display:flex; flex-wrap:wrap; gap:6px 14px; font-size:14px; }
nav a { color:var(--accent); text-decoration:none; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 20px; margin:18px 0; }
h2 { margin:0 0 12px; font-size:19px; }
h3 { margin:18px 0 8px; font-size:15px; color:var(--muted); }
table { border-collapse:collapse; width:100%; font-size:13.5px; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:5px 10px 5px 0; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
.scroll { overflow-x:auto; }
img { max-width:100%; height:auto; border:1px solid var(--line); border-radius:6px; background:#fff; margin:6px 0; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px; }
.kpi { border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.kpi .v { font-size:20px; font-weight:600; }
.kpi .l { color:var(--muted); font-size:12.5px; }
.warn { background:var(--warn-bg); color:var(--warn); border-radius:8px; padding:10px 14px; }
.warn ul { margin:6px 0 0 18px; padding:0; }
.ok { color:var(--ok); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
pre { white-space:pre-wrap; word-break:break-word; font-size:12.5px; background:var(--bg); border:1px solid var(--line);
      border-radius:6px; padding:10px; max-height:520px; overflow:auto; }
code { font-size:12.5px; }
a { color:var(--accent); }
"""


def _e(x):
    return html.escape(str(x))


def _img(path, alt=''):
    try:
        with open(path, 'rb') as fh:
            data = base64.b64encode(fh.read()).decode()
    except OSError:
        return f"<p class='meta'>(missing image {_e(path)})</p>"
    return f"<img alt='{_e(alt)}' src='data:image/png;base64,{data}'>"


def _table(head, rows):
    h = "".join(f"<th>{_e(c)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>"


def _kpis(items):
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{_e(l)}</div></div>"
                                          for l, v in items) + "</div>"


def _rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def _db(v):
    return f"{20 * np.log10(abs(v) + 1e-15):.2f}" if v == v else "–"


def _sparam_rows(freq, S, names):
    """Per S-parameter: |S| in dB at fmin, centre and fmax, and its minimum / maximum over the band."""
    rows, n = [], S.shape[1]
    ic = len(freq) // 2
    for j in range(n):
        for i in range(j, n):
            s = S[:, i, j]
            if not np.isfinite(s).any():
                continue
            db = 20 * np.log10(np.abs(s[np.isfinite(s)]) + 1e-15)
            fb = freq[np.isfinite(s)]
            rows.append([f"S({_e(names[i])},{_e(names[j])})", _db(s[0]), _db(s[ic]), _db(s[-1]),
                         f"{db.min():.2f} @ {fb[np.argmin(db)] / 1e9:.3f} GHz",
                         f"{db.max():.2f} @ {fb[np.argmax(db)] / 1e9:.3f} GHz"])
    return rows


def build(job):
    a, m, g = job.a, job.m, job.grid['info']
    base = os.path.dirname(os.path.abspath(job.path('_report.html')))
    mode = 'preview' if a.preview else ('antenna' if job.antenna else 'S-parameters')
    nav, body = [], []

    def section(sid, title, content):
        nav.append(f"<a href='#{sid}'>{_e(title)}</a>")
        body.append(f"<section id='{sid}'><h2>{_e(title)}</h2>{content}</section>")

    # ---- summary ----------------------------------------------------------------------------------
    k = [('mode', _e(mode)), ('band', f"{a.fmin:g}–{a.fmax:g} GHz"),
         ('ports', _e(", ".join(p.name for p in m.ports))), ('mesh', f"{g['cells'] / 1e6:.2f} M cells"),
         ('time step', f"{g['dt_ps']:.3f} ps")]
    if job.ant:
        mt = job.ant['match']
        row = min(job.ant['rows'], key=lambda r: abs(r['f'] - job.ant['f_pattern']))
        k += [('resonance', f"{mt['f_res'] / 1e9:.3f} GHz"), ('S11 at resonance', f"{mt['s11_db']:.1f} dB"),
              ('−10 dB band', f"{mt['bw10'] / mt['f_res'] * 100:.1f} %" if mt['bw10'] else 'none'),
              ('directivity', f"{row['D_dbi']:.2f} dBi" if row['D_dbi'] is not None else '–'),
              ('efficiency', f"{row['eff'] * 100:.0f} %"),
              ('gain', f"{row['G_dbi']:.2f} dBi" if row['G_dbi'] is not None else '–')]
    if job.levels and 'convergence' in job.info:
        ok = job.info['convergence']['converged']
        k.append(('mesh convergence', f"<span class='{'ok' if ok else 'bad'}'>{'converged' if ok else 'not converged'}</span>"))
    if job.result is not None:
        k.append(('run time', f"{sum(v for v in job.result.seconds.values() if v):.0f} s solver"))
    warn = job.warnings
    w = (f"<div class='warn'><b>{len(warn)} warning(s)</b><ul>" + "".join(f"<li>{_e(x)}</li>" for x in warn) +
         "</ul></div>") if warn else "<p class='ok'>No warnings.</p>"
    section('summary', 'Summary', _kpis(k) + "<h3>Warnings</h3>" + w)

    # ---- model ----------------------------------------------------------------------------------------
    stack = [[_e(l['name']), f"{l['z0']:.3f}–{l['z1']:.3f}", f"{l['z1'] - l['z0']:.3f}", f"{l['er']:.2f}",
              f"{l['tand']:.4f}"] for l in m.layers[::-1]]
    cu = [[_e(n), f"{m.cu_z[n]:.3f}", 'yes' if n in m.copper else 'nothing selected'] for n in m.cu_order[::-1]]
    ports = []
    for p in m.ports:
        where = f"({p.c[0]:.3f}, {-p.c[1]:.3f})"
        if p.kind == 'gap':
            ports.append([_e(p.name), 'gap', _e(p.layer), f"gap {p.gap[1] - p.gap[0]:.3f} mm along {p.direction}",
                          f"{p.w:.3f}", f"{p.z0:g}", where])
        else:
            ports.append([_e(p.name), 'vertical', f"{_e(p.layer)} → {_e(p.ref_layer)}",
                          f"h {abs(p.z_sig - p.z_ref):.3f} mm", f"{p.w:.3f}", f"{p.z0:g}", where])
    ob = m.outline.bounds
    content = (f"<p>Board <code>{_e(a.board)}</code>: outline {ob[2] - ob[0]:.2f} × {ob[3] - ob[1]:.2f} mm, "
               f"{len(m.vias)} vias ({sum(m.via_signal)} on signal nets), nets "
               f"{_e(a.nets or 'all')}, dielectric loss {_e(a.diel)}.</p>"
               "<h3>Dielectrics (top to bottom)</h3>" +
               _table(['layer', 'z (mm)', 'thickness (mm)', 'εr', 'tan δ'], stack) +
               "<h3>Copper</h3>" + _table(['layer', 'z (mm)', 'modelled'], cu) +
               "<h3>Ports</h3>" + _table(['port', 'kind', 'layer', 'size', 'width (mm)', 'Z0 (Ω)', 'at (KiCad mm)'], ports))
    if m.parts:
        content += "<h3>Lumped parts</h3>" + _table(
            ['part', 'value', 'layer', 'gap (mm)', 'openEMS elements'],
            [[_e(p.ref), _e(p.value_str()), _e(p.layer), f"{p.gap:.3f}", str(len(p.pieces()))] for p in m.parts])
    if m.notes:
        content += "<h3>Notes</h3><ul>" + "".join(f"<li>{_e(n)}</li>" for n in m.notes) + "</ul>"
    section('model', 'Model', content)

    # ---- mesh ------------------------------------------------------------------------------------------
    rows = [['cells', f"{g['nx']} × {g['ny']} × {g['nz']} = {g['cells']:,}"],
            ['fine cell (copper edges)', f"{g['fine_mm']:.4f} mm"],
            ['largest cell (dielectric / air)', f"{g['maxres_die_mm']:.3f} / {g['maxres_air_mm']:.3f} mm"],
            ['smallest cell x / y / z', " / ".join(f"{v:.4f}" for v in g['min_cell_mm']) + " mm"],
            ['time step (estimate)', f"{g['dt_ps']:.4f} ps"], ['memory (estimate)', f"{g['mem_gb']:.2f} GB"],
            ['air margin', f"{g['air_mm']:.1f} mm"],
            ['boundaries', _e(" ".join(f"{s}={b}" for s, b in zip(('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'),
                                                                   g['bc'])))],
            ['curves / vias', f"{_e(g['curves'])} / {_e(g['vias'])} ({g['vias_snapped']} snapped)"],
            ['lines across port traces', _e(", ".join(f"{k}: {v}" for k, v in g.get('lines_across_ports', {}).items()))],
            ['excitation pulse', f"~{job.pulse:,} timesteps; --max-steps {a.max_steps:,}"]]
    content = _table(['', ''], rows) + _img(job.path('_mesh.png'), 'model and mesh')
    if job.levels:
        content += "<h3>Convergence levels</h3>" + _table(
            ['factor', 'cells', 'fine (mm)', 'time step (ps)'],
            [[f"×{f:g}", f"{lm['info']['cells']:,}", f"{lm['info']['fine_mm']:.4f}", f"{lm['info']['dt_ps']:.4f}"]
             for f, lm in job.levels])
    section('mesh', 'Mesh', content)

    # ---- results -------------------------------------------------------------------------------------
    if job.result is not None:
        rows = _sparam_rows(job.freq, job.Smm, job.names)
        content = (_img(job.path('_openems.png'), 'S-parameters') +
                   f"<h3>|S| in dB</h3>" + _table(['', f"{a.fmin:g} GHz", f"{job.freq[len(job.freq) // 2] / 1e9:g} GHz",
                                                   f"{a.fmax:g} GHz", 'minimum', 'maximum'], rows))
        if a.compare:
            content += f"<p class='meta'>Dashed: <code>{_e(a.compare)}</code></p>"
        section('results', 'S-parameters', content)
    if job.ant:
        rows = [[f"{r['f'] / 1e9:.3f}" + (' ◀' if abs(r['f'] - job.ant['f_pattern']) < 1 else ''),
                 '–' if r['D_dbi'] is None else f"{r['D_dbi']:.2f}", f"{r['eff'] * 100:.1f}",
                 '–' if r['G_dbi'] is None else f"{r['G_dbi']:.2f}",
                 '–' if r['Grealized_dbi'] is None else f"{r['Grealized_dbi']:.2f}"] for r in job.ant['rows']]
        section('antenna', 'Antenna',
                _img(job.path('_antenna.png'), 'antenna') + _img(job.path('_pattern.png'), 'patterns') +
                "<h3>Over frequency (◀ pattern frequency)</h3>" +
                _table(['f (GHz)', 'directivity (dBi)', 'efficiency (%)', 'gain (dBi)', 'realized gain (dBi)'], rows))
    if job.levels and 'convergence' in job.info:
        c = job.info['convergence']
        rows = [[f"×{l['factor']:g}", f"{l['cells']:,}",
                 f"{l['max_db']:.3f}" if 'max_db' in l else 'reference', f"{l['max_deg']:.2f}" if 'max_deg' in l else '']
                for l in c['levels']]
        section('convergence', 'Mesh convergence',
                f"<p>Tolerance {c['tol_db']:g} dB / {c['tol_deg']:g}°: <span class='{'ok' if c['converged'] else 'bad'}'>"
                f"{'converged' if c['converged'] else 'not converged'}</span></p>" +
                _table(['level', 'cells', 'max |Δ| (dB)', 'max Δ phase (°)'], rows) +
                _img(job.path('_converge.png'), 'convergence'))
    if getattr(job, 'dump_pngs', None):
        section('dumps', 'Field maps', "".join(f"<h3>{_e(l)}</h3>{_img(p, l)}" for l, p in job.dump_pngs))

    # ---- runs, files, settings, log ------------------------------------------------------------------
    if job.result is not None:
        from .simulation import _run_dir, log_path
        rows = []
        for k_ in job.excite:
            name = m.ports[k_].name
            lp = log_path(_run_dir(m, k_, a.out))
            steps = job.result.steps.get(name)
            rows.append([_e(name), f"{job.result.seconds.get(name, 0):.0f} s", f"{steps:,}" if steps else '–',
                         f"<a href='{_e(_rel(lp, base))}'>openems.log</a>" if os.path.exists(lp) else '–'])
        section('runs', 'openEMS runs', _table(['driven port', 'time', 'timesteps', 'solver output'], rows))
    files = [[_e(l), f"<a href='{_e(_rel(p, base))}'>{_e(os.path.basename(p))}</a>"] for l, p in job.files]
    section('files', 'Output files', _table(['', 'file'], files))
    opts = [[f"<code>{_e(k_)}</code>", _e(v)] for k_, v in sorted(vars(a).items()) if k_ != 'config_notes']
    section('settings', 'Settings', _table(['option', 'value'], opts))
    section('log', 'Log', f"<details><summary>{len(job.messages)} lines</summary><pre>{_e(chr(10).join(job.messages))}</pre></details>")

    from . import __version__
    head = (f"<header><h1>{_e(job.name)}</h1><div class='meta'>kicad2openems {__version__} · "
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · {_e(mode)} · "
            f"<code>{_e(' '.join(['kicad2openems'] + sys.argv[1:]))}</code>"
            f"</div></header>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(job.name)} – kicad2openems</title><style>{CSS}</style></head><body><main>{head}"
            f"<nav>{''.join(nav)}</nav>{''.join(body)}</main></body></html>")


def write_report(job):
    path = job.path('_report.html')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(build(job))
    return path
