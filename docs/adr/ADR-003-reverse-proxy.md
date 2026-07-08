# ADR-003: Reverse Proxy and HTTPS on Oracle Free Tier

**Status:** Accepted  
**Date:** 2026-07-07

## Context

The API must be reachable over HTTPS from:
- Telegram Bot API (webhooks require HTTPS)
- Browser/mobile clients

Oracle Cloud Free Tier (Always Free) provides a public IP on the ARM VM but no managed load balancer. TLS certificates must be obtained and renewed automatically — manual certificate management is not acceptable for a personal platform.

## Decision

**Use Caddy 2 as the reverse proxy and TLS terminator.**

## Rationale

- **Automatic TLS.** Caddy provisions and renews Let's Encrypt certificates with zero configuration. No certbot cron, no volume mounts for certificates, no renewal restarts.
- **ARM64 native.** Caddy is a static Go binary compiled for `linux/arm64` — runs natively on the A1 Ampere instance without emulation.
- **Minimal configuration.** A complete production Caddyfile for this setup is 4 lines:
  ```
  api.yourdomain.com {
      reverse_proxy app:8000
      encode gzip
  }
  ```
- **Built-in features.** Automatic HTTP→HTTPS redirect, gzip compression, health-check pass-through, timeout configuration — all without plugins.

## Alternatives Rejected

| Option | Reason rejected |
|---|---|
| nginx + certbot | Requires certbot renewal cron, correct Docker volume mounts, and a reload/restart on renewal. Significant operational overhead for a solo-maintained personal platform. |
| Traefik | Docker-label-driven dynamic config adds complexity for a static two-service setup. Heavier than needed. |
| HAProxy | No built-in ACME/Let's Encrypt; requires external cert tooling. |

## Consequences

- **Requires a domain name** pointing to the Oracle VM's public IP (A record). Caddy cannot provision certificates for bare IP addresses.
- **Oracle security list + iptables:** ports 80 and 443 must be opened to 0.0.0.0/0.
- **Caddy runs as a Docker container** alongside the app and worker; shares a Docker network.
- Let's Encrypt rate limits apply (5 certificates per registered domain per week). Use staging endpoint during initial setup.
- If the domain expires or DNS lapses, certificate renewal will fail. Monitor DNS separately.
