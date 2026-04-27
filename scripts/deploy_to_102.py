from __future__ import annotations

import argparse
import os
import posixpath
from pathlib import Path

import paramiko

HOST = "10.0.252.102"
USER = "root"
PASSWORD = os.getenv("BVP_SSH_PASSWORD", "")

REMOTE_ROOT = "/root/bitable-video-preview-plugin"
REMOTE_DIST = f"{REMOTE_ROOT}/dist"
REMOTE_LOG = f"{REMOTE_ROOT}/server.log"
DEFAULT_PORT = 18174


def run_cmd(ssh: paramiko.SSHClient, command: str) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=30)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return code, out.strip(), err.strip()


def ensure_remote_dirs(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current += f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def upload_dir(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str) -> int:
    file_count = 0
    for local_path in local_dir.rglob("*"):
        rel = local_path.relative_to(local_dir).as_posix()
        remote_path = posixpath.join(remote_dir, rel)
        if local_path.is_dir():
            ensure_remote_dirs(sftp, remote_path)
            continue
        ensure_remote_dirs(sftp, posixpath.dirname(remote_path))
        sftp.put(str(local_path), remote_path)
        file_count += 1
    return file_count


def connect() -> paramiko.SSHClient:
    if not PASSWORD:
        raise RuntimeError("Missing env BVP_SSH_PASSWORD")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    return ssh


def probe(ssh: paramiko.SSHClient, port: int) -> None:
    print(f"[probe] host={HOST}")
    for cmd in [
        "hostname",
        "python3 --version",
        "ss -ltnp | head -n 20",
        f"ss -ltnp | grep ':{port} ' || true",
        "ps -ef | grep -E 'crawler_order_query_service|http.server' | grep -v grep || true",
        f"test -d {REMOTE_ROOT} && echo 'remote_dir_exists=1' || echo 'remote_dir_exists=0'",
    ]:
        code, out, err = run_cmd(ssh, cmd)
        print(f"\n$ {cmd}\nexit={code}")
        if out:
            print(out)
        if err:
            print(err)


def deploy(ssh: paramiko.SSHClient, local_dist: Path, port: int) -> None:
    if not local_dist.exists():
        raise FileNotFoundError(f"local dist not found: {local_dist}")

    sftp = ssh.open_sftp()
    try:
        ensure_remote_dirs(sftp, REMOTE_DIST)
        run_cmd(ssh, f"mkdir -p {REMOTE_DIST}")
        run_cmd(ssh, f"rm -rf {REMOTE_DIST}/*")
        uploaded = upload_dir(sftp, local_dist, REMOTE_DIST)
        print(f"[deploy] uploaded files: {uploaded}")
    finally:
        sftp.close()

    run_cmd(
        ssh,
        f"pkill -f \"python3 -m http.server {port}\" || true",
    )
    start_cmd = (
        f"cd {REMOTE_DIST} && "
        f"nohup python3 -m http.server {port} --bind 0.0.0.0 > {REMOTE_LOG} 2>&1 &"
    )
    code, out, err = run_cmd(ssh, start_cmd)
    print(f"[deploy] start_server exit={code}")
    if out:
        print(out)
    if err:
        print(err)

    run_cmd(ssh, "sleep 1")
    code, out, err = run_cmd(ssh, f"ss -ltnp | grep ':{port} ' || true")
    print(f"[verify] port={port}\n{out or err or '(empty)'}")
    print(f"[verify] url=http://{HOST}:{port}/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dist", default=str(Path(__file__).resolve().parents[1] / "dist"))
    args = parser.parse_args()

    if not args.probe and not args.deploy:
        parser.error("use --probe or --deploy")

    ssh = connect()
    try:
        if args.probe:
            probe(ssh, args.port)
        if args.deploy:
            deploy(ssh, Path(args.dist), args.port)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
