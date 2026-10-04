# Methods

## 1. Fire-weather risk (FR-6, FR-7, FR-8)

**Grid.** Cell centres on a regular lat/lon grid inside the jurisdiction boundary (0.5° Texas, 0.25°
Portugal). The UI always states the grid spacing so no false precision is implied (NFR 5.2).

**Weather.** Hourly 2 m temperature, RH, 10 m wind speed/direction/gusts, precipitation and VPD from
Open-Meteo, using the model named in the pack (NOAA HRRR 3 km for Texas; ECMWF IFS 0.25° for Portugal).
Gaps in the primary model are filled from a fallback model and reported.

**Canadian Forest Fire Weather Index System** (Van Wagner 1987), as used by EFFIS and GWIS:
FFMC, DMC, DC → ISI, BUI → FWI, DSR. Daily values use local solar noon (UTC + lon/15) and 24 h rain
ending at noon. Latitude-adjusted day-length factors follow the `cffdrs` R package. The codes are
spun up from standard start values (85, 6, 15) over 60 days of analysis. DC has a long memory, so a
60-day spin-up underestimates deep drought after wet winters; a production system should carry state
between runs or start from a seasonal climatology.
Verified in `tests/` against the Van Wagner & Pickett (1985) reference sequence
(day 1: FFMC 87.7, DMC 8.5, DC 19.0, ISI 10.9, BUI 8.5, FWI 10.1).

**Supplementary drivers.** Fosberg FFWI (hourly max; responsive to wind and RH, useful for grass fires),
surface Hot-Dry-Windy Index (VPD hPa × wind m/s; the canonical index uses the lowest 500 m, so this is
an approximation), minimum RH, maximum gust, days since ≥ 2.5 mm rain.

**Classes.** Pack-configured thresholds on a chosen index (default: EFFIS FWI classes 5.2 / 11.2 / 21.3 / 38).
These are danger classes, **not probabilities**. Each pack records `validation.status`; neither shipped
pack is validated. Calibration path: fit thresholds or a gradient-boosted model (LightGBM/XGBoost) on
historical ignitions (e.g. FPA-FOD for the US) with these drivers plus fuels (LANDFIRE), then report
calibration and discrimination by ecosystem and season (PRD §11).

## 2. Detection intelligence (FR-9 to FR-14)

1. **Normalise.** FIRMS CSV → `Observation` envelope with sensor, platform, observation and ingestion
   time, native resolution (375 m VIIRS, 1 km MODIS), FRP, day/night and licence.
2. **Prior per pixel.** VIIRS low/nominal/high → 0.30 / 0.70 / 0.90 (low includes sun glint and weak
   anomalies). MODIS confidence/100, clamped to 0.05–0.95.
3. **Cluster.** Single-linkage: two observations join when within `cluster_radius_km + max_spread_kmh × Δt`
   (Δt capped at 6 h) and inside the time window. The spread term keeps a wind-driven head fire that moved
   several km between passes as one fire.
4. **Pass-aware fusion.** Pixels from one overpass are not independent, so each pass contributes
   `1 − (1 − p_max)·0.85^(n−1)`; passes combine by noisy-OR. Independent platforms and repeat passes
   therefore raise confidence (PRD UC-D2).
5. **Static sources.** ≥ 3 distinct days, extent ≤ 0.8 km and ≤ 2 pixels per pass → likely gas flare or
   industrial heat (common in the Permian Basin). Score × 0.25.
6. **Weather plausibility.** Low / moderate fire weather or ≥ 5 mm rain in 24 h scales the score by 0.85–0.92.
7. **Incident match.** Inside a WFIGS perimeter or within `incident_match_km` of an incident point.
8. **Route** (thresholds per pack): known incident → monitor; static → suppress; ≥ 0.85 → priority review;
   ≥ 0.50 → verify with camera/aircraft/UAS (with a suggested search radius); else watch.
9. **Exposure.** Populated places within the pack radius, with distance, bearing and a downwind flag
   (bearing within 45° of the wind's travel direction).

All weights are explicit, configurable and shown in the UI. They are engineering priors, not fitted
values; analyst feedback from the API (confirm/reject) is the training signal for fitting them.

## 3. Explanations and LLM guardrails (FR-14, FR-26, FR-28)

Statements are generated deterministically and tagged Observed / Calculated / Model-derived / Unknown with
evidence IDs. The optional LLM may only rephrase evidence; a verifier removes any sentence whose numbers
are not in the evidence, and the count of removed claims is published (PRD §11 "unsupported-claim rate").

## 4. Data sources and licences

| Source | Use | Licence |
|---|---|---|
| NASA FIRMS (LANCE) VIIRS/MODIS NRT and SP | Active fire | NASA open data, attribution requested |
| Open-Meteo (HRRR, ECMWF IFS, archived runs) | Weather | CC BY 4.0; free tier non-commercial |
| NIFC WFIGS | Incidents, perimeters | US Government public data |
| GeoNames cities1000 | Exposure | CC BY 4.0 |
| Natural Earth | Boundaries, fallback places | Public domain |
| MapLibre GL JS 5.24 | Map | BSD-3-Clause |
| IBM Plex, Barlow Condensed | Fonts | SIL OFL 1.1 |
| CARTO basemap tiles | Optional basemap | © OSM contributors (ODbL), © CARTO; free tier limits apply |

For commercial or government production use, replace Open-Meteo's free tier with a self-hosted
Open-Meteo instance or direct NOAA NOMADS / ECMWF Open Data ingestion.

## 5. Replay (PRD §16, Screen 3)

`--as-of 2024-02-27T21:00Z` rebuilds the situation at that moment: FIRMS standard-processing archives for
the preceding 3 days, archived HRRR runs from Open-Meteo's historical-forecast API, and observations
after the as-of time removed. WFIGS has no history, so the replay notes that the reference perimeter
comparison is the next step (MTBS / NIFC historical perimeters, then dNBR in Screen 5).
