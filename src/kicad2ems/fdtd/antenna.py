"""
Antenna mode on an FDTD engine: one excited port, absorbing boundaries, a quarter wavelength of air and a
near-field to far-field (NF2FF) box recorded at a set of frequencies; after the run, the far field analysed
(fields/antenna.py: match, gain, polarisation, patterns).
"""
import functools
import numpy as np
from ..constants import C0
from ..fields.antenna import match_summary, gain_table, add_polarisation, pattern_gain, describe


def antenna_air(fmin, fmax, air=None):
    """Air margin (mm) of antenna runs: lambda / 4 at the band centre, at least 5 mm (unless given)."""
    return max(5.0, C0 / (0.5 * (fmin + fmax)) * 1e3 / 4) if air is None else air



def exc_fmax(fmin, fmax):
    """Upper edge of the antenna runs' excitation: 30 % of the band beyond fmax."""
    return fmax + 0.3 * (fmax - fmin)



def record_freqs(fmin, fmax, extra=(), n=21):
    """Frequencies (Hz) at which the NF2FF box records: n over the band plus any given ones."""
    return np.unique(np.concatenate([np.linspace(fmin, fmax, n), np.asarray(extra, float)]))



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
    from .run import run_excitations
    from .engines import engine_name
    ffr = record_freqs(freq[0], freq[-1], ff_extra)
    log(f"{engine_name(run_kw.get('engine'))[1]}: antenna run, {m.ports[k].name} driven, NF2FF box recording "
        f"{len(ffr)} frequencies")
    return run_excitations(m, mesh, freq, [k], bc, out_dir, jobs=1, exc_fmax=exc_fmax(freq[0], freq[-1]),
                           setup=nf2ff_box(ffr), log=log,
                           analyse=functools.partial(analyse, m, freq, ffr, ff_pattern, step, log), **run_kw)
