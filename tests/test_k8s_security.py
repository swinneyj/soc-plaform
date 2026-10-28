"""K8S-01..04 — structural validation for security/deployment manifests.

String/structure-level by repo convention (no new deps): each manifest must
declare the kind, selectors, and controls the work order requires, and must
never embed secret material.
"""
import re
from pathlib import Path

K8S = Path(__file__).resolve().parent.parent / "k8s"


def _text(name: str) -> str:
    p = K8S / name
    assert p.exists(), f"manifest missing: {p}"
    return p.read_text(encoding="utf-8")


def test_k8s01_api_ingress_restricts_to_ingress_nginx():
    text = _text("netpol-api-ingress.yaml")
    assert "kind: NetworkPolicy" in text
    assert "app: soc-api" in text
    assert "port: 8000" in text
    assert "ingress-nginx" in text
    # Ingress-only policy (egress unrestricted for the API here).
    assert "Ingress" in text
    # No open CIDR allow (ignore comments).
    _code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    assert "0.0.0.0/0" not in _code


def test_k8s02_llm_zero_egress():
    text = _text("netpol-llm-zero-egress.yaml")
    assert "kind: NetworkPolicy" in text
    assert "app: soc-llm" in text
    assert "PolicyTypes" in text or "policyTypes" in text
    assert "Ingress" in text and "Egress" in text
    # Ingress only from soc-api.
    assert "app: soc-api" in text
    # DNS-only egress (port 53), no open egress.
    assert "port: 53" in text
    _code2 = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    assert "0.0.0.0/0" not in _code2
    # Inference port pinned.
    assert "11434" in text


def test_k8s03_ingress_strips_identity_headers():
    text = _text("ingress-nginx.yaml")
    assert "kind: Ingress" in text
    assert "ingressClassName: nginx" in text
    assert "tls:" in text and "soc-tls" in text
    # Header strip control via snippet annotation.
    assert "configuration-snippet" in text or "server-snippet" in text
    assert "X-Auth-Request-User" in text
    assert "X-SSL-Client-S-DN" in text
    # Routes both frontend and api.
    assert "name: soc-api" in text and "name: soc-frontend" in text


def test_k8s04_postgres_hardened():
    text = _text("postgres-statefulset.yaml")
    assert "kind: StatefulSet" in text
    assert "kind: Service" in text
    assert "kind: StorageClass" in text
    assert "postgres:15" in text
    assert "clusterIP: None" in text
    # Non-root.
    assert "runAsNonRoot: true" in text
    assert re.search(r"runAsUser:\s*999", text), "must run as uid 999"
    # Credentials via secretKeyRef, never inline.
    assert "secretKeyRef" in text
    assert "soc-postgres" in text
    assert "postgres-password" in text
    # Encrypted storage contract + PVC.
    assert 'encrypted: "true"' in text
    assert "volumeClaimTemplates" in text
    assert "storage: 10Gi" in text
    # No literal credential material.
    assert re.search(r"(?m)\bvalue:\s+[A-Za-z0-9+/]{32,}=*\s*$", text) is None
