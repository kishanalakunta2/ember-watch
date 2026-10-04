# Security

Baseline controls mapped to PRD NFR 5.6. Exact frameworks (e.g. Texas TAC 202, TX-RAMP, NIS2) are
jurisdiction-specific and need a formal assessment before production.

## Supply chain
* **Hash-pinned dependencies.** Every install uses `pip install --require-hashes` from `requirements/*.txt`.
* **Actions pinned to commit SHAs**, Dependabot keeps them current (pip, actions, docker).
* **CI gates:** ruff with bandit (`S`) rules, 27 tests, `pip-audit --strict`, secret scan, CodeQL
  (Python, JavaScript, Actions), offline end-to-end run.
* **Provenance:** each run's `manifest.json` (SHA-256 of every data product) gets a GitHub build-provenance
  attestation (`gh attestation verify`), and run records are kept as artifacts for 90 days.
* **No CDNs at runtime.** MapLibre and fonts are vendored; the site's CSP is `'self'` plus basemap tiles and
  the configured API origin only.

## Secrets
* `FIRMS_MAP_KEY`, `LLM_API_KEY`, `API_KEYS` live in GitHub/host secret stores, never in the repo.
* Keys are redacted from logs and never written into provenance (`source_uri` shows `<MAP_KEY>`).
* The workflow's own token is only sent to `models.github.ai`, never to any other LLM host.
* Workflows default to `permissions: {}`; each job requests the minimum (`contents: read`, Pages deploy
  scopes only in the deploy job). `persist-credentials: false` on checkout.

## Pipeline
* Outbound requests go only to an explicit host allowlist (no SSRF via config), HTTPS with verification,
  no redirects, bounded timeouts and a 60 MB response cap.
* All provider records are validated with strict Pydantic models; malformed rows are dropped and logged.
* Jurisdiction IDs are regex-validated and pack file paths cannot escape their folder.

## Evidence API
* API keys `wfi_<id>_<secret>`; only SHA-256 of the secret is stored; constant-time comparison.
* RBAC: viewer < analyst < admin. Writes need analyst; audit needs admin.
* Token-bucket rate limits (reads 120/min, writes 20/min per key), 16 KB body cap, strict schemas
  (`extra="forbid"`), control-character stripping, path-parameter regexes.
* Security headers: HSTS, CSP `default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store`.
* OpenAPI docs are off unless `ENABLE_DOCS=1`. CORS allows only `ALLOWED_ORIGINS`.
* Data products are re-verified against `manifest.json` hashes before being served (tampered files → 503).
* **Tamper-evident audit log:** every feedback, query and failed login is appended to a hash chain;
  `/v1/audit` reports whether the chain is intact.
* Container runs as UID 10001 with hash-pinned deps and a health check.

## Dashboard
* All data is inserted with `textContent`; no `innerHTML` of data anywhere.
* Data files are verified against the manifest with WebCrypto before rendering.
* An analyst API key, if entered, stays in memory for the tab only (no storage).

## Known limits (be honest with stakeholders)
* **GitHub Pages is public.** Everything it serves is public satellite and weather data. Agency-sensitive
  data (dispatch, cameras, UAV imagery, analyst notes) must stay behind the Evidence API or a private host
  (GitHub Enterprise private Pages, Cloudflare Access, agency network).
* **Free hosting has ephemeral disks** (Render free tier). Mount persistent storage or move the audit log to
  a managed database (e.g. free-tier Postgres) before relying on it.
* API keys are a demo-grade identity layer. Production should use the agency identity provider (OIDC/SAML)
  with MFA, per PRD 5.6.
* Rate limits are in-memory per instance; use a shared store when scaling out.

Report vulnerabilities privately via GitHub Security Advisories on this repository.
