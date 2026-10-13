"""
openEMS as a solver plugin: the board lowered to boundaries, a rectilinear mesh (or the --converge levels) and
CSXCAD geometry, solved by openEMS's FDTD engine; antenna mode, field dumps and mesh convergence are its own.

    prepare  boundaries (bc), the mesh (auto_mesh / mesh_levels) with the edge walls snapped onto it, the
             closed-box resonance check, the field-dump specs, the excitation length; the mesh preview
    solve    one openEMS run per driven port (--jobs in parallel), the coarser --converge levels first; in
             antenna mode one run with the far field
    outputs  field-dump maps, the CSX file, per-run times, antenna results and plots, the convergence table

What openEMS changes on the way (declared deviations, docs/architecture.md): vias snapped onto mesh lines
(--vias), parts with ESR / ESL split in two elements (openEMS 0.37), edge walls moved onto mesh lines.
"""
import os
from types import SimpleNamespace

import numpy as np

from ...options import Option, strlist, items, floatlist, floats
from .. import Solver, Result, Deviation


def _bc(v):
    return ",".join(strlist(v) or ['auto'])


G_MESH, G_RUN, G_DUMP = 'openEMS/Mesh', 'openEMS/Run', 'openEMS/Field dumps'   # the GUI's openEMS sub-sections
G_ANT, G_SHARED_DUMP = 'antenna', 'field dumps'           # shared with mom25d: the GUI's Antenna and Field dumps
# --solvers auto (docs/architecture.md#choosing-solvers), fitted on openEMS runs of the 13 examples (24 cores):
# after the pulse the fields ring down for RING x |end_db| / 17.4 dB x the line model's peak group delay (a
# resonance's energy falls 17.4 dB per group delay; measured 0.4..3x that, median 1.7, and far longer where the
# board rings without the line model showing it: rf_gcpw, rf_stubnotch), or PULSES - 1 pulse lengths without a line
# model; CELLS_PER_CORE_S cell updates per second and core (13..35 M, median 21 M). Time within 2x on 9 of 13.
RING, PULSES, CELLS_PER_CORE_S = 1.7, 5.0, 21e6


def _peak_delay(run):
    """The line model's largest group delay (s) over its terms that carry signal: how long the board rings. None
    without a line model or a circuit solver for it."""
    from ..lines.fastsolve import solve, resolve_backend
    if run.lm is None or not run.lm.paths:
        return None
    try:
        f = np.linspace(run.fmin, run.fmax, 801)
        S, _ = solve(run.lm, run.m.ports, f, backend=resolve_backend('auto', getattr(run.s, 'qucsator', None))[0])
    except Exception:                                               # no scikit-rf / qucsator, a failing netlist
        return None
    best = None
    for s in S.reshape(len(f), -1).T:
        if np.all(np.isfinite(s)) and np.abs(s).max() > 1e-3:
            g = -np.gradient(np.unwrap(np.angle(s)), 2 * np.pi * f)
            g = g[np.abs(s) > 0.1 * np.abs(s).max()]
            best = max(best or 0.0, float(g.max()))
    return best


class OpenEMS(Solver):
    name, aliases, label = 'openems', ('fdtd',), 'openEMS (FDTD)'
    consumes, previewed, rank = 'board', True, 2
    capabilities = dict(ports='yes', gap_ports='yes', diff_pairs='yes',
                        parts='approximated: parts with ESR / ESL split in two elements (openEMS 0.37)',
                        vias='approximated: off the signal nets moved onto mesh lines (--vias, --via-snap)',
                        outline='yes', planes='yes', edge_walls='yes: PEC sheets', thickness='ignored: conducting sheets',
                        conductor_loss='yes', dispersion='yes: fitted at mid-band, or wideband Debye', radiation='yes',
                        cavity='yes', antenna='yes')
    options = [
        Option('res', 20.0, "cells per wavelength at fmax in each medium (default {default})", float,
               gui='Cells per wavelength', group=G_MESH),
        Option('fine', None, "mm, cell size at copper edges (default: the narrowest port trace or twice the thinnest "
               "dielectric, whichever is smaller, / 4)", float, gui='Fine cell at copper edges (mm, empty: auto)',
               group=G_MESH),
        Option('air', None, "mm of air between the board and the boundary (default 5; antenna mode: a quarter "
               "wavelength at the band centre, at least 5)", float, group=G_MESH),
        Option('ratio', 1.4, "max growth between neighbouring cells (default {default})", float, group=G_MESH),
        Option('nz', 4, "minimum cells through each dielectric layer (default {default})", int,
               gui='Cells through each dielectric', group=G_MESH),
        Option('z_sheet', 1.0, "first z cell either side of a copper layer with signal edges, as a multiple of the "
               "fine cell, graded into the dielectric (default {default}; 0: uniform, --nz cells)", float,
               gui='First z cell at signal copper (x fine)', group=G_MESH),
        Option('curve_res', 6.0, "over curved copper (arcs, fans) the largest cell is the dielectric's / this "
               "(default {default}; 1: no refinement)", float, gui='Refinement over curved copper', group=G_MESH),
        Option('curves', 'extremes', "mesh lines for rounded corners / arcs / circles: only the ends and extremes of "
               "each curve (default) or every vertex", choices=('extremes', 'vertices'), group=G_MESH),
        Option('vias', 'auto', "via mesh lines: auto = exact on signal nets, snap elsewhere; exact = centre and "
               "+-radius; centre = one line per axis; snap = move onto existing lines (within --via-snap)",
               choices=('auto', 'exact', 'centre', 'snap'), group=G_MESH),
        Option('via_snap', 0.2, "mm a snapped via may move (default {default})", float, group=G_MESH),
        Option('converge', None, "mesh convergence check: run on several mesh levels, level f with --res x f and "
               "--fine / f (e.g. 1,1.5,2); the finest level gives the result", list,
               conv=lambda v: floatlist(v) or None, gui='Convergence levels (e.g. 1,1.5)', group=G_MESH,
               cli=dict(metavar='F1,F2,...')),
        Option('converge_tol', (0.1, 2.0), "converged when the second-finest level is within this of the finest "
               "(default 0.1 dB, 2 deg)", tuple, conv=floats(2), group=G_MESH, cli=dict(metavar='DB,DEG')),
        Option('bc', 'auto', "boundaries: auto (PEC on a solid bottom plane, MUR elsewhere), MUR, PML_8 (any PML_n), "
               "PEC, PMC, or six values xmin,xmax,ymin,ymax,zmin,zmax", conv=_bc, group=G_RUN),
        Option('end_db', -40.0, "stop when the field energy has decayed by this much (default {default})", float,
               gui='Stop at (dB)', group=G_RUN),
        Option('end_on', 'ports', "ports (default): stop when the port signals have decayed by --end-db, or the field "
               "energy has; energy: only the field energy (openEMS's own criterion)", choices=('ports', 'energy'),
               gui='Stop on', group=G_RUN),
        Option('max_steps', 1_000_000, "timestep limit per run (default {default}); low frequencies and high-Q "
               "circuits need more", int, gui='Timestep limit', group=G_RUN),
        Option('diel', 'fixed', "dielectric loss: fixed = conductivity fitted at the band centre (default); wideband "
               "= Djordjevic-Sarkar model (er / tand constant-ish over the band) as Debye poles",
               choices=('fixed', 'wideband'), group=G_RUN),
        Option('diel_fref', 1.0, "GHz at which the stackup er / tand hold (wideband; default 1)", float, group=G_RUN),
        Option('excite', None, "comma-separated ports to excite (default all), e.g. P1; antenna mode: the antenna's "
               "port (openEMS and mom25d)", list, gui='Driven ports (empty: all)', group='solvers'),
        Option('threads', 0, "openEMS threads per run (default 0 = all cores; with --jobs above 1: half the cores, "
               "shared by the runs)", int, gui='Threads (0: openEMS default)', group=G_RUN),
        Option('jobs', 1, "excitations run in parallel (separate processes; default 1)", int, group=G_RUN),
        Option('openems_output', False, "show openEMS's own output instead of the progress / ETA line (it is always "
               "kept in openems.log of each run)", bool, group=G_RUN),
        Option('ff_freq', [], "antenna: frequency for the patterns (default: the resonance, minimum |S11|); also "
               "recorded", list, conv=floatlist, gui='Antenna: pattern frequencies (GHz; empty: the resonance)',
               group=G_ANT, cli=dict(metavar='GHz,...')),
        Option('ff_step', 2.0, "antenna: angle step of the pattern cuts (deg, default 2)", float, group=G_ANT),
        Option('dump', [], "field dump, repeatable: E|H|J @ layer[+-mm] | x=/y=/z= | volume | box=x0,y0,z0,x1,y1,z1 "
               "[@ frequencies in GHz -> frequency domain + PNG maps; none -> time domain VTK]. e.g. E@F.Cu+0.05@2,6 "
               "J@In2.Cu@4   E@y=7", list, conv=items, gui='Dumps (FIELD@REGION[@GHz]; ...)', group=G_DUMP,
               cli=dict(action='append', metavar='FIELD@REGION[@GHz,...]')),
        Option('dump_port', None, "ports whose excitation runs get the dumps (default: the first excited port)", list,
               gui='Runs that record dumps (empty: first driven)', group=G_SHARED_DUMP),
    ]

    def check(self, s=None):
        ok, lines = self.details()
        mods = {l.split()[1]: l.split(None, 2)[2] for l in lines if l.strip().startswith(('ok ', 'FAILED '))}
        if ok:
            return True, os.path.dirname(mods.get('openEMS', '')) or 'openEMS'
        unused = next((l.split('(not used: ', 1)[1][:-1] for l in lines
                       if l.startswith('openems_venv') and '(not used: ' in l), None)
        if unused:                                                  # the setting is why: say so first
            return False, unused
        return False, next((f"{m}: {e}" for m, e in mods.items() if any(l.strip().startswith(f'FAILED  {m}') for l in lines)),
                           'openEMS cannot be imported')

    def details(self):
        """openEMS's import check: (ok, report lines with the real errors and hints)."""
        from . import simulation
        return simulation.check_openems()

    def validate(self, s):
        if s.converge is not None and (len(set(s.converge)) < 2 or min(s.converge) <= 0):
            return f"converge needs at least two positive refinement factors, e.g. 1,1.5,2 (got {s.converge})"
        if s.converge and s.antenna:
            return "converge is not available with mode = antenna"
        return None

    # ---- what it changes, and what it holds -------------------------------------------------------------
    def deviations(self, run, p):
        """Vias moved onto mesh lines, parts split in two elements, edge walls moved onto mesh lines."""
        m, out = run.m, []
        for i, (v, q) in enumerate(zip(m.vias, p.grid.get('vias', m.vias))):
            d = float(np.hypot(q[0] - v[0], q[1] - v[1]))
            if d > 1e-9:
                out.append(Deviation(f"via:{i}", 'vias', 'moved onto the mesh', d, to=list(q)))
        for pt in m.parts:
            n = len(pt.pieces())
            if n > 1 and not pt.short:
                out.append(Deviation(f"part:{pt.ref}", 'parts', f"split in {n} elements (openEMS 0.37: a series R "
                                                                f"with L / C is a sheet of its own)", to=n))
        for i, (w, q) in enumerate(zip(m.walls, p.grid.get('walls') or m.walls)):
            if abs(q[1] - w[1]) > 1e-9:
                out.append(Deviation(f"wall:{i}", 'edge_walls', 'moved onto a mesh line', abs(q[1] - w[1]), to=list(q)))
        return out

    def readback(self, run, p):
        from .readback import readback
        return readback(run.m, p.grid, p.bc, sigma=run.s.sigma)

    def estimate(self, run):
        """Cells of the mesh x timesteps (the pulse and the ring-down, at most --max-steps) x the runs, at
        CELLS_PER_CORE_S per core."""
        from .boundaries import resolve_bc
        from .mesh import auto_mesh
        from .simulation import pulse_steps, port_index
        s, m = run.s, run.m
        bc = resolve_bc('PML_8' if s.antenna and s.bc == 'auto' else s.bc, m)
        g = auto_mesh(m, run.fmin, run.fmax, res=s.res, fine=s.fine, air=s.air, ratio=s.ratio, nz_min=s.nz, bc=bc,
                      curves=s.curves, vias=s.vias, via_snap=s.via_snap, z_sheet=s.z_sheet, curve_res=s.curve_res,
                      log=lambda *a: None)
        cells, dt = g['info']['cells'], g['info']['dt_ps'] * 1e-12
        pulse = pulse_steps(run.fmin, run.fmax, g['info']['dt_ps'])
        tg = _peak_delay(run)
        ring = RING * abs(s.end_db) / 17.4 * tg / dt if tg else (PULSES - 1) * pulse
        steps = int(min(s.max_steps, pulse + ring))
        runs = (1 if s.antenna else len(port_index(m, s.excite))) * max(1, len(s.converge or []))
        return dict(seconds=runs * cells * steps / (CELLS_PER_CORE_S * (os.cpu_count() or 1)), gb=g['info']['mem_gb'],
                    what=f"{cells / 1e6:.2f} M cells, {runs} run(s) of ~{steps:,} timesteps")

    # ---- the run ---------------------------------------------------------------------------------------
    def prepare(self, run):
        from .boundaries import resolve_bc, cavity_check
        from .mesh import auto_mesh
        from .dumps import parse_dump
        from .simulation import pulse_steps
        from .antenna import exc_fmax
        from ...plots import preview
        s, m = run.s, run.m
        p = SimpleNamespace(levels=None, coarse=[], dumps=[], result=None, excite=None, ant=None)
        air, bc = s.air, s.bc
        if s.antenna:
            from .antenna import antenna_air
            air = antenna_air(run.fmin, run.fmax, s.air)
            bc = 'PML_8' if bc == 'auto' else bc
            run.log(f"antenna mode: boundaries {bc}, air margin {air:.1f} mm")
        p.bc = resolve_bc(bc, m)
        kw = dict(air=air, ratio=s.ratio, nz_min=s.nz, bc=p.bc, curves=s.curves, vias=s.vias, via_snap=s.via_snap,
                  z_sheet=s.z_sheet, curve_res=s.curve_res, log=run.log)
        if s.converge:
            from .converge import mesh_levels, describe_levels
            p.levels = mesh_levels(m, run.fmin, run.fmax, sorted(set(s.converge)), res=s.res, fine=s.fine, **kw)
            run.log("\n".join(describe_levels(p.levels)))
            p.grid = p.levels[-1][1]                                # the finest level gives the result
            run.summary['levels'] = [dict(factor=f, cells=lm['info']['cells'], fine_mm=lm['info']['fine_mm'],
                                          dt_ps=lm['info']['dt_ps']) for f, lm in p.levels]
        else:
            p.grid = auto_mesh(m, run.fmin, run.fmax, res=s.res, fine=s.fine, **kw)
        if m.walls:
            from ...edges import describe, snap
            for g in [p.grid] + [lm for _, lm in (p.levels or [])]:
                g['walls'] = snap(m.walls, g)
                g['info']['edge_walls'] = describe(m.walls, m).split(': ', 1)[1]
        run.summary['mesh'] = p.grid['info']
        from ...meshes import save
        run.wrote('openEMS grid (3D view)', save(run, 'openems', 'grid', x=p.grid['x'], y=p.grid['y'], z=p.grid['z']))
        cavity_check(m, p.grid, p.bc, run.fmax, log=run.log)
        p.dumps = [parse_dump(sp, m, p.grid) for sp in s.dump]     # validated before any run
        for d in p.dumps:
            run.log("  " + d.describe(p.grid))
        run.figure(preview(m, p.grid, run.name), '_mesh.png', 'mesh', 'model and mesh')
        run.log(f"wrote {run.path('_mesh.png')}")
        ps = pulse_steps(run.fmin, run.fmax, p.grid['info']['dt_ps'],
                         exc_fmax(run.fmin, run.fmax) if s.antenna else None)
        run.summary['pulse_steps'] = ps
        run.log(f"excitation pulse ~{ps} timesteps (estimate); --max-steps {s.max_steps} allows "
                f"{s.max_steps / ps:.0f} pulse lengths" +
                (" - WARNING: few for the fields to ring down: raise --max-steps" if s.max_steps < 20 * ps else ''))
        if s.diel == 'wideband':
            from .dielectric import describe as describe_diel
            run.log("\n".join(describe_diel(m.layers, s.diel_fref * 1e9, run.fmin, run.fmax)))
        return p

    def solve(self, run, p):
        from .simulation import run_excitations, port_index
        from .dumps import estimate_size_mb
        from ...sparams import mixed_mode, fill_reciprocal
        from ...pipeline import ram_gb
        s, m = run.s, run.m
        p.excite = port_index(m, s.excite)
        if s.antenna and s.drive:                                   # a phased drive: mom25d's for now
            first = s.drive[0].partition('@')[0].partition('=')[0].strip()
            p.excite = port_index(m, [first])
            run.log(f"note: openEMS has no phased drive yet (--drive: mom25d): driving {first} alone")
        elif s.antenna and len(p.excite) > 1:
            run.log(f"antenna mode: driving {m.ports[p.excite[0]].name} only (one port per antenna run; "
                    f"--excite to choose)")
            p.excite = p.excite[:1]
        dump_runs = port_index(m, s.dump_port) if s.dump_port else None
        if p.dumps:
            n_runs = len(dump_runs) if dump_runs else 1
            run.log(f"field dumps: ~{estimate_size_mb(p.dumps, p.grid) * n_runs:.0f} MB on disk (rough), in "
                    f"{n_runs} run(s)")
        jobs = max(1, min(s.jobs, len(p.excite)))
        ram = ram_gb()
        if jobs > 1 and ram and jobs * p.grid['info']['mem_gb'] > 0.8 * ram:
            run.log(f"WARNING: {jobs} parallel runs need ~{jobs * p.grid['info']['mem_gb']:.1f} GB, "
                    f"{ram:.1f} GB RAM installed - use fewer --jobs")
        progress = dict(mode='raw' if s.openems_output else 'bar', on_status=run.status if run.events else None)
        run_kw = dict(end_db=s.end_db, end_on=s.end_on, sigma=s.sigma, threads=s.threads, diel=s.diel,
                      max_steps=s.max_steps, diel_fref=s.diel_fref * 1e9, progress=progress, log=run.log)
        for f, lm in (p.levels or [])[:-1]:                        # convergence: coarser levels first, no dumps
            run.log(f"--- mesh level x{f:g} ({lm['info']['cells'] / 1e6:.2f} M cells) ---")
            r = run_excitations(m, lm, run.freq, p.excite, p.bc, os.path.join(s.out, 'converge', f"L{f:g}"),
                                jobs=jobs, **run_kw)
            fill_reciprocal(r.S, p.excite)
            p.coarse.append((f, mixed_mode(r.S, m)[0]))
        if p.levels:
            run.log(f"--- mesh level x{p.levels[-1][0]:g} (finest: the result) ---")
        p.xml = run.path('.xml')
        if s.antenna:
            from . import antenna
            k = p.excite[0]
            p.result = antenna.run_antenna(m, p.grid, run.freq, k, p.bc, s.out, ff_extra=[v * 1e9 for v in s.ff_freq],
                                           ff_pattern=s.ff_freq[0] * 1e9 if s.ff_freq else None, step=s.ff_step,
                                           xml_path=p.xml, dumps=p.dumps, **run_kw)
            p.ant = p.result.extra.get(m.ports[k].name)
        else:
            p.result = run_excitations(m, p.grid, run.freq, p.excite, p.bc, s.out, xml_path=p.xml, dumps=p.dumps,
                                       dump_runs=dump_runs, jobs=jobs, **run_kw)
        r = p.result
        missing = fill_reciprocal(r.S, p.excite)
        if missing:
            run.log(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the "
                    f"terms between unexcited ports are unknown (written as 0)")
        S, names, zref = mixed_mode(r.S, m)
        seconds = sum(v or 0 for v in r.seconds.values())
        return Result(S, names, zref, seconds, comment=f"openEMS FDTD ({run.name})",
                      info=dict(excited=[m.ports[k].name for k in p.excite], kpi=f"{seconds:.0f} s solver",
                                about=f"openEMS, 3D FDTD: {len(r.seconds)} run(s) on {p.grid['info']['cells'] / 1e6:.2f} M "
                                      f"cells, {seconds:.0f} s."))

    def outputs(self, run, p, res):
        from .simulation import run_dir, log_path
        from .dumps import plot_fd_dumps
        s, m, r = run.s, run.m, p.result
        run.wrote('CSX geometry (AppCSXCAD)', p.xml)
        for (pname, dname), path in r.dump_files.items():
            d = next(x for x in p.dumps if x.name == dname)
            if d.freqs and os.path.exists(path):
                tag = pname.replace('+', 'p').replace('-', 'n')
                pngs = plot_fd_dumps(d, path, m, run.path(f"_{tag}"), norm=r.dump_norm.get(pname), port=pname,
                                     log=run.log)
                run.summary['dumps'] += [dict(label=f"{dname} ({pname} driven)", png=run.rel(q)) for q in pngs]
                for q in pngs:
                    run.wrote(f"field map {dname} ({pname})", q)
                if s.dump_animate and d.normal is not None:         # one period at each frequency
                    from .fields import DumpSource, save_animation
                    src = DumpSource(os.path.dirname(path), dname, 'FD')
                    for i, f in enumerate(src.freqs):
                        q = save_animation(src, f"{run.path(f'_{tag}')}_{dname}_{f / 1e9:g}GHz.{s.dump_animate}",
                                           d.normal, 0, 'mag', [i])
                        run.wrote(f"field animation {dname} ({pname}, {f / 1e9:g} GHz)", q)
                run.log(f"  {dname} ({pname} driven): {path}" + (f" -> {len(pngs)} PNG map(s)" if pngs else
                                                                  "  (volume: the GUI's Fields tab or ParaView)"))
            else:
                run.log(f"  {dname} ({pname} driven): {path}  (time domain: the GUI's Fields tab or ParaView)")
        runs = {n: dict(seconds=r.seconds.get(n), steps=r.steps.get(n), ended=getattr(r, 'ended', {}).get(n))
                for n in r.seconds}
        for k in p.excite:
            runs.setdefault(m.ports[k].name, {})['log'] = run.rel(log_path(run_dir(m, k, s.out)))
        run.summary['runs'] = runs
        if p.ant:
            self._antenna(run, p)
        if p.levels:
            self._convergence(run, p, res)

    def _antenna(self, run, p):
        from .antenna import save as save_antenna
        from ...plots import plot_antenna, plot_pattern
        k = p.excite[0]
        run.figure(plot_antenna(run.freq, p.result.S[:, k, k], run.m.ports[k].z0, p.ant, f"{run.name} - antenna"),
                   '_antenna.png', 'antenna', 'antenna: match, gain, efficiency')
        run.figure(plot_pattern(p.ant, f"{run.name} - gain pattern"), '_pattern.png', 'pattern', 'antenna: patterns')
        js = run.path('_antenna.json')
        save_antenna(p.ant, js)
        run.wrote('antenna results (JSON)', js)
        run.summary['antenna'] = {k_: v for k_, v in p.ant.items() if k_ != 'sphere'}

    def _convergence(self, run, p, res):
        from .converge import compare_levels, verdict
        from ...plots import plot_convergence
        tol_db, tol_deg = run.s.converge_tol
        dev = compare_levels([S_ for _, S_ in p.coarse] + [res.S], res.names) + [{}]    # finest: the reference
        lines, ok = verdict([f for f, _ in p.levels], dev[:-1], tol_db, tol_deg)
        run.log("\n".join(lines))
        if not ok:
            run.log("WARNING: mesh not converged (see the convergence table)")
        run.figure(plot_convergence(run.freq, [(f"x{f:g}", S_) for f, S_ in p.coarse] +
                                    [(f"x{p.levels[-1][0]:g}", res.S)], res.names, f"{run.name} - mesh convergence"),
                   '_converge.png', 'converge', 'mesh convergence')
        run.summary['convergence'] = dict(converged=ok, tol_db=tol_db, tol_deg=tol_deg,
                                          levels=[dict(factor=f, cells=lm['info']['cells'], **d)
                                                  for (f, lm), d in zip(p.levels, dev)])
