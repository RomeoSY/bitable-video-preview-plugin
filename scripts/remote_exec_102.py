from __future__ import annotations

import argparse
import os

import paramiko


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    args = parser.parse_args()

    password = os.getenv("BVP_SSH_PASSWORD", "")
    if not password:
        raise RuntimeError("Missing env BVP_SSH_PASSWORD")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect("10.0.252.102", username="root", password=password, timeout=15)
    try:
        _, stdout, stderr = ssh.exec_command(args.command, timeout=600)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        print(f"exit={code}")
        if out:
            print(out)
        if err:
            print(err)
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
