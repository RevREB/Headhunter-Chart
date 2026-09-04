# Headhunter-Chart

Helm chart for [Headhunter](https://github.com/RevREB/Headhunter-Core) — deploys
the Core engine, the WebMCP dashboard, the scraper catalog, and the Job RBAC the
operator-lite needs, with an optional CloudNativePG database.

## Install (OCI)

```sh
helm install headhunter oci://ghcr.io/revreb/charts/headhunter \
  --namespace career-ops --create-namespace \
  --set postgres.secretName=headhunter-db
```

Core needs a Postgres URL. Either provide a secret:

```sh
kubectl -n career-ops create secret generic headhunter-db \
  --from-literal=url='postgres://user:pass@host:5432/headhunter'
```

…or set `postgres.managed=true` to render a CNPG `Cluster` (requires the
CloudNativePG operator; it emits `<release>-headhunter-db-app` with the URI under
key `uri` — point `postgres.secretName`/`urlKey` at that).

## What it deploys

| Component | Notes |
|---|---|
| Core Deployment + Service | distroless engine; `DATABASE_URL` from the DB secret; mounts the scraper catalog |
| Job RBAC (Role + RoleBinding) | lets Core's operator-lite create one Job per ATS |
| Scraper catalog ConfigMap | the git-declared catalog Core reads (`.Values.scrapers.catalog`) |
| WebMCP Deployment + Service (+ optional Ingress) | dashboard/MCP over the Core API |
| Optional CNPG `Cluster` | `postgres.managed=true` |
| Optional NetworkPolicy | `networkPolicy.enabled=true` — restricts Core egress to DNS/HTTPS/Postgres |

See `charts/headhunter/values.yaml` for the full surface (image tags, resources,
ingress host, security contexts — non-root uid 65532, read-only rootfs, drop-ALL).

## Deploying via Flux (RevNet)

```yaml
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: HelmRepository
metadata: { name: headhunter, namespace: career-ops }
spec: { type: oci, url: oci://ghcr.io/revreb/charts, interval: 1h }
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: { name: headhunter, namespace: career-ops }
spec:
  interval: 1h
  chart:
    spec:
      chart: headhunter
      version: "0.1.x"
      sourceRef: { kind: HelmRepository, name: headhunter }
  values:
    postgres: { secretName: headhunter-db }
```

## Publishing

`helm lint` + `helm template` run on every push; tagging `vX.Y.Z` packages and
pushes the chart to `oci://ghcr.io/revreb/charts/headhunter`.

## License

MIT © 2026 RevREB. See [LICENSE](LICENSE).
