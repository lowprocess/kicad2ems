"""
Antenna mode: one excited port, absorbing (PML) boundaries on all six sides, a near-field to
far-field (NF2FF) box recorded at a set of frequencies, and after the run:

  input match     S11, input impedance, resonance (minimum |S11|), -10 dB bandwidth
  over frequency  directivity, radiation efficiency (Prad / accepted power), gain, realized gain
                  (Prad / incident power) at the recorded frequencies
  polarisation    at each recorded frequency's gain maximum: the axial ratio, the sense (RHCP / LHCP, IEEE:
                  seen from behind, the field turning clockwise is right-handed) and the gain in each hand
  patterns        gain in the xz (phi = 0) and yz (phi = 90 deg) planes and the xy plane (theta = 90),
                  and a 3D pattern, at the pattern frequency (default: the resonance)

Coordinates are those of the model (x right, y up as seen from the top of the board, z out of the
board's top side), so phi = 0 is +x, theta = 0 is the board's top-side normal.
"""
import functools, json
import numpy as np
from ...constants import C0


def antenna_air(fmin, fmax, air=None):
    """Air margin (mm) of antenna runs: lambda / 4 at the band centre, at least 5 mm (unless given)."""
    return max(5.0, C0 / (0.5 * (fmin + fmax)) * 1e3 / 4) if air is None else air


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


def polarisation(E_theta, E_phi, theta, phi, phi_first=False):
    """Axial ratio (dB), sense and the right- / left-hand shares of the power at the far field's maximum (E_theta,
    E_phi complex on the theta x phi grid, or phi x theta with phi_first; theta, phi in degrees):
    E = E_R (t - j p) / sqrt 2 + E_L (t + j p) / sqrt 2 for exp(j w t). Linear polarisation has an infinite axial ratio (capped at 60 dB)."""
    et, ep = np.asarray(E_theta), np.asarray(E_phi)
    er, el = np.abs(et + 1j * ep) / np.sqrt(2), np.abs(et - 1j * ep) / np.sqrt(2)
    p = er ** 2 + el ** 2
    i = np.unravel_index(int(np.argmax(p)), p.shape)
    a, b = float(er[i]), float(el[i])
    ar = 60.0 if abs(a - b) < 1e-3 * (a + b) else min(60.0, float(20 * np.log10((a + b) / abs(a - b))))
    th, ph = np.asarray(theta, float).ravel(), np.asarray(phi, float).ravel()
    ang = (th[i[1]], ph[i[0]]) if phi_first else (th[i[0]], ph[i[1]])
    return dict(AR_db=ar, sense='RHCP' if a > b else 'LHCP', rhcp=a * a / (a * a + b * b), lhcp=b * b / (a * a + b * b),
                theta=float(ang[0]), phi=float(ang[1]))


def add_polarisation(rows, E_theta, E_phi, theta, phi, phi_first=False):
    """Each gain_table row with its polarisation at the maximum: AR_db, sense (RHCP, LHCP, or linear above 20 dB),
    G_rhcp_dbi, G_lhcp_dbi, peak direction (theta, phi deg)."""
    for r, et, ep in zip(rows, E_theta, E_phi):
        pol = polarisation(et, ep, theta, phi, phi_first)
        g = 10 ** (r['G_dbi'] / 10) if r['G_dbi'] is not None else None
        dbi = lambda v: float(10 * np.log10(v)) if g and v > 0 else None
        r.update(AR_db=pol['AR_db'], sense=pol['sense'] if pol['AR_db'] < 20 else 'linear',
                 G_rhcp_dbi=dbi(g * pol['rhcp']) if g else None, G_lhcp_dbi=dbi(g * pol['lhcp']) if g else None,
                 peak_theta=pol['theta'], peak_phi=pol['phi'])
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
    pol = any('AR_db' in r for r in rows)
    out.append("   f GHz   D dBi   eff %   G dBi   realized G dBi" + ("   AR dB  sense" if pol else ''))
    for r in rows:
        fmt = lambda v: f"{v:7.2f}" if v is not None else "      -"
        line = (f"  {r['f'] / 1e9:6.3f} {fmt(r['D_dbi'])} {r['eff'] * 100:7.1f} {fmt(r['G_dbi'])} "
                f"{fmt(r['Grealized_dbi'])}")
        if pol and 'AR_db' in r:
            line += f"          {r['AR_db']:6.1f}  {r['sense']}"
        out.append(line + ('  <- pattern' if abs(r['f'] - f_pat) < 1 else ''))
    return out


def nf2ff_box(freqs):
    """Run hook (simulation.run_excitations setup=): an NF2FF box recording at `freqs` (Hz)."""
    return functools.partial(_add_nf2ff, [float(f) for f in freqs])


def _add_nf2ff(freqs, FDTD, CSX):
    return FDTD.CreateNF2FFBox(frequency=freqs)


def analyse(m, freq, ffr, ff_pattern=None, step=2.0, log=print, *, path, ports, token, k, col):
    """Run hook (analyse=): input match, gain over frequency and the pattern cuts from a finished antenna
    run (token: the NF2FF box). Returns the result dict."""
    nf2ff, pk = token, ports[k]
    match = match_summary(freq, col[:, k], m.ports[k].z0)
    pk.CalcPort(path, ffr)
    p_acc, p_inc = np.atleast_1d(pk.P_acc), np.atleast_1d(pk.P_inc)
    ob = m.outline.bounds
    centre = [0.5 * (ob[0] + ob[2]) * 1e-3, 0.5 * (ob[1] + ob[3]) * 1e-3, 0.5 * m.thickness * 1e-3]
    # gain over frequency: Dmax / Prad on a coarse full sphere at every recorded frequency
    th, ph = np.arange(0, 181, 5.0), np.arange(0, 361, 5.0)
    full = nf2ff.CalcNF2FF(path, list(ffr), th, ph, center=centre, outfile='nf2ff_sphere.h5')
    rows = gain_table(ffr, np.atleast_1d(full.Dmax), np.atleast_1d(full.Prad), p_acc, p_inc)
    add_polarisation(rows, full.E_theta, full.E_phi, th, ph)
    ip = int(np.argmin(np.abs(ffr - (ff_pattern if ff_pattern else match['f_res']))))
    f_pat, eff, dmax = float(ffr[ip]), rows[ip]['eff'], float(np.atleast_1d(full.Dmax)[ip])
    e_max = float(np.max(full.E_norm[ip]))                       # one normalisation for all cuts
    tc = np.arange(-180, 180 + step / 2, step)
    cuts = {}
    for name, theta, phi in (('xz (phi = 0)', tc, [0.0]), ('yz (phi = 90)', tc, [90.0]),
                             ('xy (theta = 90)', [90.0], np.arange(0, 360 + step / 2, step))):
        r = nf2ff.CalcNF2FF(path, [f_pat], theta, phi, center=centre, outfile=f"nf2ff_cut_{name.split()[0]}.h5")
        g = (np.asarray(r.E_norm[0], float).ravel() / e_max) ** 2 * dmax * eff
        cuts[name] = dict(angle=list(map(float, theta if len(theta) > 1 else phi)),
                          gain_dbi=[float(v) for v in 10 * np.log10(np.maximum(g, 1e-6))])
    sphere = dict(theta=th, phi=ph, gain=pattern_gain(full.E_norm[ip], dmax, eff))
    for line in describe(match, rows, f_pat):
        log(line)
    return dict(match=match, rows=rows, f_pattern=f_pat, cuts=cuts, sphere=sphere, port=m.ports[k].name)


def run_antenna(m, mesh, freq, k, bc, out_dir, ff_extra=(), ff_pattern=None, step=2.0, log=print, **run_kw):
    """FDTD run with port k excited and an NF2FF box: simulation.run_excitations with the antenna hooks.
    The excitation reaches 30 % of the band beyond fmax: in the top ~15 % of a Gaussian's band (its -20 dB
    edge) the far field came out wrong (dipole: 6.3 dBi and 107 % efficiency at fmax). Returns the
    RunResult; RunResult.extra[port name] holds the antenna results."""
    from .simulation import run_excitations, engine_name
    ffr = record_freqs(freq[0], freq[-1], ff_extra)
    log(f"{engine_name(run_kw.get('engine'))[1]}: antenna run, {m.ports[k].name} driven, NF2FF box recording "
        f"{len(ffr)} frequencies")
    return run_excitations(m, mesh, freq, [k], bc, out_dir, jobs=1, exc_fmax=exc_fmax(freq[0], freq[-1]),
                           setup=nf2ff_box(ffr), log=log,
                           analyse=functools.partial(analyse, m, freq, ffr, ff_pattern, step, log), **run_kw)


def save(res, path):
    """JSON summary (without the 3D sphere)."""
    out = {k: v for k, v in res.items() if k != 'sphere'}
    with open(path, 'w') as fh:
        json.dump(out, fh, indent=1)


def read_sphere(path):
    """The full-sphere far field of an antenna run (<run>/nf2ff_sphere.h5, written by analyse) ->
    dict(theta, phi (deg), freq (Hz), dmax (linear), prad, e_norm (nf, nphi, ntheta) |E| far field)."""
    import h5py
    with h5py.File(path, 'r') as h:
        th, ph = np.degrees(np.asarray(h['Mesh/theta'], float)), np.degrees(np.asarray(h['Mesh/phi'], float))
        a = h['nf2ff'].attrs
        freq, dmax, prad = (np.atleast_1d(np.asarray(a[k], float)) for k in ('Frequency', 'Dmax', 'Prad'))
        e = []
        for i in range(len(freq)):
            et = np.asarray(h[f'nf2ff/E_theta/FD/f{i}_real']) + 1j * np.asarray(h[f'nf2ff/E_theta/FD/f{i}_imag'])
            ep = np.asarray(h[f'nf2ff/E_phi/FD/f{i}_real']) + 1j * np.asarray(h[f'nf2ff/E_phi/FD/f{i}_imag'])
            e.append(np.sqrt(np.abs(et) ** 2 + np.abs(ep) ** 2))
    return dict(theta=th, phi=ph, freq=freq, dmax=dmax, prad=prad, e_norm=np.array(e))


def sphere_gain(sph, i, scale=1.0):
    """Pattern (nphi, ntheta, linear) at recorded frequency i: directivity x scale (efficiency -> gain,
    Prad / Pinc -> realized gain)."""
    e = sph['e_norm'][i]
    return (e / e.max()) ** 2 * sph['dmax'][i] * scale


def sphere_cuts(sph, g):
    """The xz (phi 0 / 180), yz (phi 90 / 270) and xy (theta 90) cuts of a sphere pattern g (nphi, ntheta):
    {name: (angles deg, gain linear)}; xz / yz angles run -180..180 (theta, negative on the far side)."""
    th, ph = sph['theta'], sph['phi']

    def col(p):
        return int(np.argmin(np.abs((ph - p + 180) % 360 - 180)))
    out = {}
    for name, p0 in (('xz (phi = 0)', 0.0), ('yz (phi = 90)', 90.0)):
        a, b = g[col(p0)], g[col(p0 + 180)]
        out[name] = (np.concatenate([-th[::-1], th[1:]]), np.concatenate([b[::-1], a[1:]]))
    k = int(np.argmin(np.abs(th - 90)))
    out['xy (theta = 90)'] = (ph, g[:, k])
    return out
