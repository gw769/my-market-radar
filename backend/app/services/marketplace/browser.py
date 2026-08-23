from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote as urlquote, urlparse

from app.core.config import get_settings
from app.services.marketplace.extension_bridge import (
    ExtensionBridgeError,
    extension_ready,
    extension_request,
)

settings = get_settings()


class BrowserLaunchError(RuntimeError):
    pass


def cdp_url() -> str:
    return f"http://127.0.0.1:{settings.BROWSER_CDP_PORT}"


def browser_ready(timeout: float = 0.5) -> bool:
    if settings.BROWSER_MODE == "extension":
        return extension_ready()
    try:
        with urllib.request.urlopen(f"{cdp_url()}/json/version", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _existing_path(value: str | os.PathLike[str] | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path) if path.is_file() else None


def find_chrome_executable() -> str | None:
    configured = _existing_path(settings.BROWSER_EXECUTABLE)
    if configured:
        return configured

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found

    candidates: list[Path] = []
    if sys.platform == "win32":
        roots = [
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        ]
        for root in roots:
            if root:
                candidates.extend(
                    [
                        Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                        Path(root) / "Chromium" / "Application" / "chrome.exe",
                    ]
                )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("/opt/google/chrome/chrome"),
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
            ]
        )

    return next((str(path) for path in candidates if path.is_file()), None)


def _headless_required() -> bool:
    if sys.platform in ("win32", "darwin"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ensure_browser(initial_urls: list[str] | None = None) -> None:
    if browser_ready():
        return

    if settings.BROWSER_MODE == "extension":
        raise BrowserLaunchError(
            "主 Google Chrome 扩展尚未连接。请保持你的 Chrome 打开，并确认 MY Market Radar Browser Bridge 已加载。"
        )

    executable = find_chrome_executable()
    if not executable:
        raise BrowserLaunchError(
            "未找到 Google Chrome/Chromium。请安装 Chrome，或通过 BROWSER_EXECUTABLE 指定浏览器路径。"
        )

    headless = _headless_required()
    if headless and not settings.BROWSER_HEADLESS_FALLBACK:
        raise BrowserLaunchError(
            "当前环境没有桌面显示服务，无法启动可见 Chrome。请在桌面环境运行，或启用 BROWSER_HEADLESS_FALLBACK。"
        )

    args = [
        executable,
        f"--user-data-dir={settings.browser_profile_path}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={settings.BROWSER_CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
    ]
    if headless:
        args.extend(["--headless=new", "--disable-gpu"])
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")
    args.extend(initial_urls or ["about:blank"])

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise BrowserLaunchError(f"Chrome 启动失败：{exc}") from exc

    deadline = time.monotonic() + settings.BROWSER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if browser_ready():
            return
        return_code = process.poll()
        if return_code is not None:
            raise BrowserLaunchError(f"Chrome 启动失败：退出码 {return_code}")
        time.sleep(0.2)
    raise BrowserLaunchError(
        f"Chrome 已启动，但 {settings.BROWSER_CDP_PORT} 调试端口未就绪。请关闭旧的项目浏览器窗口后重试。"
    )


def list_tabs() -> list[dict[str, Any]]:
    if not browser_ready():
        return []
    if settings.BROWSER_MODE == "extension":
        try:
            payload = extension_request("tabs", timeout=5)
            return payload if isinstance(payload, list) else []
        except ExtensionBridgeError:
            return []
    try:
        with urllib.request.urlopen(f"{cdp_url()}/json/list", timeout=2) as response:
            payload = json.load(response)
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _platform_markers(platform: str) -> tuple[str, ...]:
    if platform == "shopee":
        return ("shopee.com.my", "xiapibuy.com")
    if platform == "lazada":
        return ("lazada.com.my",)
    return ()


def _host_matches(host: str, marker: str) -> bool:
    return host == marker or host.endswith(f".{marker}")


def _parsed_tab_url(tab: dict[str, Any]):
    try:
        return urlparse(str(tab.get("url") or ""))
    except ValueError:
        return urlparse("")


def _platform_tabs(platform: str) -> list[dict[str, Any]]:
    markers = _platform_markers(platform)
    if not markers:
        return []
    candidates: list[dict[str, Any]] = []
    for tab in list_tabs():
        if tab.get("type") != "page":
            continue
        parsed = _parsed_tab_url(tab)
        host = (parsed.hostname or "").lower()
        if any(_host_matches(host, marker) for marker in markers):
            candidates.append(tab)
    return candidates


def _is_verification_tab(platform: str, tab: dict[str, Any]) -> bool:
    parsed = _parsed_tab_url(tab)
    host = (parsed.hostname or "").lower()
    path_query = f"{parsed.path} {parsed.query}".lower()
    if platform == "shopee" and _host_matches(host, "xiapibuy.com"):
        return True
    if platform == "lazada" and host.startswith("acs-m."):
        return True
    return any(
        signal in path_query
        for signal in (
            "/verify",
            "/verification",
            "/captcha",
            "/punish",
            "/security-check",
            "/security_check",
            "/challenge",
        )
    )


def _is_search_tab(platform: str, tab: dict[str, Any]) -> bool:
    parsed = _parsed_tab_url(tab)
    path = parsed.path.lower()
    if platform == "shopee":
        return path.startswith("/search")
    if platform == "lazada":
        return path.startswith("/catalog")
    return False


def _tab_priority(platform: str, tab: dict[str, Any], prefer_verification: bool) -> int:
    verification = _is_verification_tab(platform, tab)
    search = _is_search_tab(platform, tab)
    if prefer_verification:
        if verification:
            return 300
        if search:
            return 200
        return 100
    if search:
        return 300
    if verification:
        return 200
    return 100


def find_platform_tab(platform: str, prefer_verification: bool = True) -> dict[str, Any] | None:
    candidates = _platform_tabs(platform)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda tab: (
            _tab_priority(platform, tab, prefer_verification),
            str(tab.get("id") or ""),
        ),
    )


def new_tab(url: str) -> dict[str, Any] | None:
    ensure_browser()
    if settings.BROWSER_MODE == "extension":
        try:
            payload = extension_request("new_tab", timeout=10, url=url)
            return payload if isinstance(payload, dict) else None
        except ExtensionBridgeError as exc:
            raise BrowserLaunchError(f"无法在你的 Chrome 中打开标签页：{exc}") from exc
    request = urllib.request.Request(
        f"{cdp_url()}/json/new?{urlquote(url, safe='')}",
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.load(response)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        raise BrowserLaunchError(f"无法在项目 Chrome 中打开标签页：{exc}") from exc


def ensure_platform_tab(platform: str, url: str) -> dict[str, Any]:
    ensure_browser([url])
    tab = find_platform_tab(platform, prefer_verification=False)
    if tab:
        return tab
    tab = new_tab(url)
    if not tab:
        raise BrowserLaunchError(f"无法创建 {platform.title()} 标签页")
    return tab


def activate_tab(tab_id: str) -> bool:
    if not tab_id:
        return False
    if settings.BROWSER_MODE == "extension":
        try:
            return bool(extension_request("activate_tab", timeout=5, tab_id=tab_id))
        except ExtensionBridgeError:
            return False
    try:
        with urllib.request.urlopen(f"{cdp_url()}/json/activate/{tab_id}", timeout=2):
            return True
    except Exception:
        return False


def activate_platform_tab(platform: str, prefer_verification: bool = True) -> bool:
    tab = find_platform_tab(platform, prefer_verification=prefer_verification)
    return bool(tab and activate_tab(str(tab.get("id") or "")))


def open_url(url: str) -> None:
    """Open an application URL in the same dedicated Chrome used by collection."""
    ensure_browser([url])
    for tab in list_tabs():
        if str(tab.get("url", "")).rstrip("/") == url.rstrip("/"):
            activate_tab(str(tab.get("id") or ""))
            return
    tab = new_tab(url)
    if tab:
        activate_tab(str(tab.get("id") or ""))
