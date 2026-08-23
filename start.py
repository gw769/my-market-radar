#!/usr/bin/env python3
"""Build-independent launcher for MY Market Radar."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PORT = 8011


def python_executable() -> str:
    candidates = [
        ROOT / "backend" / ".venv" / "bin" / "python",
        ROOT / "backend" / ".venv" / "Scripts" / "python.exe",
        ROOT / "backend" / "venv" / "bin" / "python",
        ROOT / "backend" / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def port_ready() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def open_when_ready() -> None:
    for _ in range(60):
        if port_ready():
            webbrowser.open(f"http://localhost:{PORT}")
            return
        time.sleep(0.5)


def main() -> int:
    if port_ready():
        print(f"端口 {PORT} 已被占用，请先停止已有服务。")
        return 1
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        print("前端尚未构建：请先运行 cd frontend && npm install && npm run build")
        return 1

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    command = [
        python_executable(), "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0", "--port", str(PORT),
    ]
    print("MY Market Radar 正在启动…")
    print(f"地址：http://localhost:{PORT}")
    print("账号：admin@market.my / admin123")
    threading.Thread(target=open_when_ready, daemon=True).start()
    process = subprocess.Popen(command, cwd=ROOT / "backend", env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
