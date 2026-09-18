"""
dashboard/server.py — JARVIS Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import json
import inspect
import re
import secrets
import socket
import string
import time
from pathlib import Path
from core import device_manager

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
PORT        = 8000
PHONE_HTTP_PORT = 8002
MAX_UPLOAD_MB = 500


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'JARVIS-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"JARVIS Dashboard Port {port}"
        prog_rule  = "JARVIS Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = subprocess.run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until JARVIS is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = subprocess.run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = subprocess.run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            subprocess.run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception:
            pass  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:  # ufw
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = subprocess.run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = subprocess.run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, no internet required."""
    # Method 1: route trick (fast, works when internet is available)
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution (works offline on most systems)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Method 3: enumerate all interfaces (fully offline, no external deps)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _ensure_certs() -> bool:
    """
    Make sure config/certs holds a TLS key pair, generating a self-signed one the
    first time the dashboard runs.

    The pair is deliberately NOT shipped in the repository. A private key that
    every user downloads is the same as having no private key at all: anyone can
    present a certificate that matches it. Generating locally gives each install
    its own key, costs about a second, and happens exactly once.

    Returns True when a usable pair exists afterwards; False leaves the caller on
    plain HTTP, which still works — the QR code simply encodes http:// instead.
    """
    certs = BASE_DIR / "config" / "certs"
    key_p = certs / "jarvis.key"
    crt_p = certs / "jarvis.crt"
    if key_p.exists() and crt_p.exists():
        return True

    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("[Dashboard] cryptography not installed — serving over plain HTTP.")
        print("[Dashboard] For HTTPS run:  pip install cryptography")
        return False

    try:
        certs.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        who = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS Dashboard"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS"),
        ])

        # The SAN has to cover every address the phone might use: the LAN IP the
        # QR code encodes, plus localhost when testing on the machine itself.
        alt = [x509.DNSName("localhost"),
               x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
        try:
            lan = _local_ip()
            if not lan.startswith("127."):
                alt.append(x509.IPAddress(ipaddress.IPv4Address(lan)))
        except Exception:
            pass          # no LAN address resolvable — localhost entries still work

        # Timezone-aware UTC: datetime.utcnow() is deprecated from Python 3.12 on,
        # and the builder normalises aware values to UTC itself.
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who)
            .issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        key_p.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        try:
            import os as _os
            _os.chmod(key_p, 0o600)   # best effort — largely a no-op on Windows
        except Exception:
            pass

        print(f"[Dashboard] Generated a self-signed certificate for this machine: {certs}")
        return True
    except Exception as e:
        print(f"[Dashboard] Certificate generation failed ({e}) — serving over plain HTTP.")
        return False


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._token_devices: dict[str, str] = {} # auth_token → device_id
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._client_devices: dict[WebSocket, str] = {}
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._phone_audio_clients: set[WebSocket] = set()
        self._state_callback = None
        # Optional hooks are supplied by the core when a setting has a real
        # owner.  The dashboard never keeps a second copy of control state.
        self._control_callback = None
        self._capability_callback = None
        self._device_callback = None
        self._device_telemetry_callback = None
        self._device_results: dict[str, asyncio.Future] = {}
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self.app                          = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    @staticmethod
    def _ssl_enabled() -> bool:
        certs = BASE_DIR / "config" / "certs"
        return (certs / "jarvis.key").exists() and (certs / "jarvis.crt").exists()

    def get_url(self) -> str:
        proto = "https" if self._ssl_enabled() else "http"
        return f"{proto}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry. When HTTPS active, points to alias port (also HTTPS)."""
        if self._ssl_enabled():
            return f"{self._ip}:{PORT + 1}"
        return f"{self._ip}:{PORT}"

    def get_phone_url(self) -> str:
        """LAN-only URL that avoids self-signed TLS rejection on Android."""
        return f"http://{self._ip}:{PHONE_HTTP_PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_state_callback(self, fn) -> None:
        self._state_callback = fn

    def set_control_callback(self, fn) -> None:
        """Bind control writes to an existing core API.

        A missing callback is intentional: controls are reported as
        unsupported rather than appearing to work against dashboard-local
        state.
        """
        self._control_callback = fn

    def set_capability_callback(self, fn) -> None:
        """Bind capability registration to the core-owned registry/state."""
        self._capability_callback = fn

    def set_device_callback(self, fn) -> None:
        self._device_callback = fn

    def set_device_telemetry_callback(self, fn) -> None:
        self._device_telemetry_callback = fn

    async def send_to_device(self, device_id: str, message: dict) -> bool:
        """Send a structured message only to sessions belonging to device_id."""
        sent = False
        for ws, ws_device in list(getattr(self, "_client_devices", {}).items()):
            if ws_device != device_id:
                continue
            try:
                await ws.send_json(message)
                sent = True
            except Exception:
                self._clients.discard(ws)
        return sent

    async def request_device_action(self, device_id: str, message: dict,
                                   request_id: str, timeout: float = 8.0) -> tuple[bool, dict]:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._device_results[request_id] = future
        sent = await self.send_to_device(device_id, message)
        if not sent:
            self._device_results.pop(request_id, None)
            return False, {"ok": False, "error": "device is not connected"}
        try:
            return True, await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return False, {"ok": False, "error": "device acknowledgement timed out"}
        finally:
            self._device_results.pop(request_id, None)

    def _phone_state(self) -> dict:
        try:
            value = self._state_callback() if self._state_callback else {}
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            print(f"[Dashboard] State sync failed: {exc}")
            return {"error": "state unavailable"}

    def _control_surface(self, state: dict) -> dict:
        controls = state.get("controls")
        if isinstance(controls, dict):
            return controls
        return {
            "autonomy": {"supported": False},
            "notification_preferences": {"supported": False},
            "agents": {"supported": False},
            "plugins": {"supported": False},
            "devices": {"supported": False},
        }

    def _dashboard_state(self, limit: int = 50, device_id: str = "default") -> dict:
        """Compose a read-only dashboard view from the core's existing state.

        Missing values remain ``None``/empty; they are not inferred or
        persisted here.  This makes the API useful with both the current core
        and a future central state callback.
        """
        state = self._phone_state()
        if not isinstance(state, dict):
            state = {}
        # The current core exposes its managers through the bound state
        # callback.  Read those existing registries when available; do not
        # cache or mirror them in the dashboard.
        owner = getattr(self._state_callback, "__self__", None)
        if owner is not None:
            agents = getattr(owner, "_agent_manager", None)
            if agents is not None and "agents" not in state:
                try:
                    state["agents"] = agents.list()
                except Exception:
                    pass
            if agents is not None:
                try:
                    state["agent_tasks"] = agents.tasks(limit)
                    state["agent_communications"] = agents.communications(limit)
                except Exception:
                    state.setdefault("agent_tasks", [])
                    state.setdefault("agent_communications", [])
            plugins = getattr(owner, "_plugin_manager", None)
            if plugins is not None and not state.get("plugins"):
                try:
                    state["plugins"] = plugins.list_for_ui()
                except Exception:
                    pass
            mobile_manager = getattr(owner, "_mobile_capabilities", None)
            if mobile_manager is not None and "devices" not in state:
                try:
                    state["devices"] = mobile_manager.connected_devices()
                except Exception:
                    pass
        limit = max(1, min(int(limit or 50), 200))
        active_task = state.get("active_task")
        tasks = state.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        background = state.get("background_tasks")
        if not isinstance(background, list):
            background = [task for task in tasks if task != active_task]
        mobile = state.get("mobile")
        devices = state.get("devices")
        if not isinstance(devices, list):
            devices = (
                mobile.get("connected_devices", [])
                if isinstance(mobile, dict) else []
            )
        try:
            from core import event_engine
            events = state.get("events")
            if not isinstance(events, list):
                events = event_engine.recent(limit)
            notifications = state.get("notifications")
            if not isinstance(notifications, list):
                notifications = event_engine.pending_for_device(device_id, limit)
        except Exception:
            events, notifications = [], []
        return {
            "jarvis": {
                "status": state.get("jarvis_status"),
                "active_task": active_task,
                "active_project": state.get("active_project"),
            },
            "tasks": {"active": active_task, "background": background[:limit],
                      "items": tasks[:limit]},
            "agents": state.get("agents", []),
            "agent_hierarchy": state.get("agent_hierarchy", [
                {"name": "JARVIS", "role": "orchestrator", "parent": None,
                 "children": [row.get("name") for row in state.get("agents", [])]},
            ]),
            "agent_tasks": state.get("agent_tasks", []),
            "agent_communications": state.get("agent_communications", []),
            "devices": devices[:limit],
            "plugins": state.get("plugins", []),
            "provider": state.get("provider"),
            "events": events,
            "notifications": notifications,
            "autonomy": state.get("autonomy"),
            "goals": state.get("goals", []),
            "health": state.get("health"),
            "controls": self._control_surface(state),
        }

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def broadcast_phone_audio(self, data: bytes) -> None:
        """Send response PCM only to authenticated phone audio clients."""
        dead = set()
        for ws in list(self._phone_audio_clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        self._phone_audio_clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                paired = device_manager.register(entered)
                dev_tok, device_id = paired if paired else (secrets.token_urlsafe(32), secrets.token_urlsafe(12))
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._token_devices[tok] = device_id
                self._device_sessions[dev_tok] = {"session_key": entered, "device_id": device_id}
                self._aes_key(entered)                   # pre-derive & cache
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok, "device_token": dev_tok, "device_id": device_id, "key": entered})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in JARVIS to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok     = secrets.token_urlsafe(32)
            paired = device_manager.register(key)
            dev_tok, device_id = paired if paired else (secrets.token_urlsafe(32), secrets.token_urlsafe(12))
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._token_devices[tok] = device_id
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key, "device_id": device_id}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to JARVIS…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            stored = self._device_sessions.get(dev_tok)
            if stored:
                session_key = stored["session_key"]
                device_id = stored.get("device_id", dev_tok[:12])
            else:
                resolved = device_manager.resolve(dev_tok)
                if not resolved:
                    return JSONResponse({"ok": False}, status_code=401)
                session_key, device_id = resolved
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._token_devices[tok] = device_id
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key, "device_id": device_id})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = device_manager.revoke_all()
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.get("/api/phone/state")
        async def phone_state(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True, "state": self._phone_state()})

        # ── Control-center read model ───────────────────────────────────
        # These endpoints intentionally share one snapshot so dashboard
        # panels cannot drift from one another or create their own stores.
        def _dashboard_auth(req: Request):
            if not _auth(req):
                return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return self._token_devices.get(token, "default"), None

        @app.get("/api/state")
        @app.get("/api/dashboard/state")
        async def dashboard_state(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "state": self._dashboard_state(limit, device_id)})

        @app.get("/api/status")
        async def dashboard_status(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            state = self._dashboard_state(1, device_id)
            return JSONResponse({"ok": True, "jarvis": state["jarvis"],
                                 "health": state["health"]})

        @app.get("/api/tasks")
        async def dashboard_tasks(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "tasks": self._dashboard_state(limit, device_id)["tasks"]})

        @app.get("/api/agents")
        async def dashboard_agents(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "agents": self._dashboard_state(50, device_id)["agents"]})

        @app.get("/api/agent-communications")
        async def dashboard_agent_communications(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            view = self._dashboard_state(limit, device_id)
            return JSONResponse({"ok": True, "communications": view["agent_communications"]})

        @app.get("/api/agent-tasks")
        async def dashboard_agent_tasks(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            view = self._dashboard_state(limit, device_id)
            return JSONResponse({"ok": True, "tasks": view["agent_tasks"]})

        @app.post("/api/agents/{agent_name}/voice")
        async def dashboard_agent_voice(agent_name: str, req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            owner = getattr(self._state_callback, "__self__", None)
            manager = getattr(owner, "_agent_manager", None) if owner is not None else None
            if manager is None:
                return JSONResponse({"ok": False, "error": "Agent manager unavailable"}, status_code=503)
            try:
                config = await req.json()
                voice = manager.set_voice_config(agent_name, config)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return JSONResponse({"ok": True, "agent": agent_name.upper(), "voice_config": voice})

        @app.get("/api/devices")
        async def dashboard_devices(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "devices": self._dashboard_state(50, device_id)["devices"]})

        @app.get("/api/plugins")
        async def dashboard_plugins(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "plugins": self._dashboard_state(50, device_id)["plugins"]})

        @app.get("/api/provider")
        async def dashboard_provider(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "provider": self._dashboard_state(1, device_id)["provider"]})

        @app.get("/api/events")
        async def dashboard_events(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "events": self._dashboard_state(limit, device_id)["events"]})

        @app.get("/api/notifications")
        async def dashboard_notifications(req: Request, limit: int = 50):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "notifications": self._dashboard_state(limit, device_id)["notifications"]})

        @app.get("/api/autonomy")
        async def dashboard_autonomy(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "autonomy": self._dashboard_state(1, device_id)["autonomy"]})

        @app.get("/api/goals")
        async def dashboard_goals(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "goals": self._dashboard_state(50, device_id)["goals"]})

        @app.get("/api/health")
        async def dashboard_health(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "health": self._dashboard_state(1, device_id)["health"]})

        @app.get("/api/controls")
        async def dashboard_controls(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            return JSONResponse({"ok": True, "controls": self._dashboard_state(1, device_id)["controls"]})

        @app.post("/api/controls")
        async def dashboard_control(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            if not self._control_callback:
                return JSONResponse(
                    {"ok": False, "error": "No core control API is available."},
                    status_code=501,
                )
            try:
                body = await req.json()
                control = str(body.get("control") or "").strip()
                if not control:
                    return JSONResponse({"ok": False, "error": "control is required"}, status_code=400)
                result = self._control_callback(control, body.get("value"))
                if inspect.isawaitable(result):
                    result = await result
                return JSONResponse({"ok": True, "control": control, "result": result})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        @app.post("/api/capabilities/register")
        async def register_capabilities(req: Request):
            device_id, denied = _dashboard_auth(req)
            if denied:
                return denied
            if not self._capability_callback:
                return JSONResponse(
                    {"ok": False, "error": "No core capability registry is available."},
                    status_code=501,
                )
            try:
                body = await req.json()
                capabilities = body.get("capabilities", [])
                if not isinstance(capabilities, list):
                    return JSONResponse(
                        {"ok": False, "error": "capabilities must be an array"},
                        status_code=400,
                    )
                result = self._capability_callback(
                    [str(item).strip() for item in capabilities if str(item).strip()],
                    source=str(body.get("source") or "vscode"),
                    device_id=device_id,
                    metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                )
                if inspect.isawaitable(result):
                    result = await result
                return JSONResponse({"ok": True, "result": result})
            except Exception as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        @app.get("/api/phone/events")
        async def phone_events(req: Request, limit: int = 50):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            from core import event_engine
            device_id = self._token_devices.get(req.headers.get("authorization", "").removeprefix("Bearer ").strip(), "default")
            return JSONResponse({"ok": True, "events": event_engine.pending_for_device(device_id, limit)})

        @app.post("/api/phone/events/ack")
        async def phone_events_ack(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            device_id = self._token_devices.get(token, "default")
            from core import event_engine
            event_engine.mark_delivered(body.get("event_ids", []), device_id)
            return JSONResponse({"ok": True})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._phone_audio_clients.add(websocket)
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                self._phone_audio_clients.discard(websocket)
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            self._client_devices[websocket] = self._token_devices.get(tok, "default")
            if self._device_callback:
                self._device_callback(self._client_devices[websocket], True)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                from core import event_engine
                device_id = self._token_devices.get(tok, "default")
                for event in event_engine.pending_for_device(device_id, 50):
                    await websocket.send_json({"type": "event", "event": event})
            except Exception:
                pass
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "mobile_result":
                        request_id = str(data.get("request_id", ""))
                        future = self._device_results.get(request_id)
                        if future and not future.done():
                            future.set_result({
                                "ok": bool(data.get("ok", False)),
                                "error": data.get("error"),
                            })
                        continue
                    if data.get("type") == "mobile_telemetry":
                        if self._device_telemetry_callback:
                            self._device_telemetry_callback(
                                self._client_devices.get(websocket, "default"),
                                data.get("telemetry") or {},
                            )
                        continue
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)
                device_id = getattr(self, "_client_devices", {}).pop(websocket, "default")
                still_connected = device_id in self._client_devices.values()
                if self._device_callback and not still_connected:
                    self._device_callback(device_id, False)

        return app

    # ── serve ─────────────────────────────────────────────────────────────

    async def _serve_alias(self) -> None:
        """Second HTTPS server on PORT+1 sharing the same app and in-memory state.
        Chrome HTTPS-upgrades any bare IP:PORT the user types, so this port also needs TLS.
        User types IP:8001 → Chrome tries https → self-signed cert warning → accept once → done."""
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (type in browser, accept cert once)")
        await uvicorn.Server(cfg).serve()

    async def _serve_phone_http(self) -> None:
        """Optional LAN fallback for Android devices that reject local TLS.

        Authentication still requires the one-time pairing key/bearer token.
        This endpoint is intended only for a trusted private LAN; HTTPS remains
        the preferred transport for remote or untrusted networks.
        """
        asyncio.get_event_loop().run_in_executor(
            None, _ensure_network_access, PHONE_HTTP_PORT
        )
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PHONE_HTTP_PORT, log_level="warning"
        )
        print(
            f"[Dashboard] Android LAN fallback: http://{self._ip}:{PHONE_HTTP_PORT} "
            "(private network only)"
        )
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        # Firewall setup runs in a thread — uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        # Generate the TLS pair on first run so no private key ships in the repo.
        _ensure_certs()

        use_ssl  = self._ssl_enabled()
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"

        if use_ssl:
            asyncio.create_task(self._serve_alias())
            asyncio.create_task(self._serve_phone_http())

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        proto = "https" if use_ssl else "http"
        print(f"[Dashboard] {proto}://{self._ip}:{PORT}")
        if use_ssl:
            print(
                f"[Dashboard] Phone URL (LAN fallback): "
                f"http://{self._ip}:{PHONE_HTTP_PORT}"
            )
        print("[Dashboard] Press 'Remote Control' in JARVIS UI to get the QR code.")
        await uvicorn.Server(cfg).serve()
