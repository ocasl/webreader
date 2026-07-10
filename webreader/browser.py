"""
Browser discovery and connection management.

Auto-detects Edge/Chrome with remote debugging, manages device list.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console

console = Console()

# Default CDP ports to try
DEFAULT_CDP_PORTS = [9222, 9229, 9333, 9220]


@dataclass 
class BrowserDevice:
    """A discovered browser device."""
    id: str
    name: str
    browser_type: str  # "edge", "chrome", "chromium"
    cdp_url: str
    pid: Optional[int] = None
    user_data: str = ""
    status: str = "active"  # active, disconnected
    version: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "browser_type": self.browser_type,
            "cdp_url": self.cdp_url,
            "pid": self.pid,
            "user_data": self.user_data,
            "status": self.status,
            "version": self.version,
        }

    def __repr__(self) -> str:
        return (
            f"BrowserDevice(id={self.id!r}, name={self.name!r}, "
            f"type={self.browser_type!r}, cdp={self.cdp_url!r})"
        )


def find_edge_executable() -> str | None:
    """Find Edge executable on the current system."""
    system = platform.system()
    
    if system == "Windows":
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:  # Linux
        candidates = [
            "/usr/bin/microsoft-edge",
            "/usr/bin/microsoft-edge-stable",
            "/opt/microsoft/msedge/msedge",
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    
    # Try `which` / `where`
    try:
        if system == "Windows":
            result = subprocess.run(["where", "msedge"], capture_output=True, text=True, timeout=5)
        else:
            result = subprocess.run(["which", "microsoft-edge", "msedge"], capture_output=True, text=True, timeout=5)
        
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    return None


def find_chrome_executable() -> str | None:
    """Find Chrome executable."""
    system = platform.system()
    
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        candidates = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    return None


def check_cdp_available(port: int = 9222, timeout: float = 1.5) -> bool:
    """Check if a CDP endpoint is available on given port."""
    import urllib.request
    try:
        url = f"http://localhost:{port}/json/version"
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.getcode() == 200
    except Exception:
        return False


def get_cdp_browser_info(port: int = 9222) -> Optional[dict]:
    """Get browser info from CDP endpoint."""
    import urllib.request
    try:
        url = f"http://localhost:{port}/json/version"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data
    except Exception:
        return None


def get_cdp_tabs(port: int = 9222) -> list[dict]:
    """Get open tabs from CDP endpoint."""
    import urllib.request
    try:
        url = f"http://localhost:{port}/json/list"
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def discover_browsers(ports: list[int] | None = None) -> list[BrowserDevice]:
    """
    Auto-discover browsers with CDP enabled.
    Returns list of BrowserDevice instances.
    """
    ports = ports or DEFAULT_CDP_PORTS
    devices = []

    for port in ports:
        info = get_cdp_browser_info(port)
        if not info:
            continue

        # Determine browser type from User-Agent string
        ua = info.get("Browser", "")
        if "Edg/" in ua or "edge" in ua.lower():
            browser_type = "edge"
        elif "Chrome/" in ua:
            browser_type = "chrome"
        else:
            browser_type = "chromium"

        # Extract version
        version = ""
        if "/" in ua:
            parts = ua.split("/")
            if len(parts) >= 2:
                version = parts[-1].split()[0]

        # Get webSocketDebuggerUrl as ID
        dev_id = info.get("webSocketDebuggerUrl", "").split("/")[-1] or f"local-{port}"

        device = BrowserDevice(
            id=dev_id,
            name=f"Local {browser_type.capitalize()} ({port})",
            browser_type=browser_type,
            cdp_url=f"http://localhost:{port}",
            version=version,
            status="active",
        )
        devices.append(device)

    return devices


def launch_edge_with_cdp(
    port: int = 9222,
    user_data_dir: Optional[str] = None,
    headless: bool = False,
) -> tuple[Optional[subprocess.Popen], str]:
    """
    Launch Microsoft Edge with remote debugging enabled.
    
    Returns (process, cdp_url).
    """
    edge_path = find_edge_executable()
    if not edge_path:
        console.print("[red]❌ Edge not found. Please install Microsoft Edge.[/red]")
        return None, ""

    # Default user data directory
    if user_data_dir is None:
        system = platform.system()
        if system == "Windows":
            user_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "webreader-edge-profile")
        elif system == "Darwin":
            user_data_dir = os.path.expanduser("~/Library/Application Support/webreader-edge-profile")
        else:
            user_data_dir = os.path.expanduser("~/.config/webreader-edge-profile")

    os.makedirs(user_data_dir, exist_ok=True)

    args = [
        edge_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
    ]
    
    if headless:
        args.extend(["--headless=new"])

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        
        # Wait for CDP to be ready
        for _ in range(30):
            time.sleep(0.5)
            if check_cdp_available(port):
                break
        
        if check_cdp_available(port):
            console.print(f"[green]✅ Edge launched on port {port}[/green]")
            return proc, f"http://localhost:{port}"
        else:
            console.print(f"[red]❌ Edge launched but CDP not responding on port {port}[/red]")
            proc.terminate()
            return None, ""
            
    except Exception as e:
        console.print(f"[red]❌ Failed to launch Edge: {e}[/red]")
        return None, ""


def format_device_table(devices: list[BrowserDevice]) -> str:
    """Format devices as a readable table."""
    if not devices:
        return "\nNo browsers found.\n\nHint: Launch Edge with --remote-debugging-port=9222\nOr run: webreader launch"

    lines = []
    lines.append(f"\n{'ID':<36} {'Name':<28} {'Type':<10} {'Port'} {'Status'}")
    lines.append("-" * 85)

    for d in devices:
        port = d.cdp_url.rsplit(":", 1)[-1] if ":" in d.cdp_url else "?"
        lines.append(
            f"{d.id[:34]:<36} {d.name[:26]:<28} {d.browser_type:<10} {port:<6} {d.status}"
        )
    
    return "\n".join(lines)


# ─── Legacy alias for backward compat ──────────────────────────────

def list_devices() -> str:
    """List available browser devices (CLI-friendly output)."""
    devices = discover_browsers()
    return format_device_table(devices)
