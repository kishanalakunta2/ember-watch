"""Dynamic fire-weather risk surface (FR-6, FR-7, FR-8).

For every grid cell we spin up the FWI system over `past_days` of hourly
weather, then report today (D0) and the next days (D1, D2) with every
driver exposed. We deliberately publish *index-based danger classes*, not a
probability of ignition: no model has been trained on local fire occurrence
yet (PRD Screen 2: "do not invent a probability").
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import fwi as F
from .indices import ffwi, hdw, vpd_kpa

DRY_RAIN_MM = 2.5   # a day with < 2.5 mm counts as dry (wetting-rain threshold)


def _ok(*vals) -> bool:
    return all(v is not None for v in vals)


def classify(value: float, classes) -> tuple[int, str]:
    idx = 0
    for i, c in enumerate(classes):
        if value >= c.min:
            idx = i
    return idx, classes[idx].name


def cell_risk(lat: float, lon: float, s: dict, now: datetime, pack, horizon: int) -> dict | None:
    times: list[datetime] = s["time"]
    if not times:
        return None
    T, RH, WS, WD = s["temperature_2m"], s["relative_humidity_2m"], s["wind_speed_10m"], s["wind_direction_10m"]
    G, P, VPD = s["wind_gusts_10m"], s["precipitation"], s["vapour_pressure_deficit"]
    offset_h = lon / 15.0                         # local solar time = UTC + lon/15
    noon_utc_hour = round(12 - offset_h) % 24

    # index hourly data by local solar date
    by_day: dict[date, list[int]] = {}
    for i, t in enumerate(times):
        by_day.setdefault((t + timedelta(hours=offset_h)).date(), []).append(i)
    days = sorted(by_day)
    today = (now + timedelta(hours=offset_h)).date()

    state = (F.FFMC0, F.DMC0, F.DC0)
    dry_days, out_days, used_days = 0, [], 0
    for d in days:
        idx = by_day[d]
        noon = next((i for i in idx if times[i].hour == noon_utc_hour), None)
        if noon is None or not _ok(T[noon], RH[noon], WS[noon]):
            continue
        rain24 = sum((P[j] or 0.0) for j in range(max(0, noon - 23), noon + 1))
        day_rain = sum((P[j] or 0.0) for j in idx)
        r = F.step(T[noon], RH[noon], WS[noon], rain24, d.month, state, lat)
        state = (r.ffmc, r.dmc, r.dc)
        used_days += 1
        dry_days = 0 if day_rain >= DRY_RAIN_MM else dry_days + 1
        if d < today or (d - today).days >= horizon:
            continue
        hourly_ffwi, hourly_hdw = [], []
        for j in idx:
            if _ok(T[j], RH[j], WS[j]):
                hourly_ffwi.append(ffwi(T[j], RH[j], WS[j]))
                v = VPD[j] if VPD[j] is not None else vpd_kpa(T[j], RH[j])
                hourly_hdw.append(hdw(v, WS[j]))
        rh_vals = [RH[j] for j in idx if RH[j] is not None]
        t_vals = [T[j] for j in idx if T[j] is not None]
        g_vals = [G[j] for j in idx if G[j] is not None]
        primary = {"fwi": r.fwi, "isi": r.isi, "ffwi": max(hourly_ffwi, default=0), "hdw": max(hourly_hdw, default=0)}[pack.risk.primary_index]
        ci, cname = classify(primary, pack.risk.classes)
        out_days.append({
            "date": d.isoformat(), "lead_days": (d - today).days,
            "fwi": round(r.fwi, 1), "isi": round(r.isi, 1), "bui": round(r.bui, 1), "ffmc": round(r.ffmc, 1),
            "dmc": round(r.dmc, 1), "dc": round(r.dc, 1), "dsr": round(r.dsr, 2),
            "ffwi_max": round(max(hourly_ffwi, default=0), 1), "hdw_max": round(max(hourly_hdw, default=0), 1),
            "rh_min": round(min(rh_vals), 0) if rh_vals else None, "t_max_c": round(max(t_vals), 1) if t_vals else None,
            "gust_max_kmh": round(max(g_vals), 0) if g_vals else None,
            "wind_noon_kmh": round(WS[noon], 0), "wind_dir_noon": round(WD[noon] or 0),
            "rain_24h_mm": round(rain24, 1), "dry_days": dry_days,
            "class_index": ci, "class": cname,
        })
    if not out_days:
        return None
    # current hour conditions (for detection context)
    k = min(range(len(times)), key=lambda i: abs((times[i] - now).total_seconds()))
    current = {"time": times[k].isoformat(), "temp_c": T[k], "rh": RH[k], "wind_kmh": WS[k],
               "wind_dir": WD[k], "gust_kmh": G[k]}
    return {"id": f"r{lat:.3f}_{lon:.3f}", "lat": lat, "lon": lon, "spinup_days": used_days,
            "days": out_days, "current": current}


def compute(pack, points, series, now: datetime) -> list[dict]:
    horizon = pack.providers.weather[0].forecast_days if pack.providers.weather else 1
    cells = []
    for (lat, lon), s in zip(points, series, strict=True):
        if s is None:
            continue
        c = cell_risk(lat, lon, s, now.astimezone(timezone.utc), pack, horizon)
        if c:
            cells.append(c)
    return cells
