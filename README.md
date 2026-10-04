# Ember Watch — Global Wildfire Intelligence Platform (reference implementation)

A working implementation of the **V0.2 PRD**: live satellite fire detections, fire-weather risk,
evidence-linked alerts and an analyst API, configured per jurisdiction. It runs entirely on free
infrastructure: **GitHub Actions** pulls the live feeds every 30 minutes and **GitHub Pages** serves the dashboard.

```
NASA FIRMS (VIIRS ×3, MODIS) ─┐
Open-Meteo (HRRR / ECMWF)  ───┼─► provider adapters ─► evidence envelope ─► analytics ─► data products ─► Pages dashboard
NIFC WFIGS incidents       ───┤        (allowlisted,      (FR-4 provenance)    • FWI risk grid   (SHA-256        (CSP 'self',
GeoNames places            ───┘         redacted)                               • clustering       manifest,       integrity-
                                                                                • fusion/routing   attested)       checked)
config/jurisdictions/<id>/pack.yaml  ──► everything jurisdiction-specific                              │
                                                                                     Evidence API ◄─┘ (RBAC, audit log, query tool, optional LLM)
```

## What is real and what is not

| Capability | Status |
|---|---|
| Satellite active-fire observations (FIRMS VIIRS S-NPP, NOAA-20, NOAA-21, MODIS) | **Live**, every 30 min |
| Weather (NOAA HRRR for Texas, ECMWF IFS for Portugal) | **Live**, cached 6 h |
| Incidents and perimeters (NIFC WFIGS) | **Live** (US packs) |
| Canadian FWI system, Fosberg FFWI, Hot-Dry-Windy | **Computed** (FWI verified against Van Wagner reference values in tests) |
| Clustering, multi-sensor fusion, flare screening, incident matching, exposure | **Computed** |
| Smokehouse Creek replay (27 Feb 2024) | **Archived real data** (FIRMS SP + archived HRRR), built once and cached |
| Evidence-linked explanations | **Deterministic**, every statement cites evidence IDs |
| LLM brief / natural-language query | **Optional**, numbers machine-checked against evidence |
| Probability of ignition | **Not claimed.** Danger classes come from indices until a model is trained on local fire history |
| Dispatch, evacuation, public warning | **Out of scope** (decision support only, PRD A5) |

## Deploy in 10 minutes (free)

1. **Create the repo.** Push this folder to a new **public** GitHub repository (Actions minutes are free
   for public repos; a private repo running every 30 minutes would exceed the free 2,000 min/month).
2. **Get a FIRMS key.** Request a free MAP_KEY at <https://firms.modaps.eosdis.nasa.gov/api/map_key/>,
   then add it under *Settings → Secrets and variables → Actions → New repository secret* as `FIRMS_MAP_KEY`.
3. **Turn on Pages.** *Settings → Pages → Source: GitHub Actions*.
4. **Run it.** *Actions → Pipeline (live data to Pages) → Run workflow*. The dashboard appears at
   `https://<you>.github.io/<repo>/` and refreshes itself every 30 minutes.

Optional repository **variables** (*Settings → Secrets and variables → Actions → Variables*):

| Variable | Purpose |
|---|---|
| `DEFAULT_JURISDICTION` | Pack the dashboard opens on (default `texas`) |
| `WFI_API_URL` | Your Evidence API URL; turns on analyst decisions in the dashboard |
| `LLM_BASE_URL`, `LLM_MODEL` | OpenAI-compatible endpoint for the AI brief (see below) |
| `REPLAY_AS_OF`, `REPLAY_LABEL`, `REPLAY_PACK` | Historical replay (default: Smokehouse Creek, 2024-02-27T21:00Z) |

## Evidence API (optional, free tier)

Needed only for analyst feedback, the audit log and natural-language queries. The dashboard works without it.

```bash
python -m api.keys analyst          # prints a key once + the JSON line for API_KEYS
```

Deploy on Render with the included `render.yaml` (New → Blueprint), or any Docker host. Set
`DATA_URL=https://<you>.github.io/<repo>/data`, `API_KEYS` (JSON), `ALLOWED_ORIGINS=https://<you>.github.io`.
Then set the repo variable `WFI_API_URL` to the service URL. Endpoints:

| Method | Path | Role |
|---|---|---|
| GET | `/v1/jurisdictions`, `/v1/{j}/summary`, `/candidates`, `/candidates/{id}`, `/risk?lat=&lon=`, `/sources` | viewer |
| POST | `/v1/{j}/query` — structured query tool (routes, score, distance to places, downwind, fire-weather class) | viewer |
| POST | `/v1/{j}/candidates/{id}/feedback` — confirm / reject / correct / request verification | analyst |
| POST | `/v1/{j}/ask` — natural language → validated `query` call (needs LLM) | analyst |
| GET | `/v1/audit` — hash-chained log with integrity check | admin |

## LLM layer (PRD §7.7)

The LLM never sees pixels and never computes numbers. It receives structured evidence and may only
restate it. Any sentence containing a number not present in the evidence is removed and counted.

* **Local, recommended:** Qwen3-8B via Ollama/vLLM: `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=qwen3:8b`.
* **Free in Actions:** GitHub Models: `LLM_BASE_URL=https://models.github.ai/inference`, `LLM_MODEL=openai/gpt-4.1-mini`.
  Marked *EXTERNAL DATA PROCESSING*; benchmark use only per PRD §8.3. The Actions token is only ever sent to that host.

## Add a jurisdiction (no code changes)

Copy `config/jurisdictions/portugal/`, edit `pack.yaml` (providers, weather model, thresholds, units,
agencies), replace `boundary.geojson`, and push. `portugal` exists to prove PRD success criterion SC-5.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements/dev.txt
pytest -q                                                    # 27 tests
python -m wildfire.pipeline --all --fixtures --out site/data # offline, synthetic inputs (labelled)
FIRMS_MAP_KEY=... python -m wildfire.pipeline --all --out site/data   # live
python scripts/build_site.py && python -m http.server -d site 8000
```

## PRD coverage

| PRD item | Where |
|---|---|
| FR-CORE-1, FR-1/2/3, FR-31, FR-32, SC-5 | `config/jurisdictions/*`, `wildfire/config.py` (two packs, no jurisdiction logic in core) |
| FR-4, NFR 5.1–5.3 provenance, latency, native resolution | `wildfire/models.py`, Sources panel |
| FR-6/7/8 dynamic risk with exposed drivers | `wildfire/analytics/risk.py`, `fwi.py`, `indices.py` |
| FR-9/11/12/13/14 detection intelligence | `wildfire/analytics/detect.py`, `wildfire/explain.py` |
| FR-16/17 exposure | GeoNames / Natural Earth places, downwind test |
| FR-26/28 evidence-linked AI | `wildfire/llm.py`, `/v1/{j}/ask` |
| FR-29/30 feedback + audit | `api/app.py`, `api/security.py` |
| FR-39/40 model validation status | `risk.validation` per pack, model card in UI |
| Screen 1, 2, 3, 4, 7 | Config panel, drivers inspector, detection timeline slider + replay, evidence panel |
| NFR 5.6 security | `SECURITY.md` |

**Not built yet** (next steps from the six-week plan): dNBR burn-scar assessment from Sentinel-2/Landsat
(Screen 5), GOES geostationary ingest, UAV imagery inference (Screen 6), reference-perimeter comparison
for the replay, report generation (Screen 8), offline incident packages. See `docs/METHODS.md`.

Licence: Apache-2.0. Data licences are listed per source in the dashboard and in `docs/METHODS.md`.
