#!/usr/bin/env python3
"""Render stable ILB addresses and the opt-in authenticated TLS deployment."""

import argparse
import ipaddress
from pathlib import Path

import yaml


def render(path: Path, address: str, *, secure: bool, gateway_cidr: str):
    ipaddress.ip_address(address)
    resources = list(yaml.safe_load_all(path.read_text()))
    for resource in resources:
        if resource["kind"] == "Service":
            resource["spec"]["loadBalancerIP"] = address
            if secure:
                ipaddress.ip_network(gateway_cidr)
                resource["spec"]["loadBalancerSourceRanges"] = [gateway_cidr]
                resource["spec"]["externalTrafficPolicy"] = "Local"
                resource["spec"]["ports"][0]["name"] = "https"
        if resource["kind"] != "Deployment" or not secure:
            continue
        pod = resource["spec"]["template"]["spec"]
        container = next(c for c in pod["containers"] if c["name"] == "vllm")
        container["args"].extend([
            "--ssl-keyfile", "/app/model-tls/tls.key",
            "--ssl-certfile", "/app/model-tls/tls.crt",
            "--enable-ssl-refresh",
            "--middleware", "model_security.ModelAccessMiddleware",
            "--disable-fastapi-docs", "--disable-log-requests",
        ])
        container.setdefault("env", []).extend([
            {"name": "VLLM_API_KEY", "valueFrom": {"secretKeyRef": {"name": "model-api-auth", "key": "api-key"}}},
            {"name": "PYTHONPATH", "value": "/app/model-security"},
        ])
        container["volumeMounts"].extend([
            {"name": "model-tls", "mountPath": "/app/model-tls", "readOnly": True},
            {"name": "model-security", "mountPath": "/app/model-security", "readOnly": True},
        ])
        container["ports"][0]["name"] = "https"
        for name in ("startupProbe", "readinessProbe", "livenessProbe"):
            container[name]["httpGet"]["scheme"] = "HTTPS"
        pod["volumes"].extend([
            {"name": "model-tls", "secret": {"secretName": "model-api-tls", "defaultMode": 0o440}},
            {"name": "model-security", "configMap": {"name": "model-api-security"}},
        ])
    return resources


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("address")
    parser.add_argument("--secure", action="store_true")
    parser.add_argument("--gateway-cidr", default="10.250.0.0/26")
    args = parser.parse_args()
    print(yaml.safe_dump_all(render(args.manifest, args.address, secure=args.secure, gateway_cidr=args.gateway_cidr), sort_keys=False))
