"""Drift guard for the task-4 K8s log-forwarding manifest.

Pins the invariants of k8s/soc-api-deployment.yaml that make the
sidecar pattern actually work — a future edit that breaks the shared
volume, the tail path, the HTTPS/bearer forwarding, or (worse) embeds
secret material fails here instead of at apply time on a cluster.

Zero new dependencies by design: requirements*.txt are hash-locked and
this repo's guards are string/structure-level by convention (the
scripts/check_* family). Full schema validation belongs to
`kubectl apply --dry-run` / kubeconform, neither of which exists on the
dev Mac — recorded as NOT verified in the manifest header.
"""
import re
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "k8s" / "soc-api-deployment.yaml"


def _text() -> str:
    assert MANIFEST.exists(), f"manifest missing: {MANIFEST}"
    return MANIFEST.read_text(encoding="utf-8")


def test_manifest_declares_configmap_and_deployment():
    text = _text()
    assert "kind: ConfigMap" in text
    assert "kind: Deployment" in text
    assert "apiVersion: apps/v1" in text
    # Exactly one ConfigMap + one Deployment doc (no accidental dupes).
    assert len(re.findall(r"(?m)^kind: ", text)) == 2


def test_two_containers_api_and_sidecar():
    text = _text()
    containers = re.findall(r"(?m)^        - name: (api|fluent-bit)$", text)
    assert sorted(containers) == ["api", "fluent-bit"], containers
    assert "fluent/fluent-bit:" in text  # sidecar image pinned by tag


def test_shared_emptydir_consistent():
    """The audit-logs emptyDir volume is defined once and mounted by BOTH
    containers at the same path — the whole premise of the sidecar."""
    text = _text()
    assert "emptyDir: {}" in text
    # 1 volumes entry + 2 volumeMounts (api + fluent-bit)
    assert text.count("- name: audit-logs") == 3, "volume def/mount drift"
    assert text.count("mountPath: /var/log/soc") == 2, "mount paths drifted"
    # The sidecar's copy is read-only (it only tails).
    assert text.count("readOnly: true") >= 1


def test_tail_path_equals_app_file_path():
    """Fluent Bit must tail EXACTLY the file the app writes
    (AUDIT_LOG_FILE), through the shared mountpoint."""
    text = _text()
    m = re.search(r"- name: AUDIT_LOG_FILE\n\s+value: (\S+)", text)
    assert m, "AUDIT_LOG_FILE env missing from api container"
    app_path = m.group(1)

    p = re.search(r"(?m)^\s+Path\s+(\S+)$", text)
    assert p, "Fluent Bit [INPUT] Path missing"
    tail_path = p.group(1)

    assert tail_path == app_path, (tail_path, app_path)
    # ...and it lives under the shared mountpoint.
    mount = re.search(r"(?m)^\s+mountPath: (/var/log/soc)$", text)
    assert mount and app_path.startswith(mount.group(1) + "/")


def test_forwarding_is_https_with_bearer_from_secret():
    text = _text()
    # TLS on + verified (the "over HTTPS" requirement).
    assert re.search(r"(?m)^\s+tls\s+On\s*$", text)
    assert re.search(r"(?m)^\s+tls\.verify\s+On\s*$", text)
    # Bearer token interpolated from an env var...
    assert re.search(
        r"Header\s+Authorization Bearer \$\{SOC_LOG_BEARER_TOKEN\}", text)
    # ...whose value comes from a Secret ref, never inline.
    assert "secretKeyRef" in text
    assert "soc-log-forwarder" in text
    assert re.search(r"- name: SOC_LOG_BEARER_TOKEN\n\s+valueFrom:", text)
    # No Secret object and no literal credential material in the manifest.
    assert "kind: Secret" not in text
    assert re.search(r"(?m)\bvalue:\s+[A-Za-z0-9+/]{32,}=*\s*$", text) is None


def test_every_config_variable_is_supplied():
    """Every ${VAR} the Fluent Bit config interpolates has a matching env
    entry on the sidecar (missing one = silent empty header/host at
    runtime — exactly the class of bug this guard exists for)."""
    text = _text()
    conf = re.search(r"fluent-bit\.conf: \|(.*?)(?=\n  \S|\n---)", text, re.S)
    assert conf, "fluent-bit.conf block missing"
    variables = set(re.findall(r"\$\{([A-Z0-9_]+)\}", conf.group(1)))
    assert variables, "expected interpolation in config"
    for var in variables:
        assert re.search(rf"(?m)^            - name: {var}$", text), (
            f"{var} interpolated by fluent-bit.conf but not defined "
            "as a sidecar env entry"
        )


def test_webhook_is_configurable_not_hardcoded():
    """Host/port/URI ride env vars so the external endpoint is set per
    environment (secret/config), not baked into the repo."""
    text = _text()
    for var in ("SOC_LOG_WEBHOOK_HOST", "SOC_LOG_WEBHOOK_PORT",
                "SOC_LOG_WEBHOOK_URI"):
        assert f"${{{var}}}" in text
        assert f"- name: {var}" in text


def test_configmap_mounted_not_shadowing_image():
    """The sidecar runs with an explicit --config pointing at the mounted
    ConfigMap (both fluent-bit.conf and parsers.conf), so the image's own
    /fluent-bit/etc tree stays untouched."""
    text = _text()
    assert "args:" in text and "--config=/fluent-bit/config/fluent-bit.conf" in text
    assert "parsers.conf: |" in text          # CM ships the parser too
    assert "Parsers_File /fluent-bit/config/parsers.conf" in text
    assert text.count("- name: config") == 2  # volumes entry + mount


def test_fluentbit_state_not_in_shared_volume():
    """Fluent Bit's offset DB must NOT live on the shared audit emptyDir.

    The api container's mount creates /var/log/soc 0755 root, so a
    rootless sidecar cannot create its DB there and crash-loops (observed
    live on the soc-t4 kind cluster, 2026-10-23). Pin that the DB path is
    on the sidecar-private volume instead.
    """
    text = _text()
    m = re.search(r"(?m)^\s+DB\s+(\S+)$", text)
    assert m, "fluent-bit [INPUT] DB missing"
    db_path = m.group(1)
    assert not db_path.startswith("/var/log/soc/"), (
        "fluent-bit DB on the shared audit volume — rootless sidecar "
        "cannot write there")
    # The DB lives on a volume only the sidecar mounts.
    state = re.search(r"(?m)^\s+mountPath: (\S*)/fluentbit\s*$", text)
    assert state, "sidecar-private state volume missing"
    assert db_path.startswith(state.group(1)), (db_path, state.group(1))
    # api container must NOT mount the state volume (keeps it private).
    assert text.count("- name: fluentbit-state") == 2, (
        "state volume should be defined once and mounted once")


def test_api_container_health_and_auth_env():
    """The pod runs the same posture as local: health probe, session
    auth, key+DB from secrets."""
    text = _text()
    assert re.search(r"(?m)^\s+path: /health$", text)
    # Rollout "ready" must mean "serving", not merely "started": a
    # container that has only *started* once failed the very first
    # request (observed live on soc-t4 — curl to a not-yet-bound uvicorn
    # returned connection-refused after a green rollout).
    assert "readinessProbe:" in text, "api readinessProbe missing"
    assert re.search(r"- name: AUTH_MODE\n\s+value: session", text)
    for key in ("api-key", "database-url"):
        assert re.search(rf"(?m)^\s+key: {key}$", text)
