# Windows Launchers

## 本机启动

双击：

```text
start_local.bat
```

启动器按下面顺序找 Python：

1. `backend\.venv\Scripts\python.exe`
2. `backend\venv\Scripts\python.exe`
3. 系统 `python`

找到虚拟环境后会用它运行根目录 `start.py`。`start.py` 再启动本机 `uvicorn app.main:app`，监听 `127.0.0.1:8011`，并打开项目专用 Chrome/Chromium。

## 本机停止

双击：

```text
stop.bat
```

停止逻辑刻意保守：

- 先停止窗口标题为 `MY Market Radar` 的本机 launcher 进程树；
- 如果 8011 仍有监听，只在进程命令行能识别出 `uvicorn app.main:app`、`start.py` 或 `my-market-radar` 时才停止；
- 如果 8011 的进程无法确认属于本项目，只显示警告，不按端口强杀；
- 9231 是 MY Market Radar 保留的项目 Chrome CDP 端口，因此会停止该监听进程；
- 3000 经常被其他前端项目使用，只报告占用 PID，**绝不自动停止**。

以前的 `stop.bat` 会直接杀任何监听 8011/3000 的进程，可能误伤其他开发项目，甚至在 Docker 场景下误杀 Docker Desktop 相关进程。当前版本不再这么做。

## Docker 启动/停止

Docker 使用单独脚本：

```text
start_docker.bat
stop_docker.bat
```

`stop_docker.bat` 通过：

```bash
docker compose -f docker-compose.simple.yml down
```

停止 MY Market Radar compose 服务，不通过宿主机端口 PID 去杀 Docker 进程。

因此：

- 本机 Python 启动 → `stop.bat`
- Docker 启动 → `stop_docker.bat`

不要互换。
