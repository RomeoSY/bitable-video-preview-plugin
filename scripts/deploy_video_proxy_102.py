from __future__ import annotations

import os
from pathlib import Path

import paramiko

HOST = "10.0.252.102"
USER = "root"
PASSWORD = os.getenv("BVP_SSH_PASSWORD", "")

LOCAL_PROXY_SCRIPT = Path(__file__).parent / "video_proxy_server.py"
REMOTE_PROXY_SCRIPT = "/root/bitable-video-preview-plugin/video_proxy_server.py"
REMOTE_SERVICE_FILE = "/etc/systemd/system/bitable-video-proxy.service"
REMOTE_NGINX_CONF = "/etc/nginx/conf.d/bitable-video-preview-18174.conf"

LOCATION_BLOCK = """
    location /video-proxy {
        proxy_pass http://127.0.0.1:18175/video-proxy;
        proxy_http_version 1.1;
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
    }

    location /health-proxy {
        proxy_pass http://127.0.0.1:18175/health;
        proxy_http_version 1.1;
    }
""".strip("\n")


def run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return code, out, err


def main() -> int:
    if not PASSWORD:
        raise RuntimeError("Missing env BVP_SSH_PASSWORD")
    if not LOCAL_PROXY_SCRIPT.exists():
        raise FileNotFoundError(LOCAL_PROXY_SCRIPT)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    try:
        run_cmd(ssh, "mkdir -p /root/bitable-video-preview-plugin")
        sftp = ssh.open_sftp()
        try:
            sftp.put(str(LOCAL_PROXY_SCRIPT), REMOTE_PROXY_SCRIPT)
        finally:
            sftp.close()

        service_content = f"""
[Unit]
Description=Bitable Video Proxy Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/bitable-video-preview-plugin
ExecStart=/root/anaconda3/bin/python3 {REMOTE_PROXY_SCRIPT} --host 127.0.0.1 --port 18175
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
""".strip()

        run_cmd(ssh, f"cat > {REMOTE_SERVICE_FILE} <<'EOF'\n{service_content}\nEOF")
        run_cmd(ssh, "systemctl daemon-reload")
        run_cmd(ssh, "systemctl enable --now bitable-video-proxy")
        run_cmd(ssh, "systemctl restart bitable-video-proxy")

        # Inject nginx location block if missing.
        update_script = (
            "from pathlib import Path\n"
            f"p=Path({REMOTE_NGINX_CONF!r})\n"
            "text=p.read_text(encoding='utf-8')\n"
            f"block={LOCATION_BLOCK!r}\n"
            "if 'location /video-proxy' not in text:\n"
            "    idx=text.rfind('}')\n"
            "    text=text[:idx].rstrip()+'\\n\\n'+block+'\\n'+text[idx:]\n"
            "    p.write_text(text,encoding='utf-8')\n"
            "print('updated')\n"
        )
        code, conf_text, err = run_cmd(ssh, f"python3 - <<'PY'\n{update_script}PY")
        if code != 0:
            raise RuntimeError(err or "update nginx conf failed")

        code, out, err = run_cmd(ssh, "nginx -t")
        if code != 0:
            raise RuntimeError(out + "\n" + err)
        run_cmd(ssh, "systemctl reload nginx")

        checks = [
            "ss -ltnp | grep ':18175 ' || true",
            "curl -sS http://127.0.0.1:18175/health",
            "curl -k -sS -o /dev/null -w '%{http_code}' https://127.0.0.1:18174/health-proxy",
        ]
        for cmd in checks:
            code, out, err = run_cmd(ssh, cmd)
            print(f"$ {cmd}\n{out or err or '(empty)'}")
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
