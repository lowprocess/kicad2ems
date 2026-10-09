"""
Antenna mode: one excited port, absorbing (PML) boundaries on all six sides, a near-field to
far-field (NF2FF) box recorded at a set of frequencies, and after the run:

  input match     S11, input impedance, resonance (minimum |S11|), -10 dB bandwidth
  over frequency  directivity, radiation efficiency (Prad / accepted power), gain, realized gain
                  (Prad / incident power) at the recorded frequencies
  patterns        gain in the xz (phi = 0) and yz (phi = 90 deg) planes and the xy plane (theta = 90),
                  and a 3D pattern, at the pattern frequency (default: the resonance)

Coordinates are those of the model (x right, y up as seen from the top of the board, z out of the
board's top side), so phi = 0 is +x, theta = 0 is the board's top-side normal.
"""
import json, os, time
import numpy as np
from .constants import C0


def antenna_defaults(fmin, fmax, air=None, bc=None):
    """Air margin (mm) and boundaries for antenna runs: lambda / 4 at the band centre (at least 5 mm)
    and PML_8 on all six sides (unless given)."""
    lam_c = C0 / (0.5 * (fmin + fmax)) * 1e3
    return (max(5.0, lam_c / 4) if air is None else air), (bc or ('PML_8',) * 6)


def exc_fmax(fmin, fmax):
    """Upper edge of the antenna runs' excitation: 30 % of the band beyond fmax."""
    return fmax + 0.3 * (fmax - fmin)


def record_freqs(fmin, fmax, extra=(), n=21):
    """Frequencies (Hz) at which the NF2FF box records: n over the band plus any given ones."""
    return np.unique(np.concatenate([np.linspace(fmin, fmax, n), np.asarray(extra, float)]))


def match_summary(freq, s11, z0):
    """Resonance (minimum |S11|), its S11 in dB, input impedance there and the -10 dB band around it."""
    db = 20 * np.log10(np.abs(s11) + 1e-15)
    i = int(np.argmin(db))
    zin = z0 * (1 + s11) / (1 - s11)
    lo = hi = None
    if db[i] < -10:
        j = i
        while j > 0 and db[j - 1] < -10:
            j -= 1
        k = i
        while k < len(db) - 1 and db[k + 1] < -10:
            k += 1
        lo, hi = float(freq[j]), float(freq[k])
    return dict(f_res=float(freq[i]), s11_db=float(db[i]), zin=[float(zin[i].real), float(zin[i].imag)],
                bw10_lo=lo, bw10_hi=hi, bw10=(hi - lo) if lo is not None else None)


def gain_table(ff_freq, dmax, prad, p_acc, p_inc):
    """Per recorded frequency: directivity, efficiency, gain and realized gain (linear and dBi)."""
    rows = []
    for f, d, pr, pa, pi in zip(ff_freq, dmax, prad, p_acc, p_inc):
        eff = pr / pa if pa > 0 else np.nan
        reff = pr / pi if pi > 0 else np.nan
        dbi = lambda v: float(10 * np.log10(v)) if v > 0 else None
        rows.append(dict(f=float(f), D_dbi=dbi(d), eff=float(eff), G_dbi=dbi(d * eff), Grealized_dbi=dbi(d * reff),
                         Prad=float(pr), Pacc=float(pa), Pinc=float(pi)))
    return rows


def pattern_gain(E_norm, dmax, eff):
    """Gain (linear) over a pattern grid from the far-field magnitude: D = (|E| / |E|max)^2 Dmax."""
    e = np.asarray(E_norm, float)
    return (e / e.max()) ** 2 * dmax * eff


def describe(match, rows, f_pat):
    out = [f"antenna: resonance {match['f_res'] / 1e9:.4f} GHz, S11 {match['s11_db']:.1f} dB, "
           f"Zin {match['zin'][0]:.1f} {'+' if match['zin'][1] >= 0 else '-'} j{abs(match['zin'][1]):.1f} ohm" +
           (f", -10 dB band {match['bw10_lo'] / 1e9:.3f}-{match['bw10_hi'] / 1e9:.3f} GHz "
            f"({match['bw10'] / match['f_res'] * 100:.1f} %)" if match['bw10'] else ", S11 never below -10 dB")]
    out.append("   f GHz   D dBi   eff %   G dBi   realized G dBi")
    for r in rows:
        fmt = lambda v: f"{v:7.2f}" if v is not None else "      -"
        mark = '  <- pattern' if abs(r['f'] - f_pat) < 1 else ''
        out.append(f"  {r['f'] / 1e9:6.3f} {fmt(r['D_dbi'])} {r['eff'] * 100:7.1f} {fmt(r['G_dbi'])} "
                   f"{fmt(r['Grealized_dbi'])}{mark}")
    return out


def run_antenna(m, mesh, freq, k, bc, out_dir, ff_extra=(), ff_pattern=None, end_db=-40, sigma=None, threads=0,
                xml_path=None, dumps=(), diel='fixed', diel_fref=1e9, step=2.0, max_steps=1_000_000, log=print):
    """FDTD run with port k excited and an NF2FF box. Returns (S column (nf, n), result dict)."""
    from .simulation import build_fdtd, _run_dir, check_steps
    fmin, fmax = freq[0], freq[-1]
    ffr = record_freqs(fmin, fmax, ff_extra)
    path = _run_dir(m, k, out_dir)
    # the excitation reaches 30 % of the band beyond fmax: in the top ~15 % of a Gaussian's band (its
    # -20 dB edge) the far field came out wrong (dipole: 6.3 dBi and 107 % efficiency at fmax)
    FDTD, CSX, ports = build_fdtd(m, mesh, fmin, fmax, k, bc=bc, end_db=end_db, sigma=sigma, dumps=dumps,
                                  diel=diel, diel_fref=diel_fref, exc_fmax=exc_fmax(fmin, fmax), max_steps=max_steps)
    nf2ff = FDTD.CreateNF2FFBox(frequency=[float(f) for f in ffr])
    os.makedirs(path, exist_ok=True)
    if xml_path:
        CSX.Write2XML(os.path.abspath(xml_path))
    log(f"openEMS: antenna run, {m.ports[k].name} driven, NF2FF box recording {len(ffr)} frequencies -> {path}")
    cwd, t1 = os.getcwd(), time.time()
    try:
        FDTD.Run(path, cleanup=True, numThreads=threads, verbose=1)
    finally:
        os.chdir(cwd)
    n = check_steps(m.ports[k].name, path, mesh, max_steps, end_db, log)
    log(f"  done in {time.time() - t1:.0f} s" + (f", ~{n} timesteps" if n else ''))
    for p in ports:
        p.CalcPort(path, freq)
    col = np.array([p.uf_ref / ports[k].uf_inc for p in ports]).T
    pk = ports[k]
    match = match_summary(freq, col[:, k], m.ports[k].z0)
    pk.CalcPort(path, ffr)
    p_acc, p_inc = np.atleast_1d(pk.P_acc), np.atleast_1d(pk.P_inc)
    ob = m.outline.bounds
    centre = [0.5 * (ob[0] + ob[2]) * 1e-3, 0.5 * (ob[1] + ob[3]) * 1e-3, 0.5 * m.thickness * 1e-3]
    # gain over frequency: Dmax / Prad on a coarse full sphere at every recorded frequency
    th, ph = np.arange(0, 181, 5.0), np.arange(0, 361, 5.0)
    full = nf2ff.CalcNF2FF(path, list(ffr), th, ph, center=centre, outfile='nf2ff_sphere.h5')
    rows = gain_table(ffr, np.atleast_1d(full.Dmax), np.atleast_1d(full.Prad), p_acc, p_inc)
    f_pat = ff_pattern if ff_pattern else match['f_res']
    ip = int(np.argmin(np.abs(ffr - f_pat)))
    f_pat = float(ffr[ip])
    eff = rows[ip]['eff']
    dmax = float(np.atleast_1d(full.Dmax)[ip])
    # cuts at the pattern frequency
    tc = np.arange(-180, 180 + step / 2, step)
    cuts = {}
    for name, theta, phi in (('xz (phi = 0)', tc, [0.0]), ('yz (phi = 90)', tc, [90.0]),
                             ('xy (theta = 90)', [90.0], np.arange(0, 360 + step / 2, step))):
        r = nf2ff.CalcNF2FF(path, [f_pat], theta, phi, center=centre,
                            outfile=f"nf2ff_cut_{name.split()[0]}.h5")
        e = np.asarray(r.E_norm[0], float).ravel()
        cuts[name] = dict(angle=list(map(float, theta if len(theta) > 1 else phi)), E=e)
    # one normalisation for all cuts: the sphere's maximum at f_pat
    e_max = float(np.max(full.E_norm[ip]))
    for c in cuts.values():
        g = (c.pop('E') / e_max) ** 2 * dmax * eff
        c['gain_dbi'] = [float(v) for v in 10 * np.log10(np.maximum(g, 1e-6))]
    sphere = dict(theta=th, phi=ph, gain=pattern_gain(full.E_norm[ip], dmax, eff))
    res = dict(match=match, rows=rows, f_pattern=f_pat, cuts=cuts, sphere=sphere, port=m.ports[k].name)
    for line in describe(match, rows, f_pat):
        log(line)
    return col, res


def save(res, path):
    """JSON summary (without the 3D sphere)."""
    out = {k: v for k, v in res.items() if k != 'sphere'}
    with open(path, 'w') as fh:
        json.dump(out, fh, indent=1)
