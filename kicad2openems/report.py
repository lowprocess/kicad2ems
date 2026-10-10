"""
HTML report of a run (<name>_report.html): one self-contained page (the plots embedded) rendered from
the run summary (pipeline.Job.summary, also written as <name>_openems.json) and nothing else - so a GUI,
or a later run, can rebuild it from the JSON file. Sections: summary, model, transmission-line
sections, mesh, results (with the fast line model compared), antenna, convergence, field maps, the
openEMS runs, output files, settings and the full log.
"""
import base64, html, os

CSS = """
:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#5f6670; --line:#d8dbe0; --card:#ffffff; --accent:#1f5f99;
        --warn-bg:#fff4e0; --warn:#8a5300; --ok:#1d7a46; --bad:#b42318; --zw:#fdf0d5; --zb:#fbe1de; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa1ab; --line:#33373e; --card:#1e2126; --accent:#6aa7e0;
          --warn-bg:#3a2c12; --warn:#f0b75a; --ok:#5cc28a; --bad:#f07167; --zw:#3d3218; --zb:#46201d; } }
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
td.n, th.n { text-align:right; padding-right:14px; }
td.zw { background:var(--zw); } td.zb { background:var(--zb); }
tr.branch td { color:var(--muted); font-style:italic; }
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
    except (OSError, TypeError):
        return f"<p class='meta'>(missing image {_e(path)})</p>"
    return f"<img alt='{_e(alt)}' src='data:image/png;base64,{data}'>"


def _table(head, rows, num=()):
    """rows: lists of cells; a cell may be (html, css class). num: indexes of numeric columns."""
    h = "".join(f"<th class='{'n' if i in num else ''}'>{_e(c)}</th>" for i, c in enumerate(head))
    body = []
    for r in rows:
        cls = ''
        if isinstance(r, dict):
            cls, r = r.get('cls', ''), r['cells']
        cells = []
        for i, c in enumerate(r):
            c, k = c if isinstance(c, tuple) else (c, '')
            k = (k + (' n' if i in num else '')).strip()
            cells.append(f"<td class='{k}'>{c}</td>" if k else f"<td>{c}</td>")
        body.append(f"<tr class='{cls}'>" + "".join(cells) + "</tr>" if cls else "<tr>" + "".join(cells) + "</tr>")
    return f"<div class='scroll'><table><thead><tr>{h}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _kpis(items):
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{_e(l)}</div></div>"
                                          for l, v in items) + "</div>"


def _rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def _f(v, fmt='.2f', none='–'):
    return none if v is None else format(v, fmt)


def _zcell(z, zref, fmt='.1f'):
    """Z0 cell, shaded by its deviation from the port impedance (10 % / 25 %)."""
    if z is None:
        return ('–', '')
    d = abs(z - zref) / zref
    return (format(z, fmt), 'zb' if d > 0.25 else ('zw' if d > 0.10 else ''))


def _lines_section(sm):
    ln = sm['lines']
    parts = ["<p>Each port's net broken into uniform sections: trace runs (split where the planes under or over "
             "them change), component pads at their own width, via barrels and lumped parts, with closed-form "
             "quasi-static impedances. <b>Z0</b> uses the stackup's copper thickness; <b>Z0 sheet</b> is the same "
             "section with zero-thickness copper, as openEMS models it. Shading: more than 10 % / 25 % from the "
             "port impedance. A ballpark: no coupling between sections, no radiation.</p>"]
    if 'lines' in sm.get('images', {}):
        parts.append(_img(sm['images']['lines'], 'transmission-line sections'))
    for path in ln['paths']:
        zref = path['z_port'] or 50.0
        rows = []
        for i, r in enumerate(path['sections'], 1):
            what = _e(r['label'])
            if r['kind'] == 'part' and r.get('value'):
                what += f" <span class='meta'>{_e(r['value'])}</span>"
            note = "; ".join(r['notes'])
            xs = _e(r['xs']) + (f" <span class='meta'>s {r['s']:.3f}</span>" if r.get('s') else '')
            rows.append([str(i), what, _e(r['layer']), xs, _f(r['w'], '.3f'), _f(r['length'], '.3f'),
                         _zcell(r['z0'], zref), _zcell(r['z0_sheet'], zref), _f(r['eeff'], '.2f'),
                         _f(r['delay_ps'], '.1f'), _f(r['start'], '.2f'), _e(note)])
        for b in path['branches']:
            rows.append(dict(cls='branch', cells=['↳', _e(b['label']), '', 'branch', '', _f(b['length'], '.3f'),
                                                   _zcell(b['z0'], zref), '', '', '', _f(b['at'], '.2f'), '']))
        tot = sum(r['length'] for r in path['sections'])
        dl = sum(r['delay_ps'] or 0 for r in path['sections'])
        parts.append(f"<h3>{_e(path['port'])} → {_e(path['to'] or 'end of the trace')}: {len(path['sections'])} "
                     f"sections, {tot:.2f} mm, {dl:.0f} ps (port {zref:g} Ω)</h3>")
        parts.append(_table(['#', 'section', 'layer', 'cross-section', 'w (mm)', 'length (mm)', 'Z0 (Ω)',
                             'Z0 sheet (Ω)', 'εeff', 'delay (ps)', 'from (mm)', 'notes'], rows,
                            num=(4, 5, 6, 7, 8, 9, 10)))
    if ln['notes']:
        parts.append("<ul>" + "".join(f"<li>{_e(n)}</li>" for n in ln['notes']) + "</ul>")
    return "".join(parts)


def _fast_section(sm, base):
    fa = sm['fast']
    txt = (f"<p>The transmission-line sections solved as a circuit with <b>{_e(fa['backend'])}</b> in "
           f"{fa['seconds'] * 1e3:.0f} ms (zero-thickness copper, like the openEMS model): "
           f"<a href='{_e(_rel(fa['touchstone'], base))}'>{_e(os.path.basename(fa['touchstone']))}</a>.</p>")
    if fa.get('compare'):
        runs = sm.get('runs', {})
        t = sum(r.get('seconds') or 0 for r in runs.values())
        txt += (f"<p>Against openEMS ({t:.0f} s): dotted lines in the S-parameter plot. Large differences point at "
                "something one of the two models leaves out - coupling, radiation, plane resonances, the lumped "
                "ports - or at a mesh problem.</p>")
        rows = [[_e(r['param']), _f(r['openems_max_db'], '.1f'), _f(r['other_max_db'], '.1f'), _f(r['max_ddb'], '.2f'),
                 _f(r['max_dvec'], '.3f'), _f(r['ddelay_ps'], '+.1f')] for r in fa['compare']]
        txt += _table(['', 'openEMS max (dB)', 'line model max (dB)', 'max |Δ| (dB, transmissions)',
                       'max |ΔS| (vector)', 'Δ delay line − openEMS (ps)'], rows, num=(1, 2, 3, 4, 5))
    elif 'fast' in sm.get('images', {}):
        txt += _img(sm['images']['fast'], 'fast line model')
    return txt


def _resolved(sm, base):
    """A copy of the summary with its paths (stored relative to the output folder) made absolute."""
    ab = lambda p: p if (not p or os.path.isabs(p)) else os.path.join(base, p)
    sm = dict(sm)
    sm['images'] = {k: ab(v) for k, v in sm.get('images', {}).items()}
    sm['files'] = [(l, ab(p)) for l, p in sm.get('files', [])]
    sm['dumps'] = [dict(d, png=ab(d['png'])) for d in sm.get('dumps', [])]
    if sm.get('fast'):
        sm['fast'] = dict(sm['fast'], touchstone=ab(sm['fast']['touchstone']))
    if sm.get('runs'):
        sm['runs'] = {k: dict(r, log=ab(r.get('log'))) for k, r in sm['runs'].items()}
    return sm


def build(sm, base):
    """The report page for a run summary; `base` is the output folder (where the report goes), against
    which the summary's relative paths are resolved and links are made relative."""
    sm = _resolved(sm, base)
    model, g, st = sm['model'], sm['mesh'], sm['settings']
    nav, body = [], []

    def section(sid, title, content):
        nav.append(f"<a href='#{sid}'>{_e(title)}</a>")
        body.append(f"<section id='{sid}'><h2>{_e(title)}</h2>{content}</section>")

    # ---- summary ----------------------------------------------------------------------------------
    k = [('mode', _e(sm['mode'])), ('band', f"{st['fmin']:g}–{st['fmax']:g} GHz"),
         ('ports', _e(", ".join(p['name'] for p in model['ports']))), ('mesh', f"{g['cells'] / 1e6:.2f} M cells"),
         ('time step', f"{g['dt_ps']:.3f} ps")]
    ant = sm.get('antenna')
    if ant:
        mt = ant['match']
        row = min(ant['rows'], key=lambda r: abs(r['f'] - ant['f_pattern']))
        k += [('resonance', f"{mt['f_res'] / 1e9:.3f} GHz"), ('S11 at resonance', f"{mt['s11_db']:.1f} dB"),
              ('−10 dB band', f"{mt['bw10'] / mt['f_res'] * 100:.1f} %" if mt['bw10'] else 'none'),
              ('directivity', f"{row['D_dbi']:.2f} dBi" if row['D_dbi'] is not None else '–'),
              ('efficiency', f"{row['eff'] * 100:.0f} %"),
              ('gain', f"{row['G_dbi']:.2f} dBi" if row['G_dbi'] is not None else '–')]
    if 'convergence' in sm:
        ok = sm['convergence']['converged']
        k.append(('mesh convergence', f"<span class='{'ok' if ok else 'bad'}'>{'converged' if ok else 'not converged'}</span>"))
    if 'runs' in sm:
        k.append(('run time', f"{sum(r.get('seconds') or 0 for r in sm['runs'].values()):.0f} s solver"))
    if 'fast' in sm:
        k.append(('line model', f"{_e(sm['fast']['backend'])}, {sm['fast']['seconds'] * 1e3:.0f} ms"))
    warn = sm.get('warnings', [])
    w = (f"<div class='warn'><b>{len(warn)} warning(s)</b><ul>" + "".join(f"<li>{_e(x)}</li>" for x in warn) +
         "</ul></div>") if warn else "<p class='ok'>No warnings.</p>"
    section('summary', 'Summary', _kpis(k) + "<h3>Warnings</h3>" + w)

    # ---- model ----------------------------------------------------------------------------------------
    stack = [[_e(l['name']), f"{l['z0']:.3f}–{l['z1']:.3f}", f"{l['z1'] - l['z0']:.3f}", f"{l['er']:.2f}",
              f"{l['tand']:.4f}"] for l in model['stack']]
    cu = [[_e(c['layer']), f"{c['z']:.3f}", 'yes' if c['modelled'] else 'nothing selected'] for c in model['copper']]
    ports = []
    for p in model['ports']:
        where = f"({p['at'][0]:.3f}, {p['at'][1]:.3f})"
        if p['kind'] == 'gap':
            ports.append([_e(p['name']), 'gap', _e(p['layer']), f"gap {p['gap']:.3f} mm along {p['direction']}",
                          f"{p['w']:.3f}", f"{p['z0']:g}", where])
        else:
            ports.append([_e(p['name']), 'vertical', f"{_e(p['layer'])} → {_e(p['ref_layer'])}", f"h {p['h']:.3f} mm",
                          f"{p['w']:.3f}", f"{p['z0']:g}", where])
    nets = model['nets'] if isinstance(model['nets'], str) else ", ".join(model['nets'])
    content = (f"<p>Board <code>{_e(model['board'])}</code>: outline {model['outline_mm'][0]:.2f} × "
               f"{model['outline_mm'][1]:.2f} mm, {model['vias']} vias ({model['vias_signal']} on signal nets), nets "
               f"{_e(nets)}, dielectric loss {_e(model['diel'])}.</p>"
               "<h3>Dielectrics (top to bottom)</h3>" +
               _table(['layer', 'z (mm)', 'thickness (mm)', 'εr', 'tan δ'], stack) +
               "<h3>Copper</h3>" + _table(['layer', 'z (mm)', 'modelled'], cu) +
               "<h3>Ports</h3>" + _table(['port', 'kind', 'layer', 'size', 'width (mm)', 'Z0 (Ω)', 'at (KiCad mm)'], ports))
    if model['parts']:
        content += "<h3>Lumped parts</h3>" + _table(
            ['part', 'value', 'layer', 'gap (mm)', 'openEMS elements'],
            [[_e(p['ref']), _e(p['value']), _e(p['layer']), f"{p['gap']:.3f}", str(p['elements'])] for p in model['parts']])
    if model['notes']:
        content += "<h3>Notes</h3><ul>" + "".join(f"<li>{_e(n)}</li>" for n in model['notes']) + "</ul>"
    section('model', 'Model', content)

    # ---- transmission lines -----------------------------------------------------------------------------
    if sm.get('lines'):
        section('tlines', 'Transmission-line sections', _lines_section(sm))

    # ---- mesh ------------------------------------------------------------------------------------------
    rows = [['cells', f"{g['nx']} × {g['ny']} × {g['nz']} = {g['cells']:,}"],
            ['fine cell (copper edges)', f"{g['fine_mm']:.4f} mm"],
            ['largest cell (dielectric / air)', f"{g['maxres_die_mm']:.3f} / {g['maxres_air_mm']:.3f} mm"],
            ['smallest cell x / y / z', " / ".join(f"{v:.4f}" for v in g['min_cell_mm']) + " mm"],
            ['time step (estimate)', f"{g['dt_ps']:.4f} ps"], ['memory (estimate)', f"{g['mem_gb']:.2f} GB"],
            ['air margin', f"{g['air_mm']:.1f} mm"],
            ['boundaries', _e(" ".join(f"{s}={b}" for s, b in zip(('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'), g['bc'])))],
            ['curves / vias', f"{_e(g['curves'])} / {_e(g['vias'])} ({g['vias_snapped']} snapped)"],
            ['lines across port traces', _e(", ".join(f"{k_}: {v}" for k_, v in g.get('lines_across_ports', {}).items()))],
            ['excitation pulse', f"~{sm.get('pulse_steps', 0):,} timesteps; --max-steps {st['max_steps']:,}"]]
    content = _table(['', ''], rows) + _img(sm['images'].get('mesh'), 'model and mesh')
    if sm.get('levels'):
        content += "<h3>Convergence levels</h3>" + _table(
            ['factor', 'cells', 'fine (mm)', 'time step (ps)'],
            [[f"×{l['factor']:g}", f"{l['cells']:,}", f"{l['fine_mm']:.4f}", f"{l['dt_ps']:.4f}"] for l in sm['levels']])
    section('mesh', 'Mesh', content)

    # ---- results -------------------------------------------------------------------------------------
    res = sm.get('results')
    if res:
        fq = res['freq_ghz']
        rows = [[_e(r['param']), _f(r['fmin_db']), _f(r['fc_db']), _f(r['fmax_db']),
                 f"{r['min_db']:.2f} @ {r['f_min'] / 1e9:.3f} GHz", f"{r['max_db']:.2f} @ {r['f_max'] / 1e9:.3f} GHz"]
                for r in res['table']]
        content = (_img(sm['images'].get('sparams'), 'S-parameters') + "<h3>|S| in dB</h3>" +
                   _table(['', f"{fq[0]:g} GHz", f"{fq[1]:g} GHz", f"{fq[2]:g} GHz", 'minimum', 'maximum'], rows,
                          num=(1, 2, 3)))
        if res.get('compare_file'):
            content += f"<p class='meta'>Dashed: <code>{_e(res['compare_file'])}</code></p>"
        section('results', 'S-parameters', content)
    if 'fast' in sm:
        section('fast', 'Fast line model', _fast_section(sm, base))
    if ant:
        rows = [[f"{r['f'] / 1e9:.3f}" + (' ◀' if abs(r['f'] - ant['f_pattern']) < 1 else ''),
                 '–' if r['D_dbi'] is None else f"{r['D_dbi']:.2f}", f"{r['eff'] * 100:.1f}",
                 '–' if r['G_dbi'] is None else f"{r['G_dbi']:.2f}",
                 '–' if r['Grealized_dbi'] is None else f"{r['Grealized_dbi']:.2f}"] for r in ant['rows']]
        section('antenna', 'Antenna',
                _img(sm['images'].get('antenna'), 'antenna') + _img(sm['images'].get('pattern'), 'patterns') +
                "<h3>Over frequency (◀ pattern frequency)</h3>" +
                _table(['f (GHz)', 'directivity (dBi)', 'efficiency (%)', 'gain (dBi)', 'realized gain (dBi)'], rows))
    if 'convergence' in sm:
        c = sm['convergence']
        rows = [[f"×{l['factor']:g}", f"{l['cells']:,}",
                 f"{l['max_db']:.3f}" if 'max_db' in l else 'reference', f"{l['max_deg']:.2f}" if 'max_deg' in l else '']
                for l in c['levels']]
        section('convergence', 'Mesh convergence',
                f"<p>Tolerance {c['tol_db']:g} dB / {c['tol_deg']:g}°: <span class='{'ok' if c['converged'] else 'bad'}'>"
                f"{'converged' if c['converged'] else 'not converged'}</span></p>" +
                _table(['level', 'cells', 'max |Δ| (dB)', 'max Δ phase (°)'], rows) +
                _img(sm['images'].get('converge'), 'convergence'))
    if sm.get('dumps'):
        section('dumps', 'Field maps', "".join(f"<h3>{_e(d['label'])}</h3>{_img(d['png'], d['label'])}" for d in sm['dumps']))

    # ---- runs, files, settings, log ------------------------------------------------------------------
    if sm.get('runs'):
        rows = []
        for name, r in sm['runs'].items():
            lp = r.get('log')
            rows.append([_e(name), f"{r.get('seconds') or 0:.0f} s", f"{r['steps']:,}" if r.get('steps') else '–',
                         f"<a href='{_e(_rel(lp, base))}'>openems.log</a>" if lp and os.path.exists(lp) else '–'])
        section('runs', 'openEMS runs', _table(['driven port', 'time', 'timesteps', 'solver output'], rows))
    files = [[_e(l), f"<a href='{_e(_rel(p, base))}'>{_e(os.path.basename(p))}</a>"] for l, p in sm['files']]
    section('files', 'Output files', _table(['', 'file'], files))
    opts = [[f"<code>{_e(k_)}</code>", _e(v)] for k_, v in sorted(st.items())]
    section('settings', 'Settings', _table(['option', 'value'], opts))
    log = sm.get('log', [])
    section('log', 'Log', f"<details><summary>{len(log)} lines</summary><pre>{_e(chr(10).join(log))}</pre></details>")

    cmd = f" · <code>{_e(sm['command'])}</code>" if sm.get('command') else ''
    head = (f"<header><h1>{_e(sm['name'])}</h1><div class='meta'>kicad2openems {_e(sm['version'])} · "
            f"{_e(sm['created'])} · {_e(sm['mode'])}{cmd}</div></header>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(sm['name'])} – kicad2openems</title><style>{CSS}</style></head><body><main>{head}"
            f"<nav>{''.join(nav)}</nav>{''.join(body)}</main></body></html>")


def write_report(sm, path):
    """Write the report page for a run summary to `path`."""
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(build(sm, os.path.dirname(os.path.abspath(path))))
    return path
