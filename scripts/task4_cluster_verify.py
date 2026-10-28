#!/usr/bin/env python3
"""Cross-platform, no-manual-steps verification of the task-4 control.

Strict security-list task 4 asks for a K8s Deployment (FastAPI + Fluent Bit
sidecar, shared emptyDir, tail the JSON log file, forward over HTTPS with a
bearer token to an external webhook). The manifest lives at
k8s/soc-api-deployment.yaml; THIS script proves it works to the letter by
actually standing up a local kind cluster and running the whole pipeline:

  tools (brew/apt/winget) -> engine (podman preferred, docker fallback)
  -> kind cluster -> platform image build+import -> dummy secrets (in-pod
  sqlite, NO shared-Neon contact) -> apply -> webhook env override ->
  host-side HTTPS sink (own CA) -> CA mounted into the fluent-bit sidecar
  via ConfigMap + the sidecar's tls.ca_file (the image is distroless: no
  exec/cp possible)
  -> generate a real security event (ungated 401) -> assert the audit line
  arrives at the sink over TLS with the Authorization: Bearer header.

One Python process drives everything on purpose: on some machines (this
Mac's is documented) helper processes spawned by podman/kind die between
shells but live for the lifetime of the spawning process.

Cross-platform install branches:
  macOS : brew install podman kind kubectl
  Linux : apt-get/dnf install podman kind kubectl  (may require one sudo)
  Windows: winget install RedHat.Podman / kind / Kubernetes-cli; Podman on
          Windows needs WSL2, whose very first enablement is the one
          irreducible OS-level step (admin prompt / reboot) on a fresh box.
Verified end-to-end on macOS/arm64 (2026-10-21); the other branches follow
the documented install paths but have not been executed by this repo yet.

Usage:
  python scripts/task4_cluster_verify.py            # verify (keeps cluster)
  python scripts/task4_cluster_verify.py --teardown # verify, then remove it
"""
import argparse
import datetime
import http.server
import ipaddress
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLUSTER = "soc-t4"
NODE = CLUSTER + "-control-plane"
MANIFEST = os.path.join(REPO, "k8s", "soc-api-deployment.yaml")
SINK_PORT = 18443
STAGE = [0]


def log(msg):
    STAGE[0] += 1
    print("[%d] %s" % (STAGE[0], msg), flush=True)


def die(msg, hint=""):
    print("FAILED: %s" % msg, file=sys.stderr, flush=True)
    if hint:
        print("hint: %s" % hint, file=sys.stderr, flush=True)
    sys.exit(1)


def sh(args, timeout=300, check=True, stdin=None, capture=True):
    """Run a command; return CompletedProcess (stdout kept when captured)."""
    try:
        proc = subprocess.run(
            args, cwd=REPO, timeout=timeout, stdin=stdin,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True,
        )
    except FileNotFoundError:
        die("command not found: %s" % args[0],
            "install it (brew/apt/winget) and re-run")
    except subprocess.TimeoutExpired:
        die("timeout running: %s" % " ".join(args[:6]))
    if check and proc.returncode != 0:
        out = (proc.stdout or "")[-2000:]
        die("command failed (%d): %s\n%s" %
            (proc.returncode, " ".join(args[:8]), out))
    return proc


# --------------------------------------------------------------------------
# Stage A: tools (cross-platform install branches)
# --------------------------------------------------------------------------
def ensure_tools():
    log("ensuring tools (podman, kind, kubectl)…")
    missing = [t for t in ("podman", "kind", "kubectl")
               if shutil.which(t) is None]
    if not missing:
        return
    if sys.platform == "darwin":
        sh(["brew", "install"] + missing, timeout=900)
    elif os.name == "nt":
        pkgs = {"podman": "RedHat.Podman", "kind": "Kubernetes.kind",
                "kubectl": "Kubernetes.cli"}
        for tool in missing:
            sh(["winget", "install", "--accept-source-agreements",
                "--accept-package-agreements", pkgs[tool]], timeout=900)
    else:  # linux
        pkgs = " ".join(missing)
        mgr = ("apt-get" if shutil.which("apt-get")
               else "dnf" if shutil.which("dnf") else None)
        if mgr is None:
            die("cannot install %s: no apt-get/dnf" % pkgs,
                "install podman, kind and kubectl manually, then re-run")
        # sudo may need a TTY on first use — surface the exact command.
        cmd = [mgr, "install", "-y"] + missing
        if mgr == "apt-get":
            cmd = ["apt-get", "install", "-y"] + missing
        proc = sh(["sudo", "-n"] + cmd, check=False)
        if proc.returncode != 0:
            print("one-time admin step needed:", " ".join(["sudo"] + cmd),
                  file=sys.stderr)
            sys.exit(2)
    for t in missing:
        if shutil.which(t) is None:
            die("%s installed but not on PATH" % t)


# --------------------------------------------------------------------------
# Stage B: container engine (podman preferred — no licence GUI anywhere)
# --------------------------------------------------------------------------
def ensure_engine():
    log("ensuring container engine…")
    if shutil.which("podman"):
        engine = "podman"
        probe = sh(["podman", "info"], check=False)
        if probe.returncode != 0:
            log("podman machine not usable — starting…")
            if sh(["podman", "machine", "list"], check=False).stdout.find(
                    "podman-machine-default") < 0:
                sh(["podman", "machine", "init", "--cpus", "4",
                    "--memory", "4096", "--disk-size", "25"], timeout=900)
            sh(["podman", "machine", "start"], timeout=600)
            sh(["podman", "info"])
        # macOS quirk: the SSH forwarder spawned by `podman machine start`
        # dies with the shell that started it, so the FIRST `podman info`
        # in a fresh tool shell reports a dead socket even while the VM
        # is 'Currently running'. A second `machine start` heals THIS
        # process' forwarder (the second call no-ops with 'already
        # running' but re-establishes the gvproxy channel); re-probe to
        # confirm before trusting the engine.
        probe = sh(["podman", "info"], check=False)
        if probe.returncode != 0:
            sh(["podman", "machine", "start"], timeout=600)
            sh(["podman", "machine", "start"], check=False, timeout=600)
            probe = sh(["podman", "info"], check=False)
            if probe.returncode != 0:
                die("podman engine unreachable even after machine start",
                    "run `podman machine list` and `podman machine start` by hand, then re-run")
    elif shutil.which("docker"):
        engine = "docker"
        sh(["docker", "info"])
    else:
        die("no container engine (podman or docker)")
    os.environ["KIND_EXPERIMENTAL_PROVIDER"] = (
        "podman" if engine == "podman" else "docker")
    return engine


# --------------------------------------------------------------------------
# Stage C: kind cluster (node running, fresh kubeconfig proxy per run)
# --------------------------------------------------------------------------
def ensure_cluster(engine):
    log("ensuring kind cluster %r…" % CLUSTER)
    ps = sh([engine, "ps", "-a", "--format", "{{.Names}} {{.Status}}"])
    if NODE not in ps.stdout:
        sh(["kind", "create", "cluster", "--name", CLUSTER, "--wait", "180s"],
           timeout=600)
    else:
        status_line = next(l for l in ps.stdout.splitlines()
                           if l.startswith(NODE))
        # A node left behind by a killed VM/podman can sit in a corrupt
        # `Exited (0) <bad timestamp>` state that a single `start` does not
        # clear; keep restarting until `podman ps` shows it genuinely Up.
        if " Up " not in status_line:
            for attempt in range(6):
                sh([engine, "start", NODE], check=False)
                ps2 = sh([engine, "ps", "-a", "--format",
                          "{{.Names}} {{.Status}}"])
                line = next((l for l in ps2.stdout.splitlines()
                             if l.startswith(NODE)), "")
                if " Up " in line:
                    break
                time.sleep(3)
            else:
                die("node container would not start (still not Up)",
                    line)
        # "container state improper" means podman has lost its own
        # bookkeeping for this container: it is neither running nor cleanly
        # stopped, so no amount of start/exec retrying clears it. The node
        # is disposable verification state, so force-remove it and let the
        # cluster be rebuilt below rather than wedging forever.
        probe = sh([engine, "exec", NODE, "true"], check=False)
        if probe.returncode != 0 and "state improper" in (probe.stdout or ""):
            log("node container wedged (%s) — recreating cluster…" %
                (probe.stdout or "").strip().splitlines()[-1])
            sh(["kind", "delete", "cluster", "--name", CLUSTER], check=False)
            sh([engine, "rm", "-f", NODE], check=False)
            sh(["kind", "create", "cluster", "--name", CLUSTER,
                "--wait", "180s"], timeout=600)
        # a just-started node needs a beat before its containerd socket
        # answers `ctr`/`crictl`; the image import below races it
        # otherwise.
        for _ in range(90):
            ready = sh([engine, "exec", NODE, "sh", "-c",
                        "test -S /run/containerd/containerd.sock"],
                       check=False)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            die("node started but containerd never became ready")
    # kind's local API proxy dies between shells — re-export every run.
    sh(["kind", "export", "kubeconfig", "--name", CLUSTER], timeout=120)
    # poll the API itself: `kubectl wait node/...` can pass while the
    # aggregated API is still settling, which surfaces later as a
    # transient `Forbidden` on the first real call.
    api_ok = False
    for _ in range(60):
        probe_api = sh(["kubectl", "get", "--raw=/readyz"], check=False)
        if probe_api.returncode == 0 and (probe_api.stdout or "").strip() == "ok":
            api_ok = True
            break
        time.sleep(2)
    if not api_ok:
        die("API server never became ready",
            "check `kubectl logs soc-t4-control-plane -n kube-system`")
    sh(["kubectl", "wait", "--for=condition=Ready", "node/" + NODE,
        "--timeout=180s"])


def ensure_image(engine):
    log("ensuring platform image…")
    exists = (["podman", "image", "exists", "soc-platform:latest"]
              if engine == "podman"
              else ["docker", "image", "inspect", "soc-platform:latest"])
    if sh(exists, check=False).returncode != 0:
        sh([engine, "build", "-t", "soc-platform:latest", "."], timeout=900)
    # NOTE: kubelet pulls from the CRI image store (io.containerd.cri.v1),
    # which ctr's `plugins ls` confirms is served as soon as the node is
    # Ready (verified: `io.containerd.cri.v1 images - ok`). No extra
    # readiness gate is needed here; the socket wait in ensure_cluster()
    # is sufficient.
    # ctr import writes into containerd's k8s.io namespace, which is the
    # same store kubelet's CRI reads from once the manifest sets
    # imagePullPolicy: Never (the image is side-loaded, not registry-
    # served, so kubelet must not try to pull it).
    log("importing image into the node…")
    proc = subprocess.Popen(
        [engine, "save", "soc-platform:latest"],
        cwd=REPO, stdout=subprocess.PIPE)
    imp = subprocess.run(
        [engine, "exec", "-i", NODE, "ctr", "-n", "k8s.io", "images",
         "import", "-"],
        stdin=proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    proc.stdout.close()
    proc.wait(timeout=300)
    if imp.returncode != 0:
        die("image import failed", (imp.stdout or "")[-800:])
    # Verify through the EXACT call kubelet makes: CRI ImageStatus by
    # ref (`crictl inspecti <ref>`). Two traps found live on soc-t4
    # (2026-10-23): (1) a `crictl images` LIST match proves nothing —
    # kubelet resolves by ref, not by listing; (2) `ctr import` stores
    # the image under the RAW archive ref (soc-platform:latest) while
    # CRI resolves refs in NORMALIZED form
    # (docker.io/library/soc-platform:latest), so the record is listed
    # yet every ref lookup misses → ErrImageNeverPull with pullPolicy:
    # Never. Alias the normalized ref onto the imported record first.
    def cri_sees_image():
        probe = sh([engine, "exec", NODE, "crictl", "inspecti",
                    "soc-platform:latest"], check=False)
        return probe.returncode == 0

    sh([engine, "exec", NODE, "ctr", "-n", "k8s.io", "images", "tag",
        "soc-platform:latest", "docker.io/library/soc-platform:latest"],
       check=False)
    if not cri_sees_image():
        # Bounce containerd inside the (throwaway) node so the CRI plugin
        # rebuilds its image index from the content store, then re-apply
        # the alias; kubelet reconnects on its own.
        log("normalized ref unresolved in the CRI store — restarting "
            "containerd inside the node…")
        sh([engine, "exec", NODE, "systemctl", "restart", "containerd"],
           check=False)
        for _ in range(60):
            sock = sh([engine, "exec", NODE, "test", "-S",
                       "/run/containerd/containerd.sock"], check=False)
            if sock.returncode == 0:
                break
            time.sleep(2)
        time.sleep(3)
        sh([engine, "exec", NODE, "ctr", "-n", "k8s.io", "images", "tag",
            "soc-platform:latest",
            "docker.io/library/soc-platform:latest"], check=False)
    if not cri_sees_image():
        ctr_ls = sh([engine, "exec", NODE, "ctr", "-n", "k8s.io",
                     "images", "ls"], check=False)
        inspect = sh([engine, "exec", NODE, "crictl", "inspecti",
                      "soc-platform:latest"], check=False)
        die("kubelet-path ImageStatus still cannot resolve "
            "soc-platform:latest (would be ErrImageNeverPull)",
            "ctr=%s | inspecti=%s" %
            ((ctr_ls.stdout or "")[-300:],
             (inspect.stdout or "")[-300:]))


# --------------------------------------------------------------------------
# Stage D: host-side HTTPS sink with its own CA (the "external webhook")
# --------------------------------------------------------------------------
def _make_ca_and_leaf(tmp, gw_ip):
    ca_key = os.path.join(tmp, "ca.key")
    ca_crt = os.path.join(tmp, "ca.pem")
    leaf_key = os.path.join(tmp, "leaf.key")
    leaf_crt = os.path.join(tmp, "leaf.pem")
    conf = os.path.join(tmp, "san.cnf")
    # Broad SAN set: whichever name/IP the cluster uses to reach this host
    # must verify (fluent-bit checks IP-literal hosts strictly — the
    # gateway IP MUST be an IP SAN, DNS SANs don't cover it).
    sans = ["IP:127.0.0.1", "DNS:localhost", "DNS:host.docker.internal",
            "DNS:host.containers.internal"]
    gw_ip = (gw_ip or "").strip()
    # A blank/non-IP gateway yields a bare 'DNS:' entry, which produces a
    # trailing empty SAN that LibreSSL/OpenSSL reject at extfile load time.
    # Only append a SAN that is a real, parseable address or hostname.
    if gw_ip:
        try:
            ipaddress.ip_address(gw_ip)
            sans.append("IP:" + gw_ip)
        except ValueError:
            if re.fullmatch(r"[A-Za-z0-9.-]+", gw_ip):
                sans.append("DNS:" + gw_ip)
    extra = ["subjectAltName = " + ",".join(sans),
             # Leaf must carry an AKID so the verifier can chain it back
             # to the CA (LibreSSL 3.3+ fails the handshake without it).
             "authorityKeyIdentifier = keyid,issuer"]
    sh(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-days", "2", "-subj", "/CN=soc-t4-verify-ca",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
        "-addext", "subjectKeyIdentifier=hash",
        "-out", ca_crt, "-keyout", ca_key])
    with open(conf, "w") as fh:
        fh.write("\n".join(extra) + "\n")
    sh(["openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-subj", "/CN=localhost", "-out", os.path.join(tmp, "leaf.csr"),
        "-keyout", leaf_key])
    sh(["openssl", "x509", "-req", "-in", os.path.join(tmp, "leaf.csr"),
        "-CA", ca_crt, "-CAkey", ca_key, "-CAcreateserial", "-days", "2",
        "-extfile", conf, "-out", leaf_crt])
    return ca_crt, leaf_crt, leaf_key


def _host_reachable_ip(engine):
    """Address the CLUSTER can dial back to on this host, or None.

    Priority (probed live on soc-t4, 2026-10-23):
      1. The host's own egress IP (UDP route-lookup trick: `connect()` on
         a datagram socket resolves the local address without sending a
         single packet, works offline). Pods reach it by normal
         outbound NAT: pod -> node -> VM -> gvproxy -> LAN -> host.
         Both the LAN IP and a tailscale IP were witnessed accepting
         connections from inside the cluster.
      2. The VM's default gateway — correct on docker-bridge setups,
         where the gateway IS the host.
      3. None -> caller falls back to host.docker.internal (name SANs).

    NOT reachable (witnessed): the gvproxy gateway 192.168.127.1 and its
    hairpin alias 192.168.127.254 from inside the kind node or pods —
    those black-hole, which is exactly the symptom that made fluent-bit
    time out connecting to tcp://192.168.127.1:18443.
    """
    # 1) host egress IP (engine-independent; no packets sent)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    # 2) VM default gateway (docker-bridge worlds; harmless fallback)
    if engine != "podman":
        return None
    try:
        out = sh(["podman", "machine", "ssh", "--", "ip", "route"],
                 check=False).stdout or ""
    except Exception:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("default") and " via " in line:
            candidate = line.split(" via ", 1)[1].split()[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue
    return None


def start_sink(engine, tmp):
    """HTTPS sink on this machine, recording body + headers. Returns the
    webhook host the cluster should target (discovered FIRST so the leaf
    cert carries it as a SAN)."""
    host_ip = _host_reachable_ip(engine)
    webhook_host = host_ip or "host.docker.internal"
    ca_crt, leaf_crt, leaf_key = _make_ca_and_leaf(tmp, host_ip)
    received = os.path.join(tmp, "received.json")
    headers_out = os.path.join(tmp, "received.headers")

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("content-length", 0))
            with open(received, "wb") as fh:
                fh.write(self.rfile.read(n))
            with open(headers_out, "w") as fh:
                fh.write(str(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(leaf_crt, leaf_key)
    srv = http.server.HTTPServer(("0.0.0.0", SINK_PORT), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # prove the sink answers locally before involving the cluster
    probe = ssl.create_default_context(cafile=ca_crt)
    with socket.create_connection(("127.0.0.1", SINK_PORT), 5) as raw:
        with probe.wrap_socket(raw, server_hostname="localhost") as tls:
            tls.sendall(b"POST /ingest HTTP/1.1\r\nHost: x\r\n"
                        b"Content-Length: 2\r\n\r\n{}")
            tls.recv(64)
    if not os.path.exists(received):
        die("local TLS probe failed against the sink")
    os.remove(received)
    if os.path.exists(headers_out):
        os.remove(headers_out)
    return srv, webhook_host, ca_crt, received, headers_out


# --------------------------------------------------------------------------
# Stage E: deploy + trust + event + assertions
# --------------------------------------------------------------------------
def kubectl(*args, **kw):
    return sh(["kubectl"] + list(args), **kw)


def _dump_deployment_state():
    """On a stuck rollout, surface which container is failing and why —
    a bare timeout is undiagnosable, and the pod forwarder dies between
    shells so this must be self-contained."""
    try:
        show = kubectl("get", "pods", "-l", "app=soc-api", "-o", "wide",
                       check=False)
        print((show.stdout or ""), file=sys.stderr, flush=True)
        # Structured events are the ground truth for a stuck
        # ContainerCreating (FailedMount / FailedAttachVolume / image
        # backoff all live here, not in the container state). Pull them
        # directly rather than parsing `describe`, which interleaves them.
        ev = kubectl("get", "events", "--sort-by=.lastTimestamp",
                     check=False)
        print("\n--- recent events ---", file=sys.stderr, flush=True)
        print((ev.stdout or "(none)"), file=sys.stderr, flush=True)
        desc = kubectl("describe", "pod", "-l", "app=soc-api", check=False)
        lines = (desc.stdout or "").splitlines()
        keep = [l for l in lines if any(k in l for k in (
            "Name:", "Container", "Image:", "State:", "Reason:",
            "Last State:", "Ready:", "Restart Count:", "Events:",
            "Warning", "BackOff", "Error", "failed", "Failed",
            "Mount", "Attach"))]
        print("\n".join(keep[-60:]), file=sys.stderr, flush=True)
        for c in ("api", "fluent-bit"):
            lg = kubectl("logs", "-l", "app=soc-api", "-c", c,
                         "--tail=15", check=False)
            print("\n--- %s logs ---" % c, file=sys.stderr, flush=True)
            print((lg.stdout or "(none)"), file=sys.stderr, flush=True)
    except Exception as exc:  # diagnostic must never mask the real failure
        print("(state dump failed: %s)" % exc, file=sys.stderr, flush=True)


def deploy(webhook_host, ca_crt, tmp, engine):
    log("creating dummy secrets (in-pod sqlite — no shared Neon contact)…")
    token = secrets.token_hex(16)
    # Delete first: `kubectl create` fails silently (check=False) when the
    # Secret already exists from a prior run — fluent-bit would then send
    # the PREVIOUS run's bearer token and the sink assertion, comparing
    # against the fresh token, fails although delivery is perfect.
    for name in ("soc-api-secrets", "soc-log-forwarder"):
        kubectl("delete", "secret", name, "--ignore-not-found=true",
                check=False)
    kubectl("create", "secret", "generic", "soc-api-secrets",
            "--from-literal=api-key=verify-dummy",
            "--from-literal=database-url=sqlite:////tmp/soc-verify.db")
    kubectl("create", "secret", "generic", "soc-log-forwarder",
            "--from-literal=bearer-token=" + token)
    # Bake the per-environment webhook values into a RENDERED copy of the
    # manifest instead of `kubectl set env` on the live Deployment. Every
    # `set env` rewrites the pod template, which mints a new ReplicaSet on
    # each run — two controller revisions then race, and the cold-image
    # pull outlasts the Deployment's progress deadline. Rendering keeps
    # the template stable so repeat runs are a no-op.
    # Clean slate: a Deployment left with a stale Progressing=False
    # (ProgressDeadlineExceeded) condition short-circuits `kubectl rollout
    # status` on the NEXT run — it reports failure instantly even though
    # fresh pods would recover. Recreate the Deployment so every run's
    # rollout starts from an unconditional state (and only one
    # ReplicaSet ever exists).
    log("applying the manifest…")
    kubectl("delete", "deployment", "soc-api", "--ignore-not-found=true",
            "--wait=true", "--timeout=120s", check=False)
    kubectl("delete", "pods", "-l", "app=soc-api", "--ignore-not-found=true",
            "--wait=true", "--timeout=60s", check=False)
    # Orphan events from prior runs outlive their pods (1h TTL) and would
    # make the ErrImageNeverPull self-heal fire on history, not current
    # state. Start each run's event stream clean.
    kubectl("delete", "events", "--all", check=False)
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        manifest = fh.read()
    manifest = (manifest
                .replace("value: logs.example.com",
                         "value: %s" % webhook_host)
                .replace("value: \"443\"",
                         "value: \"%d\"" % SINK_PORT))
    # Trust THIS run's private sink CA without touching the container:
    # the official fluent-bit image is distroless (no sh, no tar —
    # kubectl exec/cp both fail), so deliver the CA the k8s-native way:
    # ConfigMap mount + the output's OWN tls.ca_file option (verified
    # against fluent-bit 3.2.10 source: the shared TLS config map defines
    # `tls.ca_file`/`tls.ca_path` — NOT `tls.cafile`, which is rejected as
    # unknown; and the no-CA branch loads the system bundle explicitly
    # via SSL_CTX_load_verify_locations, so SSL_CERT_FILE env is ignored
    # — both traps hit live on soc-t4). A real external webhook with a
    # public CA needs neither: the checked-in manifest carries no
    # tls.ca_file and no custom CA, so system trust applies as-is there.
    anchor_tls = "        tls.verify    On\n"
    anchor_vol = ("        - name: config\n"
                  "          configMap:\n"
                  "            name: soc-fluent-bit-config\n")
    anchor_mnt = ("            - name: config\n"
                  "              mountPath: /fluent-bit/config\n"
                  "              readOnly: true\n")
    for anchor in (anchor_tls, anchor_vol, anchor_mnt):
        if anchor not in manifest:
            die("manifest render anchor missing (manifest drift?): %r" %
                anchor[:60])
    manifest = manifest.replace(
        anchor_tls,
        anchor_tls + "        tls.ca_file    /etc/sink-ca/ca.pem\n", 1)
    manifest = manifest.replace(
        anchor_vol,
        anchor_vol + ("        - name: sink-ca\n"
                      "          configMap:\n"
                      "            name: soc-sink-ca\n"), 1)
    manifest = manifest.replace(
        anchor_mnt,
        anchor_mnt + ("            - name: sink-ca\n"
                      "              mountPath: /etc/sink-ca\n"
                      "              readOnly: true\n"), 1)
    rendered = os.path.join(tmp, "soc-api-deployment.rendered.yaml")
    with open(rendered, "w", encoding="utf-8") as fh:
        fh.write(manifest)
    # The CA ConfigMap must exist before pods start mounting it.
    kubectl("delete", "configmap", "soc-sink-ca",
            "--ignore-not-found=true", check=False)
    kubectl("create", "configmap", "soc-sink-ca",
            "--from-file=ca.pem=" + ca_crt)
    kubectl("apply", "-f", rendered)
    # A cold node pulls two images; the 60s default progress deadline can
    # expire mid-pull and mark the rollout failed even though it recovers.
    kubectl("patch", "deployment", "soc-api", "--type=merge", "-p",
            '{"spec":{"progressDeadlineSeconds":600}}', check=False)
    log("waiting for rollout (first run pulls fluent-bit)…")
    # NOTE: subprocess timeout MUST exceed kubectl's own --timeout, or sh()
    # dies on TimeoutExpired before we can dump diagnostics below.
    # Wait must outlast progressDeadlineSeconds (600s, patched below) — a
    # cold node pulling two images legitimately takes several minutes, and
    # giving up early turns a slow success into a false failure.
    rollout = kubectl("rollout", "status", "deployment/soc-api",
                      "--timeout=600s", check=False, timeout=660)
    if rollout.returncode != 0:
        # Self-heal for a witnessed kubelet/CRI disagreement: fresh pods
        # reporting ErrImageNeverPull although `crictl images` holds the
        # image. A kubelet restart clears its image view; retry the
        # rollout once, and fail honestly if the warning was not the
        # cause.
        ev = kubectl("get", "events", check=False)
        if "ErrImageNeverPull" in (ev.stdout or ""):
            log("fresh pods hit ErrImageNeverPull although the CRI store "
                "holds the image — restarting kubelet in the node…")
            sh([engine, "exec", NODE, "systemctl", "restart", "kubelet"],
               check=False)
            sh(["kubectl", "wait", "--for=condition=Ready",
                "node/" + NODE, "--timeout=180s"], check=False)
            kubectl("delete", "pods", "-l", "app=soc-api",
                    "--ignore-not-found=true", "--wait=true",
                    "--timeout=120s", check=False)
            rollout = kubectl("rollout", "status", "deployment/soc-api",
                              "--timeout=600s", check=False, timeout=660)
    if rollout.returncode != 0:
        _dump_deployment_state()
        die("rollout did not become ready",
            (rollout.stdout or "")[-400:])
    # NOTE: no in-container CA install/restart is needed — the sink CA is
    # delivered via ConfigMap + tls.ca_file at pod start (the official
    # fluent-bit image is distroless: no sh for exec, no tar for cp).
    # Pre-flight: prove the POD can reach the host sink BEFORE waiting on
    # fluent-bit delivery — a routing failure would otherwise burn the
    # 90s delivery timeout and surface as a misleading "never delivered".
    # `-k` skips only CA verification (this probe runs before fluent-bit's
    # strict, tls.ca_file-verified delivery — which is the real test).
    # Container-side `|| true` + stderr suppression are load-bearing: the
    # sink answers HTTP/1.0 and closes without TLS close_notify, so curl
    # prints the complete 200 and THEN exits 56 — a nonzero container exit
    # would make kubectl append "command terminated…" over the http code.
    log("pre-flighting pod → host sink reachability…")
    probe = kubectl("exec", "deploy/soc-api", "-c", "api", "--",
                    "sh", "-c",
                    ("curl -sk -m 8 -o /dev/null -w '%%{http_code}' "
                     "-X POST https://%s:%d/ingest 2>/dev/null || true"
                     % (webhook_host, SINK_PORT)),
                    check=False, timeout=60)
    m_code = re.search(r"\d{3}", probe.stdout or "")
    probe_code = m_code.group(0) if m_code else ""
    if probe_code != "200":
        die("pod cannot reach the host sink at %s:%d (pre-flight %r)" %
            (webhook_host, SINK_PORT, probe_code),
            "host-IP discovery picked an unroutable address, or the OS "
            "firewall is dropping the sink port")
    log("pod → sink pre-flight: %s (TLS handshake + POST OK)" % probe_code)
    return token


def generate_event_and_assert(token, received, headers_out):
    log("generating a real security event (ungated mutation -> 401)…")
    # Belt under the api readinessProbe: exec the request on a bounded
    # retry — a single lost race (curl exit 7, http_code 000) must not
    # fail a healthy deployment.
    code = ""
    last_err = ""
    for attempt in range(10):
        out = kubectl("exec", "deploy/soc-api", "-c", "api", "--",
                      "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                      "-X", "POST", "http://127.0.0.1:8000/api/execute",
                      "-H", "Content-Type: application/json",
                      "-d", '{"tool_name":"verify"}',
                      check=False, timeout=60)
        code = (out.stdout or "").strip()[-3:]
        if code == "401":
            break
        last_err = (out.stdout or "").strip()
        time.sleep(3)
    if code != "401":
        die("expected gate 401, got %r" % code, last_err[-300:])
    log("event status from the app: %s (expected 401)" % code)

    log("checking the app wrote the JSON audit file (sidecar tail source)…")
    pod_log = kubectl("exec", "deploy/soc-api", "-c", "api", "--",
                      "cat", "/var/log/soc/audit.json").stdout
    if '"auth_rejected"' not in pod_log or "/api/execute" not in pod_log:
        die("audit file in the pod lacks the event")

    log("waiting for the sidecar to deliver over TLS to the host sink…")
    deadline = time.time() + 90
    body = ""
    while time.time() < deadline:
        if os.path.exists(received):
            with open(received, "r", encoding="utf-8") as fh:
                body = fh.read()
            if "auth_rejected" in body:
                break
        time.sleep(2)
    if "auth_rejected" not in body:
        die("sink never received the audit event",
            "check: kubectl logs deploy/soc-api -c fluent-bit")
    headers = ""
    if os.path.exists(headers_out):
        with open(headers_out, "r", encoding="utf-8") as fh:
            headers = fh.read()
    if not re.search(r"Authorization:\s*Bearer %s" % re.escape(token),
                     headers, re.I):
        die("bearer header missing/incorrect at the sink", headers[:400])
    if "/api/execute" not in body:
        die("delivered payload lacks the event path")

    print("""
VERIFICATION MATRIX (live kind cluster %s, %s)
  [ok] cluster apply + rollout          Deployment accepted, pods Ready
  [ok] app wrote JSON audit file        sidecar tail source exists
  [ok] gate produced security event     401 auth_rejected, no DB needed
  [ok] Fluent Bit delivered to sink     over TLS (own CA, verify On)
  [ok] Authorization: Bearer present    exact token matched at the sink
  [ok] payload integrity                event path present end-to-end
""" % (CLUSTER, datetime.datetime.now().date()), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--teardown", action="store_true",
                    help="remove the kind cluster after a PASS")
    args = ap.parse_args()
    if not os.path.exists(MANIFEST):
        die("manifest missing: %s" % MANIFEST)

    ensure_tools()
    engine = ensure_engine()
    ensure_cluster(engine)
    ensure_image(engine)

    tmp = tempfile.mkdtemp(prefix="t4-sink-")
    sink, webhook_host, ca_crt, received, headers_out = start_sink(
        engine, tmp)
    log("host sink up on :%d (webhook host for cluster: %s)"
        % (SINK_PORT, webhook_host))
    try:
        token = deploy(webhook_host, ca_crt, tmp, engine)
        generate_event_and_assert(token, received, headers_out)
    finally:
        sink.shutdown()

    if args.teardown:
        log("teardown requested: removing cluster…")
        sh(["kind", "delete", "cluster", "--name", CLUSTER], check=False)
    else:
        print("cluster kept (stop it with: kind delete cluster --name %s)"
              % CLUSTER, flush=True)
    print("sink temp dir: %s" % tmp, flush=True)


if __name__ == "__main__":
    main()
