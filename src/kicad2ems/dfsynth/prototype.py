"""
Low-pass prototype element values (g0 ... g_{n+1}) and order estimation.

    g = gvalues('chebyshev', 5, ripple_db=0.1)      # [1, g1, ..., g5, 1]
    n = estimate_order('butterworth', 40, 2.0)      # 40 dB at twice the normalised cut-off

The band edge of a Chebyshev prototype is the ripple level (equal-ripple band edge); of a Butterworth
prototype the -3 dB point. Every filter designed from these keeps that convention.
"""
import math

RESPONSES = ('butterworth', 'chebyshev')


def gvalues(response, n, ripple_db=0.1):
    """[g0, g1, ..., gn, g_{n+1}] of the order-n prototype (g0 = 1, the source)."""
    if n < 1:
        raise ValueError("order must be >= 1")
    if response == 'butterworth':
        g = [2 * math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
        return [1.0] + g + [1.0]
    if response == 'chebyshev':
        if ripple_db <= 0:
            raise ValueError("Chebyshev ripple must be > 0 dB")
        beta = math.log(1 / math.tanh(ripple_db / 17.37))
        gam = math.sinh(beta / (2 * n))
        a = [math.sin((2 * k - 1) * math.pi / (2 * n)) for k in range(1, n + 1)]
        b = [gam ** 2 + math.sin(k * math.pi / n) ** 2 for k in range(1, n + 1)]
        g = [2 * a[0] / gam]
        for k in range(1, n):
            g.append(4 * a[k - 1] * a[k] / (b[k - 1] * g[k - 1]))
        gl = 1.0 if n % 2 else 1 / math.tanh(beta / 4) ** 2
        return [1.0] + g + [gl]
    raise ValueError(f"unknown response {response!r} (one of {', '.join(RESPONSES)})")


def lowpass_s21_db(response, n, ripple_db, omega):
    """|S21| in dB of the prototype at normalised frequency omega (>= 0), lossless."""
    w = abs(omega)
    if response == 'butterworth':
        t = w ** (2 * n)
    else:
        eps2 = 10 ** (ripple_db / 10) - 1
        cn = math.cos(n * math.acos(w)) if w <= 1 else math.cosh(n * math.acosh(w))
        t = eps2 * cn * cn
    return -10 * math.log10(1 + t)


def estimate_order(response, atten_db, omega_s, ripple_db=0.1, nmax=15):
    """Smallest order with at least atten_db at normalised stop-band frequency omega_s (> 1)."""
    if omega_s <= 1:
        raise ValueError("the stop-band frequency must lie outside the pass band")
    for n in range(1, nmax + 1):
        if -lowpass_s21_db(response, n, ripple_db, omega_s) >= atten_db:
            return n
    return nmax
