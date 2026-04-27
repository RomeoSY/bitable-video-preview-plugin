from __future__ import annotations

import os
import posixpath
from pathlib import Path

import paramiko

HOST = "10.0.252.102"
USER = "root"
PASSWORD = os.getenv("BVP_SSH_PASSWORD", "")
LOCAL_DIST = Path(__file__).resolve().parents[1] / "dist"
REMOTE_ROOT = "/usr/share/nginx/html/bitable-video-preview"


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def main() -> int:
    if not PASSWORD:
        raise RuntimeError("Missing env BVP_SSH_PASSWORD")
    if not LOCAL_DIST.exists():
        raise FileNotFoundError(f"dist not found: {LOCAL_DIST}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    try:
        ssh.exec_command(f"mkdir -p {REMOTE_ROOT} && rm -rf {REMOTE_ROOT}/*", timeout=60)
        sftp = ssh.open_sftp()
        try:
            ensure_remote_dir(sftp, REMOTE_ROOT)
            uploaded = 0
            for local_path in LOCAL_DIST.rglob("*"):
                rel = local_path.relative_to(LOCAL_DIST).as_posix()
                remote_path = posixpath.join(REMOTE_ROOT, rel)
                if local_path.is_dir():
                    ensure_remote_dir(sftp, remote_path)
                    continue
                ensure_remote_dir(sftp, posixpath.dirname(remote_path))
                sftp.put(str(local_path), remote_path)
                uploaded += 1
            print(f"uploaded={uploaded}")
        finally:
            sftp.close()

        ssh.exec_command(f"chown -R nginx:nginx {REMOTE_ROOT} && chmod -R a+rX {REMOTE_ROOT}", timeout=60)
        ssh.exec_command("nginx -t && systemctl reload nginx", timeout=60)
        print("reloaded=1")
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
