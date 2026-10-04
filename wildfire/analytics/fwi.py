"""Canadian Forest Fire Weather Index (FWI) System.

Implements Van Wagner (1987) "Development and structure of the Canadian
Forest Fire Weather Index System", Forestry Technical Report 35, with the
latitude-adjusted day-length factors used by the cffdrs R package
(Wang et al. 2017). This is the index family used by EFFIS (Europe) and
GEFF/GWIS (global), which keeps the core jurisdiction-neutral.

Inputs are noon local standard time observations:
    temp (°C), rh (%), wind (km/h, 10 m), rain (mm, 24 h ending at noon).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

FFMC0, DMC0, DC0 = 85.0, 6.0, 15.0   # standard start-up values

_EL = {  # DMC effective day length by latitude band (cffdrs)
    "n46": [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0],
    "n20": [7.9, 8.4, 8.9, 9.5, 9.9, 10.2, 10.1, 9.7, 9.1, 8.6, 8.1, 7.8],
    "s20": [10.1, 9.6, 9.1, 8.5, 8.1, 7.8, 7.9, 8.3, 8.9, 9.4, 9.9, 10.2],
    "s46": [11.5, 10.5, 9.2, 7.9, 6.8, 6.2, 6.5, 7.4, 8.7, 10.0, 11.2, 11.8],
}
_FL_N = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]   # DC day-length factor
_FL_S = [6.4, 5.0, 2.4, 0.4, -1.6, -1.6, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8]


@dataclass
class FWIResult:
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    dsr: float


def ffmc(temp, rh, ws, rain, ffmc_prev):
    mo = 147.2 * (101.0 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0.5:
        rf = rain - 0.5
        mr = mo + 42.5 * rf * math.exp(-100.0 / (251.0 - mo)) * (1.0 - math.exp(-6.93 / rf))
        if mo > 150.0:
            mr += 0.0015 * (mo - 150.0) ** 2 * math.sqrt(rf)
        mo = min(mr, 250.0)
    ed = 0.942 * rh ** 0.679 + 11.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
    if mo > ed:
        ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) + 0.0694 * math.sqrt(ws) * (1.0 - (rh / 100.0) ** 8)
        kd = ko * 0.581 * math.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10.0 ** (-kd)
    else:
        ew = 0.618 * rh ** 0.753 + 10.0 * math.exp((rh - 100.0) / 10.0) + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh))
        if mo < ew:
            k1 = 0.424 * (1.0 - ((100.0 - rh) / 100.0) ** 1.7) + 0.0694 * math.sqrt(ws) * (1.0 - ((100.0 - rh) / 100.0) ** 8)
            kw = k1 * 0.581 * math.exp(0.0365 * temp)
            m = ew - (ew - mo) * 10.0 ** (-kw)
        else:
            m = mo
    return max(0.0, min(101.0, 59.5 * (250.0 - m) / (147.2 + m)))


def _day_length(lat, month):
    if lat > 30:
        return _EL["n46"][month - 1]
    if lat > 10:   # cffdrs: 30 >= lat > 10 uses the 20°N table
        return _EL["n20"][month - 1]
    if lat > -10:
        return 9.0
    if lat > -30:
        return _EL["s20"][month - 1]
    return _EL["s46"][month - 1]


def dmc(temp, rh, rain, dmc_prev, month, lat=46.0):
    t = max(temp, -1.1)
    rk = 1.894 * (t + 1.1) * (100.0 - rh) * _day_length(lat, month) * 1e-4
    if rain > 1.5:
        re = 0.92 * rain - 1.27
        mo = 20.0 + math.exp(5.6348 - dmc_prev / 43.43)
        if dmc_prev <= 33.0:
            b = 100.0 / (0.5 + 0.3 * dmc_prev)
        elif dmc_prev <= 65.0:
            b = 14.0 - 1.3 * math.log(dmc_prev)
        else:
            b = 6.2 * math.log(dmc_prev) - 17.2
        mr = mo + 1000.0 * re / (48.77 + b * re)
        pr = max(0.0, 244.72 - 43.43 * math.log(mr - 20.0))
    else:
        pr = dmc_prev
    return max(0.0, pr + rk)


def dc(temp, rain, dc_prev, month, lat=46.0):
    t = max(temp, -2.8)
    fl = _FL_N[month - 1] if lat > 20 else (_FL_S[month - 1] if lat <= -20 else 1.4)
    pe = max(0.0, (0.36 * (t + 2.8) + fl) / 2.0)
    if rain > 2.8:
        rd = 0.83 * rain - 1.27
        qo = 800.0 * math.exp(-dc_prev / 400.0)
        qr = qo + 3.937 * rd
        dr = max(0.0, 400.0 * math.log(800.0 / qr))
    else:
        dr = dc_prev
    return max(0.0, dr + pe)


def isi(ffmc_v, ws):
    fm = 147.2 * (101.0 - ffmc_v) / (59.5 + ffmc_v)
    sf = 19.115 * math.exp(-0.1386 * fm) * (1.0 + fm ** 5.31 / 4.93e7)
    return sf * math.exp(0.05039 * ws)


def bui(dmc_v, dc_v):
    if dmc_v == 0 and dc_v == 0:
        return 0.0
    if dmc_v <= 0.4 * dc_v:
        b = 0.8 * dmc_v * dc_v / (dmc_v + 0.4 * dc_v)
    else:
        b = dmc_v - (1.0 - 0.8 * dc_v / (dmc_v + 0.4 * dc_v)) * (0.92 + (0.0114 * dmc_v) ** 1.7)
    return max(0.0, b)


def fwi(isi_v, bui_v):
    if bui_v <= 80.0:
        bb = 0.1 * isi_v * (0.626 * bui_v ** 0.809 + 2.0)
    else:
        bb = 0.1 * isi_v * (1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui_v)))
    return bb if bb <= 1.0 else math.exp(2.72 * (0.434 * math.log(bb)) ** 0.647)


def step(temp, rh, ws, rain, month, prev: tuple[float, float, float], lat=46.0) -> FWIResult:
    rh = max(0.0, min(100.0, rh))
    ws = max(0.0, ws)
    rain = max(0.0, rain)
    f = ffmc(temp, rh, ws, rain, prev[0])
    d = dmc(temp, rh, rain, prev[1], month, lat)
    c = dc(temp, rain, prev[2], month, lat)
    i = isi(f, ws)
    b = bui(d, c)
    w = fwi(i, b)
    return FWIResult(f, d, c, i, b, w, 0.0272 * w ** 1.77)
