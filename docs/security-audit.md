# Security audit

## Scope

The repeatable black-box audit is in `scripts/security_audit.py`. It targets
only a discocs deployment explicitly supplied with `--backend-url` and,
optionally, `--frontend-url`. It does not read or print real credentials.

Covered checks:

- anonymous access to API, private admin, debug, OpenAPI, workers, jobs and map;
- forged session cookie and forged service token;
- anonymous state-changing requests;
- cross-origin login/CSRF behavior;
- malformed login input;
- application security headers and auth response caching;
- public nginx denial of operational routes and CSP;
- controlled invalid-password bursts with fixed and rotating source addresses.

The regression suite additionally exercises two-user IDOR isolation for
playlists and playback sessions in `tests/test_multiuser_api_scope.py`, and
the security boundary regressions in `tests/test_security_hardening.py`.
The public CSP allows `wasm-unsafe-eval` solely for the packaged Signalsmith
audio engine while continuing to reject the broader JavaScript `unsafe-eval`.

## Running

Read-only and malformed-input checks:

```powershell
python scripts/security_audit.py `
  --backend-url http://192.168.1.41:8711
```

Add the public frontend when its URL is reachable from the runner:

```powershell
python scripts/security_audit.py `
  --backend-url http://192.168.1.41:8711 `
  --frontend-url https://discocs.example.com
```

Controlled brute-force verification uses a unique, nonexistent username and
at most 20 attempts. It intentionally consumes login limiter entries and sends
failed Navidrome pings, so run it deliberately:

```powershell
python scripts/security_audit.py `
  --backend-url http://192.168.1.41:8711 `
  --brute-force --attempts 6
```

Exit code is 0 when all expectations pass, 1 for a security finding, and 2
when the deployment cannot be reached.

## July 2026 audit findings

Confirmed before hardening:

1. Anonymous backend access was correctly denied with `401` for dashboard,
   private admin, debug, settings, jobs, workers, map, OpenAPI and Swagger.
2. Per-IP lockout worked for a fixed address, but rotating a client-controlled
   `X-Forwarded-For` bypassed it and every attempt reached Navidrome.
3. Auth-enabled backend responses had no security headers.
4. CORS defaulted to `Access-Control-Allow-Origin: *`; a foreign-origin login
   request reached Navidrome.

Implemented remediation:

- frontend nginx overwrites `X-Forwarded-For` and strips any incoming
  `X-Discocs-Service-Token`;
- backend accepts forwarded identity only from configured Docker proxy CIDRs;
  direct LAN `:8711` clients are keyed by their real socket address;
- login failures are limited by both source IP and normalized username;
- foreign-origin browser mutations are rejected before authentication/IdP IO;
- wildcard CORS is disabled when auth is enabled unless an explicit allowlist
  is configured;
- backend security headers, auth `Cache-Control: no-store`, and frontend CSP
  are enforced;
- regression tests and this black-box audit prevent silent reintroduction.

## Residual risks

- The login limiter is per-process memory. A restart clears it; multiple backend
  processes would each have a separate counter. Move it to SQLite before
  scaling beyond one backend process.
- The service-account Navidrome password can still be stored as plaintext in
  private `data/settings.json` when configured through `/admin`. Prefer env
  injection now; add encrypted-at-rest runtime settings if UI-based service
  credential management remains necessary.
- Sessions have a long absolute lifetime and there is no user-facing session
  inventory/revocation or 2FA. Those are Phase 3 hardening items.
- Any valid Navidrome user can invoke personal generation/streaming endpoints.
  Existing job de-duplication limits concurrency, but per-user quotas are still
  advisable before broad untrusted-user exposure.
- Keep backend port `:8711` private/LAN-only. Public exposure must terminate at
  the frontend nginx; firewall and external TLS/HSTS remain deployment controls.
- Dependency and container findings are authoritative in Jenkins Trivy and
  SonarQube after each pushed change.
