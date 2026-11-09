#!/usr/bin/env python3
"""Render dev Applications only; no secrets, backing-service creation or cluster writes."""

import argparse
import ipaddress
import re
import sys

import yaml


def render(web_image, worker_image, bucket, clickhouse_host):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,220}[a-z0-9]", clickhouse_host):
        raise ValueError("ClickHouse requires a reviewed DNS hostname without URL or credentials")
    try:
        ipaddress.ip_address(clickhouse_host)
    except ValueError:
        pass
    else:
        raise ValueError("ClickHouse requires a DNS name matching its TLS certificate")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid bucket name")
    for image in (web_image, worker_image):
        if not re.fullmatch(r"europe-north1-docker[.]pkg[.]dev/[a-zA-Z0-9_./:@-]+", image):
            raise ValueError("Use an image built into the NAIS artifact registry")
    hostname = "ao-ki-langfuse.intern.dev.nav.no"
    common = {
        "NEXTAUTH_URL": f"https://{hostname}", "HOSTNAME": "0.0.0.0",
        "TELEMETRY_ENABLED": "false", "LANGFUSE_DISABLE_SIGNUP_ONBOARDING": "true",
        "LANGFUSE_IN_APP_AGENT_ENABLED": "false", "LANGFUSE_S3_BATCH_EXPORT_ENABLED": "false",
        "LANGFUSE_USE_GOOGLE_CLOUD_STORAGE": "true", "LANGFUSE_S3_EVENT_UPLOAD_BUCKET": bucket,
        "LANGFUSE_S3_EVENT_UPLOAD_PREFIX": "events/", "CLICKHOUSE_CLUSTER_ENABLED": "false",
        "CLICKHOUSE_URL": f"https://{clickhouse_host}:8443",
        "CLICKHOUSE_MIGRATION_URL": f"clickhouse://{clickhouse_host}:9440",
        "CLICKHOUSE_MIGRATION_SSL": "true", "LANGFUSE_IDENTITY_DIR": "/var/run/langfuse-identities",
    }
    internal_clickhouse = clickhouse_host == "ao-ki-langfuse-clickhouse-tls.ao-ki-taskforce.svc.cluster.local"
    if internal_clickhouse:
        common.update(NODE_EXTRA_CA_CERTS="/var/run/clickhouse-ca/ca.crt",
                      SSL_CERT_FILE="/var/run/clickhouse-ca/ca.crt")
    apps = []
    for component, image in (("web", web_image), ("worker", worker_image)):
        name = "ao-ki-langfuse" if component == "web" else "ao-ki-langfuse-worker"
        port = 3000 if component == "web" else 3030
        health = "/api/public/health" if component == "web" else "/api/health"
        env = {**common, "PORT": str(port)}
        if component == "web":
            env["AUTH_DISABLE_SIGNUP"] = "true"
        spec = {
            "image": image, "port": port, "replicas": {"min": 1, "max": 1},
            "resources": {"requests": {"cpu": "500m", "memory": "2Gi"}, "limits": {"memory": "4Gi"}},
            "startup": {"path": health, "initialDelay": 10, "periodSeconds": 10, "failureThreshold": 90},
            "liveness": {"path": health, "initialDelay": 30, "periodSeconds": 30},
            "readiness": {"path": "/api/public/ready" if component == "web" else "/api/ready", "initialDelay": 10},
            "uses": {"postgres": [{"name": "langfuse", "envPrefix": "LF_",
                                      "role": "admin" if component == "web" else "readwrite"}]},
            "valkey": [{"instance": "langfuse", "access": "readwrite"}],
            "gcp": {"permissions": [{"resource": {
                "apiVersion": "storage.cnrm.cloud.google.com/v1beta1", "kind": "StorageBucket", "name": bucket,
            }, "role": "roles/storage.objectAdmin"}]},
            "filesFrom": [{"emptyDir": {"medium": "Memory"}, "mountPath": "/var/run/langfuse-identities"}],
            "envFrom": [{"secret": "ao-ki-langfuse-server"}],
            "env": [{"name": key, "value": value} for key, value in env.items()],
            "accessPolicy": {"outbound": {"external": [
                {"host": clickhouse_host, "ports": [{"port": 8443}, {"port": 9440}]},
                {"host": "storage.googleapis.com"}, {"host": "oauth2.googleapis.com"},
            ]}},
            "observability": {"autoInstrumentation": {"enabled": False}, "logging": {"destinations": [{"id": "loki"}]}},
        }
        if component == "web":
            spec["gcp"]["buckets"] = [{"name": bucket, "publicAccessPrevention": True,
                                        "cascadingDelete": False, "lifecycleCondition": {"age": 30}}]
            spec["ingresses"] = [f"https://{hostname}"]
            spec["azure"] = {"application": {"enabled": True, "allowAllUsers": True},
                             "sidecar": {"enabled": True, "autoLogin": True,
                                         "autoLoginIgnorePaths": ["/api/public/otel/v1/traces"]}}
            spec["envFrom"].append({"secret": "ao-ki-langfuse-bootstrap"})
            spec["accessPolicy"]["inbound"] = {"rules": [{"application": "ao-ki-transkribering"}]}
        if internal_clickhouse:
            spec["filesFrom"].append({"configmap": "ao-ki-langfuse-clickhouse-ca", "mountPath": "/var/run/clickhouse-ca"})
            spec["accessPolicy"]["outbound"]["external"] = [
                item for item in spec["accessPolicy"]["outbound"]["external"] if item["host"] != clickhouse_host]
        apps.append({"apiVersion": "nais.io/v1alpha1", "kind": "Application", "metadata": {
            "name": name, "namespace": "ao-ki-taskforce", "labels": {"team": "ao-ki-taskforce"},
        }, "spec": spec})
    return apps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--clickhouse-host", required=True)
    parser.add_argument("--component", choices=("all", "web", "worker"), default="all")
    args = parser.parse_args()
    try:
        manifests = render(args.web_image, args.worker_image, args.bucket, args.clickhouse_host)
    except ValueError as error:
        parser.error(str(error))
    if args.component != "all":
        manifests = [manifests[0 if args.component == "web" else 1]]
    yaml.safe_dump_all(manifests, sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main()
