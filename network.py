"""Private connection modes: Tor, SSH tunnel, VPN (WireGuard or VPN proxy), custom proxy.

Each mode ends up as a local proxy URL that yt-dlp sends all traffic through.
SOCKS routes use "socks5h", so DNS lookups also go through the tunnel
(no DNS leaks). Before any download, the route is checked
("kill switch"): if the check fails, nothing is downloaded.

Modes
  direct     normal connection
  tor        uses a running Tor (Tor Browser :9150 or tor service :9050),
             or starts the `tor` program automatically
  ssh        SSH dynamic tunnel (`ssh -D`) to a server you control
  wireguard  your VPN provider's WireGuard .conf, run in user space through
             `wireproxy` (no admin rights, does not change system routing)
  proxy      any HTTP/HTTPS/SOCKS proxy, e.g. the SOCKS5 server many VPN
             providers offer
"""

from __future__ import annotations

import json
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp

MODES = ["direct", "tor", "ssh", "wireguard", "proxy"]
MODE_LABELS = {
    "direct": "Direct (no protection)",
    "tor": "Tor",
    "ssh": "SSH tunnel",
    "wireguard": "VPN (WireGuard .conf)",
    "proxy": "Proxy / VPN provider proxy",
}
TOR_CHECK_URL = "https://check.torproject.org/api/ip"
IP_CHECK_URL = "https://api.ipify.org?format=json"

Status = Callable[[str], None]


@dataclass
class NetworkOptions:
    mode: str = "direct"
    proxy: str | None = None          # proxy mode
    ssh_target: str | None = None     # ssh mode: user@host
    ssh_port: int = 22
    ssh_key: str | None = None
    wg_config: str | None = None      # wireguard mode: path to .conf
    tor_path: str | None = None       # optional path to the tor program
    verify: bool = True               # kill switch: check the route first


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def wait_for_port(port: int, proc: subprocess.Popen | None, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise ConnectionError(f"{what} exited early (code {proc.returncode}).")
        if port_open(port):
            return
        time.sleep(0.3)
    raise ConnectionError(f"{what} did not start within {int(timeout)}s.")


def remote_dns(url: str) -> str:
    """socks5:// → socks5h:// so hostnames are resolved by the proxy, not locally."""
    return "socks5h://" + url[len("socks5://"):] if url.lower().startswith("socks5://") else url


def fetch(url: str, proxy: str | None, timeout: int = 20) -> str:
    opts = {"quiet": True, "no_warnings": True, "proxy": proxy or "", "socket_timeout": timeout}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.urlopen(url).read().decode("utf-8", "replace")


def tor_status(proxy: str | None) -> dict:
    """{'IsTor': bool, 'IP': str} as reported by the Tor Project."""
    return json.loads(fetch(TOR_CHECK_URL, proxy))


def public_ip(proxy: str | None) -> str:
    return json.loads(fetch(IP_CHECK_URL, proxy))["ip"]


def _tor_candidates() -> list[str]:
    home = Path.home()
    paths = [
        home / "Desktop/Tor Browser/Browser/TorBrowser/Tor/tor.exe",
        Path("C:/Program Files/Tor Browser/Browser/TorBrowser/Tor/tor.exe"),
        Path("/Applications/Tor Browser.app/Contents/MacOS/Tor/tor"),
        home / "tor-browser/Browser/TorBrowser/Tor/tor",
    ]
    found = shutil.which("tor")
    return ([found] if found else []) + [str(p) for p in paths if p.is_file()]


class Connection:
    """Context manager that opens the chosen route and closes it afterwards.

        with Connection(net, status) as conn:
            ... use conn.proxy ...
    """

    def __init__(self, net: NetworkOptions | None, status: Status | None = None,
                 interactive: bool = True) -> None:
        self.net = net or NetworkOptions()
        self.say = status or (lambda _m: None)
        self.interactive = interactive  # False in the GUI: no password prompts
        self.proxy: str | None = None
        self.exit_ip: str | None = None
        self._procs: list[subprocess.Popen] = []
        self._tmp: list[str] = []

    # ---- lifecycle ----
    def __enter__(self) -> "Connection":
        try:
            self.open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        mode = self.net.mode
        if mode not in MODES:
            raise ValueError(f"Unknown connection mode: {mode}")
        if mode == "direct":
            return
        self.proxy = getattr(self, f"_open_{mode}")()
        if self.net.verify:
            self.verify()
        else:
            self.say("Route check skipped (kill switch off).")

    def close(self) -> None:
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self._procs.clear()
        for d in self._tmp:
            shutil.rmtree(d, ignore_errors=True)
        self._tmp.clear()

    # ---- kill switch ----
    def verify(self) -> str:
        self.say("Checking the protected connection…")
        try:
            if self.net.mode == "tor":
                st = tor_status(self.proxy)
                if not st.get("IsTor"):
                    raise ConnectionError("Traffic is NOT going through Tor.")
                self.exit_ip = st.get("IP")
            else:
                self.exit_ip = public_ip(self.proxy)
                real = public_ip(None)
                if self.exit_ip == real:
                    raise ConnectionError("Your real IP is visible through this route (leak).")
        except ConnectionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ConnectionError(f"Could not reach the internet through the {self.net.mode} route: {e}") from e
        self.say(f"Protected. Sites will see IP {self.exit_ip}.")
        return self.exit_ip

    # ---- modes ----
    def _open_proxy(self) -> str:
        if not self.net.proxy:
            raise ValueError("Proxy mode needs a proxy URL, e.g. socks5://host:1080")
        return remote_dns(self.net.proxy.strip())

    def _open_tor(self) -> str:
        if not self.net.tor_path:
            for port, name in ((9150, "Tor Browser"), (9050, "Tor service")):
                if port_open(port):
                    self.say(f"Using the running {name} on port {port}.")
                    return f"socks5h://127.0.0.1:{port}"
        candidates = [self.net.tor_path] if self.net.tor_path else _tor_candidates()
        if not candidates:
            raise ConnectionError(
                "Tor not found. Either open Tor Browser (https://www.torproject.org) and leave it "
                "running, or install the 'tor' program (Linux: sudo apt install tor, "
                "macOS: brew install tor).")
        port = free_port()
        data = tempfile.mkdtemp(prefix="vdl-tor-")
        self._tmp.append(data)
        self.say("Starting Tor (this can take up to a minute)…")
        try:
            proc = subprocess.Popen(
                [candidates[0], "--SocksPort", f"127.0.0.1:{port}", "--DataDirectory", data,
                 "--ClientOnly", "1"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise ConnectionError(f"Could not start Tor at {candidates[0]}: {e.strerror}") from e
        self._procs.append(proc)
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [lines.put(ln) for ln in proc.stdout], daemon=True).start()
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                raise ConnectionError(f"Tor exited (code {proc.returncode}).")
            try:
                ln = lines.get(timeout=1)
            except queue.Empty:
                continue
            if "Bootstrapped" in ln:
                pct = ln.split("Bootstrapped", 1)[1].split("%")[0].strip()
                self.say(f"Tor connecting… {pct}%")
                if pct == "100":
                    return f"socks5h://127.0.0.1:{port}"
        raise ConnectionError("Tor did not finish connecting within 2 minutes.")

    def _open_ssh(self) -> str:
        if not self.net.ssh_target:
            raise ValueError("SSH tunnel mode needs a server, e.g. user@example.com")
        ssh = shutil.which("ssh")
        if not ssh:
            raise ConnectionError("The 'ssh' program was not found (built into Windows 10+, macOS and Linux).")
        port = free_port()
        cmd = [ssh, "-N", "-D", f"127.0.0.1:{port}", "-p", str(self.net.ssh_port),
               "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30"]
        if self.net.ssh_key:
            cmd += ["-i", str(Path(self.net.ssh_key).expanduser())]
        if not self.interactive:
            cmd += ["-o", "BatchMode=yes"]  # GUI: key-based login only
        cmd.append(self.net.ssh_target)
        self.say(f"Opening SSH tunnel to {self.net.ssh_target}…")
        proc = subprocess.Popen(cmd, stdin=None if self.interactive else subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._procs.append(proc)
        wait_for_port(port, proc, 60 if self.interactive else 25, "SSH tunnel")
        return f"socks5h://127.0.0.1:{port}"

    def _open_wireguard(self) -> str:
        conf = self.net.wg_config
        if not conf or not Path(conf).expanduser().is_file():
            raise ValueError("VPN mode needs your provider's WireGuard .conf file.")
        exe = shutil.which("wireproxy")
        if not exe:
            raise ConnectionError(
                "VPN mode uses 'wireproxy' to run WireGuard without admin rights. Download it from "
                "https://github.com/windtf/wireproxy/releases and put it on your PATH or next to this app.")
        port = free_port()
        tmp = tempfile.mkdtemp(prefix="vdl-wg-")
        self._tmp.append(tmp)
        cfg = Path(tmp) / "wireproxy.conf"
        cfg.write_text(f"WGConfig = {Path(conf).expanduser().resolve()}\n\n"
                       f"[Socks5]\nBindAddress = 127.0.0.1:{port}\n", encoding="utf-8")
        self.say("Connecting to VPN (WireGuard)…")
        proc = subprocess.Popen([exe, "-c", str(cfg)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._procs.append(proc)
        wait_for_port(port, proc, 30, "WireGuard VPN")
        return f"socks5h://127.0.0.1:{port}"


def _app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


# Let tools placed next to the app (tor, wireproxy) be found like ones on PATH.
import os as _os  # noqa: E402
_os.environ["PATH"] = str(_app_dir()) + _os.pathsep + _os.environ.get("PATH", "")
