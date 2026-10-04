"""Hourly fire-weather indices that complement the daily FWI system.

* Fosberg Fire Weather Index (Fosberg 1978; Goodrick 2002 form): responds to
  hour-by-hour humidity and wind, good for wind-driven grass fires.
* Hot-Dry-Windy Index (Srock et al. 2018): VPD (hPa) x wind (m/s). The
  canonical HDW uses the max over the lowest 500 m; with 10 m inputs this
  is a surface approximation and is labelled as such.
"""
from __future__ import annotations

import math


def _emc_fosberg(temp_f: float, rh: float) -> float:
    if rh < 10:
        return 0.03229 + 0.281073 * rh - 0.000578 * rh * temp_f
    if rh <= 50:
        return 2.22749 + 0.160107 * rh - 0.01478 * temp_f
    return 21.0606 + 0.005565 * rh ** 2 - 0.00035 * rh * temp_f - 0.483199 * rh


def ffwi(temp_c: float, rh: float, wind_kmh: float) -> float:
    temp_f = temp_c * 9 / 5 + 32
    u_mph = wind_kmh / 1.609344
    m = max(0.0, _emc_fosberg(temp_f, max(0.0, min(100.0, rh))))
    x = m / 30.0
    eta = max(0.0, 1 - 2 * x + 1.5 * x ** 2 - 0.5 * x ** 3)
    return min(100.0, eta * math.sqrt(1 + u_mph ** 2) / 0.3002)


def hdw(vpd_kpa: float, wind_kmh: float) -> float:
    return max(0.0, vpd_kpa * 10.0) * max(0.0, wind_kmh / 3.6)


def vpd_kpa(temp_c: float, rh: float) -> float:
    es = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return es * (1 - max(0.0, min(100.0, rh)) / 100.0)
