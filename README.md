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

## CI

`.github/workflows/test.yml` runs on every pull request and every push to
`main`:

- `helm lint --strict` against the default values and each `charts/headhunter/ci/*-values.yaml` scenario;
- `helm template` for three scenarios — default, `full-values.yaml` (Ingress +
  NetworkPolicy + managed CNPG), `minimal-values.yaml` (Core only, external
  ServiceAccount and DB);
- `kubeconform -strict` schema validation of every rendered object, including
  the CNPG `Cluster` CRD (no schemas skipped);
- `tests/assert_render.py` — cross-object invariants `helm lint` cannot see:
  volumes resolve to ConfigMaps the release renders, `CORE_URL` points at a
  real Service port, the Ingress backend exists, RoleBindings bind a rendered
  ServiceAccount, every container keeps its CPU/memory requests, a pinned
  (non-`latest`) image tag and the non-root/read-only/drop-ALL hardening, and
  the embedded scraper catalog parses as YAML with the fields Core requires.

It does **not** install the chart — there is no cluster in CI, so nothing here
proves the images boot or that Core can reach Postgres.

Run the same checks locally:

```sh
helm template hh charts/headhunter -f charts/headhunter/ci/full-values.yaml \
  | python3 tests/assert_render.py - --expect-db-secret hh-db-app
```

## Publishing

Tagging `vX.Y.Z` packages and pushes the chart to
`oci://ghcr.io/revreb/charts/headhunter`.

## License

MIT © 2026 RevREB. See [LICENSE](LICENSE).
