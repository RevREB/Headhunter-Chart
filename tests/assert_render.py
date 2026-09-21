#!/usr/bin/env python3
"""Assert invariants over a rendered Headhunter chart.

`helm lint` only checks that the templates produce parseable YAML; `kubeconform`
only checks each object against its Kubernetes schema. Neither notices that a
Deployment mounts a ConfigMap nobody renders, that WebMCP points CORE_URL at a
Service that does not exist, that an Ingress backs onto a missing port name, or
that a container lost its memory request (which the target k3s cluster's Kyverno
baseline policy rejects). This script checks those cross-object invariants.

Usage:
    helm template hh charts/headhunter [-f values] | \
        python3 tests/assert_render.py - \
            --require-kinds Deployment,Service \
            --forbid-kinds Ingress \
            --expect-db-secret headhunter-db
"""

from __future__ import annotations

import argparse
import re
import sys

import yaml

DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+")
CORE_URL = re.compile(r"^http://(?P<host>[^:/]+):(?P<port>\d+)/?$")

failures: list[str] = []
checks_run = 0


def check(ok: bool, message: str) -> bool:
    global checks_run
    checks_run += 1
    if not ok:
        failures.append(message)
    return ok


def pod_templates(docs):
    """Yield (doc, pod_spec) for every object carrying a pod template."""
    for doc in docs:
        if doc.get("kind") in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            spec = (doc.get("spec") or {}).get("template", {}).get("spec") or {}
            yield doc, spec


def name_of(doc) -> str:
    return (doc.get("metadata") or {}).get("name", "<unnamed>")


def ref(doc) -> str:
    return f"{doc.get('kind', '?')}/{name_of(doc)}"


# --------------------------------------------------------------------------- #
# object-level sanity
# --------------------------------------------------------------------------- #
def check_object_basics(docs) -> None:
    seen = set()
    for doc in docs:
        check(bool(doc.get("apiVersion")), f"{ref(doc)}: missing apiVersion")
        check(bool(doc.get("kind")), f"{ref(doc)}: missing kind")
        name = name_of(doc)
        check(
            bool(DNS1123.match(name)) and len(name) <= 63,
            f"{ref(doc)}: name {name!r} is not a valid DNS-1123 label (<=63 chars)",
        )
        key = (doc.get("apiVersion"), doc.get("kind"), name)
        check(key not in seen, f"{ref(doc)}: duplicate object rendered twice")
        seen.add(key)


# --------------------------------------------------------------------------- #
# workload hardening — these mirror what the target cluster actually enforces
# --------------------------------------------------------------------------- #
def check_workloads(docs) -> None:
    for doc, spec in pod_templates(docs):
        pod_sc = spec.get("securityContext") or {}
        check(
            pod_sc.get("runAsNonRoot") is True,
            f"{ref(doc)}: pod securityContext.runAsNonRoot must be true",
        )
        containers = spec.get("containers") or []
        check(bool(containers), f"{ref(doc)}: no containers")

        for c in containers:
            cid = f"{ref(doc)} container {c.get('name')}"
            image = c.get("image", "")
            # Kyverno baseline on the deployment target rejects floating tags.
            check(":" in image.rsplit("/", 1)[-1], f"{cid}: image {image!r} has no explicit tag")
            check(
                not image.endswith(":latest"),
                f"{cid}: image {image!r} uses the floating :latest tag",
            )
            requests = ((c.get("resources") or {}).get("requests")) or {}
            check(
                bool(requests.get("memory")),
                f"{cid}: no resources.requests.memory (Kyverno baseline rejects this)",
            )
            check(
                bool(requests.get("cpu")),
                f"{cid}: no resources.requests.cpu",
            )

            sc = c.get("securityContext") or {}
            check(
                sc.get("readOnlyRootFilesystem") is True,
                f"{cid}: securityContext.readOnlyRootFilesystem must be true",
            )
            check(
                sc.get("allowPrivilegeEscalation") is False,
                f"{cid}: securityContext.allowPrivilegeEscalation must be false",
            )
            drops = ((sc.get("capabilities") or {}).get("drop")) or []
            check("ALL" in drops, f"{cid}: securityContext.capabilities.drop must include ALL")

            # probes must target a port the container actually declares
            port_names = {p.get("name") for p in (c.get("ports") or [])}
            for probe_name in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(probe_name) or {}
                http = probe.get("httpGet") or {}
                port = http.get("port")
                if isinstance(port, str):
                    check(
                        port in port_names,
                        f"{cid}: {probe_name} targets port {port!r}, "
                        f"container declares {sorted(n for n in port_names if n)}",
                    )

        # a Deployment whose selector does not match its own template never
        # becomes ready; helm lint cannot see this.
        selector = ((doc.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
        tmpl_labels = ((doc.get("spec") or {}).get("template") or {}).get("metadata", {}).get(
            "labels"
        ) or {}
        check(bool(selector), f"{ref(doc)}: empty selector.matchLabels")
        for k, v in selector.items():
            check(
                tmpl_labels.get(k) == v,
                f"{ref(doc)}: selector {k}={v} does not match pod template labels {tmpl_labels}",
            )


# --------------------------------------------------------------------------- #
# cross-object wiring
# --------------------------------------------------------------------------- #
def check_wiring(docs, external_configmaps: set[str], expect_db_secret: str | None) -> None:
    configmaps = {name_of(d) for d in docs if d.get("kind") == "ConfigMap"}
    services = {name_of(d): d for d in docs if d.get("kind") == "Service"}
    accounts = {name_of(d) for d in docs if d.get("kind") == "ServiceAccount"}
    pod_label_sets = [
        ((d.get("spec") or {}).get("template") or {}).get("metadata", {}).get("labels") or {}
        for d, _ in pod_templates(docs)
    ]

    for doc, spec in pod_templates(docs):
        for vol in spec.get("volumes") or []:
            cm = (vol.get("configMap") or {}).get("name")
            if cm and cm not in external_configmaps:
                check(
                    cm in configmaps,
                    f"{ref(doc)}: volume {vol.get('name')!r} mounts ConfigMap {cm!r}, "
                    f"which this release does not render (rendered: {sorted(configmaps)})",
                )
        # If the release creates a ServiceAccount, the workloads must use it.
        if accounts:
            sa = spec.get("serviceAccountName")
            if sa is not None:
                check(
                    sa in accounts,
                    f"{ref(doc)}: serviceAccountName {sa!r} is not one of the rendered "
                    f"ServiceAccounts {sorted(accounts)}",
                )

    # Services must select at least one pod this release renders.
    for svc_name, svc in services.items():
        selector = (svc.get("spec") or {}).get("selector") or {}
        check(bool(selector), f"Service/{svc_name}: empty selector")
        matched = any(
            all(labels.get(k) == v for k, v in selector.items()) for labels in pod_label_sets
        )
        check(matched, f"Service/{svc_name}: selector {selector} matches no rendered pod template")

    # RoleBindings must bind a ServiceAccount the release created (when it does).
    for doc in docs:
        if doc.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        for subject in doc.get("subjects") or []:
            if subject.get("kind") == "ServiceAccount" and accounts:
                check(
                    subject.get("name") in accounts,
                    f"{ref(doc)}: binds ServiceAccount {subject.get('name')!r}, "
                    f"which the release does not render {sorted(accounts)}",
                )

    # Ingress backends must resolve to a rendered Service and a real port name.
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        for rule in (doc.get("spec") or {}).get("rules") or []:
            check(bool(rule.get("host")), f"{ref(doc)}: rule without a host")
            for path in (rule.get("http") or {}).get("paths") or []:
                backend = ((path.get("backend") or {}).get("service")) or {}
                svc_name = backend.get("name")
                if not check(
                    svc_name in services,
                    f"{ref(doc)}: backend Service {svc_name!r} is not rendered",
                ):
                    continue
                port = backend.get("port") or {}
                svc_ports = (services[svc_name].get("spec") or {}).get("ports") or []
                if "name" in port:
                    check(
                        port["name"] in {p.get("name") for p in svc_ports},
                        f"{ref(doc)}: backend port name {port['name']!r} not on Service/{svc_name}",
                    )
                elif "number" in port:
                    check(
                        port["number"] in {p.get("port") for p in svc_ports},
                        f"{ref(doc)}: backend port {port['number']} not on Service/{svc_name}",
                    )

    # WebMCP talks to Core over CORE_URL — it must point at a rendered Service
    # and at a port that Service actually exposes.
    for doc, spec in pod_templates(docs):
        for c in spec.get("containers") or []:
            for env in c.get("env") or []:
                if env.get("name") != "CORE_URL":
                    continue
                url = env.get("value", "")
                m = CORE_URL.match(url)
                if not check(bool(m), f"{ref(doc)}: CORE_URL {url!r} is not http://host:port"):
                    continue
                host, port = m.group("host"), int(m.group("port"))
                if not check(
                    host in services,
                    f"{ref(doc)}: CORE_URL points at {host!r}, which is not a rendered Service",
                ):
                    continue
                svc_ports = {p.get("port") for p in (services[host].get("spec") or {}).get("ports") or []}
                check(
                    port in svc_ports,
                    f"{ref(doc)}: CORE_URL port {port} is not exposed by Service/{host} {sorted(svc_ports)}",
                )

    # Core must get its DSN from a secret, and from the one we expect.
    for doc, spec in pod_templates(docs):
        for c in spec.get("containers") or []:
            for env in c.get("env") or []:
                if env.get("name") != "DATABASE_URL":
                    continue
                skr = ((env.get("valueFrom") or {}).get("secretKeyRef")) or {}
                check(
                    bool(skr.get("name")) and bool(skr.get("key")),
                    f"{ref(doc)}: DATABASE_URL secretKeyRef is incomplete: {skr}",
                )
                if expect_db_secret:
                    check(
                        skr.get("name") == expect_db_secret,
                        f"{ref(doc)}: DATABASE_URL reads secret {skr.get('name')!r}, "
                        f"expected {expect_db_secret!r}",
                    )

    # A managed CNPG Cluster must emit the secret Core was pointed at: CNPG
    # names it "<cluster>-app". This is the wiring the README documents.
    clusters = [d for d in docs if d.get("kind") == "Cluster"]
    if clusters and expect_db_secret:
        expected = {f"{name_of(c)}-app" for c in clusters}
        check(
            expect_db_secret in expected,
            f"postgres.managed renders {sorted(name_of(c) for c in clusters)} but Core reads "
            f"secret {expect_db_secret!r}; CNPG will create {sorted(expected)}",
        )


# --------------------------------------------------------------------------- #
# the scraper catalog is a YAML document embedded in a YAML string — Core
# parses it at runtime, so a bad indent here is a production outage that
# neither helm lint nor kubeconform can see.
# --------------------------------------------------------------------------- #
def check_scraper_catalog(docs) -> None:
    catalogs = [
        d
        for d in docs
        if d.get("kind") == "ConfigMap" and "catalog.yaml" in (d.get("data") or {})
    ]
    if not check(bool(catalogs), "no ConfigMap with a catalog.yaml key was rendered"):
        return

    for cm in catalogs:
        raw = cm["data"]["catalog.yaml"]
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            check(False, f"{ref(cm)}: catalog.yaml is not parseable YAML: {exc}")
            continue
        if not check(
            isinstance(parsed, dict), f"{ref(cm)}: catalog.yaml must be a mapping, got {type(parsed).__name__}"
        ):
            continue
        entries = parsed.get("scrapers")
        if not check(
            isinstance(entries, list) and bool(entries),
            f"{ref(cm)}: catalog.yaml needs a non-empty 'scrapers' list, got {entries!r}",
        ):
            continue
        for i, entry in enumerate(entries):
            where = f"{ref(cm)}: scrapers[{i}]"
            if not check(isinstance(entry, dict), f"{where} is not a mapping"):
                continue
            for field in ("ats", "tier", "manifest", "contractVersion"):
                check(field in entry, f"{where} is missing required field {field!r}")
            if "tier" in entry:
                check(isinstance(entry["tier"], int), f"{where}.tier must be an integer")
            if "manifest" in entry:
                check(
                    str(entry["manifest"]).endswith((".yaml", ".yml")),
                    f"{where}.manifest {entry['manifest']!r} is not a YAML path",
                )
            if "contractVersion" in entry:
                check(
                    bool(SEMVER.match(str(entry["contractVersion"]))),
                    f"{where}.contractVersion {entry['contractVersion']!r} is not semver",
                )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", help="rendered manifest file, or - for stdin")
    ap.add_argument("--require-kinds", default="", help="comma-separated kinds that must be present")
    ap.add_argument("--forbid-kinds", default="", help="comma-separated kinds that must be absent")
    ap.add_argument("--expect-db-secret", default=None, help="secret name Core's DATABASE_URL must read")
    ap.add_argument(
        "--external-configmaps",
        default="",
        help="comma-separated ConfigMaps that are allowed to be mounted without being rendered",
    )
    args = ap.parse_args()

    text = sys.stdin.read() if args.manifest == "-" else open(args.manifest).read()
    try:
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        print(f"FAIL: rendered output is not parseable YAML: {exc}", file=sys.stderr)
        return 1

    if not docs:
        print("FAIL: the chart rendered no objects at all", file=sys.stderr)
        return 1

    kinds = {d.get("kind") for d in docs}
    for kind in [k.strip() for k in args.require_kinds.split(",") if k.strip()]:
        check(kind in kinds, f"expected a {kind} to be rendered; rendered kinds: {sorted(kinds)}")
    for kind in [k.strip() for k in args.forbid_kinds.split(",") if k.strip()]:
        check(kind not in kinds, f"{kind} must NOT be rendered for this values scenario")

    check_object_basics(docs)
    check_workloads(docs)
    check_wiring(
        docs,
        {c.strip() for c in args.external_configmaps.split(",") if c.strip()},
        args.expect_db_secret,
    )
    check_scraper_catalog(docs)

    print(f"{len(docs)} objects: {sorted(kinds)}")
    if failures:
        print(f"\n{len(failures)} of {checks_run} assertions FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"all {checks_run} assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
