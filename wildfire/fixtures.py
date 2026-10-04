"""Synthetic inputs for offline demos and tests.

Everything produced here is SYNTHETIC and is labelled as such in the
published provenance. It exists so the full pipeline can run without network
access or API keys, using the exact response formats of the real providers.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta


def synthetic_weather(points, now: datetime, past_days: int, forecast_days: int) -> list[dict]:
    start = (now - timedelta(days=past_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (past_days + forecast_days) * 24
    times = [start + timedelta(hours=h) for h in range(hours)]
    items = []
    for lat, lon in points:
        rnd = random.Random(hash((round(lat, 2), round(lon, 2))) & 0xFFFFFFFF)
        if lon < -60:   # Americas: dry west, humid Gulf coast
            aridity = max(0.0, min(1.0, (-lon - 95.5) / 10.0))
        else:           # Iberia: hot dry south and interior, mild Atlantic north-west
            aridity = max(0.0, min(1.0, 0.7 * (42.0 - lat) / 5.0 + 0.3 * (lon + 9.5) / 3.3))
        out = {k: [] for k in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                               "wind_direction_10m", "wind_gusts_10m", "precipitation", "vapour_pressure_deficit")}
        for t in times:
            day = (t - start).days
            hod = (t.hour + lon / 15.0) % 24                       # local solar hour
            diurnal = math.sin((hod - 9) / 24 * 2 * math.pi)
            # a frontal rain event ~25 days ago (heavier in the east), then a long dry spell
            rain = 0.0
            if past_days - 26 <= day <= past_days - 24 and rnd.random() < 0.25 + 0.5 * (1 - aridity):
                rain = rnd.uniform(0.5, 4.0) * (1.3 - aridity)
            # warm, dry, windy pattern building into the last 3 days (a "red flag" setup)
            focus = max(0.0, min(1.0, (lat - 32.0) / 3.0)) if lon < -60 else max(0.0, min(1.0, (39.0 - lat) / 2.0))
            surge = max(0.0, (day - (past_days - 4)) / 4.0) * focus
            wet = 0.6 if day % 9 in (0, 1) and aridity < 0.5 else 0.0     # periodic Gulf moisture in the east
            temp = 11 + 7 * aridity + 6 * diurnal + 6 * surge - 0.4 * (lat - 30) + rnd.gauss(0, 1.2)
            rh = max(6.0, min(100.0, 74 - 34 * aridity - 13 * diurnal - 22 * surge + 15 * wet + rnd.gauss(0, 4) + 30 * (rain > 0)))
            ws = max(0.0, 7 + 6 * aridity * (0.5 + 0.5 * diurnal) + 22 * surge * (0.6 + 0.4 * diurnal) + rnd.gauss(0, 2.5))
            if wet and rnd.random() < 0.08:
                rain = rnd.uniform(3, 12)
            wd = (230 + 25 * math.sin(day / 3) + rnd.gauss(0, 10)) % 360
            es = 0.6108 * math.exp(17.27 * temp / (temp + 237.3))
            out["temperature_2m"].append(round(temp, 1))
            out["relative_humidity_2m"].append(round(rh))
            out["wind_speed_10m"].append(round(ws, 1))
            out["wind_direction_10m"].append(round(wd))
            out["wind_gusts_10m"].append(round(ws * 1.55 + rnd.uniform(0, 6), 1))
            out["precipitation"].append(round(rain, 1))
            out["vapour_pressure_deficit"].append(round(es * (1 - rh / 100), 2))
        items.append({"latitude": lat, "longitude": lon, "hourly": {
            "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times], **out}})
    return items


def firms_csv_rows(now: datetime, seed: int = 7, pack_id: str = "texas") -> dict[str, str]:
    """Synthetic FIRMS CSVs (exact API columns) for the demo packs."""
    if pack_id != "texas":
        return _firms_portugal(now, seed)
    rnd = random.Random(seed)
    viirs_head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
    modis_head = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"
    files = {k: [viirs_head] for k in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")}
    files["MODIS_NRT"] = [modis_head]
    sat = {"VIIRS_SNPP_NRT": "N", "VIIRS_NOAA20_NRT": "N20", "VIIRS_NOAA21_NRT": "N21"}

    def viirs(ds, lat, lon, t, conf, frp):
        dn = "D" if 14 <= t.hour <= 23 else "N"
        files[ds].append(f"{lat:.5f},{lon:.5f},{rnd.uniform(330, 367):.2f},0.{rnd.randint(39,60)},0.{rnd.randint(36,55)},"
                         f"{t:%Y-%m-%d},{t:%H%M},{sat[ds]},VIIRS,{conf},2.0NRT,{rnd.uniform(285, 300):.2f},{frp:.2f},{dn}")

    def modis(lat, lon, t, conf, frp):
        files["MODIS_NRT"].append(f"{lat:.4f},{lon:.4f},{rnd.uniform(310, 340):.1f},1.1,1.0,{t:%Y-%m-%d},{t:%H%M},"
                                  f"{rnd.choice('TA')},MODIS,{conf},6.1NRT,{rnd.uniform(290, 300):.1f},{frp:.1f},D")

    pass_times = [now - timedelta(hours=h, minutes=rnd.randint(0, 40)) for h in (1, 3, 12, 14)]
    # 1) Wind-driven grass fire, Texas Panhandle (near Canadian, TX) — multi-sensor, growing
    # the head runs NE with a SW wind; each pass sees a longer front further downwind
    for k, t in enumerate(pass_times[:3]):
        ds = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA21_NRT"][k]
        n = [12, 7, 3][k]
        clat, clon = 35.80 + 0.018 * (2 - k), -100.47 + 0.022 * (2 - k)
        for m in range(n):
            off = (m - (n - 1) / 2) * 0.0036
            viirs(ds, clat + off * 0.7 + rnd.gauss(0, 0.0012), clon - off * 0.7 + rnd.gauss(0, 0.0012),
                  t, rnd.choice("nnhh"), rnd.uniform(8, 60))
    modis(35.835, -100.43, pass_times[0] - timedelta(minutes=20), 86, 112.0)
    # 2) Matches a known WFIGS incident (Hill Country)
    for ds, t in (("VIIRS_NOAA20_NRT", pass_times[0]), ("VIIRS_SNPP_NRT", pass_times[2])):
        for _ in range(3):
            viirs(ds, 30.215 + rnd.gauss(0, 0.006), -98.62 + rnd.gauss(0, 0.006), t, "n", rnd.uniform(4, 15))
    # 3) Single nominal VIIRS pixel in the Rolling Plains — the "send a drone" case
    viirs("VIIRS_NOAA21_NRT", 33.42, -99.86, pass_times[0], "n", 3.1)
    # 4) Permian Basin gas flare: same pixel, night passes, three consecutive days
    for d in range(3):
        t = (now - timedelta(days=d)).replace(hour=8, minute=24)
        if t < now:
            viirs("VIIRS_SNPP_NRT", 31.9012 + rnd.gauss(0, 0.0015), -102.3307 + rnd.gauss(0, 0.0015), t, "n", rnd.uniform(2, 5))
    # 5) Low-confidence glint in East Texas
    viirs("VIIRS_NOAA20_NRT", 31.33, -94.73, pass_times[1], "l", 0.9)
    return {k: "\n".join(v) + "\n" for k, v in files.items()}


def _firms_portugal(now: datetime, seed: int) -> dict[str, str]:
    rnd = random.Random(seed)
    head = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"
    rows = {"VIIRS_SNPP_NRT": [head], "VIIRS_NOAA20_NRT": [head], "VIIRS_NOAA21_NRT": [head],
            "MODIS_NRT": ["latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight"]}
    for ds, sat, h, n in (("VIIRS_NOAA20_NRT", "N20", 2, 7), ("VIIRS_SNPP_NRT", "N", 3, 4)):
        t = now - timedelta(hours=h)
        for _ in range(n):   # Serra de Monchique (Algarve)
            rows[ds].append(f"{37.31 + rnd.gauss(0, .003):.5f},{-8.56 + rnd.gauss(0, .004):.5f},{rnd.uniform(335, 367):.2f},0.45,0.40,"
                            f"{t:%Y-%m-%d},{t:%H%M},{sat},VIIRS,{rnd.choice('nh')},2.0NRT,295.1,{rnd.uniform(6, 40):.2f},D")
    t = now - timedelta(hours=2)
    rows["VIIRS_NOAA20_NRT"].append(f"40.65,-7.91,331.2,0.41,0.38,{t:%Y-%m-%d},{t:%H%M},N20,VIIRS,n,2.0NRT,292.0,2.4,D")
    return {k: "\n".join(v) + "\n" for k, v in rows.items()}


def wfigs_fixture(now: datetime) -> tuple[dict, dict]:
    ms = int((now - timedelta(hours=20)).timestamp() * 1000)
    inc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Point", "coordinates": [-98.618, 30.214]},
        "properties": {"IncidentName": "PEDERNALES CREEK", "IncidentTypeCategory": "WF", "IncidentSize": 640,
                       "PercentContained": 35, "FireDiscoveryDateTime": ms, "ModifiedOnDateTime_dt": ms + 3600_000 * 18,
                       "UniqueFireIdentifier": "2026-TXTXS-000412", "IrwinID": "{SAMPLE-0412}", "POOState": "US-TX"}}]}
    ring = [[-98.635 + 0.03 * math.cos(a / 12 * 2 * math.pi), 30.214 + 0.02 * math.sin(a / 12 * 2 * math.pi)] for a in range(12)]
    ring.append(ring[0])
    per = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(x, 4), round(y, 4)] for x, y in ring]]},
        "properties": {"attr_UniqueFireIdentifier": "2026-TXTXS-000412", "poly_IncidentName": "Pedernales Creek"}}]}
    return inc, per
