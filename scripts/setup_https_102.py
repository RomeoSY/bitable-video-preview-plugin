from __future__ import annotations

import argparse
import os
from pathlib import Path

import paramiko

HOST = "10.0.252.102"
USER = "root"
PASSWORD = os.getenv("BVP_SSH_PASSWORD", "")

REMOTE_CERT_DIR = "/etc/nginx/ssl/bitable-video-preview"
REMOTE_CERT_PATH = f"{REMOTE_CERT_DIR}/befriends.com.cn.pem"
REMOTE_KEY_PATH = f"{REMOTE_CERT_DIR}/befriends.com.cn.key"
REMOTE_CONF_PATH = "/etc/nginx/conf.d/bitable-video-preview.conf"
REMOTE_CONF_18174_PATH = "/etc/nginx/conf.d/bitable-video-preview-18174.conf"
UPSTREAM = "http://127.0.0.1:18174/"
STATIC_ROOT = "/root/bitable-video-preview-plugin/dist"


def run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return code, out, err


def connect() -> paramiko.SSHClient:
    if not PASSWORD:
        raise RuntimeError("Missing env BVP_SSH_PASSWORD")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    return ssh


def ensure_nginx(ssh: paramiko.SSHClient) -> None:
    code, out, _ = run_cmd(ssh, "command -v nginx || true")
    print(f"[ensure_nginx] nginx_path={out}", flush=True)
    if out and "/" in out:
        return

    code, out, _ = run_cmd(ssh, "command -v yum || true")
    print(f"[ensure_nginx] yum_path={out}", flush=True)
    if out and "/" in out:
        code, _, err = run_cmd(ssh, "yum install -y nginx", timeout=300)
        print(f"[ensure_nginx] yum_install_exit={code}", flush=True)
        if code != 0:
            raise RuntimeError(f"yum install nginx failed: {err}")
        return

    code, out, _ = run_cmd(ssh, "command -v apt-get || true")
    print(f"[ensure_nginx] apt_path={out}", flush=True)
    if out and "/" in out:
        code, _, err = run_cmd(ssh, "apt-get update && apt-get install -y nginx", timeout=300)
        print(f"[ensure_nginx] apt_install_exit={code}", flush=True)
        if code != 0:
            raise RuntimeError(f"apt-get install nginx failed: {err}")
        return

    raise RuntimeError("neither yum nor apt-get found; cannot install nginx")


def upload_file(sftp: paramiko.SFTPClient, local_path: Path, remote_path: str) -> None:
    sftp.put(str(local_path), remote_path)


def apply(ssh: paramiko.SSHClient, cert_file: Path, key_file: Path) -> None:
    print("[apply] start", flush=True)
    ensure_nginx(ssh)
    print("[apply] nginx ready", flush=True)
    code, _, err = run_cmd(ssh, f"mkdir -p {REMOTE_CERT_DIR}")
    if code != 0:
        raise RuntimeError(f"create cert dir failed: {err}")

    sftp = ssh.open_sftp()
    try:
        upload_file(sftp, cert_file, REMOTE_CERT_PATH)
        upload_file(sftp, key_file, REMOTE_KEY_PATH)
    finally:
        sftp.close()

    run_cmd(ssh, f"chmod 644 {REMOTE_CERT_PATH}")
    run_cmd(ssh, f"chmod 600 {REMOTE_KEY_PATH}")
    print("[apply] cert uploaded", flush=True)

    conf = f"""
server {{
    listen 443 ssl;
    server_name tborders.befriends.com.cn;

    ssl_certificate {REMOTE_CERT_PATH};
    ssl_certificate_key {REMOTE_KEY_PATH};
    ssl_session_timeout 5m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location = /bitable-video-preview {{
        return 301 /bitable-video-preview/;
    }}

    location /bitable-video-preview/ {{
        proxy_pass {UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }}
}}
""".strip()

    cmd = f"cat > {REMOTE_CONF_PATH} <<'EOF'\n{conf}\nEOF"
    code, _, err = run_cmd(ssh, cmd)
    if code != 0:
        raise RuntimeError(f"write nginx conf failed: {err}")

    code, out, err = run_cmd(ssh, "nginx -t")
    print(f"[apply] nginx_test_exit={code}", flush=True)
    if code != 0:
        raise RuntimeError(f"nginx -t failed: {out}\n{err}")

    # Use reload if running, otherwise start.
    code, _, _ = run_cmd(ssh, "systemctl is-active nginx || true")
    run_cmd(ssh, "systemctl enable nginx")
    run_cmd(ssh, "systemctl restart nginx")
    print("[apply] nginx restarted", flush=True)

    code, out, err = run_cmd(
        ssh,
        "curl -k -I --resolve tborders.befriends.com.cn:443:127.0.0.1 https://tborders.befriends.com.cn/bitable-video-preview/",
    )
    print("[verify-local-https]")
    print(out or err or f"exit={code}")


def apply_direct_18174_https(ssh: paramiko.SSHClient) -> None:
    ensure_nginx(ssh)

    # Stop legacy plain HTTP server on 18174 to avoid port conflicts.
    run_cmd(ssh, "pkill -f 'python3 -m http.server 18174' || true")

    conf = f"""
server {{
    listen 18174 ssl;
    server_name tborders.befriends.com.cn;

    ssl_certificate {REMOTE_CERT_PATH};
    ssl_certificate_key {REMOTE_KEY_PATH};
    ssl_session_timeout 5m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    root {STATIC_ROOT};
    index index.html;

    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
""".strip()

    cmd = f"cat > {REMOTE_CONF_18174_PATH} <<'EOF'\n{conf}\nEOF"
    code, _, err = run_cmd(ssh, cmd)
    if code != 0:
        raise RuntimeError(f"write 18174 nginx conf failed: {err}")

    code, out, err = run_cmd(ssh, "nginx -t")
    if code != 0:
        raise RuntimeError(f"nginx -t failed: {out}\n{err}")

    run_cmd(ssh, "systemctl enable nginx")
    run_cmd(ssh, "systemctl restart nginx")

    code, out, err = run_cmd(
        ssh,
        "curl -k -I --resolve tborders.befriends.com.cn:18174:127.0.0.1 https://tborders.befriends.com.cn:18174/",
    )
    print("[verify-local-18174-https]")
    print(out or err or f"exit={code}")


def probe(ssh: paramiko.SSHClient) -> None:
    cmds = [
        "hostname",
        "which nginx || true",
        "nginx -v 2>&1 || true",
        "ss -ltnp | grep ':443 ' || true",
        "ss -ltnp | grep ':18174 ' || true",
        f"test -f {REMOTE_CONF_PATH} && echo conf_exists=1 || echo conf_exists=0",
    ]
    for c in cmds:
        code, out, err = run_cmd(ssh, c)
        print(f"\n$ {c}\nexit={code}")
        print(out or err or "(empty)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-18174", action="store_true")
    parser.add_argument("--cert", default=str(Path(__file__).parent / "cert_tmp" / "befriends.com.cn.pem"))
    parser.add_argument("--key", default=str(Path(__file__).parent / "cert_tmp" / "befriends.com.cn.key"))
    args = parser.parse_args()

    if not args.probe and not args.apply and not args.apply_18174:
        parser.error("use --probe or --apply or --apply-18174")

    ssh = connect()
    try:
        if args.probe:
            probe(ssh)
        if args.apply:
            cert_file = Path(args.cert)
            key_file = Path(args.key)
            if not cert_file.exists() or not key_file.exists():
                raise FileNotFoundError("cert/key file not found locally")
            apply(ssh, cert_file, key_file)
        if args.apply_18174:
            apply_direct_18174_https(ssh)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
