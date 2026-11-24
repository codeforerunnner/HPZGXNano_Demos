#!/usr/bin/env python3
"""Unified HP ZGX Nano Fleet Hub & Node Console (single-package deployment)."""

import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import hmac
import io
import ipaddress
import json
import os
import pty
import re
import secrets
import select
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTAL_DIR = Path(__file__).resolve().parent
KIT_DIR = Path(os.environ.get("ZGX_KIT_DIR", str(PORTAL_DIR.parent / "zgx-partner-kit")))
if not (KIT_DIR / "portable.py").exists() and (Path.home() / "zgx-partner-kit" / "portable.py").exists():
    KIT_DIR = Path.home() / "zgx-partner-kit"
DEMOS_ROOT = Path(os.environ.get("ZGX_DEMOS_ROOT", str(Path.home() / "zgx-demos")))
LOG_FILE = DEMOS_ROOT / "launcher-current.log"
FLEET_FILE = DEMOS_ROOT / "fleet.json"
CONFIG_FILE = DEMOS_ROOT / "console-config.json"
TOKEN_LEDGER_FILE = DEMOS_ROOT / "token_ledger.json"
SSH_CREDENTIALS_FILE = DEMOS_ROOT / "ssh-credentials.json"
SSH_CREDENTIALS_LOCK = threading.Lock()
LISTEN_HOST = os.environ.get("ZGX_LAUNCHER_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("ZGX_LAUNCHER_PORT", "18888"))
DISCOVERY_PORT = int(os.environ.get("ZGX_DISCOVERY_PORT", str(LISTEN_PORT)))
RESILIENCE_INTERVAL = int(os.environ.get("ZGX_RESILIENCE_INTERVAL", "15"))
MASTER_FAILOVER_THRESHOLD = int(os.environ.get("ZGX_MASTER_FAILOVER_THRESHOLD", "3"))
RESILIENCE_LOCK = threading.Lock()
_RESILIENCE_STATE = {"master_failures": 0, "last_announced_ip": ""}

DEFAULT_TOKEN_RATES = {
    "input_eur_per_1m": 2.76,
    "output_eur_per_1m": 13.80,
    "usd_to_eur": 0.92,
}
TOKEN_LOCK = threading.Lock()
_LAST_LLAMA_SAMPLE = {"key": None, "in": 0, "out": 0}
_LAST_NANO_SAMPLE = {"in": 0, "out": 0}
_LAST_FURY_SAMPLE = {"key": None, "in": 0, "out": 0}
_LAST_VLLM_SAMPLE = {"key": None, "in": 0, "out": 0}
_LIVE_TOK_S = {"val": 0.0}

DEFAULT_USER = "admin"
DEFAULT_PASS = "Admin321"

SERVICE_META = {
    "ollama.service": {"thumb": "ollama", "port": 11434},
    "comfyui.service": {"thumb": "comfyui", "port": 8188, "open_url": "http://{host}:8188"},
    "sd-webui.service": {"thumb": "sd-webui", "port": 7860, "open_url": "http://{host}:7860"},
    "hermes-voice.service": {"thumb": "hermes-voice", "port": 0},
    "hermes-agent.service": {"thumb": "hermes-voice", "port": 0},
    "hermes-dashboard.service": {"thumb": "hermes-voice", "port": 0},
    "zgx-watchdog.service": {"thumb": "zgx-watchdog", "port": 0},
    "zgx-launcher.service": {"thumb": "zgx-watchdog", "port": 18888},
    "docker.service": {"thumb": "docker", "port": 0},
}
PROTECTED_STOP = {"ssh.service", "sshd.service", "zgx-launcher.service"}
UNIT_RE = re.compile(r"^[a-zA-Z0-9_.@-]+\.service$")

DEMOS = {
    "campus": {
        "name": "ZGX CampusCompass",
        "tag": "RAG + RERANKER",
        "port": 18000,
        "route": "/",
        "req_mem_gib": 24.0,
        "req_ports": [18000, 8081],
        "desc_es": "Asesoria academica local con Qwen 35B, embeddings y reranker.",
        "desc_en": "Local academic advising with Qwen 35B, embeddings, and reranker.",
    },
    "atlas": {
        "name": "ZGX Atlas Voice",
        "tag": "VOICE + ANALYTICS",
        "port": 18110,
        "route": "/",
        "req_mem_gib": 24.0,
        "req_ports": [18110, 18000],
        "desc_es": "Analitica empresarial por voz (Whisper + Piper) y Qwen 35B.",
        "desc_en": "Voice business analytics (Whisper + Piper) and Qwen 35B.",
    },
    "guard": {
        "name": "ZGX Beat the Guard",
        "tag": "POLICY + AUDIT",
        "port": 18080,
        "route": "/play",
        "req_mem_gib": 24.0,
        "req_ports": [18080, 18000],
        "desc_es": "Juego de politicas y validacion con perfil publico Qwen.",
        "desc_en": "Policy validation challenge running on public Qwen profile.",
    },
    "vision": {
        "name": "ZGX Research Observatory",
        "tag": "RF-DETR + REASONING",
        "port": 18120,
        "route": "/",
        "req_mem_gib": 24.0,
        "req_ports": [18120, 18000],
        "desc_es": "Deteccion de objetos RF-DETR (CPU) y razonamiento local Qwen.",
        "desc_en": "RF-DETR CPU object detection and local Qwen reasoning.",
    },
    "nano-collective": {
        "name": "ZGX Nano Collective v3.2",
        "tag": "COLLECTIVE :18808",
        "port": 18808,
        "route": "/free",
        "req_mem_gib": 96.0,
        "req_ports": [18808, 18000, 18001, 18100, 18204, 18800],
        "desc_es": "Demo Nano Collective v3.2.0-nano.3: Free Mode (:18808/free) y Presenter Mode (:18808/stage).",
        "desc_en": "Nano Collective v3.2.0-nano.3 showcase: Free Mode (:18808/free) and Presenter Mode (:18808/stage).",
    },
}

NANO_COLLECTIVE_DIR = Path.home() / "hp-zgx-nano"


def _resolve_fury_dir() -> Path:
    candidates = [
        os.environ.get("ZGX_FURY_DIR", "").strip(),
        str(PORTAL_DIR.parent / "fury-showcase"),
        str(Path.home() / "hp-zgx-fury-showcase"),
    ]
    for cand in candidates:
        if cand and (Path(cand) / "launch.sh").exists():
            return Path(cand)
    return Path(os.environ.get("ZGX_FURY_DIR", str(PORTAL_DIR.parent / "fury-showcase")))


FURY_DIR = _resolve_fury_dir()
FURY_PROFILES = {
    "fury-collective": {
        "name": "Fury Collective v3+",
        "profile_key": "collective",
        "tag": "GB300 ALL-IN-ONE V3+",
        "port": 8080,
        "route": "/",
        "req_mem_gib": 240.0,
        "req_ports": [8080],
        "desc_es": "Stack completo GB300: DeepSeek-V4-Flash + Qwen3.6-27B + FLUX + Wan2.2 + Agent Factory + Heimdall Fury + Executive Stage.",
        "desc_en": "Full GB300 stack: DeepSeek-V4-Flash + Qwen3.6-27B + FLUX + Wan2.2 + Agent Factory + Heimdall Fury + Executive Stage.",
    },
    "fury-arena": {
        "name": "Fury Department Arena",
        "profile_key": "arena-max",
        "tag": "24x CONCURRENCY QR",
        "port": 8080,
        "route": "/experience/arena",
        "req_mem_gib": 125.0,
        "req_ports": [8080],
        "desc_es": "Departamento en una Caja: Qwen3.6-27B a maxima concurrencia (24 seqs) para 15-25 usuarios simultaneos por QR + Data Room OCR.",
        "desc_en": "Department in a Box: Qwen3.6-27B at maximum concurrency (24 seqs) for 15-25 live QR users + Data Room OCR.",
    },
    "fury-ailab": {
        "name": "Fury AI Lab (LoRA + Dub)",
        "profile_key": "ai-lab",
        "tag": "TRAIN + MEDIA",
        "port": 8080,
        "route": "/experience/lora",
        "req_mem_gib": 65.0,
        "req_ports": [8080],
        "desc_es": "Fine-tuning LoRA en vivo (>180 GiB HBM3e libres) + Qwen3.6 + Video Dubbing Studio offline ES/EN.",
        "desc_en": "Live LoRA fine-tuning (>180 GiB free HBM3e) + Qwen3.6 + offline ES/EN Video Dubbing Studio.",
    },
    "fury-titan": {
        "name": "Fury Titan Frontier",
        "profile_key": "titan",
        "tag": "FRONTIER NVFP4",
        "port": 8080,
        "route": "/experience/warroom",
        "req_mem_gib": 230.0,
        "req_ports": [8080],
        "desc_es": "Modelo unico clase frontier sobre HBM3e + Grace NVLink-C2C con contexto largo para Heimdall, War Room y Data Room.",
        "desc_en": "Single frontier-class model over HBM3e + Grace NVLink-C2C with long context for Heimdall, War Room and Data Room.",
    },
}
FURY_DEMO = {
    "fury": {
        "name": "HP ZGX Fury Showcase",
        "tag": "GB300 SHOWCASE",
        "port": 8080,
        "route": "/",
        "desc_es": "Stack completo HP ZGX Fury (GB300) en el puerto 8080.",
        "desc_en": "Full HP ZGX Fury (GB300) showcase stack on port 8080.",
    }
}

CERN_DEMO_DIR = Path.home() / "cern-lhc-demo"
CERN_DEMO = {
    "name": "CERN LHC Live AI Trigger",
    "tag": "CERN OPEN DATA · 3D TRACKML",
    "port": 8900,
    "route": "/",
    "req_mem_gib": 8.0,
    "req_ports": [8900],
    "desc_es": "Trigger de colisiones CMS/ATLAS (CERN Open Data) + reconstruccion 3D TrackML con IA local.",
    "desc_en": "CMS/ATLAS collision trigger (CERN Open Data) + 3D TrackML reconstruction with local AI.",
}

WATCH_PORTS = [
    (18888, "ZGX Console"),
    (8900, "CERN LHC Live AI Trigger"),
    (8080, "Fury Mission Control"),
    (8001, "Fury Gateway"),
    (8000, "Qwen vLLM"),
    (8100, "DeepSeek/Titan vLLM"),
    (8200, "Atlas Analytics"),
    (8202, "ASR Speech"),
    (8204, "Kokoro TTS"),
    (8501, "Agent Factory"),
    (8765, "Presentations"),
    (8800, "Creative FLUX+Wan"),
    (18000, "CampusCompass"),
    (18110, "Atlas Voice"),
    (18080, "Beat the Guard"),
    (18120, "Research Obs."),
    (11434, "Ollama API"),
]

STATE_LOCK = threading.Lock()
CURRENT = {
    "key": None,
    "proc": None,
    "log_handle": None,
    "started_at": None,
    "status": "idle",
    "last_error": "",
}

TELEM_LOCK = threading.Lock()
PREV_CPU_LINES = {}
PREV_NET = None
PREV_DISK = None
HISTORY = deque(maxlen=60)
LATEST_SYS = {}

CFG_LOCK = threading.Lock()
ENROLL_LOCK = threading.Lock()
ENROLL_JOB = {
    "running": False,
    "phase": "idle",
    "step": 0,
    "total_steps": 3,
    "started_at": 0.0,
    "ended_at": 0.0,
    "host": "",
    "ok": None,
    "error": "",
    "log": [],
}
DEPLOY_LOCK = threading.Lock()
DEPLOY_JOB = {
    "running": False,
    "phase": "idle",
    "step": 0,
    "total_steps": 3,
    "started_at": 0.0,
    "ended_at": 0.0,
    "node_id": "",
    "host": "",
    "ok": None,
    "error": "",
    "log": [],
}
AUTO_LOCK = threading.Lock()
AUTO_JOB = {"running": False, "phase": "idle", "items": {}, "log": [], "cancel": False}


def _token_digest(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return dk.hex()


def load_config() -> dict:
    with CFG_LOCK:
        DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        changed = False
        if "role" not in cfg:
            cfg["role"] = os.environ.get("ZGX_ROLE", "hub")
            changed = True
        if "node_token" not in cfg or not cfg["node_token"]:
            cfg["node_token"] = os.environ.get("ZGX_NODE_TOKEN") or secrets.token_hex(24)
            changed = True
        if os.environ.get("ZGX_NODE_TOKEN") and cfg["node_token"] != os.environ["ZGX_NODE_TOKEN"]:
            cfg["node_token"] = os.environ["ZGX_NODE_TOKEN"]
            changed = True
        if "node_id" not in cfg or not cfg["node_id"]:
            cfg["node_id"] = f"{socket.gethostname()}-{cfg['node_token'][:8]}"
            changed = True
        if "hub_epoch" not in cfg or not isinstance(cfg["hub_epoch"], int):
            cfg["hub_epoch"] = 1 if cfg.get("role") == "hub" else 0
            changed = True
        if "session_secret" not in cfg:
            cfg["session_secret"] = secrets.token_hex(32)
            changed = True
        if "users" not in cfg or not isinstance(cfg["users"], dict) or not cfg["users"]:
            salt = secrets.token_hex(16)
            cfg["users"] = {
                DEFAULT_USER: {
                    "salt": salt,
                    "hash": _hash_password(DEFAULT_PASS, salt),
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            }
            cfg.pop("auth_user", None)
            cfg.pop("auth_salt", None)
            cfg.pop("auth_hash", None)
            changed = True
        if changed:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            try:
                os.chmod(CONFIG_FILE, 0o600)
            except OSError:
                pass
        return cfg


def save_config(cfg: dict) -> None:
    with CFG_LOCK:
        DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass


def verify_credentials(username: str, password: str) -> bool:
    cfg = load_config()
    u = cfg.get("users", {}).get(username)
    if not u:
        return False
    cand = _hash_password(password, u["salt"])
    return hmac.compare_digest(cand, u["hash"])


def list_users() -> list:
    cfg = load_config()
    return [
        {"username": k, "created_at": v.get("created_at", "-")}
        for k, v in sorted(cfg.get("users", {}).items())
    ]


def add_user(username: str, password: str) -> dict:
    username = (username or "").strip()
    if not re.match(r"^[a-zA-Z0-9_.-]{2,32}$", username):
        return {"ok": False, "error": "Usuario invalido (2-32 caracteres alfanumericos)."}
    if len(password or "") < 4:
        return {"ok": False, "error": "Contrasena demasiado corta (minimo 4 caracteres)."}
    cfg = load_config()
    if username in cfg["users"]:
        return {"ok": False, "error": "El usuario ya existe."}
    salt = secrets.token_hex(16)
    cfg["users"][username] = {
        "salt": salt,
        "hash": _hash_password(password, salt),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_config(cfg)
    sync_fleet_to_peers()
    return {"ok": True}


def change_user_password(username: str, password: str) -> dict:
    username = (username or "").strip()
    if len(password or "") < 4:
        return {"ok": False, "error": "Contrasena demasiado corta (minimo 4 caracteres)."}
    cfg = load_config()
    if username not in cfg["users"]:
        return {"ok": False, "error": "Usuario no encontrado."}
    salt = secrets.token_hex(16)
    cfg["users"][username]["salt"] = salt
    cfg["users"][username]["hash"] = _hash_password(password, salt)
    save_config(cfg)
    sync_fleet_to_peers()
    return {"ok": True}


def remove_user(username: str) -> dict:
    username = (username or "").strip()
    cfg = load_config()
    if username not in cfg["users"]:
        return {"ok": False, "error": "Usuario no encontrado."}
    if len(cfg["users"]) <= 1:
        return {"ok": False, "error": "No se puede eliminar el ultimo usuario."}
    del cfg["users"][username]
    save_config(cfg)
    sync_fleet_to_peers()
    return {"ok": True}


def make_session_cookie(username: str) -> str:
    cfg = load_config()
    exp = int(time.time()) + 86400 * 7
    payload = f"{username}|{exp}"
    sig = hmac.new(cfg["session_secret"].encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}|{sig}".encode("utf-8")).decode("ascii")
    return f"zgx_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"


def check_session_cookie(cookie_hdr: str) -> str | None:
    if not cookie_hdr:
        return None
    c = SimpleCookie()
    try:
        c.load(cookie_hdr)
    except Exception:
        return None
    morsel = c.get("zgx_session")
    if not morsel:
        return None
    try:
        raw = base64.urlsafe_b64decode(morsel.value.encode("ascii")).decode("utf-8")
        user, exp_s, sig = raw.rsplit("|", 2)
        if int(exp_s) < int(time.time()):
            return None
        cfg = load_config()
        expected = hmac.new(
            cfg["session_secret"].encode("utf-8"), f"{user}|{exp_s}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(sig, expected):
            return user
    except Exception:
        return None
    return None


def load_fleet() -> list:
    DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
    if not FLEET_FILE.exists():
        return []
    try:
        data = json.loads(FLEET_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_fleet(nodes: list) -> None:
    DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".fleet-", dir=FLEET_FILE.parent)
    temp = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(nodes, file, indent=2)
        temp.replace(FLEET_FILE)
    finally:
        temp.unlink(missing_ok=True)


def _ssh_credential_key(host: str, port: int, user: str) -> str:
    return json.dumps([host, port, user], ensure_ascii=True)


def _load_ssh_credentials() -> dict:
    if not SSH_CREDENTIALS_FILE.exists():
        return {}
    data = json.loads(SSH_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError("El archivo de credenciales SSH tiene un formato invalido.")
    return data


def _get_ssh_password(host: str, port: int, user: str) -> str:
    with SSH_CREDENTIALS_LOCK:
        credentials = _load_ssh_credentials()
        return (credentials.get(_ssh_credential_key(host, port, user))
                or credentials.get(_ssh_credential_key("", 0, user), ""))


def _get_default_ssh_password(user: str) -> str:
    with SSH_CREDENTIALS_LOCK:
        return _load_ssh_credentials().get(_ssh_credential_key("", 0, user), "")


def _save_ssh_password(host: str, port: int, user: str, password: str) -> None:
    with SSH_CREDENTIALS_LOCK:
        credentials = _load_ssh_credentials()
        credentials[_ssh_credential_key(host, port, user)] = password
        DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".ssh-credentials-", dir=DEMOS_ROOT)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(credentials, file, ensure_ascii=True)
            os.replace(temp_name, SSH_CREDENTIALS_FILE)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _select_gpu_row(lines: list[str]) -> str:
    rows = [ln for ln in lines if ln.strip()]
    if not rows:
        return ""
    gpu_env = os.environ.get("FURY_GPU", "").strip()
    if gpu_env.isdigit() and int(gpu_env) < len(rows):
        return rows[int(gpu_env)]
    return next((r for r in rows if "GB300" in r.upper()), rows[0])


def _detect_gb300_index() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
            text=True,
            timeout=3,
        )
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",", 1)]
            if len(parts) == 2 and "GB300" in parts[1].upper():
                return parts[0]
    except Exception:
        pass
    return ""


def build_env() -> dict:
    env = os.environ.copy()
    home = str(Path.home())
    env["HOME"] = home
    env["PYTHONUNBUFFERED"] = "1"
    env["ZGX_DEMOS_ROOT"] = str(DEMOS_ROOT)
    extra_path = f"/usr/local/cuda/bin:{home}/.local/bin"
    env["PATH"] = f"{extra_path}:{env.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"/usr/local/cuda/lib64:{ld}" if ld else "/usr/local/cuda/lib64"
    if not env.get("FURY_GPU", "").strip():
        gb300_idx = _detect_gb300_index()
        if gb300_idx:
            env["FURY_GPU"] = gb300_idx
    return env


def _stop_current_locked() -> None:
    proc = CURRENT["proc"]
    if proc is not None and proc.poll() is None:
        CURRENT["status"] = "stopping"
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 12.0
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.3)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
    for pattern in ("portable.py launch", "public_entry.py", "public_services.py", "campuscompass-local", "runtime/llama.cpp", "atlas-claude", "beat-the-guard", "research-observatory"):
        try:
            subprocess.run(["pkill", "-TERM", "-f", pattern], capture_output=True, timeout=3)
        except Exception:
            pass
    if (NANO_COLLECTIVE_DIR / "launch.sh").exists():
        try:
            nano_env = dict(os.environ)
            nano_env["FURY_PROFILE"] = "gb10"
            subprocess.run(["bash", str(NANO_COLLECTIVE_DIR / "launch.sh"), "stop"], cwd=str(NANO_COLLECTIVE_DIR), env=nano_env, capture_output=True, timeout=20)
        except Exception:
            pass
    if (FURY_DIR / "launch.sh").exists():
        try:
            subprocess.run(["bash", str(FURY_DIR / "launch.sh"), "stop"], cwd=str(FURY_DIR), capture_output=True, timeout=15)
        except Exception:
            pass
    wait_ports = [8080, 8081, 8082, 8083, 8110, 8130, 18000, 18110, 18080, 18120, 18808]
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not any(port_open(p) for p in wait_ports):
            break
        time.sleep(0.25)
    if CURRENT["log_handle"] is not None:
        try:
            CURRENT["log_handle"].close()
        except OSError:
            pass
    CURRENT["proc"] = None
    CURRENT["log_handle"] = None
    CURRENT["key"] = None
    CURRENT["started_at"] = None
    CURRENT["status"] = "idle"


def _mem_available_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / (1024 * 1024), 1)
    except Exception:
        pass
    return 0.0


def _free_heavy_services() -> None:
    for u in ("sd-webui.service", "comfyui.service", "ollama.service", "hermes-voice.service"):
        try:
            subprocess.run(["sudo", "-n", "systemctl", "stop", u], capture_output=True, timeout=6)
        except Exception:
            pass
    time.sleep(1.0)


def _is_fury_key(key: str) -> bool:
    return key == "fury" or key in FURY_PROFILES


def _fury_profile_key_for(key: str) -> str:
    if key in FURY_PROFILES:
        return FURY_PROFILES[key]["profile_key"]
    return "collective"


def _active_fury_demo_key() -> str:
    prof_file = FURY_DIR / "state" / "active-profile.json"
    if prof_file.is_file():
        try:
            pk = json.loads(prof_file.read_text(encoding="utf-8")).get("profile", "collective")
            for k, meta in FURY_PROFILES.items():
                if meta["profile_key"] == pk:
                    return k
        except Exception:
            pass
    return "fury-collective"


def _gpu_free_gib() -> float | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.free", "--format=csv,noheader,nounits"],
            text=True,
            timeout=3,
        ).strip()
        if out:
            row = _select_gpu_row(out.splitlines())
            if row:
                return round(float(row.split(",")[-1].strip()) / 1024.0, 1)
    except Exception:
        pass
    return None


def preflight_demo(key: str, auto_fix: bool = False) -> dict:
    checks = []
    meta = FURY_PROFILES.get(key) or (CERN_DEMO if key == "cern-lhc" else (FURY_DEMO.get(key) if key == "fury" else DEMOS.get(key)))
    if not meta:
        return {"ok": False, "key": key, "checks": [], "error": f"Unknown demo: {key}"}

    # 1. File / model prerequisites
    req_files = []
    if key == "cern-lhc":
        req_files = [CERN_DEMO_DIR / ".venv/bin/python", CERN_DEMO_DIR / "backend"]
    elif _is_fury_key(key):
        req_files = [FURY_DIR / "launch.sh", FURY_DIR / "ops/fury-profile.py"]
    elif key == "nano-collective":
        req_files = [
            NANO_COLLECTIVE_DIR / "launch.sh",
            NANO_COLLECTIVE_DIR / ".venv/bin/python",
            NANO_COLLECTIVE_DIR / ".tools/llama.cpp/build/bin/llama-server",
            NANO_COLLECTIVE_DIR / "models/gpt-oss-120b-GGUF",
            NANO_COLLECTIVE_DIR / "models/Qwen3.6-35B-A3B-GGUF",
            NANO_COLLECTIVE_DIR / "models/FLUX.2-klein-4B",
            NANO_COLLECTIVE_DIR / "models/Kokoro-82M",
        ]
    else:
        folder_map = {
            "campus": DEMOS_ROOT / "campuscompass-local",
            "atlas": DEMOS_ROOT / "atlas-claude",
            "guard": DEMOS_ROOT / "beat-the-guard",
            "vision": DEMOS_ROOT / "research-observatory",
        }
        dpath = folder_map.get(key, DEMOS_ROOT)
        req_files = [KIT_DIR / "portable.py", dpath / ".venv/bin/python"]

    missing = [str(p) for p in req_files if not p.exists()]
    if missing:
        checks.append({"name": "files_models", "ok": False, "detail": "Missing: " + ", ".join(missing)})
    else:
        checks.append({"name": "files_models", "ok": True, "detail": f"{len(req_files)} prerequisites present"})

    # 2. Clean previous demos / heavy services if auto_fix
    if auto_fix:
        _stop_current_locked()
        if key == "nano-collective" or _is_fury_key(key) or _mem_available_gib() < float(meta.get("req_mem_gib", 20.0)):
            _free_heavy_services()

    # 3. Memory prerequisite
    req_mem = float(meta.get("req_mem_gib", 16.0))
    if _is_fury_key(key):
        hbm_free = _gpu_free_gib()
        avail_gib = hbm_free if hbm_free is not None else _mem_available_gib()
        mem_ok = avail_gib >= min(req_mem, 240.0) if hbm_free is not None else avail_gib >= 64.0
        mem_lbl = f"HBM3e free {avail_gib} GiB (target ~{req_mem} GiB)" if hbm_free is not None else f"Grace MemAvailable {avail_gib} GiB"
        checks.append({"name": "memory", "ok": mem_ok, "detail": mem_lbl})
    else:
        avail_gib = _mem_available_gib()
        mem_ok = avail_gib >= req_mem
        checks.append({"name": "memory", "ok": mem_ok, "detail": f"MemAvailable {avail_gib} GiB (required >= {req_mem} GiB)"})

    # 4. Disk prerequisite (>= 2 GB free)
    disk_free_gb = round(shutil.disk_usage("/").free / (1024 ** 3), 1)
    disk_ok = disk_free_gb >= 2.0
    checks.append({"name": "disk", "ok": disk_ok, "detail": f"Disk free {disk_free_gb} GB (required >= 2.0 GB)"})

    # 5. Ports free prerequisite
    req_ports = meta.get("req_ports", [meta["port"]])
    busy_ports = [p for p in req_ports if port_open(p)]
    ports_ok = len(busy_ports) == 0
    checks.append({"name": "ports", "ok": ports_ok, "detail": "All ports free" if ports_ok else f"Ports in use: {busy_ports}"})

    all_ok = all(c["ok"] for c in checks)
    err_msg = "; ".join(c["detail"] for c in checks if not c["ok"]) if not all_ok else ""
    return {"ok": all_ok, "key": key, "checks": checks, "error": err_msg}


def start_demo(key: str) -> dict:
    if key == "cern-lhc":
        cwd_dir = CERN_DEMO_DIR
        cmd = [str(CERN_DEMO_DIR / ".venv/bin/python"), "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8900"]
    elif _is_fury_key(key):
        cwd_dir = FURY_DIR
        profile_key = _fury_profile_key_for(key)
        cmd = ["python3", "-u", str(FURY_DIR / "ops/fury-profile.py"), "switch", profile_key]
    elif key == "nano-collective":
        cwd_dir = NANO_COLLECTIVE_DIR
        cmd = ["bash", str(NANO_COLLECTIVE_DIR / "launch.sh"), "free"]
    elif key in DEMOS:
        cwd_dir = KIT_DIR
        cmd = ["python3", "-u", str(KIT_DIR / "portable.py"), "launch", key, "--no-browser"]
    else:
        return {"ok": False, "error": f"Unknown demo: {key}"}
    with STATE_LOCK:
        pre = preflight_demo(key, auto_fix=True)
        DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
        pre_lines = [f"[PREFLIGHT {'OK' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in pre["checks"]]
        if not pre["ok"]:
            LOG_FILE.write_text("\n".join(pre_lines) + f"\n[PREFLIGHT BLOCKED] {pre['error']}\n", encoding="utf-8")
            CURRENT["last_error"] = "Preflight failed: " + pre["error"]
            return {"ok": False, "error": CURRENT["last_error"], "preflight": pre}
        handle = LOG_FILE.open("w", encoding="utf-8", errors="replace")
        handle.write("\n".join(pre_lines) + "\n[PREFLIGHT PASSED] Starting demo...\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd_dir),
            env=build_env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        CURRENT["key"] = key
        CURRENT["proc"] = proc
        CURRENT["log_handle"] = handle
        CURRENT["started_at"] = time.time()
        CURRENT["status"] = "starting"
        CURRENT["last_error"] = ""
    return {"ok": True, "key": key, "pid": proc.pid, "preflight": pre}


def stop_demo() -> dict:
    with STATE_LOCK:
        _stop_current_locked()
        CURRENT["last_error"] = ""
    return {"ok": True}


def _probe_nano_mode() -> str:
    if not port_open(18808, timeout=0.15):
        return ""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:18808/free/api/state", timeout=0.6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        mode = data.get("mode", "")
        return mode if mode in ("free", "presenter") else ""
    except Exception:
        return ""


def switch_nano_mode(mode: str) -> dict:
    mode = (mode or "").strip().lower()
    if mode not in ("free", "presenter"):
        return {"ok": False, "error": f"Invalid Nano mode: {mode}"}
    if not port_open(18808, timeout=0.3):
        return {"ok": False, "error": "ZGX Nano Collective (:18808) is not running. Start it first."}
    route = "/stage" if mode == "presenter" else "/free"
    if _probe_nano_mode() == mode:
        return {"ok": True, "mode": mode, "route": route, "changed": False}
    endpoint = "/free/api/mode" if mode == "presenter" else "/free/api/presenter-exit"
    payload = json.dumps({"mode": "presenter"} if mode == "presenter" else {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:18808{endpoint}",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=30.0) as resp:
            resp.read()
        return {"ok": True, "mode": mode, "route": route, "changed": True}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                detail = parsed.get("detail") or parsed.get("error") or ""
        except Exception:
            detail = ""
        return {"ok": False, "error": detail or f"HTTP {exc.code}: could not switch to {mode} mode"}
    except Exception as exc:
        return {"ok": False, "error": f"Could not switch to {mode} mode: {exc}"}


def control_service(unit: str, action: str) -> dict:
    if not UNIT_RE.match(unit):
        return {"ok": False, "error": f"Invalid service unit: {unit}"}
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "error": f"Action not allowed: {action}"}
    if action == "stop" and unit in PROTECTED_STOP:
        return {"ok": False, "error": f"Protected service cannot be stopped from UI: {unit}"}
    try:
        res = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", action, unit],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            return {"ok": False, "error": err or f"systemctl {action} failed (code {res.returncode})"}
        return {"ok": True, "unit": unit, "action": action}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_all_services() -> list:
    custom_units = set()
    etc_sys = Path("/etc/systemd/system")
    if etc_sys.is_dir():
        for p in etc_sys.glob("*.service"):
            if not p.name.startswith("dbus-"):
                custom_units.add(p.name)

    units_map = {}
    try:
        out = subprocess.check_output(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend", "--plain"],
            text=True,
            timeout=4,
        )
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            unit, load_st, act_st, sub_st = parts[0], parts[1], parts[2], parts[3]
            if load_st == "not-found" or not unit.endswith(".service"):
                continue
            desc = parts[4].strip() if len(parts) > 4 else unit
            units_map[unit] = {
                "unit": unit,
                "name": unit[:-8],
                "desc": desc,
                "active_state": act_st,
                "sub_state": sub_st,
                "main_pid": 0,
                "cpu_pct": 0.0,
                "memory_gib": 0.0,
                "is_custom": unit in custom_units or unit in SERVICE_META,
                "protected_stop": unit in PROTECTED_STOP,
                "models": [],
            }
    except Exception:
        pass

    for unit in custom_units:
        if unit not in units_map:
            units_map[unit] = {
                "unit": unit,
                "name": unit[:-8],
                "desc": unit,
                "active_state": "inactive",
                "sub_state": "dead",
                "main_pid": 0,
                "cpu_pct": 0.0,
                "memory_gib": 0.0,
                "is_custom": True,
                "protected_stop": unit in PROTECTED_STOP,
                "models": [],
            }

    active_units = [u for u, v in units_map.items() if v["active_state"] == "active"]
    pid_cpu = {}
    try:
        ps_out = subprocess.check_output(["ps", "-eo", "pid,pcpu", "--no-headers"], text=True, timeout=2)
        for ln in ps_out.splitlines():
            cols = ln.split()
            if len(cols) == 2:
                try:
                    pid_cpu[int(cols[0])] = float(cols[1])
                except ValueError:
                    pass
    except Exception:
        pass
    if active_units:
        try:
            out = subprocess.check_output(
                ["systemctl", "show", *active_units, "-p", "Id", "-p", "MainPID", "-p", "MemoryCurrent"],
                text=True,
                timeout=4,
            )
            cur = {}
            for line in out.splitlines() + [""]:
                if not line.strip():
                    uid = cur.get("Id")
                    if uid and uid in units_map:
                        try:
                            mpid = int(cur.get("MainPID", "0") or 0)
                            units_map[uid]["main_pid"] = mpid
                            units_map[uid]["cpu_pct"] = pid_cpu.get(mpid, 0.0)
                        except ValueError:
                            pass
                        try:
                            mb = int(cur.get("MemoryCurrent", "0") or 0)
                            if 0 < mb < (1 << 60):
                                units_map[uid]["memory_gib"] = round(mb / (1024 ** 3), 2)
                        except ValueError:
                            pass
                    cur = {}
                elif "=" in line:
                    k, v = line.split("=", 1)
                    cur[k] = v.strip()
        except Exception:
            pass

    ollama_models = []
    if units_map.get("ollama.service", {}).get("active_state") == "active":
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=0.5) as resp:
                data = json.load(resp)
            for m in data.get("models", []):
                vram_gb = round((m.get("size_vram") or m.get("size") or 0) / (1024 ** 3), 1)
                ollama_models.append({"name": m.get("name", "model"), "size_gb": vram_gb})
        except Exception:
            pass

    result = []
    for unit, item in units_map.items():
        meta = SERVICE_META.get(unit, {})
        port = meta.get("port", 0)
        item["thumb"] = meta.get("thumb", "systemd")
        item["port"] = port
        item["port_listening"] = port_open(port) if port else False
        item["open_url"] = meta.get("open_url", "")
        if unit == "ollama.service":
            item["models"] = ollama_models
        result.append(item)

    result.sort(key=lambda s: (
        0 if s["unit"] in SERVICE_META else (1 if s["is_custom"] else 2),
        0 if s["active_state"] == "active" else 1,
        s["unit"],
    ))
    return result


def detect_hw_profile(gpu_name: str = "") -> dict:
    arch = os.uname().machine
    vendor = ""
    product = ""
    for p, key in (("/sys/class/dmi/id/sys_vendor", "v"), ("/sys/class/dmi/id/product_name", "p")):
        try:
            val = Path(p).read_text(encoding="utf-8", errors="ignore").strip()
            if key == "v":
                vendor = val
            else:
                product = val
        except OSError:
            pass
    g = (gpu_name or "").upper()
    prod_up = product.upper()
    if "GB300" in g or "FURY" in prod_up:
        hw_model = "ZGX-FURY"
    elif "GB10" in g or "NANO" in prod_up:
        hw_model = "ZGX-NANO"
    elif arch == "aarch64" and g:
        hw_model = "ZGX-ARM64"
    elif g:
        hw_model = "LINUX-GPU"
    else:
        hw_model = "LINUX"
    return {"hw_model": hw_model, "arch": arch, "vendor": vendor, "product": product}


def read_tail(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError as exc:
        return f"[ERROR] Could not read log: {exc}"


def _detect_install_activity() -> tuple[bool, str, str]:
    """Detecta si hay una instalacion de demos en curso y devuelve (activa, resumen, tail_log)."""
    patterns = (
        "run_zgx_demos_install.sh",
        "nano-auto-setup.sh",
        "portable.py setup",
        "bootstrap-gb10.sh",
        "download-models.py",
        "build-gb10-llama.py",
        "cmake --build",
    )
    active_proc = ""
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit() or int(proc.name) in (os.getpid(), os.getppid()):
                continue
            try:
                cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                continue
            for pat in patterns:
                if pat in cmd:
                    active_proc = pat
                    break
            if active_proc:
                break
    except OSError:
        pass

    candidates = [
        Path.home() / "install-demos.log",
        Path.home() / ".local/state/zgx-setup/auto.log",
    ]
    existing = [p for p in candidates if p.is_file() and p.stat().st_size > 0]
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    log_text = ""
    if existing:
        newest = existing[0]
        age_s = time.time() - newest.stat().st_mtime
        if active_proc or age_s < 1800:
            tail = read_tail(newest, 80)
            if tail:
                header = f"[INSTALL {'ACTIVE' if active_proc else 'RECENT'}] {active_proc or newest.name} ({newest})\n"
                log_text = header + tail
    return bool(active_proc), active_proc, log_text


def _count_active_demo_clients() -> dict:
    """Cuenta conexiones TCP establecidas a los puertos de demos para estimar usuarios concurrentes."""
    demo_ports = {8080, 8900, 18808, 18000, 18080, 18110, 18120}
    external_ips = set()
    total_conns = 0
    for tcp_file in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not tcp_file.is_file():
            continue
        try:
            lines = tcp_file.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "01":  # 01 = ESTABLISHED
                continue
            try:
                local_ip_hex, local_port_hex = parts[1].split(":")
                rem_ip_hex, _ = parts[2].split(":")
                lport = int(local_port_hex, 16)
            except ValueError:
                continue
            if lport not in demo_ports:
                continue
            is_loopback = rem_ip_hex in ("0100007F", "00000000000000000000000001000000", "00000000", "00000000000000000000000000000000")
            if not is_loopback:
                external_ips.add(rem_ip_hex)
                total_conns += 1
    return {"unique_clients": len(external_ips), "connections": total_conns}


def read_cpu_stats() -> tuple:
    global PREV_CPU_LINES
    total_pct = 0.0
    per_core = []
    try:
        lines = Path("/proc/stat").read_text().splitlines()
        for line in lines:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            label = parts[0]
            vals = [int(x) for x in parts[1:8]]
            idle = vals[3] + vals[4]
            total = sum(vals)
            prev = PREV_CPU_LINES.get(label)
            PREV_CPU_LINES[label] = (total, idle)
            pct = 0.0
            if prev is not None:
                dt = total - prev[0]
                di = idle - prev[1]
                if dt > 0:
                    pct = round(100.0 * (dt - di) / dt, 1)
            if label == "cpu":
                total_pct = pct
            else:
                per_core.append(pct)
    except Exception:
        pass
    return total_pct, per_core


def read_net_rates() -> dict:
    global PREV_NET
    now = time.time()
    rx_bytes = tx_bytes = 0
    ifaces = []
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            name = name.strip()
            if name == "lo" or name.startswith("veth") or name.startswith("br-") or name.startswith("docker"):
                continue
            cols = rest.split()
            rx = int(cols[0])
            tx = int(cols[8])
            rx_bytes += rx
            tx_bytes += tx
            ifaces.append(name)
    except Exception:
        pass

    rx_kbps = tx_kbps = 0.0
    if PREV_NET is not None:
        dt = now - PREV_NET[0]
        if dt > 0.1:
            rx_kbps = round(max(0.0, (rx_bytes - PREV_NET[1]) / dt / 1024.0), 1)
            tx_kbps = round(max(0.0, (tx_bytes - PREV_NET[2]) / dt / 1024.0), 1)
    PREV_NET = (now, rx_bytes, tx_bytes)
    return {
        "rx_kbps": rx_kbps,
        "tx_kbps": tx_kbps,
        "rx_gb": round(rx_bytes / (1024 ** 3), 2),
        "tx_gb": round(tx_bytes / (1024 ** 3), 2),
        "ifaces": ifaces[:3],
    }


def read_disk_io() -> dict:
    global PREV_DISK
    now = time.time()
    read_sec = write_sec = 0
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            cols = line.split()
            if len(cols) >= 14 and cols[2] == "nvme0n1":
                read_sec = int(cols[5])
                write_sec = int(cols[9])
                break
    except Exception:
        pass
    read_mbs = write_mbs = 0.0
    if PREV_DISK is not None:
        dt = now - PREV_DISK[0]
        if dt > 0.1:
            read_mbs = round(max(0.0, (read_sec - PREV_DISK[1]) * 512 / dt / (1024 ** 2)), 2)
            write_mbs = round(max(0.0, (write_sec - PREV_DISK[2]) * 512 / dt / (1024 ** 2)), 2)
    PREV_DISK = (now, read_sec, write_sec)
    return {"read_mbs": read_mbs, "write_mbs": write_mbs}


def read_top_procs() -> list:
    procs = []
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,user,%cpu,%mem,rss,comm", "--sort=-%cpu"],
            text=True,
            timeout=2,
        )
        for line in out.splitlines()[1:9]:
            cols = line.split(None, 5)
            if len(cols) == 6:
                procs.append({
                    "pid": cols[0],
                    "user": cols[1],
                    "cpu": float(cols[2]),
                    "mem": float(cols[3]),
                    "rss_mb": round(int(cols[4]) / 1024.0, 1),
                    "cmd": cols[5],
                })
    except Exception:
        pass
    return procs


def _read_grace_numa() -> dict:
    try:
        nodes = [p for p in Path("/sys/devices/system/node").glob("node[0-9]*") if (p / "cpulist").read_text(encoding="utf-8", errors="ignore").strip()]
        if len(nodes) == 1:
            vals = {}
            for line in (nodes[0] / "meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    vals[parts[2].rstrip(":")] = int(parts[3]) * 1024
            tot = vals.get("MemTotal", 0)
            free = vals.get("MemFree", 0)
            used = max(0, tot - free)
            if tot > 0:
                return {
                    "node": nodes[0].name,
                    "total_gib": round(tot / (1024 ** 3), 1),
                    "free_gib": round(free / (1024 ** 3), 1),
                    "used_gib": round(used / (1024 ** 3), 1),
                    "pct": round(100.0 * used / tot, 1),
                }
    except Exception:
        pass
    return {}


def _read_highspeed_nics() -> float:
    max_mbps = 0
    try:
        for p in Path("/sys/class/net").iterdir():
            if p.name == "lo":
                continue
            sp = p / "speed"
            if sp.is_file():
                try:
                    val = int(sp.read_text(encoding="utf-8", errors="ignore").strip())
                    if val > max_mbps:
                        max_mbps = val
                except Exception:
                    pass
    except Exception:
        pass
    return round(max_mbps / 1000.0, 1) if max_mbps > 0 else 0.0


def collect_system_metrics() -> tuple:
    cpu_pct, per_core = read_cpu_stats()
    cores = os.cpu_count() or 1
    try:
        load1, load5, load15 = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load1 = load5 = load15 = 0.0

    uptime_s = 0
    try:
        uptime_s = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        pass

    ram = {
        "total_gib": 0, "used_gib": 0, "avail_gib": 0,
        "cached_gib": 0, "swap_total_gib": 0, "swap_used_gib": 0, "pct": 0,
    }
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            meminfo[k] = int(v.strip().split()[0])
        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", 0)
        cached_kb = meminfo.get("Cached", 0) + meminfo.get("Buffers", 0)
        swap_total_kb = meminfo.get("SwapTotal", 0)
        swap_free_kb = meminfo.get("SwapFree", 0)
        used_kb = max(0, total_kb - avail_kb)
        if total_kb > 0:
            ram = {
                "total_gib": round(total_kb / (1024 * 1024), 1),
                "used_gib": round(used_kb / (1024 * 1024), 1),
                "avail_gib": round(avail_kb / (1024 * 1024), 1),
                "cached_gib": round(cached_kb / (1024 * 1024), 1),
                "swap_total_gib": round(swap_total_kb / (1024 * 1024), 1),
                "swap_used_gib": round(max(0, swap_total_kb - swap_free_kb) / (1024 * 1024), 1),
                "pct": round(100.0 * used_kb / total_kb, 1),
            }
    except Exception:
        pass

    disk = {"total_gb": 0, "used_gb": 0, "free_gb": 0, "pct": 0}
    try:
        du = shutil.disk_usage(str(Path.home()))
        disk = {
            "total_gb": round(du.total / (1024 ** 3), 1),
            "used_gb": round(du.used / (1024 ** 3), 1),
            "free_gb": round(du.free / (1024 ** 3), 1),
            "pct": round(100.0 * du.used / du.total, 1) if du.total else 0,
        }
    except Exception:
        pass

    gpu = {
        "name": "NVIDIA GB10", "util_pct": None, "temp_c": None,
        "power_w": None, "driver": "", "sm_mhz": None, "pstate": "",
        "hbm_used_gib": None, "hbm_total_gib": None, "hbm_free_gib": None, "hbm_pct": None,
    }
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,utilization.gpu,temperature.gpu,power.draw,clocks.current.sm,pstate,memory.used,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=3,
        ).strip()
        if out:
            row = _select_gpu_row(out.splitlines())
            cols = [c.strip() for c in row.split(",")] if row else []
            def _num(val):
                try:
                    return round(float(val), 1)
                except Exception:
                    return None
            hbm_used_mib = _num(cols[7]) if len(cols) > 7 else None
            hbm_tot_mib = _num(cols[8]) if len(cols) > 8 else None
            hbm_free_mib = _num(cols[9]) if len(cols) > 9 else None
            gpu = {
                "name": cols[0],
                "driver": cols[1] if len(cols) > 1 else "",
                "util_pct": _num(cols[2]) if len(cols) > 2 else None,
                "temp_c": _num(cols[3]) if len(cols) > 3 else None,
                "power_w": _num(cols[4]) if len(cols) > 4 else None,
                "sm_mhz": _num(cols[5]) if len(cols) > 5 else None,
                "pstate": cols[6] if len(cols) > 6 else "",
                "hbm_used_gib": round(hbm_used_mib / 1024.0, 1) if hbm_used_mib is not None else None,
                "hbm_total_gib": round(hbm_tot_mib / 1024.0, 1) if hbm_tot_mib is not None else None,
                "hbm_free_gib": round(hbm_free_mib / 1024.0, 1) if hbm_free_mib is not None else None,
                "hbm_pct": round(100.0 * hbm_used_mib / hbm_tot_mib, 1) if hbm_used_mib is not None and hbm_tot_mib else None,
            }
    except Exception:
        pass

    gpu_apps = []
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
            text=True,
            timeout=3,
        )
        for line in out.splitlines():
            if line.strip():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_apps.append({"pid": parts[0], "name": Path(parts[1]).name, "mem": parts[2]})
    except Exception:
        pass

    net = read_net_rates()
    net["max_speed_gbps"] = _read_highspeed_nics()
    grace = _read_grace_numa()
    disk_io = read_disk_io()
    top_procs = read_top_procs()
    ports_map = [{"port": p, "label": lbl, "open": port_open(p)} for p, lbl in WATCH_PORTS]

    sample = {
        "ts": time.strftime("%H:%M:%S"),
        "cpu": cpu_pct,
        "ram": ram["pct"],
        "ram_gib": ram["used_gib"],
        "gpu": gpu["util_pct"] if gpu["util_pct"] is not None else 0.0,
        "temp": gpu["temp_c"] if gpu["temp_c"] is not None else 0.0,
        "power": gpu["power_w"] if gpu["power_w"] is not None else 0.0,
        "rx": net["rx_kbps"],
        "tx": net["tx_kbps"],
    }

    hw = detect_hw_profile(gpu.get("name", ""))
    sys_data = {
        "hostname": socket.gethostname(),
        "hw": hw,
        "uptime_s": uptime_s,
        "cpu": {
            "pct": cpu_pct, "cores": cores, "per_core": per_core,
            "load1": load1, "load5": load5, "load15": load15,
        },
        "ram": ram,
        "grace": grace,
        "disk": disk,
        "disk_io": disk_io,
        "net": net,
        "gpu": gpu,
        "gpu_apps": gpu_apps,
        "top_procs": top_procs,
        "ports_map": ports_map,
    }
    return sys_data, sample


def _load_token_ledger() -> dict:
    if TOKEN_LEDGER_FILE.exists():
        try:
            data = json.loads(TOKEN_LEDGER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("rates", dict(DEFAULT_TOKEN_RATES))
                data.setdefault("by_demo", {})
                return data
        except Exception:
            pass
    return {"rates": dict(DEFAULT_TOKEN_RATES), "by_demo": {}, "updated_at": int(time.time())}


def _save_token_ledger(data: dict) -> None:
    try:
        DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = int(time.time())
        TOKEN_LEDGER_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _calc_eur(inp: int, out: int, rates: dict) -> float:
    in_rate = float(rates.get("input_eur_per_1m", DEFAULT_TOKEN_RATES["input_eur_per_1m"]))
    out_rate = float(rates.get("output_eur_per_1m", DEFAULT_TOKEN_RATES["output_eur_per_1m"]))
    return round((inp * in_rate + out * out_rate) / 1_000_000.0, 4)


def _probe_llama_metrics(ports: tuple = (8001, 8081, 8082, 8000, 18000, 18100)) -> tuple:
    total_in, total_out = 0, 0
    for p in ports:
        if not port_open(p, timeout=0.1):
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/metrics", timeout=0.35) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            for line in body.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                if line.startswith("llamacpp:prompt_tokens_total"):
                    total_in += int(float(line.split()[-1]))
                elif line.startswith("llamacpp:tokens_predicted_total"):
                    total_out += int(float(line.split()[-1]))
        except Exception:
            pass
    return total_in, total_out


def _probe_nano_collective_tokens() -> tuple:
    if not port_open(18808, timeout=0.15):
        return 0, 0
    for path in ("/api/economics", "/api/telemetry"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:18808{path}", timeout=0.45) as resp:
                obj = json.loads(resp.read().decode("utf-8"))
            if isinstance(obj, dict):
                inp = obj.get("input_tokens") or obj.get("total_input_tokens") or 0
                out = obj.get("output_tokens") or obj.get("total_output_tokens") or 0
                if not inp and isinstance(obj.get("usage"), dict):
                    inp = obj["usage"].get("input_tokens", 0)
                    out = obj["usage"].get("output_tokens", 0)
                if not inp and isinstance(obj.get("totals"), dict):
                    inp = obj["totals"].get("input_tokens", 0)
                    out = obj["totals"].get("output_tokens", 0)
                if inp or out:
                    return int(inp), int(out)
        except Exception:
            pass
    return 0, 0


def _parse_vllm_metrics_text(body: str) -> tuple:
    total_in, total_out = 0, 0
    for line in (body or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if line.startswith("vllm:prompt_tokens_total"):
            try:
                total_in += int(float(line.split()[-1]))
            except (ValueError, IndexError):
                pass
        elif line.startswith("vllm:generation_tokens_total"):
            try:
                total_out += int(float(line.split()[-1]))
            except (ValueError, IndexError):
                pass
    return total_in, total_out


def _probe_vllm_metrics(ports: tuple = (8000, 8100)) -> tuple:
    total_in, total_out = 0, 0
    for p in ports:
        if not port_open(p, timeout=0.1):
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/metrics", timeout=0.35) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            p_in, p_out = _parse_vllm_metrics_text(body)
            total_in += p_in
            total_out += p_out
        except Exception:
            pass
    return total_in, total_out


def _probe_fury_operator_tokens() -> tuple:
    if not port_open(8080, timeout=0.15):
        return 0, 0, None
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/live", timeout=0.45) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        if isinstance(obj, dict):
            totals = obj.get("totals") or {}
            inp = sum(int(v.get("input", 0) or 0) for v in totals.values() if isinstance(v, dict))
            out = sum(int(v.get("output", 0) or 0) for v in totals.values() if isinstance(v, dict))
            tps = (obj.get("telemetry") or {}).get("tokens_per_second")
            return inp, out, tps
    except Exception:
        pass
    return 0, 0, None


def collect_token_metrics(active_key: str | None) -> None:
    if not active_key:
        _LAST_LLAMA_SAMPLE["key"] = None
        _LAST_LLAMA_SAMPLE["in"] = 0
        _LAST_LLAMA_SAMPLE["out"] = 0
        _LAST_FURY_SAMPLE["key"] = None
        _LAST_FURY_SAMPLE["in"] = 0
        _LAST_FURY_SAMPLE["out"] = 0
        _LAST_VLLM_SAMPLE["key"] = None
        _LAST_VLLM_SAMPLE["in"] = 0
        _LAST_VLLM_SAMPLE["out"] = 0
        return
    d_in, d_out = 0, 0
    if _is_fury_key(active_key):
        f_in, f_out, f_tps = _probe_fury_operator_tokens()
        v_in, v_out = _probe_vllm_metrics((8000, 8100))
        if v_in or v_out:
            if _LAST_VLLM_SAMPLE["key"] == active_key and v_in >= _LAST_VLLM_SAMPLE["in"] and v_out >= _LAST_VLLM_SAMPLE["out"]:
                d_in += v_in - _LAST_VLLM_SAMPLE["in"]
                d_out += v_out - _LAST_VLLM_SAMPLE["out"]
            elif _LAST_VLLM_SAMPLE["key"] != active_key:
                d_in += v_in
                d_out += v_out
            _LAST_VLLM_SAMPLE.update({"key": active_key, "in": v_in, "out": v_out})
        elif f_in or f_out:
            if _LAST_FURY_SAMPLE["key"] == active_key and f_in >= _LAST_FURY_SAMPLE["in"] and f_out >= _LAST_FURY_SAMPLE["out"]:
                d_in += f_in - _LAST_FURY_SAMPLE["in"]
                d_out += f_out - _LAST_FURY_SAMPLE["out"]
            elif _LAST_FURY_SAMPLE["key"] != active_key:
                d_in += f_in
                d_out += f_out
            _LAST_FURY_SAMPLE.update({"key": active_key, "in": f_in, "out": f_out})
        if f_tps is not None and float(f_tps) > 0:
            _LIVE_TOK_S["val"] = round(float(f_tps), 1)
    elif active_key == "nano-collective":
        n_in, n_out = _probe_nano_collective_tokens()
        if n_in >= _LAST_NANO_SAMPLE["in"] and n_out >= _LAST_NANO_SAMPLE["out"]:
            d_in += n_in - _LAST_NANO_SAMPLE["in"]
            d_out += n_out - _LAST_NANO_SAMPLE["out"]
        else:
            d_in += n_in
            d_out += n_out
        _LAST_NANO_SAMPLE["in"] = n_in
        _LAST_NANO_SAMPLE["out"] = n_out
        if d_in == 0 and d_out == 0:
            l_in, l_out = _probe_llama_metrics((18000, 18100))
            if _LAST_LLAMA_SAMPLE["key"] == active_key and l_in >= _LAST_LLAMA_SAMPLE["in"] and l_out >= _LAST_LLAMA_SAMPLE["out"]:
                d_in += l_in - _LAST_LLAMA_SAMPLE["in"]
                d_out += l_out - _LAST_LLAMA_SAMPLE["out"]
            elif _LAST_LLAMA_SAMPLE["key"] != active_key:
                d_in += l_in
                d_out += l_out
            _LAST_LLAMA_SAMPLE.update({"key": active_key, "in": l_in, "out": l_out})
    else:
        l_in, l_out = _probe_llama_metrics((8001, 8081, 8082, 8000))
        if _LAST_LLAMA_SAMPLE["key"] == active_key:
            d_in = max(0, l_in - _LAST_LLAMA_SAMPLE["in"]) if l_in >= _LAST_LLAMA_SAMPLE["in"] else l_in
            d_out = max(0, l_out - _LAST_LLAMA_SAMPLE["out"]) if l_out >= _LAST_LLAMA_SAMPLE["out"] else l_out
        else:
            d_in, d_out = l_in, l_out
        _LAST_LLAMA_SAMPLE.update({"key": active_key, "in": l_in, "out": l_out})
    if d_in > 0 or d_out > 0:
        if not (_is_fury_key(active_key) and _LIVE_TOK_S["val"] > 0):
            _LIVE_TOK_S["val"] = round(d_out / 2.0, 1) if d_out > 0 else 0.0
        with TOKEN_LOCK:
            ledger = _load_token_ledger()
            entry = ledger["by_demo"].setdefault(active_key, {"input_tokens": 0, "output_tokens": 0})
            entry["input_tokens"] = int(entry.get("input_tokens", 0)) + d_in
            entry["output_tokens"] = int(entry.get("output_tokens", 0)) + d_out
            _save_token_ledger(ledger)
    elif not _is_fury_key(active_key):
        _LIVE_TOK_S["val"] = 0.0


_LIVE_TOK_S = {"val": 0.0, "ts": 0.0}
LIVE_EVENT_LOG = []
LIVE_EVENT_LOCK = threading.Lock()


def _push_live_event(kind: str, source: str, message: str) -> None:
    entry = {
        "ts": time.time(),
        "time": time.strftime("%H:%M:%S"),
        "kind": kind,
        "source": source,
        "message": message,
    }
    with LIVE_EVENT_LOCK:
        LIVE_EVENT_LOG.append(entry)
        if len(LIVE_EVENT_LOG) > 80:
            del LIVE_EVENT_LOG[:-80]


def record_token_report(payload: dict) -> dict:
    source = str(payload.get("source") or "external-demo").strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", source):
        return {"ok": False, "error": "Invalid source"}
    try:
        in_tok = max(0, int(payload.get("input_tokens") or 0))
        out_tok = max(0, int(payload.get("output_tokens") or 0))
        tps = max(0.0, float(payload.get("tok_per_s") or 0.0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid numeric values"}
    if in_tok > 0 or out_tok > 0:
        with TOKEN_LOCK:
            ledger = _load_token_ledger()
            entry = ledger["by_demo"].setdefault(source, {"input_tokens": 0, "output_tokens": 0})
            entry["input_tokens"] = int(entry.get("input_tokens", 0)) + in_tok
            entry["output_tokens"] = int(entry.get("output_tokens", 0)) + out_tok
            _save_token_ledger(ledger)
    if tps > 0:
        _LIVE_TOK_S["val"] = round(tps, 1)
        _LIVE_TOK_S["ts"] = time.time()
    elif out_tok > 0:
        _LIVE_TOK_S["val"] = round(out_tok / 2.0, 1)
        _LIVE_TOK_S["ts"] = time.time()
    if in_tok > 0 or out_tok > 0:
        _push_live_event(
            "TOKENS",
            source,
            f"+{in_tok + out_tok} tok (in={in_tok}, out={out_tok}) @ {_LIVE_TOK_S['val']:.1f} tok/s",
        )
    return {"ok": True, "source": source, "input_tokens": in_tok, "output_tokens": out_tok}


def get_token_summary() -> dict:
    with TOKEN_LOCK:
        ledger = _load_token_ledger()
    rates = ledger.get("rates", dict(DEFAULT_TOKEN_RATES))
    by_demo_out = {}
    tot_in, tot_out = 0, 0
    all_keys = list(DEMOS.keys()) + list(FURY_PROFILES.keys()) + ["fury"]
    for k in set(all_keys) | set(ledger.get("by_demo", {}).keys()):
        raw = ledger.get("by_demo", {}).get(k, {})
        inp = int(raw.get("input_tokens", 0))
        out = int(raw.get("output_tokens", 0))
        tot_in += inp
        tot_out += out
        by_demo_out[k] = {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "eur_saved": _calc_eur(inp, out, rates),
        }
    live_tps = _LIVE_TOK_S.get("val", 0.0)
    if _LIVE_TOK_S.get("ts") and (time.time() - float(_LIVE_TOK_S["ts"])) > 6.0 and not CURRENT.get("key"):
        live_tps = 0.0
    return {
        "total": {
            "input_tokens": tot_in,
            "output_tokens": tot_out,
            "total_tokens": tot_in + tot_out,
            "eur_saved": _calc_eur(tot_in, tot_out, rates),
            "tok_per_s": live_tps,
        },
        "by_demo": by_demo_out,
        "rates": rates,
    }


def reset_token_ledger() -> dict:
    with TOKEN_LOCK:
        ledger = _load_token_ledger()
        ledger["by_demo"] = {}
        _save_token_ledger(ledger)
    _LAST_LLAMA_SAMPLE.update({"key": None, "in": 0, "out": 0})
    _LAST_NANO_SAMPLE.update({"in": 0, "out": 0})
    return {"ok": True, "tokens": get_token_summary()}


def update_token_rates(in_eur: float, out_eur: float) -> dict:
    with TOKEN_LOCK:
        ledger = _load_token_ledger()
        ledger["rates"] = {
            "input_eur_per_1m": max(0.0, float(in_eur)),
            "output_eur_per_1m": max(0.0, float(out_eur)),
            "usd_to_eur": DEFAULT_TOKEN_RATES["usd_to_eur"],
        }
        _save_token_ledger(ledger)
    return {"ok": True, "tokens": get_token_summary()}


def telemetry_loop():
    while True:
        try:
            sys_data, sample = collect_system_metrics()
            with TELEM_LOCK:
                LATEST_SYS.clear()
                LATEST_SYS.update(sys_data)
                HISTORY.append(sample)
            with STATE_LOCK:
                active_key = CURRENT.get("key")
            collect_token_metrics(active_key)
        except Exception:
            pass
        time.sleep(2.0)


def get_status() -> dict:
    cfg = load_config()
    with STATE_LOCK:
        key = CURRENT["key"]
        proc = CURRENT["proc"]
        if proc is not None:
            rc = proc.poll()
            if rc is not None:
                port = 8080 if _is_fury_key(key) else DEMOS[key]["port"]
                if rc == 0 and (key == "nano-collective" or _is_fury_key(key)) and port_open(port):
                    if CURRENT["log_handle"] is not None:
                        try:
                            CURRENT["log_handle"].close()
                        except OSError:
                            pass
                    CURRENT["proc"] = None
                    CURRENT["log_handle"] = None
                    CURRENT["status"] = "ready"
                    CURRENT["last_error"] = ""
                else:
                    if CURRENT["status"] not in ("idle", "stopping"):
                        CURRENT["last_error"] = f"Demo '{key}' exited with code {rc}. Check logs."
                    if CURRENT["log_handle"] is not None:
                        try:
                            CURRENT["log_handle"].close()
                        except OSError:
                            pass
                    CURRENT["proc"] = None
                    CURRENT["log_handle"] = None
                    CURRENT["key"] = None
                    CURRENT["started_at"] = None
                    CURRENT["status"] = "idle"
                    key = None
            else:
                port = 8080 if _is_fury_key(key) else DEMOS[key]["port"]
                CURRENT["status"] = "ready" if port_open(port) else "starting"
        elif key == "nano-collective" or _is_fury_key(key):
            port = 8080 if _is_fury_key(key) else DEMOS[key]["port"]
            if port_open(port):
                CURRENT["status"] = "ready"
            else:
                CURRENT["key"] = None
                CURRENT["started_at"] = None
                CURRENT["status"] = "idle"
        elif key is None and port_open(18808):
            CURRENT["key"] = "nano-collective"
            CURRENT["started_at"] = CURRENT["started_at"] or time.time()
            CURRENT["status"] = "ready"
        elif key is None and port_open(8080) and (FURY_DIR / "launch.sh").exists():
            CURRENT["key"] = _active_fury_demo_key()
            CURRENT["started_at"] = CURRENT["started_at"] or time.time()
            CURRENT["status"] = "ready"
        active = {
            "key": CURRENT["key"],
            "status": CURRENT["status"],
            "pid": CURRENT["proc"].pid if CURRENT["proc"] else None,
            "uptime_s": int(time.time() - CURRENT["started_at"]) if CURRENT["started_at"] else 0,
            "last_error": CURRENT["last_error"],
        }
    with TELEM_LOCK:
        sys_snapshot = dict(LATEST_SYS)
        hist_snapshot = list(HISTORY)
    if not sys_snapshot:
        sys_snapshot, _ = collect_system_metrics()
    hw_model = sys_snapshot.get("hw", {}).get("hw_model", "")
    fury_installed = (FURY_DIR / "launch.sh").exists()
    nano_checks = {
        "campus": (KIT_DIR / "portable.py").exists() and (DEMOS_ROOT / "campuscompass-local/.venv/bin/python").exists(),
        "atlas": (KIT_DIR / "portable.py").exists() and (DEMOS_ROOT / "atlas-claude/.venv/bin/python").exists(),
        "guard": (KIT_DIR / "portable.py").exists() and (DEMOS_ROOT / "beat-the-guard/.venv/bin/python").exists(),
        "vision": (KIT_DIR / "portable.py").exists() and (DEMOS_ROOT / "research-observatory/.venv/bin/python").exists(),
        "nano-collective": (NANO_COLLECTIVE_DIR / "launch.sh").exists() and (NANO_COLLECTIVE_DIR / ".venv/bin/python").exists(),
    }
    if hw_model == "ZGX-FURY" or (fury_installed and not any(nano_checks.values())):
        demos_info = {
            pk: {
                **pmeta,
                "installed": fury_installed,
                "port_listening": port_open(8080),
            }
            for pk, pmeta in FURY_PROFILES.items()
        }
        cern_installed = (CERN_DEMO_DIR / ".venv/bin/python").exists() and (CERN_DEMO_DIR / "backend").exists()
        if cern_installed or port_open(8900):
            demos_info["cern-lhc"] = {
                **CERN_DEMO,
                "installed": cern_installed,
                "port_listening": port_open(8900),
            }
    else:
        demos_info = {
            k: {
                **meta,
                "installed": nano_checks.get(k, False),
                "port_listening": port_open(meta["port"]),
                **({"nano_mode": _probe_nano_mode() if port_open(meta["port"]) else ""} if k == "nano-collective" else {}),
            }
            for k, meta in DEMOS.items()
        }
    services_info = list_all_services()
    demo_log = read_tail(LOG_FILE, 80)
    install_active, install_summary, install_log = _detect_install_activity()
    if not active.get("key") and install_log and (install_active or not demo_log):
        demo_log = install_log
    with LIVE_EVENT_LOCK:
        local_events = list(LIVE_EVENT_LOG[-30:])
    return {
        "role": cfg["role"],
        "active": active,
        "install_active": install_active,
        "install_summary": install_summary,
        "concurrent_users": _count_active_demo_clients(),
        "recent_events": local_events,
        "services": services_info,
        "demos": demos_info,
        "log": demo_log,
        "system": sys_snapshot,
        "history": hist_snapshot,
        "tokens": get_token_summary(),
    }


def _node_http_json(host: str, port: int, path: str, token: str, method: str = "GET", timeout: float = 2.0, body: dict = None) -> dict:
    url = f"http://{host}:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else (b"{}" if method == "POST" else None)
    req = urllib.request.Request(url, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-ZGX-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get_local_ip() -> str:
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "route", "get", "1.1.1.1"], text=True, timeout=2)
        m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "127.0.0.1"


def _identity_payload(cfg: dict = None) -> dict:
    cfg = cfg or load_config()
    return {
        "id": cfg.get("node_id") or f"{socket.gethostname()}-{cfg['node_token'][:8]}",
        "hostname": socket.gethostname(),
        "port": LISTEN_PORT,
        "role": cfg.get("role", "hub"),
        "hub_epoch": int(cfg.get("hub_epoch", 0)),
        "token_sha256": _token_digest(cfg.get("node_token", "")),
    }


def _probe_http_candidate(ip: str, port: int = LISTEN_PORT, timeout: float = 0.4) -> dict | None:
    try:
        req = urllib.request.Request(f"http://{ip}:{port}/api/node/probe", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("ok") is True and data.get("token_sha256"):
                data["host"] = ip
                data.setdefault("port", port)
                return data
    except Exception:
        return None
    return None


def _broadcast_targets(local_ip: str) -> list[str]:
    targets = ["255.255.255.255"]
    try:
        net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        bcast = str(net.broadcast_address)
        if bcast not in targets:
            targets.append(bcast)
    except ValueError:
        pass
    return targets


def discover_zgx_candidates(timeout: float = 0.6, allow_subnet_scan: bool = True) -> list[dict]:
    cfg = load_config()
    local_digest = _token_digest(cfg["node_token"])
    candidates = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.15)
        query = json.dumps({"type": "zgx-discover", "from_id": cfg.get("node_id", "")}).encode("utf-8")
        for target in _broadcast_targets(get_local_ip()):
            try:
                sock.sendto(query, (target, DISCOVERY_PORT))
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "zgx-here":
                continue
            digest = msg.get("token_sha256", "")
            if not digest or digest == local_digest:
                continue
            candidates[digest] = {
                "host": addr[0],
                "port": int(msg.get("port", LISTEN_PORT)),
                "id": msg.get("id", ""),
                "hostname": msg.get("hostname", ""),
                "role": msg.get("role", "node"),
                "hub_epoch": int(msg.get("hub_epoch", 0)),
                "token_sha256": digest,
            }
    finally:
        sock.close()

    if not candidates and allow_subnet_scan:
        local_ip = get_local_ip()
        try:
            net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
            ips = [str(ip) for ip in net.hosts() if str(ip) != local_ip]
            with ThreadPoolExecutor(max_workers=48) as pool:
                for res in pool.map(lambda ip: _probe_http_candidate(ip, LISTEN_PORT, 0.35), ips):
                    if res and res.get("token_sha256") != local_digest:
                        candidates[res["token_sha256"]] = res
        except ValueError:
            pass

    return list(candidates.values())


def discovery_loop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(1.0)
    except OSError as exc:
        print(f"[WARN] Descubrimiento UDP deshabilitado en puerto {DISCOVERY_PORT}: {exc}", flush=True)
        sock.close()
        return
    try:
        while True:
            try:
                raw, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("type") == "zgx-discover":
                reply = {"type": "zgx-here", **_identity_payload()}
                try:
                    sock.sendto(json.dumps(reply).encode("utf-8"), addr)
                except OSError:
                    pass
    finally:
        sock.close()


def reconcile_fleet_ips(missing_hosts: list[str] = None, allow_subnet_scan: bool = True) -> dict:
    nodes = load_fleet()
    if not nodes:
        return {"updated": [], "discovered_hub": None}
    candidates = discover_zgx_candidates(allow_subnet_scan=allow_subnet_scan)
    by_digest = {c["token_sha256"]: c for c in candidates if c.get("token_sha256")}
    updated = []
    discovered_hub = None
    changed = False
    for node in nodes:
        tok = node.get("token", "")
        if not tok:
            continue
        if missing_hosts is not None and node.get("host") not in missing_hosts and node.get("role") != "hub":
            continue
        cand = by_digest.get(_token_digest(tok))
        if not cand:
            continue
        new_host = cand["host"]
        new_port = int(cand.get("port", node.get("port", 18888)))
        try:
            role_info = _node_http_json(new_host, new_port, "/api/role", tok, timeout=1.5)
        except (OSError, ValueError):
            continue
        if not isinstance(role_info, dict) or role_info.get("ok") is not True:
            continue
        old_host = node.get("host", "")
        if new_host != old_host or new_port != int(node.get("port", 18888)):
            if node.get("alias") == old_host:
                node["alias"] = new_host
            node["host"] = new_host
            node["port"] = new_port
            updated.append((old_host, new_host))
            changed = True
        remote_role = role_info.get("role")
        if remote_role in ("hub", "node") and node.get("role") != remote_role:
            node["role"] = remote_role
            changed = True
        if remote_role == "hub":
            discovered_hub = dict(node)
            discovered_hub["hub_epoch"] = int(role_info.get("hub_epoch", cand.get("hub_epoch", 0)))
    if changed:
        save_fleet(nodes)
    return {"updated": updated, "discovered_hub": discovered_hub}


def handle_fleet_announce(payload: dict) -> dict:
    cfg = load_config()
    if cfg.get("role") != "hub":
        return {"ok": False, "error": "Solo el Master acepta anuncios de IP."}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Payload invalido."}
    digest = payload.get("token_sha256", "")
    new_host = str(payload.get("host", "")).strip()
    new_port = int(payload.get("port", LISTEN_PORT) or LISTEN_PORT)
    if not digest or not new_host:
        return {"ok": False, "error": "Faltan datos de identidad."}
    nodes = load_fleet()
    matched = None
    for node in nodes:
        if node.get("token") and hmac.compare_digest(_token_digest(node["token"]), digest):
            matched = node
            break
    if not matched:
        return {"ok": False, "error": "Nodo no reconocido en la flota."}
    old_host = matched.get("host", "")
    if matched.get("alias") == old_host:
        matched["alias"] = new_host
    matched["host"] = new_host
    matched["port"] = new_port
    if payload.get("hostname") and matched.get("alias") == old_host:
        matched["alias"] = new_host
    save_fleet(nodes)
    return {"ok": True, "host": new_host, "port": new_port}


def announce_self_to_hub(hub: dict) -> bool:
    if not hub or not hub.get("host") or not hub.get("token"):
        return False
    cfg = load_config()
    local_ip = get_local_ip()
    body = {
        "id": cfg.get("node_id", ""),
        "token_sha256": _token_digest(cfg["node_token"]),
        "host": local_ip,
        "port": LISTEN_PORT,
        "hostname": socket.gethostname(),
    }
    try:
        res = _node_http_json(
            hub["host"], hub.get("port", 18888), "/api/fleet/announce",
            hub["token"], method="POST", timeout=2.0, body=body,
        )
        if isinstance(res, dict) and res.get("ok") is True:
            with RESILIENCE_LOCK:
                _RESILIENCE_STATE["last_announced_ip"] = local_ip
            return True
    except (OSError, ValueError):
        pass
    return False


def _should_step_down_hub(local_cfg: dict, peer_role_info: dict, peer_token: str) -> bool:
    if peer_role_info.get("role") != "hub":
        return False
    local_epoch = int(local_cfg.get("hub_epoch", 0))
    peer_epoch = int(peer_role_info.get("hub_epoch", 0))
    if peer_epoch != local_epoch:
        return peer_epoch > local_epoch
    return _token_digest(peer_token) < _token_digest(local_cfg.get("node_token", ""))


def _elect_master_if_winner(nodes: list[dict], max_seen_epoch: int = 0) -> dict | None:
    cfg = load_config()
    local_ip = get_local_ip()
    candidates = [(_token_digest(cfg["node_token"]), cfg.get("node_id", ""), local_ip, True)]
    for n in nodes:
        if not n.get("host") or not n.get("token") or n.get("host") == local_ip:
            continue
        try:
            info = _node_http_json(n["host"], n.get("port", 18888), "/api/role", n["token"], timeout=1.5)
            if isinstance(info, dict) and info.get("ok") is True:
                max_seen_epoch = max(max_seen_epoch, int(info.get("hub_epoch", 0)))
                if info.get("role") == "hub":
                    return None
                candidates.append((_token_digest(n["token"]), n.get("id", ""), n["host"], False))
        except (OSError, ValueError):
            continue
    candidates.sort(key=lambda item: item[0])
    if not candidates or not candidates[0][3]:
        return None
    for n in nodes:
        if n.get("role") == "hub":
            n["role"] = "node"
    save_fleet(nodes)
    role_res = set_local_role("hub", min_epoch=max_seen_epoch)
    failed = sync_fleet_to_peers()
    print(f"[INFO] Failover automatico: este nodo ({local_ip}) asume el rol de Master (epoch={role_res.get('hub_epoch')}).", flush=True)
    return {"ok": True, "promoted": True, "hub_epoch": role_res.get("hub_epoch"), "sync_failed": failed}


def check_master_and_failover(allow_subnet_scan: bool = True) -> dict:
    cfg = load_config()
    nodes = load_fleet()
    local_ip = get_local_ip()

    if cfg.get("role") == "hub":
        for n in nodes:
            if not n.get("host") or not n.get("token") or n.get("host") == local_ip:
                continue
            try:
                info = _node_http_json(n["host"], n.get("port", 18888), "/api/role", n["token"], timeout=1.5)
                if isinstance(info, dict) and info.get("ok") is True and _should_step_down_hub(cfg, info, n["token"]):
                    n["role"] = "hub"
                    save_fleet(nodes)
                    set_local_role("node")
                    announce_self_to_hub(n)
                    print(f"[INFO] Split-brain resuelto: {n['host']} es Master activo; este nodo pasa a node.", flush=True)
                    return {"ok": True, "stepped_down_to": n["host"]}
            except (OSError, ValueError):
                continue
        return {"ok": True, "role": "hub"}

    hubs = [n for n in nodes if n.get("role") == "hub" and n.get("host") != local_ip]
    active_hub = None
    max_seen_epoch = int(cfg.get("hub_epoch", 0))
    for h in hubs:
        try:
            info = _node_http_json(h["host"], h.get("port", 18888), "/api/role", h.get("token", ""), timeout=2.0)
            if isinstance(info, dict) and info.get("ok") is True:
                max_seen_epoch = max(max_seen_epoch, int(info.get("hub_epoch", 0)))
                if info.get("role") == "hub":
                    active_hub = h
                    break
        except (OSError, ValueError):
            continue

    if not active_hub:
        rec = reconcile_fleet_ips(allow_subnet_scan=allow_subnet_scan)
        if rec.get("discovered_hub"):
            active_hub = rec["discovered_hub"]
            nodes = load_fleet()

    if active_hub:
        with RESILIENCE_LOCK:
            _RESILIENCE_STATE["master_failures"] = 0
            last_ip = _RESILIENCE_STATE["last_announced_ip"]
        if last_ip != local_ip:
            announce_self_to_hub(active_hub)
        return {"ok": True, "role": "node", "hub": active_hub["host"]}

    with RESILIENCE_LOCK:
        _RESILIENCE_STATE["master_failures"] += 1
        failures = _RESILIENCE_STATE["master_failures"]

    if failures >= MASTER_FAILOVER_THRESHOLD:
        elected = _elect_master_if_winner(nodes, max_seen_epoch=max_seen_epoch)
        if elected:
            with RESILIENCE_LOCK:
                _RESILIENCE_STATE["master_failures"] = 0
            return elected
    return {"ok": False, "role": "node", "master_failures": failures}


def fleet_resilience_loop() -> None:
    while True:
        try:
            check_master_and_failover()
        except Exception as exc:
            print(f"[WARN] Comprobacion de resiliencia de flota: {exc}", flush=True)
        time.sleep(RESILIENCE_INTERVAL)


def sync_fleet_to_peers() -> list[str]:
    cfg = load_config()
    if cfg.get("role") != "hub":
        return []
    nodes = load_fleet()
    local_ip = get_local_ip()
    self_peer = {
        "id": cfg.get("node_id") or ("hub-" + socket.gethostname()),
        "alias": socket.gethostname(),
        "tag": "HUB",
        "host": local_ip,
        "port": LISTEN_PORT,
        "token": cfg["node_token"],
        "role": "hub",
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    def _push_to_target(target_nodes: list[dict]) -> tuple[list[str], list[str]]:
        full_ring = [self_peer] + [n for n in target_nodes if n.get("host") != local_ip]
        users_snapshot = cfg.get("users", {})
        try:
            ssh_creds_snapshot = _load_ssh_credentials()
        except Exception:
            ssh_creds_snapshot = {}
        try:
            rates_snapshot = _load_token_ledger().get("rates", dict(DEFAULT_TOKEN_RATES))
        except Exception:
            rates_snapshot = dict(DEFAULT_TOKEN_RATES)
        failed_list = []
        unreachable_list = []
        for n in target_nodes:
            if n.get("host") == local_ip:
                continue
            peers_for_target = [p for p in full_ring if p.get("host") != n.get("host")]
            try:
                peer_role = _node_http_json(n["host"], n.get("port", 18888), "/api/role",
                                            n.get("token", ""), timeout=1.5)
                if not isinstance(peer_role, dict) or peer_role.get("ok") is not True or peer_role.get("role") != "node":
                    failed_list.append(n["host"])
                    continue
                result = _node_http_json(
                    n["host"],
                    n.get("port", 18888),
                    "/api/fleet/sync",
                    n.get("token", ""),
                    method="POST",
                    timeout=1.5,
                    body={
                        "peers": peers_for_target,
                        "users": users_snapshot,
                        "ssh_credentials": ssh_creds_snapshot,
                        "token_rates": rates_snapshot,
                    },
                )
                if not isinstance(result, dict) or result.get("ok") is not True:
                    failed_list.append(n["host"])
            except (OSError, ValueError):
                failed_list.append(n["host"])
                unreachable_list.append(n["host"])
        return failed_list, unreachable_list

    failed, unreachable = _push_to_target(nodes)
    if unreachable:
        rec = reconcile_fleet_ips(missing_hosts=unreachable, allow_subnet_scan=(len(unreachable) >= len(nodes)))
        if rec.get("updated"):
            nodes = load_fleet()
            failed, _ = _push_to_target(nodes)
    return failed


def reorganize_fleet_action() -> dict:
    cfg = load_config()
    rec = reconcile_fleet_ips(allow_subnet_scan=True)
    sync_failed = []
    if cfg.get("role") == "hub":
        sync_failed = sync_fleet_to_peers()
    else:
        check_master_and_failover(allow_subnet_scan=True)
    return {
        "ok": True,
        "role": load_config().get("role", cfg.get("role")),
        "updated": rec.get("updated", []),
        "sync_failed": sync_failed,
    }


def start_all_nano_collective_action() -> dict:
    nodes = load_fleet()
    local_ip = get_local_ip()
    started = []
    failed = []

    def _is_nano(node: dict) -> bool:
        hw = str(node.get("hw_model") or node.get("tag") or "").upper()
        return "FURY" not in hw

    def _start_one(node: dict) -> tuple[str, bool, str]:
        label = node.get("alias") or node["host"]
        try:
            res = _node_http_json(
                node["host"], node.get("port", 18888),
                "/api/start/nano-collective", node.get("token", ""),
                method="POST", timeout=20.0,
            )
            if isinstance(res, dict) and res.get("ok") is True:
                return label, True, ""
            return label, False, str((res or {}).get("error") or "Error al iniciar")
        except Exception as exc:
            return label, False, str(exc)

    try:
        local_hw = get_status().get("system", {}).get("hw", {}).get("hw_model", "")
        if "FURY" not in str(local_hw).upper():
            r_loc = start_demo("nano-collective")
            if r_loc.get("ok"):
                started.append("local")
            else:
                failed.append({"host": "local", "error": r_loc.get("error", "Error")})
    except Exception:
        pass

    targets = [n for n in nodes if n.get("host") and n.get("host") != local_ip and _is_nano(n)]
    if targets:
        retry_nodes = []
        with ThreadPoolExecutor(max_workers=min(12, len(targets))) as pool:
            for node, (label, ok_flag, err_msg) in zip(targets, pool.map(_start_one, targets)):
                if ok_flag:
                    started.append(label)
                elif "timed out" in err_msg or "No route" in err_msg or "Connection" in err_msg:
                    retry_nodes.append((node, label, err_msg))
                else:
                    failed.append({"host": label, "error": err_msg})
        if retry_nodes:
            rec = reconcile_fleet_ips(missing_hosts=[n["host"] for n, _, _ in retry_nodes], allow_subnet_scan=True)
            if rec.get("updated"):
                updated_by_id = {n["id"]: n for n in load_fleet()}
                retry_targets = [updated_by_id.get(n["id"], n) for n, _, _ in retry_nodes]
                with ThreadPoolExecutor(max_workers=min(12, len(retry_targets))) as pool:
                    for label, ok_flag, err_msg in pool.map(_start_one, retry_targets):
                        if ok_flag:
                            started.append(label)
                        else:
                            failed.append({"host": label, "error": err_msg})
            else:
                for _, label, err_msg in retry_nodes:
                    failed.append({"host": label, "error": err_msg})

    return {"ok": True, "started": started, "failed": failed}


def apply_fleet_sync(payload: dict) -> dict:
    cfg = load_config()
    if cfg.get("role") != "node":
        return {"ok": False, "error": "El Master no acepta inventarios de otro portal."}
    peers = payload.get("peers")
    if not isinstance(peers, list) or not all(
        isinstance(p, dict) and isinstance(p.get("host"), str) and p["host"]
        and isinstance(p.get("id"), str) and p["id"] for p in peers
    ):
        return {"ok": False, "error": "Inventario de flota no valido."}
    local_ip = get_local_ip()
    save_fleet([p for p in peers if p["host"] != local_ip])
    users = payload.get("users")
    if isinstance(users, dict) and users:
        merged_users = dict(cfg.get("users", {}))
        for username, credentials in users.items():
            merged_users.setdefault(username, credentials)
        cfg["users"] = merged_users
        save_config(cfg)
    ssh_creds = payload.get("ssh_credentials")
    if isinstance(ssh_creds, dict) and ssh_creds:
        try:
            with SSH_CREDENTIALS_LOCK:
                merged_creds = _load_ssh_credentials()
                changed_creds = False
                for k, v in ssh_creds.items():
                    if k not in merged_creds and isinstance(v, str) and v:
                        merged_creds[k] = v
                        changed_creds = True
                if changed_creds:
                    DEMOS_ROOT.mkdir(parents=True, exist_ok=True)
                    SSH_CREDENTIALS_FILE.write_text(json.dumps(merged_creds, indent=2), encoding="utf-8")
                    try:
                        os.chmod(SSH_CREDENTIALS_FILE, 0o600)
                    except OSError:
                        pass
        except Exception:
            pass
    rates = payload.get("token_rates")
    if isinstance(rates, dict) and "input_eur_per_1m" in rates and "output_eur_per_1m" in rates:
        try:
            update_token_rates(float(rates["input_eur_per_1m"]), float(rates["output_eur_per_1m"]))
        except Exception:
            pass
    return {"ok": True}


def claim_master() -> dict:
    nodes = load_fleet()
    local_ip = get_local_ip()
    hubs = [n for n in nodes if n.get("role") == "hub" and n.get("host") != local_ip]
    if len(hubs) > 1:
        return {"ok": False, "error": "Hay varios hubs registrados; no se puede hacer un traspaso inequivoco."}
    for hub in hubs:
        host = hub["host"]
        try:
            previous = _node_http_json(host, hub.get("port", 18888), "/api/role",
                                       hub.get("token", ""), timeout=2.0)
            if not isinstance(previous, dict) or previous.get("ok") is not True:
                raise ValueError("No se pudo consultar el rol anterior.")
            result = _node_http_json(
                host, hub.get("port", 18888), "/api/role", hub.get("token", ""),
                method="POST", timeout=2.0, body={"role": "node"},
            )
            if isinstance(result, dict) and result.get("ok") is True and result.get("role") == "node":
                result = _node_http_json(host, hub.get("port", 18888), "/api/role",
                                         hub.get("token", ""), timeout=2.0)
        except (OSError, ValueError):
            result = None
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("role") != "node":
            return {"ok": False, "error": f"El hub {host} no ha confirmado el traspaso. Comprueba su conexion y version del portal."}
        hub["role"] = "node"
        save_fleet(nodes)
    set_local_role("hub")
    return {"ok": True, "role": "hub", "sync_failed": sync_fleet_to_peers()}


def _monitoring_snapshot_path(node: dict) -> Path:
    identity = json.dumps([node["host"], int(node.get("port", 18888)), node.get("token", "")])
    return DEMOS_ROOT / "monitoring-snapshots" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")


def _monitoring_origin() -> str:
    return hashlib.sha256(load_config()["node_token"].encode("utf-8")).hexdigest()


def _monitoring_db() -> sqlite3.Connection:
    DEMOS_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = DEMOS_ROOT / "monitoring-history.sqlite"
    db = sqlite3.connect(path, timeout=15)
    try:
        os.chmod(path, 0o600)
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            origin TEXT NOT NULL, seq INTEGER NOT NULL, observed_at REAL NOT NULL,
            status TEXT NOT NULL, ledger TEXT NOT NULL, PRIMARY KEY (origin, seq))""")
        db.commit()
        return db
    except Exception:
        db.close()
        raise


def _record_local_monitoring() -> None:
    status = get_status()
    with TOKEN_LOCK:
        ledger = _load_token_ledger()
    origin = _monitoring_origin()
    with closing(_monitoring_db()) as db, db:
        db.execute(
            """INSERT INTO observations (origin, seq, observed_at, status, ledger)
               SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?
               FROM observations WHERE origin = ?""",
            (origin, time.time(), json.dumps(status), json.dumps(ledger), origin),
        )


def _monitoring_cursors() -> dict:
    with closing(_monitoring_db()) as db, db:
        return dict(db.execute("SELECT origin, MAX(seq) FROM observations GROUP BY origin"))


def export_monitoring(payload: dict) -> dict:
    cursors = payload.get("cursors", {})
    if not isinstance(cursors, dict) or not all(
        isinstance(k, str) and re.fullmatch(r"[0-9a-f]{64}", k)
        and type(v) is int and v >= 0 for k, v in cursors.items()
    ):
        return {"ok": False, "error": "Cursores de monitorizacion no validos."}
    records = []
    with closing(_monitoring_db()) as db, db:
        origins = [row[0] for row in db.execute("SELECT DISTINCT origin FROM observations")]
        for origin in origins:
            rows = db.execute(
                "SELECT origin, seq, observed_at, status, ledger FROM observations "
                "WHERE origin = ? AND seq > ? ORDER BY seq LIMIT 50",
                (origin, cursors.get(origin, 0)),
            )
            records.extend(rows)
    records.sort(key=lambda row: (row[0], row[1]))
    return {"ok": True, "records": [
        {"origin": row[0], "seq": row[1], "observed_at": row[2],
         "status": json.loads(row[3]), "ledger": json.loads(row[4])}
        for row in records[:50]
    ]}


def _import_monitoring(records: list) -> None:
    if not isinstance(records, list) or len(records) > 50:
        raise ValueError("Lote de monitorizacion no valido.")
    local_origin = _monitoring_origin()
    with closing(_monitoring_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Registro de monitorizacion no valido.")
            origin, seq, observed_at = record.get("origin"), record.get("seq"), record.get("observed_at")
            status, ledger = record.get("status"), record.get("ledger")
            if not (isinstance(origin, str) and re.fullmatch(r"[0-9a-f]{64}", origin)
                    and type(seq) is int and seq > 0
                    and type(observed_at) in (int, float) and 0 < observed_at < 1e12
                    and isinstance(status, dict) and isinstance(status.get("system"), dict)
                    and isinstance(ledger, dict)):
                raise ValueError("Registro de monitorizacion no valido.")
            if origin == local_origin:
                continue
            last = db.execute("SELECT COALESCE(MAX(seq), 0) FROM observations WHERE origin = ?",
                              (origin,)).fetchone()[0]
            if seq > last + 1:
                raise ValueError("El historico recibido tiene registros ausentes.")
            db.execute("INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?)",
                       (origin, seq, observed_at, json.dumps(status), json.dumps(ledger)))


def _read_monitoring_snapshot(node: dict) -> dict:
    path = _monitoring_snapshot_path(node)
    if not path.is_file():
        return {}
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("status"), dict):
            raise ValueError("Formato de monitorizacion no valido.")
        return snapshot
    except (OSError, ValueError) as exc:
        print(f"[WARN] No se pudo leer la monitorizacion de {node['host']}: {exc}", flush=True)
        return {}


def _replicate_monitoring(node: dict) -> None:
    host = node["host"]
    try:
        status = _node_http_json(host, int(node.get("port", 18888)), "/api/status", node["token"], timeout=3.0)
        if not isinstance(status, dict) or not isinstance(status.get("system"), dict):
            raise ValueError("Respuesta de monitorizacion no valida.")
        path = _monitoring_snapshot_path(node)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, filename = tempfile.mkstemp(dir=path.parent)
        temp = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({"observed_at": time.time(), "status": status}, file)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        result = _node_http_json(host, int(node.get("port", 18888)), "/api/monitoring/export",
                                 node["token"], method="POST", timeout=10.0,
                                 body={"cursors": _monitoring_cursors()})
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError("El portal remoto no permite sincronizar el historico.")
        _import_monitoring(result.get("records"))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"[WARN] Monitorizacion de {host} no sincronizada: {exc}", flush=True)


def fleet_monitoring_loop() -> None:
    while True:
        try:
            _record_local_monitoring()
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"[WARN] No se pudo guardar el historico local: {exc}", flush=True)
        local_ip = get_local_ip()
        peers = [n for n in load_fleet() if n.get("token") and n.get("host") != local_ip]
        if peers:
            with ThreadPoolExecutor(max_workers=min(12, len(peers))) as pool:
                list(pool.map(_replicate_monitoring, peers))
        time.sleep(60)


def get_fleet_status() -> dict:
    cfg = load_config()
    local_st = get_status()
    local_sys = local_st["system"]
    local_entry = {
        "id": "local",
        "alias": local_sys.get("hostname", "zgx-local"),
        "tag": "LOCAL",
        "host": "127.0.0.1",
        "port": LISTEN_PORT,
        "is_local": True,
        "online": True,
        "latency_ms": 0,
        "role": cfg["role"],
        "hw_model": local_sys.get("hw", {}).get("hw_model", "ZGX-NANO"),
        "demos_installed": sum(1 for d in local_st.get("demos", {}).values() if d.get("installed")),
        "demos_total": len(local_st.get("demos", {})),
        "active": local_st["active"],
        "cpu_pct": local_sys.get("cpu", {}).get("pct", 0),
        "ram": local_sys.get("ram", {}),
        "grace": local_sys.get("grace", {}),
        "gpu": local_sys.get("gpu", {}),
        "disk": local_sys.get("disk", {}),
        "concurrent_users": local_st.get("concurrent_users", {"unique_clients": 0, "connections": 0}),
        "recent_events": local_st.get("recent_events", []),
        "tokens": local_st.get("tokens", {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "eur_saved": 0.0}, "by_demo": {}}),
    }

    nodes = load_fleet()

    def probe_node(n):
        t0 = time.time()
        entry = {
            "id": n["id"],
            "alias": n.get("alias", n["host"]),
            "tag": n.get("tag", "NODE"),
            "host": n["host"],
            "port": n.get("port", 18888),
            "ssh_port": n.get("ssh_port", 22),
            "ssh_user": n.get("ssh_user", "nvidia"),
            "arch": n.get("arch", ""),
            "agent_installed": n.get("agent_installed", True),
            "is_local": False,
            "online": False,
            "latency_ms": None,
            "role": n.get("role", "node"),
            "hw_model": n.get("hw_model", "ZGX"),
            "demos_installed": 0,
            "demos_total": 0,
            "active": {"key": None, "status": "offline"},
            "cpu_pct": 0,
            "ram": {},
            "gpu": {"name": n["gpu"], "util_pct": None} if n.get("gpu") else {},
            "disk": {},
            "concurrent_users": {"unique_clients": 0, "connections": 0},
            "tokens": {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "eur_saved": 0.0}, "by_demo": {}},
        }
        try:
            st = _node_http_json(n["host"], n.get("port", 18888), "/api/status", n.get("token", ""), timeout=1.6)
            if not isinstance(st, dict) or not isinstance(st.get("system"), dict):
                raise ValueError("Respuesta de monitorizacion no valida.")
            entry["online"] = True
            entry["agent_installed"] = True
            entry["latency_ms"] = int((time.time() - t0) * 1000)
        except (OSError, ValueError):
            snapshot = _read_monitoring_snapshot(n)
            st = snapshot.get("status", {})
            entry["observed_at"] = snapshot.get("observed_at")
        if st:
            entry["role"] = st.get("role", entry["role"])
            if entry["online"]:
                entry["active"] = st.get("active", {})
            sys_s = st.get("system", {})
            entry["hw_model"] = sys_s.get("hw", {}).get("hw_model", entry["hw_model"])
            dmap = st.get("demos", {})
            entry["demos_installed"] = sum(1 for d in dmap.values() if d.get("installed"))
            entry["demos_total"] = len(dmap)
            entry["cpu_pct"] = sys_s.get("cpu", {}).get("pct", 0)
            entry["ram"] = sys_s.get("ram", {})
            entry["gpu"] = sys_s.get("gpu", {})
            entry["disk"] = sys_s.get("disk", {})
            if isinstance(st.get("concurrent_users"), dict):
                entry["concurrent_users"] = st["concurrent_users"]
            if isinstance(st.get("recent_events"), list):
                entry["recent_events"] = st["recent_events"]
            if isinstance(st.get("tokens"), dict):
                entry["tokens"] = st["tokens"]
        return entry

    results = [local_entry]
    if nodes:
        with ThreadPoolExecutor(max_workers=min(12, len(nodes))) as ex:
            results.extend(list(ex.map(probe_node, nodes)))

    fleet_in, fleet_out, fleet_eur = 0, 0, 0.0
    fleet_by_demo = {}
    for nd in results:
        tk = nd.get("tokens", {})
        tot = tk.get("total", {})
        fleet_in += int(tot.get("input_tokens", 0))
        fleet_out += int(tot.get("output_tokens", 0))
        fleet_eur += float(tot.get("eur_saved", 0.0))
        for dk, dv in tk.get("by_demo", {}).items():
            agg = fleet_by_demo.setdefault(dk, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "eur_saved": 0.0})
            agg["input_tokens"] += int(dv.get("input_tokens", 0))
            agg["output_tokens"] += int(dv.get("output_tokens", 0))
            agg["total_tokens"] += int(dv.get("total_tokens", 0))
            agg["eur_saved"] = round(agg["eur_saved"] + float(dv.get("eur_saved", 0.0)), 4)

    return {
        "ok": True,
        "role": cfg["role"],
        "nodes": results,
        "fleet_tokens": {
            "total": {
                "input_tokens": fleet_in,
                "output_tokens": fleet_out,
                "total_tokens": fleet_in + fleet_out,
                "eur_saved": round(fleet_eur, 4),
            },
            "by_demo": fleet_by_demo,
            "rates": local_st.get("tokens", {}).get("rates", dict(DEFAULT_TOKEN_RATES)),
        },
    }


def _read_ssh_banner(ip: str, port: int, timeout: float = 0.45) -> tuple[bool, str]:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            try:
                data = sock.recv(128)
            except OSError:
                data = b""
            banner = data.decode("utf-8", errors="replace").splitlines()[0].strip() if data else ""
            return True, banner
    except OSError:
        return False, ""


def _resolve_mdns_hostname(ip: str, timeout: float = 0.55) -> str:
    try:
        res = subprocess.run(
            ["avahi-resolve-address", "-4", ip],
            capture_output=True, text=True, timeout=timeout,
        )
        parts = (res.stdout or "").strip().split()
        if res.returncode == 0 and len(parts) >= 2:
            return parts[1].strip().removesuffix(".local").removesuffix(".")
    except Exception:
        pass
    return ""


def _classify_scanned_host(
    *,
    is_local: bool,
    enrolled: bool,
    agent_up: bool,
    hw_model: str,
    ssh_up: bool,
    ssh_banner: str,
    hostname: str,
) -> tuple[str, bool, str]:
    """Return (kind, zgx_candidate, non_zgx_reason)."""
    if is_local:
        return "local", True, ""
    if hw_model == "ZGX-NANO":
        return "nano", True, ""
    if hw_model == "ZGX-FURY":
        return "fury", True, ""
    if agent_up:
        return "zgx", True, ""
    if enrolled:
        return "enrolled", True, ""
    if not ssh_up:
        return "other", False, "no-ssh"

    banner_low = (ssh_banner or "").lower()
    if any(tok in banner_low for tok in ("windows", "mocana", "mpssh", "dropbear", "cisco", "forti", "routeros")):
        return "non-zgx", False, "ssh-non-linux"
    if banner_low and "openssh" not in banner_low and "ubuntu" not in banner_low:
        return "non-zgx", False, "ssh-non-openssh"

    host_low = (hostname or "").lower()
    if host_low.startswith(("spark-", "zgx-")) or "dgx" in host_low:
        return "nano-hint", True, ""
    if any(
        tok in host_low
        for tok in ("cups", "printer", "laserjet", "designjet", "officejet", "smart-tank", "desktop", "workstation", "laptop", "nas", "_gateway", "router", "switch", "lightware", "crestron", "extron", "polycom", "cisco", "camera")
    ):
        return "non-zgx", False, "hostname-non-zgx"

    if "ubuntu" in banner_low:
        return "linux-candidate", True, ""
    return "non-zgx", False, "ssh-no-ubuntu-banner"


def _probe_ssh_zgx_hardware(host: str, user: str, password: str, ssh_port: int = 22) -> dict:
    probe_cmd = (
        "printf 'ZGX_SSH_OK\\n'; "
        "printf 'ARCH=%s\\n' \"$(uname -m 2>/dev/null)\"; "
        "printf 'HOSTNAME=%s\\n' \"$(hostname 2>/dev/null)\"; "
        "printf 'GPU=%s\\n' \"$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)\""
    )
    rc, output = _ssh_run(host, user, password, probe_cmd, timeout=5, ssh_port=ssh_port)
    if rc != 0 or "ZGX_SSH_OK" not in output:
        error = "Tiempo de espera SSH agotado." if rc == 124 else "No se pudo autenticar o conectar por SSH."
        return {"ok": False, "error": error}
    arch = ""
    hostname = ""
    gpu = ""
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("ARCH="):
            arch = s.split("=", 1)[1].strip()
        elif s.startswith("HOSTNAME="):
            hostname = s.split("=", 1)[1].strip()
        elif s.startswith("GPU="):
            gpu = s.split("=", 1)[1].strip()
    hw_model = _infer_hw_model_from_gpu(gpu, "")
    is_nano = arch == "aarch64" and "GB10" in gpu.upper()
    is_fury = "GB300" in gpu.upper()
    return {
        "ok": True,
        "arch": arch,
        "hostname": hostname,
        "gpu": gpu,
        "hw_model": hw_model,
        "is_nano": is_nano,
        "is_fury": is_fury,
        "is_zgx": bool(is_nano or is_fury),
    }


def scan_local_network(
    cidr_override: str = "",
    ssh_port: int = 22,
    ssh_user: str = "",
    ssh_pass: str = "",
    verify_candidates: bool = False,
) -> dict:
    try:
        port_num = int(ssh_port or 22)
        if not 1 <= port_num <= 65535:
            port_num = 22
    except (TypeError, ValueError):
        port_num = 22

    cidrs = []
    if cidr_override and str(cidr_override).strip():
        try:
            net = ipaddress.ip_network(str(cidr_override).strip(), strict=False)
            if net.version != 4 or net.num_addresses > 1024:
                return {"ok": False, "error": "Indica una subred IPv4 valida (ej. 192.168.1.0/24).", "hosts": []}
            cidrs.append(str(net))
        except ValueError:
            return {"ok": False, "error": "Formato de subred CIDR invalido.", "hosts": []}
    else:
        try:
            out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True, timeout=2)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    iface = parts[1]
                    cidr = parts[3]
                    if iface == "lo" or iface.startswith(("docker", "br-", "veth", "tun", "wg")):
                        continue
                    net = ipaddress.ip_network(cidr, strict=False)
                    if net.prefixlen >= 22:
                        cidrs.append(str(net))
        except Exception:
            pass
    if not cidrs:
        return {"ok": False, "error": "No se pudo detectar la subred local.", "hosts": []}

    target_net = ipaddress.ip_network(cidrs[0], strict=False)
    all_ips = [str(ip) for ip in target_net.hosts()][:512]
    local_ip = get_local_ip()
    fleet_by_ip = {n.get("host"): n for n in load_fleet() if n.get("host")}

    def check_ip(ip: str):
        ssh_up, ssh_banner = _read_ssh_banner(ip, port_num, timeout=0.4)
        agent_up = False
        hw_model = ""
        gpu_name = ""
        arch = ""
        probe_hostname = ""
        if port_open(18888, host=ip, timeout=0.35):
            try:
                with urllib.request.urlopen(f"http://{ip}:18888/api/node/probe", timeout=0.55) as resp:
                    pr = json.load(resp)
                if isinstance(pr, dict) and pr.get("ok") is True:
                    agent_up = True
                    hw_model = str(pr.get("hw_model") or "")
                    gpu_name = str(pr.get("gpu") or "")
                    arch = str(pr.get("arch") or "")
                    probe_hostname = str(pr.get("hostname") or "")
            except Exception:
                pass
        if not ssh_up and not agent_up:
            return None

        hostname = probe_hostname
        if not hostname:
            try:
                rev = socket.gethostbyaddr(ip)[0]
                if rev and rev != "_gateway":
                    hostname = rev.removesuffix(".local").removesuffix(".")
                elif rev == "_gateway":
                    hostname = rev
            except Exception:
                pass
        if not hostname and ssh_up and ("ubuntu" in ssh_banner.lower() or not ssh_banner):
            hostname = _resolve_mdns_hostname(ip, timeout=0.55)

        enrolled_node = fleet_by_ip.get(ip)
        enrolled = enrolled_node is not None
        is_local = ip == local_ip
        if enrolled_node:
            hw_model = hw_model or str(enrolled_node.get("hw_model") or "")
            gpu_name = gpu_name or str(enrolled_node.get("gpu") or "")
            arch = arch or str(enrolled_node.get("arch") or "")
            if not hostname and enrolled_node.get("alias") and enrolled_node.get("alias") != ip:
                hostname = str(enrolled_node.get("alias"))

        kind, zgx_candidate, non_zgx_reason = _classify_scanned_host(
            is_local=is_local,
            enrolled=enrolled,
            agent_up=agent_up,
            hw_model=hw_model,
            ssh_up=ssh_up,
            ssh_banner=ssh_banner,
            hostname=hostname,
        )
        return {
            "ip": ip,
            "hostname": hostname if hostname != "_gateway" else "",
            "ssh": ssh_up,
            "ssh_banner": ssh_banner,
            "agent": agent_up,
            "hw_model": hw_model,
            "gpu": gpu_name,
            "arch": arch,
            "enrolled": enrolled,
            "is_local": is_local,
            "kind": kind,
            "zgx_candidate": zgx_candidate,
            "non_zgx_reason": non_zgx_reason,
            "ssh_verified": False,
        }

    found = []
    with ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(check_ip, all_ips):
            if res is not None:
                found.append(res)

    clean_user = (ssh_user or "").strip()
    user_valid = bool(clean_user and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\$?", clean_user))
    has_saved_default = False
    if user_valid:
        try:
            has_saved_default = bool(_get_default_ssh_password(clean_user))
        except (OSError, ValueError):
            has_saved_default = False

    if verify_candidates and user_valid:
        candidates_to_probe = [
            h for h in found
            if h["zgx_candidate"] and not h["is_local"] and not h["enrolled"] and h["ssh"]
        ]

        def probe_candidate(item: dict) -> None:
            pw = ssh_pass
            if not pw:
                try:
                    pw = _get_ssh_password(item["ip"], port_num, clean_user)
                except (OSError, ValueError):
                    pw = ""
            if not pw:
                return
            pr = _probe_ssh_zgx_hardware(item["ip"], clean_user, pw, ssh_port=port_num)
            if not pr.get("ok"):
                item["ssh_error"] = str(pr.get("error") or "")
                return
            try:
                _save_ssh_password(item["ip"], port_num, clean_user, pw)
                item["ssh_verified"] = True
            except (OSError, ValueError):
                pass
            if pr.get("hostname") and not item["hostname"]:
                item["hostname"] = str(pr["hostname"])
            if pr.get("arch"):
                item["arch"] = str(pr["arch"])
            if pr.get("gpu"):
                item["gpu"] = str(pr["gpu"])
            if pr.get("hw_model"):
                item["hw_model"] = str(pr["hw_model"])
            if pr.get("is_nano"):
                item["kind"] = "nano"
                item["zgx_candidate"] = True
            elif pr.get("is_fury"):
                item["kind"] = "fury"
                item["zgx_candidate"] = True
            else:
                item["kind"] = "non-zgx"
                item["zgx_candidate"] = False
                item["non_zgx_reason"] = "hw-non-zgx"

        if candidates_to_probe:
            with ThreadPoolExecutor(max_workers=min(8, len(candidates_to_probe))) as ex:
                list(ex.map(probe_candidate, candidates_to_probe))
        for item in candidates_to_probe:
            if item.get("ssh_error") and item.get("kind") == "linux-candidate" and not item.get("agent"):
                item["zgx_candidate"] = False
                item["non_zgx_reason"] = "ssh-auth-failed"

    def sort_key(x: dict) -> tuple:
        ip_tuple = tuple(int(p) for p in x["ip"].split("."))
        if x.get("zgx_candidate") and not x.get("enrolled") and not x.get("is_local"):
            rank = 0 if x.get("kind") in ("nano", "fury", "zgx", "nano-hint") else 1
        elif x.get("enrolled") or x.get("is_local"):
            rank = 2
        else:
            rank = 3
        return (rank, ip_tuple)

    found.sort(key=sort_key)
    return {
        "ok": True,
        "subnet": str(target_net),
        "has_default_ssh": has_saved_default,
        "hosts": found,
    }


def _pty_ssh_run(host: str, user: str, password: str, remote_cmd: str, stdin_data: bytes = b"", timeout: int = 45, ssh_port: int = 22, argv: list = None, stdin_prompt: bytes = b"") -> tuple:
    pid, fd = pty.fork()
    if pid == 0:
        command = argv or [
                "ssh",
                "-p", str(ssh_port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=8",
                "-o", "PubkeyAuthentication=no",
                "-o", "NumberOfPasswordPrompts=1",
                f"{user}@{host}",
                remote_cmd,
            ]
        os.execvp(command[0], command)
    output = bytearray()
    pw_sent = False
    stdin_sent = False
    eof = False
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    eof = True
                    break
                if not chunk:
                    eof = True
                    break
                output.extend(chunk)
                low = output[-300:].lower()
                if not pw_sent and b"password:" in low:
                    os.write(fd, (password + "\n").encode("utf-8"))
                    pw_sent = True
                    if stdin_data and not stdin_sent and not stdin_prompt:
                        time.sleep(0.25)
                        os.write(fd, stdin_data)
                        stdin_sent = True
                if pw_sent and stdin_data and not stdin_sent and stdin_prompt and stdin_prompt in output:
                    import termios
                    settings = termios.tcgetattr(fd)
                    settings[3] &= ~termios.ECHO
                    termios.tcsetattr(fd, termios.TCSANOW, settings)
                    os.write(fd, stdin_data)
                    stdin_sent = True
            else:
                if pw_sent and stdin_data and not stdin_sent and not stdin_prompt:
                    os.write(fd, stdin_data)
                    stdin_sent = True
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    while True:
                        r2, _, _ = select.select([fd], [], [], 0.05)
                        if not r2:
                            break
                        try:
                            chunk = os.read(fd, 4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                    code = os.waitstatus_to_exitcode(status)
                    return code, output.decode("utf-8", errors="replace")
        if eof:
            reap_deadline = time.time() + 3.0
            while time.time() < reap_deadline:
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    code = os.waitstatus_to_exitcode(status)
                    return code, output.decode("utf-8", errors="replace")
                time.sleep(0.05)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        os.waitpid(pid, 0)
        return 124, output.decode("utf-8", errors="replace") + "\n[TIMEOUT]"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _ssh_run(host: str, user: str, password: str, remote_cmd: str, timeout: int = 45, ssh_port: int = 22) -> tuple[int, str]:
    try:
        import paramiko
    except ImportError:
        return _pty_ssh_run(host, user, password, remote_cmd, timeout=timeout, ssh_port=ssh_port)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            port=int(ssh_port),
            username=user,
            password=password,
            timeout=min(10, timeout),
            allow_agent=False,
            look_for_keys=False,
            banner_timeout=10,
            auth_timeout=10,
        )
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(15)
        chan = transport.open_session()
        chan.set_combine_stderr(True)
        chan.exec_command(remote_cmd)
        chunks = bytearray()
        deadline = time.time() + timeout
        while not chan.exit_status_ready():
            while chan.recv_ready():
                chunks.extend(chan.recv(4096))
            if time.time() >= deadline:
                try:
                    chan.close()
                except Exception:
                    pass
                return 124, chunks.decode("utf-8", errors="replace") + "\n[TIMEOUT]"
            time.sleep(0.05)
        while chan.recv_ready():
            chunks.extend(chan.recv(4096))
        rc = chan.recv_exit_status()
        chan.close()
        return rc, chunks.decode("utf-8", errors="replace")
    except paramiko.AuthenticationException:
        return 255, "Authentication failed (invalid SSH user or password)."
    except (TimeoutError, socket.timeout):
        return 124, "[TIMEOUT]"
    except Exception as exc:
        return 255, str(exc)
    finally:
        try:
            client.close()
        except Exception:
            pass


def verify_ssh_credentials(payload: dict[str, object]) -> dict[str, bool | str]:
    host = payload.get("host")
    user = payload.get("ssh_user")
    password = payload.get("ssh_pass")
    try:
        if not isinstance(host, str) or ipaddress.ip_address(host).version != 4:
            raise ValueError("IP invalida")
        port_value = payload.get("ssh_port", 22)
        if isinstance(port_value, bool) or not isinstance(port_value, (int, str)):
            raise ValueError("Puerto invalido")
        port = int(port_value)
        if not 1 <= port <= 65535:
            raise ValueError("Puerto invalido")
    except (TypeError, ValueError):
        return {"ok": False, "error": "IP o puerto SSH invalido."}
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\$?", user):
        return {"ok": False, "error": "Usuario SSH invalido."}
    if password is None or password == "":
        try:
            password = _get_default_ssh_password(user)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"No se pueden leer las credenciales SSH: {exc}"}
    if not isinstance(password, str) or not password:
        return {"ok": False, "error": "Introduce la contrasena SSH."}

    pr = _probe_ssh_zgx_hardware(host, user, password, ssh_port=port)
    if not pr.get("ok"):
        return {"ok": False, "error": str(pr.get("error") or "No se pudo autenticar o conectar por SSH.")}
    try:
        _save_ssh_password(host, port, user, password)
    except (OSError, ValueError) as exc:
        return {"ok": False, "authenticated": True, "error": f"Conexion verificada, pero no se pudo guardar la credencial: {exc}"}
    return {
        "ok": True,
        "hostname": pr.get("hostname", ""),
        "arch": pr.get("arch", ""),
        "gpu": pr.get("gpu", ""),
        "hw_model": pr.get("hw_model", ""),
        "is_nano": bool(pr.get("is_nano")),
        "is_fury": bool(pr.get("is_fury")),
        "is_zgx": bool(pr.get("is_zgx")),
    }


def save_default_ssh_credentials(payload: dict) -> dict:
    user = payload.get("ssh_user")
    password = payload.get("ssh_pass")
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\$?", user):
        return {"ok": False, "error": "Usuario SSH invalido."}
    if not isinstance(password, str) or not password:
        return {"ok": False, "error": "Introduce la contrasena SSH."}
    try:
        _save_ssh_password("", 0, user, password)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"No se pueden guardar las credenciales SSH: {exc}"}
    return {"ok": True}


def _ssh_upload_file(host: str, user: str, password: str, local_path: Path, remote_path: str, ssh_port: int = 22, timeout: int = 180) -> tuple:
    try:
        import paramiko
    except ImportError:
        command = [
            "scp", "-P", str(ssh_port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8",
            "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1",
            str(local_path), f"{user}@{host}:{remote_path}",
        ]
        rc, out = _pty_ssh_run(
            host, user, password, "", timeout=timeout, ssh_port=ssh_port, argv=command,
        )
        return rc == 0, out[-300:]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host,
            port=int(ssh_port),
            username=user,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
            banner_timeout=10,
            auth_timeout=10,
        )
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(15)
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(timeout)
        sftp.put(str(local_path), remote_path)
        sftp.close()
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _enroll_log(msg: str, step: int = None, phase: str = None) -> None:
    with ENROLL_LOCK:
        if step is not None:
            ENROLL_JOB["step"] = step
        if phase is not None:
            ENROLL_JOB["phase"] = phase
        ENROLL_JOB["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")


def get_enroll_status() -> dict:
    with ENROLL_LOCK:
        end_ts = time.time() if ENROLL_JOB["running"] else (ENROLL_JOB.get("ended_at") or time.time())
        elapsed = int(max(0.0, end_ts - ENROLL_JOB["started_at"])) if ENROLL_JOB["started_at"] else 0
        return {
            "running": ENROLL_JOB["running"],
            "phase": ENROLL_JOB["phase"],
            "step": ENROLL_JOB["step"],
            "total_steps": ENROLL_JOB["total_steps"],
            "elapsed_s": elapsed,
            "host": ENROLL_JOB["host"],
            "job_id": ENROLL_JOB.get("job_id"),
            "ok": ENROLL_JOB["ok"],
            "error": ENROLL_JOB["error"],
            "log": "\n".join(ENROLL_JOB["log"][-60:]),
        }


def _infer_hw_model_from_gpu(gpu_name: str, fallback: str = "ZGX-NANO") -> str:
    up = (gpu_name or "").upper()
    if "GB300" in up:
        return "ZGX-FURY"
    if "GB10" in up:
        return "ZGX-NANO"
    return fallback or "ZGX-NANO"


def _run_enroll_job(host: str, alias: str, tag: str, port: int, ssh_port: int, role: str, ssh_user: str, ssh_pass: str, explicit_token: str = "") -> None:
    existing = next((n for n in load_fleet() if (n.get("host"), int(n.get("port", 18888))) == (host, port)), None)
    arch = existing.get("arch", "") if existing else ""
    gpu_name = existing.get("gpu", "") if existing else ""
    probed_token = ""
    remote_hostname = ""
    agent_ready = False
    try:
        if ssh_pass:
            _enroll_log(f"Conectando por SSH a {ssh_user}@{host}:{ssh_port}...", step=1, phase="SSH Check & Hardware")
            if not port_open(ssh_port, host=host, timeout=3.0):
                raise RuntimeError(f"Puerto SSH {host}:{ssh_port} inaccesible.")

            probe_py = """
import json, pathlib, platform, re, socket, subprocess
print("ARCH=" + platform.machine())
print("HOSTNAME=" + socket.gethostname())
gpus = []
try:
    out = subprocess.check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], text=True, timeout=4)
    gpus = [ln.strip() for ln in out.splitlines() if ln.strip()]
except Exception:
    pass
sel = next((g for g in gpus if "GB300" in g.upper()), next((g for g in gpus if "GB10" in g.upper()), (gpus[0] if gpus else "")))
print("GPU=" + sel)
if gpus:
    print("GPUS=" + ", ".join(gpus))
svc = "unknown"
try:
    r = subprocess.run(["systemctl", "is-active", "zgx-launcher.service"], capture_output=True, text=True, timeout=3)
    svc = (r.stdout or r.stderr or "").strip() or "inactive"
except Exception:
    pass
print("AGENT_SVC=" + svc)
home = pathlib.Path.home()
demos_root = home / "zgx-demos"
svc_file = pathlib.Path("/etc/systemd/system/zgx-launcher.service")
if svc_file.exists():
    try:
        m = re.search(r"^Environment=ZGX_DEMOS_ROOT=(.+)$", svc_file.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            demos_root = pathlib.Path(m.group(1).strip().strip('"'))
    except Exception:
        pass
present = []
for k, p in [
    ("campus", demos_root / "campuscompass-local/.venv/bin/python"),
    ("atlas", demos_root / "atlas-claude/.venv/bin/python"),
    ("guard", demos_root / "beat-the-guard/.venv/bin/python"),
    ("vision", demos_root / "research-observatory/.venv/bin/python"),
    ("nano-collective", home / "hp-zgx-nano/launch.sh"),
    ("fury-showcase", home / "hp-zgx-fury-showcase/launch.sh"),
    ("partner-kit", home / "zgx-partner-kit/portable.py"),
]:
    if p.exists():
        present.append(k)
print("DEMOS_PRESENT=" + (",".join(present) if present else "none"))
tok = ""
cfg_path = demos_root / "console-config.json"
if cfg_path.exists():
    try:
        tok = json.loads(cfg_path.read_text(encoding="utf-8")).get("node_token", "")
    except Exception:
        pass
print("NODE_TOKEN=" + tok)
""".strip()
            probe_b64 = base64.b64encode(probe_py.encode("utf-8")).decode("ascii")
            c0, out0 = _ssh_run(host, ssh_user, ssh_pass, f"echo '{probe_b64}' | base64 -d | python3 -", timeout=20, ssh_port=ssh_port)
            if c0 != 0:
                raise RuntimeError(f"Fallo autenticacion o conexion SSH (code {c0}): {out0[-300:]}")
            _save_ssh_password(host, ssh_port, ssh_user, ssh_pass)
            for ln in out0.splitlines():
                ln_s = ln.strip()
                if ln_s.startswith("ARCH="):
                    arch = ln_s.split("=", 1)[1].strip()
                    _enroll_log(f"Detectado: ARCH={arch}")
                elif ln_s.startswith("HOSTNAME="):
                    remote_hostname = ln_s.split("=", 1)[1].strip()
                    _enroll_log(f"Detectado: HOSTNAME={remote_hostname}")
                elif ln_s.startswith("GPU="):
                    gpu_name = ln_s.split("=", 1)[1].strip()
                    if gpu_name:
                        _enroll_log(f"Detectado: GPU={gpu_name}")
                elif ln_s.startswith(("GPUS=", "AGENT_SVC=", "DEMOS_PRESENT=")):
                    _enroll_log(f"Detectado: {ln_s}")
                elif ln_s.startswith("NODE_TOKEN="):
                    probed_token = ln_s.split("=", 1)[1].strip()
        else:
            _enroll_log(f"Equipo ya conocido ({host}); omitiendo SSH y verificando conexion con el agente existente...", step=1, phase="Connection Check")

        _enroll_log(f"Verificando agente ZGX existente en {host}:{port}...", step=2, phase="Verify Agent")
        token = probed_token or (existing.get("token", "") if existing else "") or explicit_token
        hw_model = _infer_hw_model_from_gpu(gpu_name, existing.get("hw_model", tag) if existing else tag)
        st = None
        last_exc = ""
        if token and port_open(port, host=host, timeout=2.5):
            try:
                st = _node_http_json(host, port, "/api/status", token, timeout=3.0)
                agent_ready = True
            except Exception as exc:
                last_exc = str(exc)

        if agent_ready and st:
            hw_model = st.get("system", {}).get("hw", {}).get("hw_model") or hw_model
            gpu_from_st = st.get("system", {}).get("gpu", {}).get("name", "")
            if gpu_from_st:
                gpu_name = gpu_from_st
            _enroll_log(f"[OK] Agente ZGX activo y verificado (sin redesplegar): hw_model={hw_model}, demos={len(st.get('demos', {}))}.")
        else:
            if not ssh_pass:
                detail = f": {last_exc}" if last_exc else " (puerto o token no disponible)"
                raise RuntimeError(f"El agente no responde en {host}:{port}{detail}. Indica la contrasena SSH para verificar o desplegar.")
            _enroll_log(f"Conexion SSH verificada. Agente ZGX no activo en {host}:{port}; no se ha desplegado nada automaticamente. Usa el boton 'Desplegar' en el portal cuando quieras instalar agente o demos.")

        _enroll_log("Guardando nodo en fleet.json y sincronizando flota...", step=3, phase="Fleet Sync")
        nodes = [n for n in load_fleet() if (n["host"], int(n.get("port", 18888))) != (host, port)]
        node_id = (existing.get("id") if existing else None) or ("zgx-" + secrets.token_hex(4))
        final_tag = hw_model if tag in ("ZGX-NANO", "ZGX", "ZGX-FURY") and hw_model else tag
        nodes.append({
            "id": node_id,
            "alias": alias or remote_hostname or host,
            "tag": final_tag,
            "hw_model": hw_model,
            "host": host,
            "port": port,
            "ssh_port": ssh_port,
            "ssh_user": ssh_user,
            "arch": arch,
            "gpu": gpu_name,
            "agent_installed": agent_ready,
            "token": token,
            "role": role,
            "added_at": (existing.get("added_at") if existing else None) or time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        save_fleet(nodes)
        if agent_ready:
            sync_fleet_to_peers()
        _enroll_log(f"[OK] COMPLETADO: {alias} ({hw_model}) enrolado sin autodespliegue.", step=3, phase="Done")
        with ENROLL_LOCK:
            ENROLL_JOB["ok"] = True
            ENROLL_JOB["ended_at"] = time.time()
            ENROLL_JOB["running"] = False
    except Exception as exc:
        _enroll_log(f"ERROR: {exc}")
        with ENROLL_LOCK:
            ENROLL_JOB["ok"] = False
            ENROLL_JOB["error"] = str(exc)
            ENROLL_JOB["phase"] = "Error"
            ENROLL_JOB["ended_at"] = time.time()
            ENROLL_JOB["running"] = False


def enroll_node(payload: dict) -> dict:
    host = (payload.get("host") or "").strip()
    alias = (payload.get("alias") or host).strip()
    tag = (payload.get("tag") or "ZGX-NANO").strip()
    port = int(payload.get("port") or 18888)
    ssh_port = int(payload.get("ssh_port") or 22)
    role = payload.get("role") if payload.get("role") in ("node", "hub") else "node"
    ssh_user = (payload.get("ssh_user") or "nvidia").strip()
    ssh_pass = payload.get("ssh_pass") or ""
    explicit_token = (payload.get("node_token") or "").strip()
    verified_only = payload.get("verified_ssh") is True

    if not host:
        return {"ok": False, "error": "Host / IP requerida."}
    if verified_only:
        if host == get_local_ip():
            return {"ok": True, "skipped": True}
        try:
            with SSH_CREDENTIALS_LOCK:
                ssh_pass = _load_ssh_credentials().get(_ssh_credential_key(host, ssh_port, ssh_user), "")
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"No se pueden leer las credenciales SSH: {exc}"}
        if not ssh_pass:
            return {"ok": False, "error": "No hay credenciales verificadas para este equipo, usuario y puerto."}
    elif not ssh_pass:
        try:
            ssh_pass = _get_ssh_password(host, ssh_port, ssh_user)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"No se pueden leer las credenciales SSH: {exc}"}
    existing = next((n for n in load_fleet() if (n.get("host"), int(n.get("port", 18888))) == (host, port)), None)
    if not ssh_pass and not existing and not explicit_token:
        return {"ok": False, "error": "Contrasena SSH requerida para verificar y enrolar un dispositivo nuevo."}

    with ENROLL_LOCK:
        if ENROLL_JOB["running"]:
            return {"ok": False, "busy": True, "error": f"Ya hay un enrolamiento en curso hacia {ENROLL_JOB['host']}."}
        if verified_only and any(n.get("host") == host for n in load_fleet()):
            return {"ok": True, "skipped": True}
        job_id = secrets.token_hex(8)
        ENROLL_JOB["job_id"] = job_id
        ENROLL_JOB["running"] = True
        ENROLL_JOB["phase"] = "Starting"
        ENROLL_JOB["step"] = 0
        ENROLL_JOB["total_steps"] = 3
        ENROLL_JOB["started_at"] = time.time()
        ENROLL_JOB["ended_at"] = 0.0
        ENROLL_JOB["host"] = f"{host}:{ssh_port}"
        ENROLL_JOB["ok"] = None
        ENROLL_JOB["error"] = ""
        ENROLL_JOB["log"] = [f"[{time.strftime('%H:%M:%S')}] Iniciando registro y prueba de conexion hacia {host}:{ssh_port} (sin autodespliegue)..."]

    threading.Thread(
        target=_run_enroll_job,
        args=(host, alias, tag, port, ssh_port, role, ssh_user, ssh_pass, explicit_token),
        daemon=True,
    ).start()
    return {"ok": True, "started": True, "job_id": job_id}


def _deploy_log(msg: str, step: int = None, phase: str = None) -> None:
    with DEPLOY_LOCK:
        if step is not None:
            DEPLOY_JOB["step"] = step
        if phase is not None:
            DEPLOY_JOB["phase"] = phase
        DEPLOY_JOB["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")


def get_deploy_status() -> dict:
    with DEPLOY_LOCK:
        end_ts = time.time() if DEPLOY_JOB["running"] else (DEPLOY_JOB.get("ended_at") or time.time())
        elapsed = int(max(0.0, end_ts - DEPLOY_JOB["started_at"])) if DEPLOY_JOB["started_at"] else 0
        return {
            "running": DEPLOY_JOB["running"],
            "phase": DEPLOY_JOB["phase"],
            "step": DEPLOY_JOB["step"],
            "total_steps": DEPLOY_JOB["total_steps"],
            "elapsed_s": elapsed,
            "node_id": DEPLOY_JOB["node_id"],
            "host": DEPLOY_JOB["host"],
            "ok": DEPLOY_JOB["ok"],
            "error": DEPLOY_JOB["error"],
            "log": "\n".join(DEPLOY_JOB["log"][-80:]),
        }


def get_nano_setup_status(node_id: str, user: str = "", port: str = "") -> dict:
    node = next((n for n in load_fleet() if n.get("id") == node_id), None)
    if not node:
        return {"ok": False, "error": "Nodo no encontrado en la flota."}
    try:
        user = user.strip() or node.get("ssh_user") or "nvidia"
        port = int(port or node.get("ssh_port") or 22)
        if not 1 <= port <= 65535:
            raise ValueError("Puerto SSH no valido.")
        password = _get_ssh_password(node["host"], port, user)
        if not password:
            return {"ok": False, "error": "No hay credenciales SSH guardadas para consultar la preparacion."}
        return {"ok": True, "status": _auto_setup_status(node["host"], user, port, password)}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


def _ensure_no_active_nano_setup(host: str, user: str, port: int, password: str) -> None:
    command = """set -eu
state="$HOME/.local/state/zgx-setup/auto.status"
if [ ! -f "$state" ]; then exit 0; fi
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
active="$(systemctl --user show zgx-auto-setup.service --property=ActiveState --value)"
case "$active" in
  active|activating|reloading|deactivating) exit 23 ;;
  inactive|failed) exit 0 ;;
  *) echo "Estado desconocido del servicio de preparacion: $active" >&2; exit 1 ;;
esac
"""
    encoded = base64.b64encode(command.encode()).decode("ascii")
    code, output = _ssh_run(host, user, password, f"echo '{encoded}' | base64 -d | bash -s",
                            timeout=20, ssh_port=port)
    if code == 23:
        raise RuntimeError("La preparacion Nano sigue activa en este equipo; espera a que termine.")
    if code:
        raise RuntimeError(f"No se pudo comprobar la preparacion Nano ({code}): {output[-300:]}")


SOURCE_MANIFEST = ".zgx-source-files.json"


def _repository_root() -> Path:
    configured = os.environ.get("ZGX_REPOSITORY_DIR")
    root = Path(configured).expanduser() if configured else PORTAL_DIR.parent
    if not configured and not (root / ".git").exists() and (Path.home() / ".zgx-source" / SOURCE_MANIFEST).is_file():
        root = Path.home() / ".zgx-source"
    if not (root / "fleet-console" / "launcher_web.py").is_file() or not (root / "install.sh").is_file():
        raise RuntimeError("Repositorio completo no disponible. Instala primero la copia fuente en este equipo.")
    if not (root / ".git").exists() and not (root / SOURCE_MANIFEST).is_file():
        raise RuntimeError("Falta el manifiesto de la copia fuente del repositorio.")
    return root.resolve()


def _deploy_package_sources() -> dict:
    root = _repository_root()
    fury = root / "ZGXFURY_demo" / "fury-showcase"
    if fury.exists() and not (fury / "config" / "distribution-files.json").is_file():
        raise RuntimeError("Falta la lista de distribucion del showcase Fury.")
    options = {
        "portal": ("Portal", [root / "fleet-console" / "launcher_web.py"]),
        "fury-showcase": ("Demos Fury", [fury / "launch.sh", fury / "ops" / "fury-profile.py",
                                         fury / "config" / "fury-profiles.json", fury / "ops" / "install-fury-extras.sh"]),
        "nano-collective": ("Demos Nano ZGX", [root / "hp-zgx-fury-showcase-v3.2.0-nano.3" / "install.sh",
                                                root / "hp-zgx-fury-showcase-v3.2.0-nano.3" / "launch.sh",
                                                root / "fleet-console" / "nano-auto-setup.sh"]),
    }
    return {key: {"key": key, "name": name} for key, (name, paths) in options.items()
            if all(path.is_file() for path in paths)}


def get_deploy_catalog() -> dict:
    errors = []
    try:
        pkgs = _deploy_package_sources()
    except (OSError, RuntimeError) as exc:
        pkgs = {}
        errors.append(str(exc))
    sources = {"portal": [], "fury-showcase": [], "nano-collective": []}
    for key in sources:
        if key in pkgs:
            sources[key].append({"id": "local", "name": socket.gethostname()})
    for node in load_fleet():
        try:
            catalog = _node_http_json(
                node["host"], int(node.get("port", 18888)), "/api/fleet/deploy/local-catalog",
                node.get("token", ""), timeout=2.0,
            )
            if not catalog.get("ok") or catalog.get("repository_version") != 1:
                raise RuntimeError(catalog.get("error", "catalogo no disponible"))
            for entry in catalog.get("demos", []):
                key = entry.get("key")
                if key in sources:
                    sources[key].append({"id": node["id"], "name": node.get("alias") or node["host"]})
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{node.get('alias') or node['host']}: {exc}")
    return {
        "ok": True,
        "agent": True,
        "sources": sources,
        "source_errors": errors,
        "demos": [
            {
                "key": p["key"],
                "name": p["name"],
                "desc_es": "",
                "desc_en": "",
            }
            for p in pkgs.values()
        ],
    }


def get_local_deploy_catalog() -> dict:
    try:
        root = _repository_root()
        snapshots = Path.home() / ".zgx-repositories"
        revision = root.name if root.parent == snapshots.resolve() and re.fullmatch(r"[a-f0-9]{24}", root.name) else ""
        return {"ok": True, "repository_version": 1, "repository_revision": revision,
                "demos": [{"key": key} for key in _deploy_package_sources()]}
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


def _download_deploy_package(node: dict, pkg_key: str) -> Path:
    url = f"http://{node['host']}:{int(node.get('port', 18888))}/api/fleet/deploy/archive/{pkg_key}"
    request = urllib.request.Request(url, headers={"X-ZGX-Token": node.get("token", "")})
    fd, filename = tempfile.mkstemp(prefix=f"zgx-deploy-{pkg_key}-", suffix=".tar.gz")
    path = Path(filename)
    try:
        with os.fdopen(fd, "wb") as target, urllib.request.urlopen(request, timeout=300) as response:
            shutil.copyfileobj(response, target)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _mirror_hub_repository(hub: dict) -> None:
    if not hub.get("token"):
        raise ValueError("El Master no tiene token de sincronizacion.")
    catalog = _node_http_json(hub["host"], int(hub.get("port", 18888)), "/api/fleet/deploy/local-catalog",
                              hub["token"], timeout=5.0)
    if not isinstance(catalog, dict) or catalog.get("ok") is not True or catalog.get("repository_version") != 1:
        raise RuntimeError("Catalogo compatible del Master no disponible.")
    revision = catalog.get("repository_revision")
    if not revision:
        return  # A working checkout is not a published snapshot.
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{24}", revision):
        raise ValueError("Revision del repositorio no valida.")

    home = Path.home()
    link = home / ".zgx-source"
    if link.exists() and not link.is_symlink():
        raise RuntimeError("~/.zgx-source no es un enlace; no se ha sobrescrito.")
    store = home / ".zgx-repositories"
    snapshot = store / revision
    if snapshot.is_symlink():
        raise RuntimeError("La revision local no puede ser un enlace.")
    if not all((snapshot / name).is_file() for name in (SOURCE_MANIFEST, "fleet-console/launcher_web.py", "install.sh")):
        store.mkdir(parents=True, exist_ok=True)
        archive = _download_deploy_package(hub, "portal")
        try:
            with tempfile.TemporaryDirectory(prefix="zgx-incoming-", dir=store) as directory:
                staged = Path(directory) / "snapshot"
                staged.mkdir()
                with tarfile.open(archive, "r:gz") as source:
                    members = source.getmembers()
                    names = {m.name for m in members}
                    if len(names) != len(members) or any(
                        not m.isfile() or "\\" in m.name or ":" in m.name
                        or any(part in ("", ".", "..") for part in m.name.split("/"))
                        for m in members
                    ):
                        raise ValueError("La copia fuente contiene rutas o tipos de archivo invalidos.")
                    source.extractall(staged, members, filter="data")
                for name in (SOURCE_MANIFEST, "fleet-console/launcher_web.py", "install.sh"):
                    if not (staged / name).is_file():
                        raise ValueError(f"Falta {name} en la copia fuente.")
                manifest = json.loads((staged / SOURCE_MANIFEST).read_text(encoding="utf-8"))
                if not isinstance(manifest, list) or not all(isinstance(name, str) for name in manifest) \
                        or sorted(manifest) != sorted(names - {SOURCE_MANIFEST}):
                    raise ValueError("El contenido de la copia fuente no coincide con su manifiesto.")
                latest = _node_http_json(hub["host"], int(hub.get("port", 18888)),
                                         "/api/fleet/deploy/local-catalog", hub["token"], timeout=5.0)
                if not isinstance(latest, dict) or latest.get("repository_revision") != revision:
                    raise RuntimeError("La revision del Master ha cambiado durante la copia; se reintentara.")
                staged.rename(snapshot)
        finally:
            archive.unlink(missing_ok=True)
    current_hubs = [n for n in load_fleet() if n.get("role") == "hub"]
    if load_config().get("role") != "node" or len(current_hubs) != 1 or any(
        current_hubs[0].get(key) != hub.get(key) for key in ("host", "port", "token")
    ):
        return
    if link.resolve() != snapshot.resolve():
        with tempfile.TemporaryDirectory(prefix="zgx-source-link-", dir=home) as directory:
            pending = Path(directory) / "link"
            pending.symlink_to(snapshot, target_is_directory=True)
            pending.replace(link)


def fleet_sync_loop() -> None:
    while True:
        try:
            if load_config()["role"] == "hub":
                failed = sync_fleet_to_peers()
                if failed:
                    print(f"[WARN] Sincronizacion de flota pendiente: {', '.join(failed)}", flush=True)
            else:
                peers = [node for node in load_fleet() if node.get("role") == "hub"]
                if len(peers) == 1:
                    _mirror_hub_repository(peers[0])
        except (OSError, RuntimeError, ValueError, tarfile.TarError) as exc:
            print(f"[WARN] Sincronizacion de flota: {exc}", flush=True)
        time.sleep(60)


def _build_repository_tarball(root: Path) -> Path:
    executable_files = set()
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=True, stdout=subprocess.PIPE,
        )
        files = [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]
        if os.name == "nt":
            index = subprocess.run(["git", "-C", str(root), "ls-files", "--stage", "-z"],
                                   check=True, stdout=subprocess.PIPE)
            for entry in index.stdout.split(b"\0"):
                if entry.startswith(b"100755 ") and b"\t" in entry:
                    executable_files.add(os.fsdecode(entry.split(b"\t", 1)[1]))
        fury_prefix = "ZGXFURY_demo/fury-showcase/"
        fury_manifest = root / fury_prefix / "config" / "distribution-files.json"
        if (root / fury_prefix).exists() and not fury_manifest.is_file():
            raise RuntimeError("Falta la lista de distribucion del showcase Fury.")
        if fury_manifest.exists():
            fury_files = json.loads(fury_manifest.read_text(encoding="utf-8"))
            if not isinstance(fury_files, list) or not all(isinstance(name, str) for name in fury_files):
                raise ValueError("Lista de distribucion Fury invalida.")
            files = [name for name in files if not name.replace("\\", "/").startswith(fury_prefix)]
            files.extend(fury_prefix + name for name in fury_files)
    else:
        files = json.loads((root / SOURCE_MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(files, list) or not all(isinstance(name, str) for name in files):
            raise ValueError("Manifiesto de origen invalido.")
    fd, tmp_name = tempfile.mkstemp(prefix="zgx-repository-", suffix=".tar.gz")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tarfile.open(tmp_path, "w:gz", dereference=True) as tf:
            included = []
            for name in sorted(set(files)):
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ValueError(f"Ruta de origen invalida: {name}")
                private_dirs = {".git", ".ssh", ".codex", ".venv", "venv", ".model-venv",
                                ".deepseek-venv", ".image-venv", "__pycache__", "node_modules",
                                "models", "state", "logs", "runs", "devruns", "devruns-archive",
                                "monitoring-snapshots"}
                private_files = {"ssh-credentials.json", "console-config.json", "fleet.json",
                                 "token_ledger.json", ".publication.json", "monitoring-history.sqlite",
                                 "monitoring-history.sqlite-wal", "monitoring-history.sqlite-shm",
                                 "monitoring-history.sqlite-journal", "automatic-deployment.json"}
                if (any(part in private_dirs for part in relative.parts)
                        or (".config" in relative.parts and "gh" in relative.parts)
                        or relative.name in private_files
                        or relative.name.startswith(("id_rsa", "id_ed25519", "tailscale.state"))
                        or relative.name == ".env"
                        or (relative.name.startswith(".env.") and relative.name != ".env.example")
                        or relative.suffix.lower() in (".key", ".pem", ".p12", ".pfx", ".sqlite", ".db",
                                                       ".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".ckpt")):
                    continue
                fp = root / relative
                if not fp.exists() and (root / ".git").exists() and not name.replace("\\", "/").startswith("ZGXFURY_demo/fury-showcase/"):
                    continue
                if not fp.is_file() or root.resolve() not in fp.resolve().parents:
                    raise RuntimeError(f"Archivo de origen no valido: {name}")
                arc = relative.as_posix()
                info = tf.gettarinfo(str(fp), arcname=arc)
                with fp.open("rb") as stream:
                    if os.name == "nt" and (arc in executable_files or fp.suffix == ".sh" or stream.read(2) == b"#!"):
                        info.mode |= 0o111
                    stream.seek(0)
                    tf.addfile(info, stream)
                included.append(arc)
            data = json.dumps(included, ensure_ascii=True).encode("utf-8")
            info = tarfile.TarInfo(SOURCE_MANIFEST)
            info.size = len(data)
            info.mode = 0o600
            tf.addfile(info, io.BytesIO(data))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def _run_deploy_job(node: dict, ssh_user: str, ssh_port: int, ssh_pass: str, deploy_agent: bool, deploy_demos: list, source: dict = None, copy_only: bool = False) -> None:
    host = node["host"]
    port = int(node.get("port", 18888))
    total_steps = 2 + (1 if deploy_agent else 0) + len(deploy_demos)
    with DEPLOY_LOCK:
        DEPLOY_JOB["total_steps"] = max(1, total_steps)
    cur_step = 0
    try:
        cur_step += 1
        _deploy_log(f"Verificando conectividad SSH con {ssh_user}@{host}:{ssh_port}...", step=cur_step, phase="SSH Check")
        if not port_open(ssh_port, host=host, timeout=3.0):
            raise RuntimeError(f"Puerto SSH {host}:{ssh_port} inaccesible.")
        rc0, out0 = _ssh_run(host, ssh_user, ssh_pass, "echo SSH_OK", timeout=15, ssh_port=ssh_port)
        if rc0 != 0 or "SSH_OK" not in out0:
            raise RuntimeError(f"Fallo autenticacion o conexion SSH (code {rc0}): {out0[-300:]}")
        _save_ssh_password(host, ssh_port, ssh_user, ssh_pass)
        _deploy_log("[OK] Conexion SSH verificada.")
        _ensure_no_active_nano_setup(host, ssh_user, ssh_port, ssh_pass)

        cur_step += 1
        _deploy_log("Empaquetando el repositorio completo...", step=cur_step, phase="Repository")
        source_key = "portal" if deploy_agent or copy_only else deploy_demos[0]
        archive = (_build_repository_tarball(_repository_root()) if source is None
                   else _download_deploy_package(source, source_key))
        nonce = secrets.token_hex(12)
        remote_tar = f"/tmp/zgx-repository-{nonce}.tar.gz"
        try:
            _deploy_log(f"Transfiriendo repositorio ({archive.stat().st_size // (1024 * 1024)} MB)...")
            ok_up, err_up = _ssh_upload_file(host, ssh_user, ssh_pass, archive, remote_tar, ssh_port=ssh_port, timeout=1800)
            if not ok_up:
                raise RuntimeError(f"Fallo transfiriendo repositorio: {err_up}")
        finally:
            archive.unlink(missing_ok=True)
        extract_script = f"""set -eu
ARCHIVE={shlex.quote(remote_tar)}
SNAPSHOT="$HOME/.zgx-repositories/{nonce}"
LINK="$HOME/.zgx-source"
if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
  echo "El destino $LINK ya existe y no es un enlace; no se ha sobrescrito." >&2
  exit 1
fi
mkdir -p "$SNAPSHOT"
trap 'rm -f "$ARCHIVE"' EXIT
python3 - "$ARCHIVE" "$SNAPSHOT" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    archive.extractall(sys.argv[2], filter='data')
PY
test -f "$SNAPSHOT/{SOURCE_MANIFEST}"
test -f "$SNAPSHOT/fleet-console/launcher_web.py"
test -f "$SNAPSHOT/install.sh"
ln -s "$SNAPSHOT" "$HOME/.zgx-source-{nonce}"
mv -Tf "$HOME/.zgx-source-{nonce}" "$LINK"
echo REPOSITORY_OK
"""
        ex_b64 = base64.b64encode(extract_script.encode("utf-8")).decode("ascii")
        rc_e, out_e = _ssh_run(host, ssh_user, ssh_pass, f"echo '{ex_b64}' | base64 -d | bash -s", timeout=600, ssh_port=ssh_port)
        if rc_e != 0 or "REPOSITORY_OK" not in out_e:
            raise RuntimeError(f"Fallo instalando la copia fuente: {out_e[-1000:]}")
        _deploy_log("[OK] Repositorio disponible en ~/.zgx-source para instalaciones futuras.")

        if deploy_agent:
            cur_step += 1
            _deploy_log("Inspeccionando configuracion existente del agente remoto...", step=cur_step, phase="Deploy Agent")
            inspect_py = """
import json, pathlib, re
home = pathlib.Path.home()
svc = pathlib.Path("/etc/systemd/system/zgx-launcher.service")
info = {
    "home": str(home),
    "script_path": str(home / "zgx-partner-kit/launcher_web.py"),
    "work_dir": str(home / "zgx-partner-kit"),
    "demos_root": str(home / "zgx-demos"),
    "kit_dir": "",
    "fury_dir": "",
    "role": "",
    "token": "",
}
if svc.exists():
    txt = svc.read_text(encoding="utf-8", errors="ignore")
    m_wd = re.search(r"^WorkingDirectory=(.+)$", txt, re.M)
    if m_wd:
        info["work_dir"] = m_wd.group(1).strip().strip('"')
    m_es = re.search(r"^ExecStart=(.+)$", txt, re.M)
    if m_es:
        parts = m_es.group(1).strip().split()
        py_files = [p for p in parts if p.endswith("launcher_web.py")]
        if py_files:
            info["script_path"] = py_files[0]
            if not m_wd:
                info["work_dir"] = str(pathlib.Path(py_files[0]).parent)
    for k, env_k in [("demos_root", "ZGX_DEMOS_ROOT"), ("kit_dir", "ZGX_KIT_DIR"), ("fury_dir", "ZGX_FURY_DIR"), ("role", "ZGX_ROLE")]:
        me = re.search(rf"^Environment={env_k}=(.+)$", txt, re.M)
        if me:
            info[k] = me.group(1).strip().strip('"')
cfg_p = pathlib.Path(info["demos_root"]) / "console-config.json"
if cfg_p.exists():
    try:
        c = json.loads(cfg_p.read_text(encoding="utf-8"))
        info["token"] = c.get("node_token", "") or ""
        if c.get("role") in ("hub", "node"):
            info["role"] = c.get("role", "")
    except Exception:
        pass
print(json.dumps(info))
""".strip()
            ins_b64 = base64.b64encode(inspect_py.encode("utf-8")).decode("ascii")
            rc_i, out_i = _ssh_run(host, ssh_user, ssh_pass, f"echo '{ins_b64}' | base64 -d | python3 -", timeout=20, ssh_port=ssh_port)
            if rc_i != 0:
                raise RuntimeError(f"Error inspeccionando servicio remoto (code {rc_i}): {out_i[-300:]}")
            rinfo = json.loads(out_i.strip().splitlines()[-1])
            remote_script = rinfo["script_path"]
            remote_workdir = rinfo["work_dir"]
            remote_demos_root = rinfo["demos_root"]
            remote_role = rinfo.get("role") or node.get("role") or "node"
            node_token = rinfo.get("token") or node.get("token") or secrets.token_hex(24)

            _deploy_log(f"Instalando el portal desde el repositorio en {remote_script}...")
            rc_m, out_m = _ssh_run(host, ssh_user, ssh_pass, f"mkdir -p {shlex.quote(remote_workdir)} {shlex.quote(remote_demos_root)}", timeout=15, ssh_port=ssh_port)
            if rc_m != 0:
                raise RuntimeError(f"No se pueden crear los directorios remotos: {out_m[-300:]}")
            script_arg = shlex.quote(remote_script)
            rc_up, out_up = _ssh_run(host, ssh_user, ssh_pass,
                                     f"src=\"$HOME/.zgx-source/fleet-console/launcher_web.py\"; "
                                     f"if [ \"$(readlink -f \"$src\")\" != \"$(readlink -f {script_arg})\" ]; "
                                     f"then cp \"$src\" {script_arg}; fi",
                                     timeout=60, ssh_port=ssh_port)
            if rc_up != 0:
                raise RuntimeError(f"Fallo instalando launcher_web.py: {out_up[-300:]}")
            rc_c, out_c = _ssh_run(host, ssh_user, ssh_pass, f"python3 -m py_compile {script_arg} && echo PY_OK", timeout=20, ssh_port=ssh_port)
            if rc_c != 0 or "PY_OK" not in out_c:
                raise RuntimeError(f"Fallo validando sintaxis en nodo remoto: {out_c[-300:]}")

            env_lines = [
                f"Environment=HOME={rinfo['home']}",
                "Environment=PYTHONUNBUFFERED=1",
                f"Environment=ZGX_DEMOS_ROOT={remote_demos_root}",
                f"Environment=ZGX_REPOSITORY_DIR={rinfo['home']}/.zgx-source",
            ]
            if rinfo.get("kit_dir"):
                env_lines.append(f"Environment=ZGX_KIT_DIR={rinfo['kit_dir']}")
            if rinfo.get("fury_dir"):
                env_lines.append(f"Environment=ZGX_FURY_DIR={rinfo['fury_dir']}")
            env_lines.extend([
                f"Environment=ZGX_LAUNCHER_HOST=0.0.0.0",
                f"Environment=ZGX_LAUNCHER_PORT={port}",
                f"Environment=ZGX_ROLE={remote_role}",
                f"Environment=ZGX_NODE_TOKEN={node_token}",
            ])
            unit_text = "[Unit]\nDescription=HP ZGX Fleet Console\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\n"
            unit_text += f"User={ssh_user}\nGroup={ssh_user}\nWorkingDirectory={remote_workdir}\n"
            unit_text += "\n".join(env_lines) + "\n"
            unit_text += f"ExecStart=/usr/bin/python3 -u {remote_script}\nRestart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n"
            unit_b64 = base64.b64encode(unit_text.encode("utf-8")).decode("ascii")
            sudoers_line = f"{ssh_user} ALL=(root) NOPASSWD: /usr/bin/systemctl start *.service, /usr/bin/systemctl stop *.service, /usr/bin/systemctl restart *.service, /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/docker system prune *"
            sudoers_b64 = base64.b64encode((sudoers_line + "\n").encode("utf-8")).decode("ascii")
            sys_script = f"""set -e
umask 077
tmp="$(mktemp -d)"
trap 'rm -f "$tmp/sudoers" "$tmp/service"; rmdir "$tmp"' EXIT
echo '{sudoers_b64}' | base64 -d > "$tmp/sudoers"
echo '{unit_b64}' | base64 -d > "$tmp/service"
visudo -cf "$tmp/sudoers"
install -o root -g root -m 440 "$tmp/sudoers" /etc/sudoers.d/zgx-launcher
install -o root -g root -m 644 "$tmp/service" /etc/systemd/system/zgx-launcher.service
systemctl daemon-reload
systemctl enable --now zgx-launcher.service
systemctl restart zgx-launcher.service
"""
            _deploy_log("Configurando systemd zgx-launcher.service y reiniciando agente...")
            _auto_sudo(host, ssh_user, ssh_port, ssh_pass, sys_script, timeout=45)

            st = None
            last_exc = ""
            for _ in range(10):
                time.sleep(1.0)
                try:
                    st = _node_http_json(host, port, "/api/status", node_token, timeout=3.0)
                    break
                except Exception as exc:
                    last_exc = str(exc)
            if st is None:
                raise RuntimeError(f"El servicio se reinicio pero no responde en {host}:{port}: {last_exc}")

            hw_model = st.get("system", {}).get("hw", {}).get("hw_model", node.get("hw_model", "ZGX"))
            nodes = load_fleet()
            for item in nodes:
                if item.get("id") == node.get("id"):
                    item["token"] = node_token
                    item["hw_model"] = hw_model
                    item["agent_installed"] = True
                    item["ssh_user"] = ssh_user
                    item["ssh_port"] = ssh_port
            save_fleet(nodes)
            sync_fleet_to_peers()
            _deploy_log(f"[OK] Agente activo y verificado ({hw_model}, demos={len(st.get('demos', {}))}).")

        for pkg_key in deploy_demos:
            cur_step += 1
            _deploy_log(f"Preparando demo '{pkg_key}'...", step=cur_step, phase=f"Deploy {pkg_key}")
            if pkg_key == "nano-collective":
                info = _auto_probe(host, ssh_user, ssh_port, ssh_pass)
                if info.get("arch") != "aarch64" or "GB10" not in info.get("gpu", "").upper():
                    raise RuntimeError("La instalacion Nano requiere ARM64 y GPU GB10.")
                if not info.get("setup_running"):
                    if info.get("busy"):
                        _, tail_out = _ssh_run(
                            host, ssh_user, ssh_pass,
                            "ps aux | grep -E 'portable|install|cmake|download-models|bootstrap|llama-server' | grep -v grep | head -n 5; "
                            "echo '--- LOG ---'; "
                            "tail -n 12 \"$HOME/install-demos.log\" \"$HOME/.local/state/zgx-setup/auto.log\" 2>/dev/null || true",
                            timeout=10, ssh_port=ssh_port,
                        )
                        for line in tail_out.strip().splitlines()[-15:]:
                            _deploy_log("  " + line)
                        raise RuntimeError("Hay una demo o instalacion activa; no se interrumpira.")
                    _auto_prerequisites(host, ssh_user, ssh_port, ssh_pass)
                    _auto_sudo(host, ssh_user, ssh_port, ssh_pass,
                               "loginctl enable-linger " + shlex.quote(ssh_user), timeout=30)
                    _auto_start_setup(host, ssh_user, ssh_port, ssh_pass, collective_only=True)
                _deploy_log("Preparacion de Nano Collective iniciada; puede tardar horas.")
                deadline = time.monotonic() + 30
                while True:
                    state = _auto_setup_status(host, ssh_user, ssh_port, ssh_pass)
                    if state.startswith("ERROR:"):
                        raise RuntimeError(state)
                    if state.startswith(("RUNNING:", "DONE:")):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("No se pudo confirmar el inicio de la preparacion Nano; consulta el equipo.")
                    time.sleep(1)
                _deploy_log(state)
                if state.startswith("DONE:"):
                    continue
                _deploy_log("Repositorio entregado; la preparacion y los modelos continuan en segundo plano.")
                with DEPLOY_LOCK:
                    DEPLOY_JOB["phase"] = "Background"
                    DEPLOY_JOB["ok"] = None
                    DEPLOY_JOB["ended_at"] = time.time()
                    DEPLOY_JOB["running"] = False
                return
            rel = "hp-zgx-fury-showcase"
            source_rel = "ZGXFURY_demo/fury-showcase"
            install_cmd = "FURY_PROFILE=gb300 bash ./launch.sh setup\nbash ops/install-fury-extras.sh --with-lora-deps"
            install_script = f"""set -e
DEST="$HOME/{rel}"
SOURCE="$HOME/.zgx-source/{source_rel}"
test -f "$SOURCE/launch.sh"
if [ -L "$DEST" ]; then
  DEST="$(readlink -f "$DEST")"
fi
mkdir -p "$DEST"
cp -a "$SOURCE/." "$DEST/"
cd "$DEST"
{install_cmd}
echo INSTALL_OK
"""
            _deploy_log("Preparando dependencias y modelos; puede tardar horas.")
            install_b64 = base64.b64encode(install_script.encode("utf-8")).decode("ascii")
            rc_setup, out_setup = _ssh_run(host, ssh_user, ssh_pass, f"echo '{install_b64}' | base64 -d | bash -s", timeout=21600, ssh_port=ssh_port)
            if rc_setup != 0 or "INSTALL_OK" not in out_setup:
                raise RuntimeError(f"Fallo preparando {pkg_key} (code {rc_setup}): {out_setup[-1500:]}")
            _deploy_log(f"[OK] Demo {pkg_key} instalada en ~/{rel}.")

        _deploy_log(f"[OK] COMPLETADO: despliegue finalizado en {node.get('alias', host)}.", step=total_steps, phase="Done")
        with DEPLOY_LOCK:
            DEPLOY_JOB["ok"] = True
            DEPLOY_JOB["ended_at"] = time.time()
            DEPLOY_JOB["running"] = False
    except Exception as exc:
        _deploy_log(f"ERROR: {exc}")
        with DEPLOY_LOCK:
            DEPLOY_JOB["ok"] = False
            DEPLOY_JOB["error"] = str(exc)
            DEPLOY_JOB["phase"] = "Error"
            DEPLOY_JOB["ended_at"] = time.time()
            DEPLOY_JOB["running"] = False


def deploy_to_node(payload: dict) -> dict:
    node_id = (payload.get("node_id") or "").strip()
    ssh_pass = payload.get("ssh_pass") or ""
    deploy_agent = bool(payload.get("deploy_agent", False))
    deploy_demos = [str(k).strip() for k in (payload.get("deploy_demos") or []) if str(k).strip()]
    copy_only = bool(payload.get("copy_only", False))
    source_id = (payload.get("source_id") or "local").strip()

    node = next((n for n in load_fleet() if n.get("id") == node_id), None)
    if not node:
        return {"ok": False, "error": "Nodo no encontrado en la flota."}
    ssh_user = (payload.get("ssh_user") or node.get("ssh_user") or "nvidia").strip()
    ssh_port = int(payload.get("ssh_port") or node.get("ssh_port") or 22)
    if not ssh_pass:
        try:
            ssh_pass = _get_ssh_password(node["host"], ssh_port, ssh_user)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"No se pueden leer las credenciales SSH: {exc}"}
    if not ssh_pass:
        return {"ok": False, "error": "Contrasena SSH requerida para desplegar."}
    if int(deploy_agent) + len(deploy_demos) + int(copy_only) != 1:
        return {"ok": False, "error": "Selecciona solo una opcion: copiar fuente, portal, Fury o Nano."}

    source = None if source_id == "local" else next((n for n in load_fleet() if n.get("id") == source_id), None)
    if source_id != "local" and not source:
        return {"ok": False, "error": "Equipo de origen no encontrado en la flota."}
    if source and source.get("host") == node.get("host"):
        return {"ok": False, "error": "El origen y el destino deben ser equipos diferentes."}
    source_key = "portal" if deploy_agent or copy_only else deploy_demos[0]
    for dk in deploy_demos:
        if dk not in ("fury-showcase", "nano-collective"):
            return {"ok": False, "error": f"Demo no soportada: {dk}"}
    try:
        cat = (get_local_deploy_catalog() if source is None else
               _node_http_json(source["host"], int(source.get("port", 18888)),
                               "/api/fleet/deploy/local-catalog", source.get("token", ""), timeout=3.0))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"No se puede consultar el origen; actualiza su portal: {exc}"}
    if not cat.get("ok") or cat.get("repository_version") != 1:
        return {"ok": False, "error": cat.get("error", "Actualiza el portal del origen para compartir el repositorio completo.")}
    if source_key not in [p.get("key") for p in cat.get("demos", [])]:
        return {"ok": False, "error": "La opcion seleccionada no esta disponible en el origen."}

    with DEPLOY_LOCK:
        if DEPLOY_JOB["running"]:
            return {"ok": False, "busy": True, "error": f"Ya hay un despliegue manual en curso hacia {DEPLOY_JOB['host']}."}
        DEPLOY_JOB["running"] = True
        DEPLOY_JOB["phase"] = "Starting"
        DEPLOY_JOB["step"] = 0
        DEPLOY_JOB["total_steps"] = 2 + (1 if deploy_agent else 0) + len(deploy_demos)
        DEPLOY_JOB["started_at"] = time.time()
        DEPLOY_JOB["ended_at"] = 0.0
        DEPLOY_JOB["node_id"] = node_id
        DEPLOY_JOB["host"] = f"{node['host']}:{ssh_port}"
        DEPLOY_JOB["ok"] = None
        DEPLOY_JOB["error"] = ""
        DEPLOY_JOB["log"] = [f"[{time.strftime('%H:%M:%S')}] Iniciando despliegue manual hacia {node.get('alias', node['host'])} ({node['host']}:{ssh_port})..."]

    threading.Thread(
        target=_run_deploy_job,
        args=(node, ssh_user, ssh_port, ssh_pass, deploy_agent, deploy_demos, source, copy_only),
        daemon=True,
    ).start()
    return {"ok": True, "started": True}


def _auto_state_path() -> Path:
    return DEMOS_ROOT / "automatic-deployment.json"


def _auto_update(phase: str = "", host: str = "", message: str = "", **fields) -> None:
    with AUTO_LOCK:
        if phase:
            AUTO_JOB["phase"] = phase
        if host:
            AUTO_JOB["items"][host] = message
        if message:
            AUTO_JOB["log"].append(f"[{time.strftime('%H:%M:%S')}] {host + ': ' if host else ''}{message}")
            AUTO_JOB["log"] = AUTO_JOB["log"][-500:]
        AUTO_JOB.update(fields)
        DEMOS_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".automatic-deployment-", dir=DEMOS_ROOT)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(AUTO_JOB, file)
            os.replace(name, _auto_state_path())
        finally:
            Path(name).unlink(missing_ok=True)


def get_auto_status() -> dict:
    with AUTO_LOCK:
        return json.loads(json.dumps(AUTO_JOB))


def _auto_cancelled() -> bool:
    with AUTO_LOCK:
        return AUTO_JOB["cancel"]


def _auto_probe(host: str, user: str, port: int, password: str) -> dict:
    script = r"""
import hashlib, json, os, pathlib, platform, re, socket, subprocess
home = pathlib.Path.home()
try:
    gpu = subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],
                                  text=True, stderr=subprocess.DEVNULL, timeout=8)
except (OSError, subprocess.SubprocessError):
    gpu = ''
env = dict(os.environ, XDG_RUNTIME_DIR='/run/user/' + str(os.getuid()),
           DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/' + str(os.getuid()) + '/bus')
running = subprocess.run(['systemctl','--user','is-active','--quiet','zgx-auto-setup.service'],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
busy = False
patterns = ('portable.py launch', 'portable.py setup', 'public_entry.py', 'public_services.py',
            'llama-server', 'launch.sh setup', 'nano-auto-setup.sh', 'bootstrap-gb10.sh')
for proc in pathlib.Path('/proc').iterdir():
    if not proc.name.isdigit() or int(proc.name) in (os.getpid(), os.getppid()):
        continue
    try:
        cmd = (proc/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except FileNotFoundError:
        continue
    except PermissionError:
        busy = True
        continue
    if any(pattern in cmd for pattern in patterns):
        busy = True
for port in (8080, 8081, 8082, 8110, 8130, 18000, 18110, 18080, 18120, 18808):
    with socket.socket() as sock:
        sock.settimeout(.1)
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            busy = True
svc = pathlib.Path('/etc/systemd/system/zgx-launcher.service')
script_path = ''
clean = False
digest = ''
if svc.exists():
    text = svc.read_text(encoding='utf-8')
    match = re.search(r'^ExecStart=.*?(\S*launcher_web\.py)(?:\s|$)', text, re.M)
    if match:
        path = pathlib.Path(match.group(1))
        old_source = home/'.zgx-source/fleet-console/launcher_web.py'
        if path.is_file() and old_source.is_file():
            contents = path.read_bytes()
            digest = hashlib.sha256(contents).hexdigest()
            clean = contents == old_source.read_bytes()
            script_path = str(path)
print(json.dumps({'arch': platform.machine(), 'gpu': gpu.strip(), 'busy': busy,
                  'setup_running': running, 'portal_exists': svc.exists(),
                  'portal_script': script_path, 'portal_clean': clean, 'portal_sha256': digest}))
"""
    encoded = base64.b64encode(script.encode()).decode("ascii")
    code, output = _ssh_run(host, user, password, f"echo '{encoded}' | base64 -d | python3 -",
                            timeout=25, ssh_port=port)
    if code != 0:
        raise RuntimeError(f"No se pudo comprobar el equipo (SSH {code}): {output[-300:]}")
    info = json.loads(output.strip().splitlines()[-1])
    return info


def _auto_deploy(node: dict, user: str, port: int, password: str, agent: bool) -> None:
    with DEPLOY_LOCK:
        if DEPLOY_JOB["running"]:
            raise RuntimeError("Hay otro despliegue en curso; reintenta despues.")
        DEPLOY_JOB.update(running=True, phase="Starting", step=0, started_at=time.time(),
                          ended_at=0.0, node_id=node["id"], host=node["host"],
                          ok=None, error="", log=[])
    _run_deploy_job(node, user, port, password, agent, [], None, not agent)
    result = get_deploy_status()
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "El despliegue no finalizo correctamente.")


def _auto_enroll(host: str, user: str, port: int, password: str) -> dict:
    existing = next((node for node in load_fleet() if node.get("host") == host), None)
    if existing and existing.get("role") == "hub":
        raise RuntimeError("El equipo figura como Master; no se modificara automaticamente.")
    result = enroll_node({"host": host, "ssh_user": user, "ssh_port": port,
                          "ssh_pass": password, "role": "node"})
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "No se pudo iniciar el enrolamiento."))
    while True:
        result = get_enroll_status()
        if not result.get("running"):
            break
        time.sleep(0.5)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "No se pudo enrolar.")
    return next(node for node in load_fleet() if node.get("host") == host)


def _auto_sudo(host: str, user: str, port: int, password: str, command: str, timeout: int) -> None:
    remote = "printf ZGX_SUDO_READY; sudo -k -S -p '' -- sh -c " + shlex.quote(command)
    try:
        import paramiko
    except ImportError:
        code, output = _pty_ssh_run(host, user, password, remote,
                                    stdin_data=(password + "\n").encode(),
                                    timeout=timeout, ssh_port=port, stdin_prompt=b"ZGX_SUDO_READY")
    else:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, port=port, username=user, password=password, timeout=10,
                           allow_agent=False, look_for_keys=False, auth_timeout=10)
            transport = client.get_transport()
            if transport is None:
                raise RuntimeError("La conexion SSH no dispone de transporte.")
            transport.set_keepalive(15)
            channel = transport.open_session()
            channel.set_combine_stderr(True)
            channel.exec_command(remote)
            channel.sendall((password + "\n").encode())
            channel.shutdown_write()
            data = bytearray()
            deadline = time.time() + timeout
            while not channel.exit_status_ready():
                while channel.recv_ready():
                    data.extend(channel.recv(4096))
                if time.time() >= deadline:
                    channel.close()
                    raise TimeoutError("La operacion administrativa supero el tiempo limite.")
                time.sleep(0.1)
            while channel.recv_ready():
                data.extend(channel.recv(4096))
            code, output = channel.recv_exit_status(), data.decode("utf-8", errors="replace")
        finally:
            client.close()
    if code:
        raise RuntimeError(f"Operacion administrativa fallida ({code}): {output.replace(password, '[REDACTED]')[-500:]}")


def _auto_prerequisites(host: str, user: str, port: int, password: str) -> None:
    packages = ("git", "curl", "ca-certificates", "xz-utils", "build-essential", "cmake",
                "ninja-build", "pkg-config", "python3.12", "python3.12-venv", "bubblewrap",
                "apparmor-utils", "util-linux", "libsndfile1", "libespeak-ng1",
                "fonts-liberation", "fonts-noto-core", "gnome-terminal", "xdg-utils",
                "xdg-user-dirs", "ffmpeg")
    command = ("for p in " + " ".join(packages) +
               "; do dpkg-query -W -f='${db:Status-Status}' \"$p\" 2>/dev/null | "
               "grep -qx installed || echo \"$p\"; done")
    code, output = _ssh_run(host, user, password, command, timeout=30, ssh_port=port)
    if code:
        raise RuntimeError("No se pudieron comprobar los paquetes del sistema.")
    missing = output.split()
    if any(package not in packages for package in missing):
        raise RuntimeError("Respuesta no valida al comprobar los paquetes del sistema.")
    if missing:
        _auto_sudo(host, user, port, password,
                   "export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y "
                   + " ".join(missing), timeout=1800)


def _auto_update_existing_portal(host: str, user: str, port: int, password: str, info: dict) -> None:
    if not info.get("portal_clean") or not info.get("portal_script"):
        raise RuntimeError("El portal existente contiene cambios no reconocidos; no se sobrescribira.")
    target = shlex.quote(info["portal_script"])
    digest = info["portal_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Huella del portal existente no valida.")
    backup = "$HOME/.zgx-backups/launcher-" + secrets.token_hex(8) + ".py"
    command = f"""set -eu
target={target}
test "$(sha256sum "$target" | cut -d' ' -f1)" = {digest}
mkdir -p "$HOME/.zgx-backups"
cp -p "$target" "{backup}"
cp "$HOME/.zgx-source/fleet-console/launcher_web.py" "$target.new"
python3 -m py_compile "$target.new"
mv -f "$target.new" "$target"
"""
    encoded = base64.b64encode(command.encode()).decode("ascii")
    code, output = _ssh_run(host, user, password, f"echo '{encoded}' | base64 -d | bash -s",
                            timeout=30, ssh_port=port)
    if code:
        raise RuntimeError(f"No se pudo actualizar el portal: {output[-300:]}")
    try:
        _auto_sudo(host, user, port, password, "systemctl restart zgx-launcher.service", timeout=60)
    except Exception as exc:
        restore = f'cp -p "{backup}" {target}'
        rc, output = _ssh_run(host, user, password, restore, timeout=30, ssh_port=port)
        if rc:
            raise RuntimeError(f"No se pudo reiniciar ni restaurar el portal: {output[-300:]}") from exc
        try:
            _auto_sudo(host, user, port, password, "systemctl restart zgx-launcher.service", timeout=60)
        except Exception as restore_exc:
            raise RuntimeError(f"Se restauro el archivo, pero el portal no arranco: {restore_exc}") from exc
        raise RuntimeError(f"Se restauro el portal anterior tras el fallo: {exc}") from exc


def _auto_start_setup(host: str, user: str, port: int, password: str,
                      collective_only: bool = False) -> None:
    mode = "collective" if collective_only else "all"
    command = """set -eu
umask 077
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
mkdir -p "$HOME/.local/state/zgx-setup"
exec 8>"$HOME/.local/state/zgx-setup/auto-start.lock"
flock -n 8 || { echo "Another Nano setup is starting." >&2; exit 1; }
exec 7>"$HOME/.local/state/zgx-setup/auto.lock"
flock -n 7 || { echo "Existing Nano setup is active." >&2; exit 1; }
if systemctl --user is-active --quiet zgx-auto-setup.service; then
  echo "Existing Nano setup service is active." >&2
  exit 1
fi
if [ __SETUP_MODE__ = collective ] && ! grep -Fxq '# ZGX_SETUP_MODES: all collective' "$HOME/.zgx-source/fleet-console/nano-auto-setup.sh"; then
  echo "Source does not support Collective-only setup; update the source portal." >&2
  exit 1
fi
mkdir -p "$HOME/.config/systemd/user"
unit="$HOME/.config/systemd/user/zgx-auto-setup.service"
candidate="$(mktemp "$HOME/.config/systemd/user/.zgx-auto-XXXXXX")"
trap 'rm -f "$candidate"' EXIT
cat > "$candidate" <<'UNIT'
[Unit]
Description=ZGX Nano automatic setup
After=network-online.target

[Service]
Type=exec
ExecStart=/bin/bash -c 'exec /bin/bash "$HOME/.zgx-source/fleet-console/nano-auto-setup.sh" >> "$HOME/.local/state/zgx-setup/auto.log" 2>&1'

[Install]
WantedBy=default.target
UNIT
if [ -e "$unit" ]; then
  cmp -s "$candidate" "$unit" || { echo "Existing setup unit differs; not overwriting it." >&2; exit 1; }
else
  mv "$candidate" "$unit"
fi
printf '%s\\n' __SETUP_MODE__ > "$HOME/.local/state/zgx-setup/auto.mode.tmp"
mv -f "$HOME/.local/state/zgx-setup/auto.mode.tmp" "$HOME/.local/state/zgx-setup/auto.mode"
printf 'QUEUED\\n' > "$HOME/.local/state/zgx-setup/auto.status"
systemctl --user daemon-reload
systemctl --user enable zgx-auto-setup.service
flock -u 7
systemctl --user start zgx-auto-setup.service
""".replace("__SETUP_MODE__", shlex.quote(mode))
    encoded = base64.b64encode(command.encode()).decode("ascii")
    code, output = _ssh_run(host, user, password, f"echo '{encoded}' | base64 -d | bash -s",
                            timeout=30, ssh_port=port)
    if code:
        raise RuntimeError(f"No se pudo iniciar el servicio de instalacion: {output[-500:]}")


def _auto_setup_status(host: str, user: str, port: int, password: str) -> str:
    command = """if [ ! -f "$HOME/.local/state/zgx-setup/auto.status" ]; then
  echo NO_SETUP_STATUS
  exit 0
fi
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user is-active zgx-auto-setup.service 2>/dev/null || true
cat "$HOME/.local/state/zgx-setup/auto.status" 2>/dev/null || true
case "$(cat "$HOME/.local/state/zgx-setup/auto.status" 2>/dev/null || true)" in
  ERROR:*) tail -n 8 "$HOME/.local/state/zgx-setup/auto.log" 2>/dev/null || true ;;
esac
"""
    encoded = base64.b64encode(command.encode()).decode("ascii")
    code, output = _ssh_run(host, user, password, f"echo '{encoded}' | base64 -d | bash -s",
                            timeout=20, ssh_port=port)
    if code:
        raise RuntimeError("No se pudo consultar la instalacion remota.")
    if output.strip() == "NO_SETUP_STATUS":
        return ""
    lines = output.strip().splitlines()
    state, status = (lines + ["", ""])[:2]
    if status.startswith("ERROR:"):
        return status + ("\n" + "\n".join(lines[2:])[-1200:] if len(lines) > 2 else "")
    if status.startswith("DONE:"):
        return status
    if state == "active":
        return status or "RUNNING: iniciando"
    return "ERROR: el servicio de instalacion termino sin confirmar su resultado; revisa auto.log"


def _auto_worker(cidr: str, user: str, port: int) -> None:
    pending = {}
    failures = 0
    discovered = 0
    try:
        _auto_update("Discovery", message=f"Buscando equipos en {cidr}...")
        scan = scan_local_network(cidr, port)
        if not scan.get("ok"):
            raise RuntimeError(scan.get("error", "Fallo el descubrimiento."))
        for candidate in scan["hosts"]:
            if _auto_cancelled():
                break
            host = candidate["ip"]
            if not candidate.get("ssh"):
                continue
            password = ""
            try:
                password = _get_ssh_password(host, port, user)
                if not password:
                    raise RuntimeError("No hay una contrasena SSH guardada para este usuario.")
                _auto_update("Preparing", host, "Comprobando SSH y hardware...")
                info = _auto_probe(host, user, port, password)
                if info.get("arch") != "aarch64" or "GB10" not in info.get("gpu", "").upper():
                    _auto_update("Preparing", host, "Omitido: no es una Nano ARM64 con GPU GB10.")
                    continue
                discovered += 1
                _save_ssh_password(host, port, user, password)
                local = host == get_local_ip()
                if info.get("setup_running"):
                    pending[host] = True
                    _auto_update("Preparing", host, "Instalacion ya activa; reanudando seguimiento.")
                    continue
                if info.get("busy"):
                    raise RuntimeError("Hay una demo o instalacion activa; no se interrumpira.")
                if info.get("portal_exists") and not local and not info.get("portal_clean"):
                    raise RuntimeError("El portal existente contiene cambios no reconocidos; no se sobrescribira.")
                node = ({"id": "local", "host": host} if local
                        else _auto_enroll(host, user, port, password))
                _auto_update("Preparing", host, "Copiando fuente y preparando el portal...")
                _auto_deploy(node, user, port, password, agent=not info.get("portal_exists") and not local)
                if info.get("portal_exists") and not local:
                    _auto_update_existing_portal(host, user, port, password, info)
                    node = _auto_enroll(host, user, port, password)
                if not local and not next(
                    (n.get("agent_installed") for n in load_fleet() if n.get("host") == host), False
                ):
                    raise RuntimeError("El portal no responde tras la instalacion.")
                _auto_update("Preparing", host, "Comprobando e instalando requisitos del sistema...")
                _auto_prerequisites(host, user, port, password)
                _auto_sudo(host, user, port, password, "loginctl enable-linger " + shlex.quote(user), timeout=30)
                _auto_start_setup(host, user, port, password)
                pending[host] = True
                _auto_update("Preparing", host, "Instalacion iniciada en segundo plano.")
            except Exception as exc:
                failures += 1
                message = str(exc).replace(password, "[REDACTED]") if password else str(exc)
                _auto_update("Preparing", host, "ERROR: " + message)
        if not discovered and not _auto_cancelled():
            raise RuntimeError("No se ha identificado ninguna Nano GB10 accesible.")
        _auto_update("Monitoring", message=f"Siguiendo {len(pending)} instalaciones.")
        while pending and not _auto_cancelled():
            for host in list(pending):
                password = ""
                try:
                    password = _get_ssh_password(host, port, user)
                    state = _auto_setup_status(host, user, port, password)
                    if get_auto_status()["items"].get(host) != state:
                        _auto_update("Monitoring", host, state)
                    if state.startswith(("DONE:", "ERROR:")):
                        failures += int(state.startswith("ERROR:"))
                        del pending[host]
                except Exception as exc:
                    message = str(exc).replace(password, "[REDACTED]") if password else str(exc)
                    _auto_update("Monitoring", host, "Sin conexion; se reintentara: " + message)
            if pending:
                time.sleep(30)
        failed_sync = sync_fleet_to_peers()
        if failed_sync:
            failures += len(failed_sync)
            _auto_update("Monitoring", message="Sincronizacion pendiente: " + ", ".join(failed_sync))
        cancelled = _auto_cancelled()
        _auto_update("Cancelled" if cancelled else ("Error" if failures else "Done"),
                     message=("Seguimiento cancelado; las instalaciones remotas continuan." if cancelled else
                              f"Finalizado: {failures} errores; revisa el resultado de cada equipo."),
                     running=False, ok=not failures and not cancelled)
    except Exception as exc:
        _auto_update("Error", message="ERROR: " + str(exc), running=False, ok=False)


def start_auto_deployment(payload: dict) -> dict:
    try:
        network = ipaddress.ip_network((payload.get("cidr") or "192.168.1.0/24").strip(), strict=False)
        if network.version != 4 or network.num_addresses > 512:
            raise ValueError("Indica una subred IPv4 de hasta 512 direcciones.")
        user = (payload.get("ssh_user") or "nvidia").strip()
        port = int(payload.get("ssh_port") or 22)
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", user) or not 1 <= port <= 65535:
            raise ValueError("Usuario o puerto SSH no valido.")
        if load_config().get("role") != "hub":
            raise ValueError("Inicia el despliegue automatico desde el Master.")
        password = payload.get("ssh_pass") or _get_default_ssh_password(user)
        if not password:
            raise ValueError("Guarda o indica la contrasena SSH predeterminada.")
        with AUTO_LOCK:
            if AUTO_JOB["running"]:
                return {"ok": False, "error": "Ya hay un despliegue automatico activo."}
            AUTO_JOB.update(running=True, phase="Starting", items={}, log=[], cancel=False,
                            ok=None, cidr=str(network), ssh_user=user, ssh_port=port)
        _save_ssh_password("", 0, user, password)
        _auto_update(message="Despliegue automatico iniciado.")
        threading.Thread(target=_auto_worker, args=(str(network), user, port), daemon=True).start()
        return {"ok": True, "started": True}
    except (OSError, ValueError) as exc:
        with AUTO_LOCK:
            AUTO_JOB["running"] = False
        return {"ok": False, "error": str(exc)}


def resume_auto_deployment() -> None:
    path = _auto_state_path()
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("running") and not state.get("cancel"):
            with AUTO_LOCK:
                AUTO_JOB.update(state)
            threading.Thread(target=_auto_worker,
                             args=(state["cidr"], state["ssh_user"], state["ssh_port"]),
                             daemon=True).start()
    except (OSError, ValueError, KeyError) as exc:
        print(f"[ERROR] No se pudo reanudar el despliegue automatico: {exc}", flush=True)


def remove_fleet_node(node_id: str) -> dict:
    nodes = [n for n in load_fleet() if n["id"] != node_id]
    save_fleet(nodes)
    sync_fleet_to_peers()
    return {"ok": True}


def set_local_role(new_role: str, min_epoch: int = 0) -> dict:
    if new_role not in ("hub", "node"):
        return {"ok": False, "error": "Rol invalido"}
    cfg = load_config()
    prev_role = cfg.get("role")
    cfg["role"] = new_role
    if new_role == "hub" and (prev_role != "hub" or min_epoch >= int(cfg.get("hub_epoch", 0))):
        cfg["hub_epoch"] = max(int(cfg.get("hub_epoch", 0)), int(min_epoch)) + 1
    save_config(cfg)
    return {"ok": True, "role": new_role, "hub_epoch": cfg.get("hub_epoch", 0)}


def free_gpu_action() -> dict:
    stopped = []
    errors = []
    for unit in ("zgx-watchdog.service", "ollama.service", "comfyui.service", "sd-webui.service"):
        res = control_service(unit, "stop")
        if res.get("ok"):
            stopped.append(unit)
        else:
            errors.append(f"{unit}: {res.get('error')}")
    return {"ok": True, "stopped": stopped, "errors": errors}


LOCATE_LOCK = threading.Lock()
_LOCATE_STATE = {"running": False}


def locate_node_action(duration_s: int = 8) -> dict:
    duration_s = max(3, min(20, int(duration_s or 8)))
    with LOCATE_LOCK:
        if _LOCATE_STATE["running"]:
            return {"ok": True, "already_running": True, "duration_s": duration_s, "hostname": socket.gethostname()}
        _LOCATE_STATE["running"] = True

    def _burst():
        procs = []
        try:
            workers = max(2, min(12, os.cpu_count() or 4))
            code = f"import time; end=time.monotonic()+{duration_s}; x=1.0001\nwhile time.monotonic()<end: x=(x*1.000001)%997.0"
            for _ in range(workers):
                procs.append(subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            for p in procs:
                try:
                    p.wait(timeout=duration_s + 5)
                except Exception:
                    p.kill()
        except Exception:
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
        finally:
            with LOCATE_LOCK:
                _LOCATE_STATE["running"] = False

    threading.Thread(target=_burst, daemon=True).start()
    return {"ok": True, "duration_s": duration_s, "hostname": socket.gethostname()}


def _run_nmcli(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    cmd = ["nmcli"] + args
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode == 0:
        return res
    sudo_res = subprocess.run(["sudo", "-n", "/usr/bin/nmcli"] + args, capture_output=True, text=True, timeout=timeout)
    return sudo_res if sudo_res.returncode == 0 or not res.stderr else res


def _get_active_nm_connection() -> dict | None:
    res = subprocess.run(
        ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE,UUID", "connection", "show", "--active"],
        capture_output=True, text=True, timeout=5,
    )
    if res.returncode != 0:
        return None
    conns = []
    for line in res.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        name, ctype, dev, uuid = parts[0], parts[1], parts[2], parts[3]
        if ctype in ("802-3-ethernet", "802-11-wireless"):
            conns.append({"name": name, "type": ctype, "device": dev, "uuid": uuid})
    if not conns:
        return None
    conns.sort(key=lambda c: 0 if c["type"] == "802-3-ethernet" else 1)
    return conns[0]


def _default_gateway_and_cidr(device: str) -> tuple[str, str]:
    gw = ""
    cidr = ""
    try:
        r = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=4)
        for line in r.stdout.splitlines():
            parts = line.split()
            if "via" in parts:
                gw = parts[parts.index("via") + 1]
                break
    except Exception:
        pass
    try:
        r2 = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", device], capture_output=True, text=True, timeout=4)
        for line in r2.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                cidr = parts[parts.index("inet") + 1]
                break
    except Exception:
        pass
    return gw, cidr


def set_network_mode_action(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Payload invalido."}
    mode = str(payload.get("mode", "")).strip().lower()
    if mode not in ("static", "dhcp"):
        return {"ok": False, "error": "Modo de red invalido (usa static o dhcp)."}
    conn = _get_active_nm_connection()
    if not conn:
        return {"ok": False, "error": "No se encontro una conexion NetworkManager activa."}
    uuid = conn["uuid"]
    cur_gw, cur_cidr = _default_gateway_and_cidr(conn["device"])

    if mode == "static":
        ip_cidr = str(payload.get("ip_cidr") or cur_cidr or "").strip()
        if not ip_cidr:
            return {"ok": False, "error": "No se pudo determinar la IP/CIDR actual."}
        if "/" not in ip_cidr:
            ip_cidr = f"{ip_cidr}/24"
        try:
            iface = ipaddress.ip_interface(ip_cidr)
            ip_cidr = str(iface)
        except ValueError:
            return {"ok": False, "error": f"IP/CIDR invalida: {ip_cidr}"}
        gateway = str(payload.get("gateway") or cur_gw or "").strip()
        if not gateway:
            gateway = str(iface.network.network_address + 1)
        try:
            ipaddress.ip_address(gateway)
        except ValueError:
            return {"ok": False, "error": f"Gateway invalido: {gateway}"}
        dns = str(payload.get("dns") or f"{gateway},8.8.8.8").strip()
        mod = _run_nmcli([
            "connection", "modify", uuid,
            "ipv4.method", "manual",
            "ipv4.addresses", ip_cidr,
            "ipv4.gateway", gateway,
            "ipv4.dns", dns,
        ])
        if mod.returncode != 0:
            return {"ok": False, "error": (mod.stderr or mod.stdout or "Error configurando IP fija").strip()}
        summary = {"ok": True, "mode": "static", "connection": conn["name"], "device": conn["device"], "ip_cidr": ip_cidr, "gateway": gateway, "dns": dns}
    else:
        mod = _run_nmcli([
            "connection", "modify", uuid,
            "ipv4.method", "auto",
            "ipv4.addresses", "",
            "ipv4.gateway", "",
            "ipv4.dns", "",
        ])
        if mod.returncode != 0:
            return {"ok": False, "error": (mod.stderr or mod.stdout or "Error configurando DHCP").strip()}
        summary = {"ok": True, "mode": "dhcp", "connection": conn["name"], "device": conn["device"]}

    def _reapply():
        time.sleep(1.0)
        _run_nmcli(["connection", "up", uuid], timeout=20)
        time.sleep(2.0)
        try:
            hubs = [n for n in load_fleet() if n.get("role") == "hub"]
            if hubs:
                announce_self_to_hub(hubs[0])
        except Exception:
            pass

    threading.Thread(target=_reapply, daemon=True).start()
    return summary


def prune_docker_action() -> dict:
    try:
        res = subprocess.run(
            ["sudo", "-n", "/usr/bin/docker", "system", "prune", "-af", "--volumes"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode != 0:
            return {"ok": False, "error": (res.stderr or res.stdout or "").strip()}
        return {"ok": True, "output": (res.stdout or "").strip()[-400:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def power_action(kind: str) -> dict:
    if kind not in ("reboot", "poweroff"):
        return {"ok": False, "error": "Invalid power action"}
    try:
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", kind])
        return {"ok": True, "action": kind}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def self_update_node(payload: dict) -> dict:
    code_b64 = payload.get("code_b64") or ""
    if not code_b64:
        return {"ok": False, "error": "Missing code_b64"}
    try:
        raw = base64.b64decode(code_b64.encode("ascii"))
        tmp = Path("/tmp/launcher_web_update.py")
        tmp.write_bytes(raw)
        chk = subprocess.run(["python3", "-m", "py_compile", str(tmp)], capture_output=True, text=True, timeout=10)
        if chk.returncode != 0:
            return {"ok": False, "error": chk.stderr or "py_compile failed"}
        Path(__file__).write_bytes(raw)
        tmp.unlink(missing_ok=True)
        subprocess.Popen(["bash", "-c", "sleep 0.6 && sudo -n /usr/bin/systemctl restart zgx-launcher.service"])
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def push_update_fleet() -> dict:
    code_b64 = base64.b64encode(Path(__file__).read_bytes()).decode("ascii")
    nodes = load_fleet()
    results = []
    for n in nodes:
        try:
            r = _node_http_json(
                n["host"], n.get("port", 18888), "/api/node/self-update", n.get("token", ""),
                method="POST", timeout=15.0, body={"code_b64": code_b64}
            )
            results.append({"host": n["host"], "alias": n.get("alias"), "ok": bool(r.get("ok")), "error": r.get("error", "")})
        except Exception as exc:
            results.append({"host": n["host"], "alias": n.get("alias"), "ok": False, "error": str(exc)})
    sync_fleet_to_peers()
    return {"ok": True, "results": results}


def proxy_node_request(node_id: str, sub_path: str, method: str = "GET", body: dict = None) -> dict:
    if node_id == "local":
        if method == "GET" and sub_path == "/status":
            return get_status()
        if method == "POST" and sub_path.startswith("/start/"):
            return start_demo(sub_path.split("/start/", 1)[1].strip("/"))
        if method == "POST" and sub_path == "/stop":
            return stop_demo()
        if method == "POST" and sub_path.startswith("/nano-mode/"):
            return switch_nano_mode(sub_path.split("/nano-mode/", 1)[1].strip("/"))
        if method == "POST" and sub_path == "/free-gpu":
            return free_gpu_action()
        if method == "POST" and sub_path == "/locate":
            return locate_node_action()
        if method == "POST" and sub_path == "/network/mode":
            return set_network_mode_action(body or {})
        if method == "POST" and sub_path == "/prune-docker":
            return prune_docker_action()
        if method == "POST" and sub_path.startswith("/power/"):
            return power_action(sub_path.split("/power/", 1)[1].strip("/"))
        if method == "POST" and sub_path.startswith("/service/"):
            parts = sub_path.split("/service/", 1)[1].strip("/").split("/")
            if len(parts) == 2:
                return control_service(urllib.parse.unquote(parts[0]), parts[1])
        return {"ok": False, "error": "Invalid local route"}

    node = next((n for n in load_fleet() if n["id"] == node_id), None)
    if not node:
        return {"ok": False, "error": f"Node not found: {node_id}"}
    if method == "GET" and sub_path == "/last-status":
        snapshot = _read_monitoring_snapshot(node)
        return {"ok": True, **snapshot} if snapshot else {"ok": False, "error": "Sin datos sincronizados."}
    try:
        return _node_http_json(
            node["host"], node.get("port", 18888), "/api" + sub_path, node.get("token", ""), method=method, timeout=120.0, body=body
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HP ZGX Nano - Login</title>
<style>
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: radial-gradient(circle at top right, #0d1d2f 0%, #070b11 60%);
    color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  .box {
    width: 100%; max-width: 390px; background: #0f1621; border: 1px solid #223044;
    border-radius: 14px; padding: 28px 26px; box-shadow: 0 20px 50px rgba(0,0,0,0.45);
  }
  .badge {
    display: inline-block; background: linear-gradient(135deg, #0096d6, #005b8f);
    color: #fff; font-weight: 700; font-size: 11px; letter-spacing: 0.08em;
    padding: 5px 10px; border-radius: 6px; margin-bottom: 12px;
  }
  h1 { margin: 0 0 6px; font-size: 20px; }
  p { margin: 0 0 20px; font-size: 13px; color: #8b9cb0; }
  label { display: block; font-size: 12px; color: #8b9cb0; margin-bottom: 6px; }
  input {
    width: 100%; box-sizing: border-box; padding: 10px 12px; margin-bottom: 14px;
    background: #080d14; border: 1px solid #223044; border-radius: 8px;
    color: #e6edf3; font-size: 14px; outline: none;
  }
  input:focus { border-color: #0096d6; }
  .password-row { display: flex; gap: 8px; align-items: flex-start; }
  .password-row input { flex: 1; min-width: 0; }
  .password-row button {
    width: auto; padding: 10px 12px; background: #223044; white-space: nowrap;
  }
  button {
    width: 100%; padding: 11px; border: none; border-radius: 8px; cursor: pointer;
    background: #0096d6; color: #fff; font-size: 14px; font-weight: 600;
  }
  .err {
    display: none; background: rgba(248,81,73,0.14); border: 1px solid #f85149;
    color: #ffa198; padding: 8px 10px; border-radius: 7px; font-size: 12px; margin-bottom: 12px;
  }
</style>
</head>
<body>
  <div class="box">
    <div class="badge">HP ZGX NANO FLEET</div>
    <h1>Acceso al Portal</h1>
    <p>Introduce tus credenciales para gestionar la flota ZGX Nano.</p>
    <div id="err" class="err"></div>
    <form onsubmit="doLogin(event)">
      <label for="u">Usuario</label>
      <input id="u" type="text" autocomplete="username" required>
      <label for="p">Contrasena</label>
      <div class="password-row">
        <input id="p" type="password" autocomplete="current-password" required>
        <button type="button" aria-controls="p" aria-pressed="false" onclick="toggleLoginPassword(this)">Mostrar</button>
      </div>
      <button type="submit">Entrar</button>
    </form>
  </div>
<script>
function toggleLoginPassword(button) {
  const input = document.getElementById("p");
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  button.textContent = visible ? "Ocultar" : "Mostrar";
  button.setAttribute("aria-pressed", String(visible));
}
async function doLogin(e) {
  e.preventDefault();
  const err = document.getElementById("err");
  err.style.display = "none";
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: document.getElementById("u").value, password: document.getElementById("p").value })
  });
  const d = await res.json();
  if (d.ok) {
    window.location.href = "/";
  } else {
    err.textContent = d.error || "Credenciales invalidas";
    err.style.display = "block";
  }
}
</script>
</body>
</html>
"""


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HP ZGX Nano - Fleet Console</title>
<style>
  :root {
    --bg: #070b11;
    --surface: #0f1621;
    --surface2: #151f2e;
    --border: #223044;
    --text: #e6edf3;
    --muted: #8b9cb0;
    --hp-blue: #0096d6;
    --nv-green: #76b900;
    --ok: #2ea043;
    --warn: #d29922;
    --err: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: radial-gradient(circle at top right, #0d1d2f 0%, var(--bg) 55%);
    color: var(--text);
    line-height: 1.45;
  }
  .wrap { max-width: 1260px; margin: 0 auto; padding: 20px 20px 48px; }
  header {
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
    gap: 14px; padding-bottom: 16px; border-bottom: 1px solid var(--border); margin-bottom: 16px;
  }
  .brand { display: flex; align-items: center; gap: 14px; }
  .badge-hw {
    background: linear-gradient(135deg, #0096d6, #005b8f);
    color: #fff; font-weight: 700; font-size: 12px; letter-spacing: 0.08em;
    padding: 8px 12px; border-radius: 8px; cursor: pointer;
  }
  h1 { margin: 0; font-size: 20px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .header-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .pill {
    padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--border); background: var(--surface);
  }
  .pill.ready { border-color: var(--ok); color: #56d364; background: rgba(46,160,67,0.12); }
  .pill.starting { border-color: var(--warn); color: #e3b341; background: rgba(210,153,34,0.12); }
  .pill.idle { color: var(--muted); }

  .lang-switch {
    display: inline-flex; background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden;
  }
  .lang-btn {
    border: none; background: transparent; color: var(--muted);
    padding: 6px 10px; font-size: 11px; font-weight: 700; cursor: pointer;
    letter-spacing: 0.05em; border-radius: 0;
  }
  .lang-btn.active { background: var(--hp-blue); color: #fff; }

  button, a.btn {
    cursor: pointer; border: 1px solid var(--border); border-radius: 7px;
    padding: 6px 11px; font-size: 12px; font-weight: 600; text-decoration: none;
    background: var(--surface2); color: var(--text); display: inline-flex; align-items: center;
  }
  button.primary { background: rgba(0, 150, 214, 0.20); border-color: var(--hp-blue); color: #79c0ff; }
  button.solid { background: var(--hp-blue); border-color: var(--hp-blue); color: #fff; }
  button.warn { background: rgba(210, 153, 34, 0.16); border-color: var(--warn); color: #e3b341; }
  button.danger { background: rgba(248, 81, 73, 0.14); border-color: var(--err); color: #ff7b72; }
  a.btn.open, button.open { background: rgba(46, 160, 67, 0.16); border-color: var(--ok); color: #56d364; }
  button:disabled { opacity: 0.42; cursor: not-allowed; }

  /* Fleet view */
  .fleet-bar {
    display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;
    gap: 10px; margin-bottom: 16px;
  }
  .fleet-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 16px;
  }
  .fleet-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 16px; display: flex; flex-direction: column; gap: 12px;
  }
  .fleet-card.online { border-color: #274463; }
  .fleet-card.offline { opacity: 0.72; border-color: #3a2326; }
  .fleet-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
  .fleet-metrics {
    display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;
    background: #09101a; border: 1px solid #1b2738; border-radius: 8px; padding: 10px;
    font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  /* Modal */
  .modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.72); display: none;
    align-items: center; justify-content: center; z-index: 9999; padding: 16px;
  }
  .modal-bg.open { display: flex; }
  .modal {
    width: 100%; max-width: 720px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 14px; padding: 20px; max-height: 90vh; overflow-y: auto;
  }
  .modal h2 { margin: 0 0 12px; font-size: 18px; }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-bottom: 12px; }
  .field label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; }
  .field input, .field select {
    width: 100%; background: #080d14; border: 1px solid var(--border); color: var(--text);
    border-radius: 7px; padding: 8px 10px; font-size: 13px; outline: none;
  }
  .password-row { display: flex; align-items: center; gap: 6px; }
  .password-row input { flex: 1; min-width: 0; }
  .password-row button { flex: none; }
  .scan-list {
    max-height: 230px; overflow-y: auto; border: 1px solid #1b2738; border-radius: 8px;
    background: #070b10; margin-bottom: 10px;
  }
  .scan-row {
    display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 10px;
    border-bottom: 1px solid #121b28; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    cursor: pointer; flex-wrap: wrap;
  }
  .scan-row:hover { background: #131e2c; }
  .scan-row.scan-dim { opacity: 0.62; }
  .scan-badge {
    display: inline-block; padding: 2px 6px; border-radius: 5px; font-size: 10px; font-weight: 700;
    border: 1px solid #2b4566; background: #0c1724; color: #9fb3c8; margin-right: 4px;
  }
  .scan-badge.nano { border-color: var(--nv-green); color: #76b900; background: rgba(118, 185, 0, 0.14); }
  .scan-badge.fury { border-color: var(--hp-blue); color: #58a6ff; background: rgba(0, 150, 214, 0.16); }
  .scan-badge.enrolled { border-color: #30445e; color: #8b949e; background: #0d141e; }
  .scan-badge.ok { border-color: var(--ok); color: #56d364; background: rgba(46, 160, 67, 0.14); }
  .scan-filters { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
  .scan-chip {
    cursor: pointer; background: #09101a; border: 1px solid var(--border); color: var(--muted);
    padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
  }
  .scan-chip.active { background: rgba(0, 150, 214, 0.18); border-color: var(--hp-blue); color: #fff; }
  .deploy-note {
    background: rgba(0, 150, 214, 0.10); border: 1px solid #1f4466; border-radius: 8px;
    padding: 9px 12px; font-size: 12px; color: #b7d5ee; margin-bottom: 12px;
  }
  .deploy-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
  .deploy-item {
    display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px;
    background: #080d14; border: 1px solid #1b2738; border-radius: 8px; cursor: pointer;
  }
  .deploy-item:hover { border-color: #2b4566; }
  .deploy-item input[type="radio"] { margin-top: 3px; width: auto; accent-color: var(--hp-blue); }
  .deploy-item strong { display: block; font-size: 13px; color: var(--text); }
  .deploy-item span { display: block; font-size: 11px; color: var(--muted); margin-top: 2px; }

  /* Telemetry & Node Console */
  .telemetry {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
    gap: 12px; margin-bottom: 18px;
  }
  .metric-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px;
  }
  .metric-head {
    display: flex; justify-content: space-between; align-items: baseline;
    font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em;
  }
  .metric-val { font-size: 20px; font-weight: 700; margin: 4px 0 6px; }
  .metric-sub { font-size: 11px; color: var(--muted); margin-top: 5px; }
  .bar-track {
    width: 100%; height: 7px; background: #090e15; border-radius: 999px;
    overflow: hidden; border: 1px solid #1b2738;
  }
  .bar-fill {
    height: 100%; width: 0%; border-radius: 999px; transition: width 0.4s ease, background 0.3s ease;
    background: linear-gradient(90deg, var(--hp-blue), #38bdf8);
  }
  .bar-fill.gpu { background: linear-gradient(90deg, #569e00, var(--nv-green)); }
  .bar-fill.warn { background: linear-gradient(90deg, #d29922, #f0b429); }
  .bar-fill.crit { background: linear-gradient(90deg, #da3633, #f85149); }

  .nav-bar {
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 18px;
  }
  .nav-tab {
    cursor: pointer; background: var(--surface); border: 1px solid var(--border);
    color: var(--muted); padding: 8px 16px; border-radius: 8px;
    font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 8px;
  }
  .nav-tab.active { background: rgba(0, 150, 214, 0.16); border-color: var(--hp-blue); color: #fff; }
  .nav-count {
    font-size: 11px; padding: 1px 7px; border-radius: 999px;
    background: #09101a; border: 1px solid var(--border); color: var(--muted);
  }
  .tab-pane { display: none; }
  .tab-pane.active { display: block; }
  .section-note { font-size: 13px; color: var(--muted); margin: 0 0 14px; }

  .svc-toolbar { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; margin-bottom: 14px; }
  .svc-filters { display: flex; gap: 6px; flex-wrap: wrap; }
  .svc-filter { cursor: pointer; background: var(--surface); border: 1px solid var(--border); color: var(--muted); padding: 6px 12px; border-radius: 7px; font-size: 12px; font-weight: 600; }
  .svc-filter.active { background: rgba(118, 185, 0, 0.16); border-color: var(--nv-green); color: #fff; }
  .svc-search { background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 7px; padding: 7px 12px; font-size: 13px; min-width: 220px; outline: none; }
  select.svc-select { background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 7px; padding: 6px 10px; font-size: 12px; font-weight: 600; outline: none; }
  .svc-controls-right { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .svc-table-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; overflow-x: auto; }

  .svc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 16px; }
  .demo-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(265px, 1fr)); gap: 16px; }
  .demo-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
  .demo-card.active { border-color: var(--hp-blue); box-shadow: 0 0 0 1px var(--hp-blue); }
  .demo-card.svc-active { border-color: var(--nv-green); }
  .thumb { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #09101a; border-bottom: 1px solid var(--border); overflow: hidden; }
  .svc-grid .thumb { aspect-ratio: 21 / 8; }
  .thumb svg { width: 100%; height: 100%; display: block; }
  .thumb-tag { position: absolute; top: 10px; left: 10px; font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 5px; background: rgba(7, 11, 17, 0.82); border: 1px solid rgba(255,255,255,0.14); color: #cde4f7; }
  .thumb-state { position: absolute; top: 10px; right: 10px; font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 5px; background: rgba(7, 11, 17, 0.85); border: 1px solid var(--border); color: var(--muted); }
  .thumb-state.ready { border-color: var(--ok); color: #56d364; }
  .thumb-state.starting { border-color: var(--warn); color: #e3b341; }
  .demo-body { padding: 14px 16px 16px; display: flex; flex-direction: column; justify-content: space-between; flex: 1; gap: 10px; }
  .demo-body h2 { margin: 0 0 5px; font-size: 16px; font-weight: 600; }
  .demo-body p { margin: 0; font-size: 13px; color: var(--muted); }
  .svc-telem { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; color: #9fb3c8; background: #09101a; border: 1px solid #1b2738; border-radius: 6px; padding: 6px 9px; }
  .demo-footer { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding-top: 10px; border-top: 1px solid #1b2738; flex-wrap: wrap; }
  .port-lbl { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--muted); }
  .btn-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .mode-switch { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; background: #080d14; border: 1px solid #1b2738; border-radius: 8px; padding: 3px; margin: 10px 0 6px; }
  .mode-pill { border: 0; background: transparent; color: var(--muted); font-size: 11px; font-weight: 600; padding: 6px 8px; border-radius: 6px; cursor: pointer; display: flex; flex-direction: column; align-items: flex-start; gap: 1px; text-align: left; transition: all 0.2s; }
  .mode-pill:hover { color: #e6edf3; background: rgba(255,255,255,0.04); }
  .mode-pill.selected { background: rgba(0, 150, 214, 0.16); color: #cde4f7; box-shadow: inset 0 0 0 1px rgba(0, 150, 214, 0.55); }
  .mode-pill.selected.arena { background: rgba(118, 185, 0, 0.16); color: #d8f5a2; box-shadow: inset 0 0 0 1px rgba(118, 185, 0, 0.55); }
  .mode-pill small { font-size: 10px; font-weight: 500; opacity: 0.78; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  .switch { position: relative; display: inline-block; width: 46px; height: 24px; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider { position: absolute; cursor: pointer; inset: 0; background: #1f2d40; border: 1px solid #30445e; transition: 0.25s; border-radius: 999px; }
  .slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 2px; bottom: 2px; background: #c9d1d9; transition: 0.25s; border-radius: 50%; }
  input:checked + .slider { background: var(--hp-blue); border-color: var(--hp-blue); }
  input:checked + .slider:before { transform: translateX(22px); background: #fff; }

  .charts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 14px; margin-bottom: 16px; }
  .chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .chart-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  .chart-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 600; }
  .chart-live { font-size: 15px; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .chart-svg { width: 100%; height: 115px; display: block; background: #070b10; border: 1px solid #1b2738; border-radius: 8px; }
  .cores-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(56px, 1fr)); gap: 6px; }
  .core-item { background: #080d14; border: 1px solid #1b2738; border-radius: 6px; padding: 5px 6px; text-align: center; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .core-lbl { font-size: 10px; color: var(--muted); }
  .core-val { font-size: 11px; font-weight: 700; margin: 2px 0; }
  .core-bar { height: 4px; background: #141e2b; border-radius: 99px; overflow: hidden; }
  .core-fill { height: 100%; background: var(--hp-blue); transition: width 0.3s ease; }
  .mem-stack { height: 16px; width: 100%; background: #080d14; border: 1px solid #1b2738; border-radius: 999px; overflow: hidden; display: flex; margin: 8px 0; }
  .mem-seg-used { background: #0096d6; height: 100%; }
  .mem-seg-cache { background: #2dd4bf; height: 100%; }
  .mem-legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 11px; color: var(--muted); }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
  .two-col { display: grid; grid-template-columns: 1fr 1.2fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 15px 16px; }
  .panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .panel-head h3 { margin: 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
  pre { margin: 0; background: #060a0f; border: 1px solid #1b2738; border-radius: 8px; padding: 11px 12px; font-size: 12px; color: #c9d1d9; overflow-x: auto; white-space: pre-wrap; word-break: break-word; max-height: 260px; overflow-y: auto; }
  .gpu-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .gpu-proc { display: flex; justify-content: space-between; font-size: 12px; background: #09101a; border: 1px solid #1b2738; border-radius: 6px; padding: 6px 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  table.ptable { width: 100%; border-collapse: collapse; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  table.ptable th { text-align: left; color: var(--muted); font-weight: 600; padding: 5px 6px; border-bottom: 1px solid #1b2738; }
  table.ptable td { padding: 5px 6px; border-bottom: 1px solid #111a26; }
  .ports-wrap { display: flex; flex-wrap: wrap; gap: 6px; }
  .port-chip { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; padding: 4px 8px; border-radius: 6px; border: 1px solid #1b2738; background: #080d14; color: var(--muted); }
  .port-chip.open { border-color: var(--ok); color: #56d364; background: rgba(46,160,67,0.1); }
  .errbox { background: rgba(248, 81, 73, 0.12); border: 1px solid var(--err); color: #ffa198; padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; display: none; }
  .token-banner { background: linear-gradient(135deg, rgba(0,150,214,0.09), rgba(46,160,67,0.07)); border: 1px solid #1f3b58; border-radius: 12px; padding: 14px 16px; margin-bottom: 16px; }
  .token-banner-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .token-banner-head h3 { margin: 0; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: #79c0ff; }
  .token-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 10px; }
  .token-kpi { background: rgba(8,13,20,0.75); border: 1px solid #1b2d42; border-radius: 8px; padding: 10px 12px; }
  .token-kpi-lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .token-kpi-val { font-size: 20px; font-weight: 700; margin-top: 3px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .token-kpi-val.eur { color: #56d364; }
  .token-kpi-sub { font-size: 11px; color: var(--muted); margin-top: 2px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .token-demo-strip { display: flex; flex-wrap: wrap; gap: 8px; }
  .token-chip { background: #080d14; border: 1px solid #1b2738; border-radius: 8px; padding: 6px 10px; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; display: flex; align-items: center; gap: 8px; }
  .token-chip strong { color: var(--text); }
  .token-chip .eur-tag { color: #56d364; font-weight: 700; }
  .demo-tok-bar { display: flex; justify-content: space-between; align-items: center; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #080d14; border: 1px solid #1b2738; border-radius: 6px; padding: 5px 8px; margin-top: 8px; color: var(--muted); }
  .demo-tok-bar .eur-tag { color: #56d364; font-weight: 700; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="badge-hw" onclick="showFleet()">HP ZGX</div>
      <div>
        <h1 id="host-title">ZGX Fleet Console</h1>
        <div class="sub" id="host-sub">--</div>
      </div>
    </div>
    <div class="header-right">
      <button id="btn-back-fleet" class="primary" style="display:none;" onclick="showFleet()"></button>
      <button id="btn-claim-master" class="warn" style="display:none;" onclick="claimMaster()"></button>
      <a class="btn" href="/live" target="_blank" rel="noopener">Wallboard /live</a>
      <button id="btn-users" onclick="openUsersModal()"></button>
      <div class="lang-switch">
        <button id="lang-es" class="lang-btn" onclick="setLang('es')">CASTELLANO</button>
        <button id="lang-en" class="lang-btn" onclick="setLang('en')">ENGLISH</button>
      </div>
      <span id="global-status" class="pill idle">[IDLE]</span>
      <button id="btn-stop-all" class="danger" style="display:none;" onclick="stopDemo()"></button>
      <button id="btn-logout" onclick="doLogout()"></button>
    </div>
  </header>

  <div id="err-box" class="errbox"></div>

  <!-- FLEET ACCESS VIEW -->
  <div id="view-fleet" style="display:none;">
    <div class="fleet-bar">
      <div>
        <h2 id="fleet-title" style="margin:0;font-size:18px;"></h2>
        <div id="fleet-sub" class="sub"></div>
      </div>
      <div class="btn-row">
        <button id="btn-start-all-nano" class="solid" onclick="startAllNanoCollective()"></button>
        <button id="btn-reorganize-fleet" class="primary" onclick="reorganizeFleet()"></button>
        <button id="btn-push-update" class="primary" onclick="pushFleetUpdate()"></button>
        <button id="btn-role-toggle" onclick="toggleLocalRole()"></button>
        <button id="btn-add-node" class="solid" onclick="openEnrollModal()"></button>
      </div>
    </div>
    <div id="fleet-token-panel" class="token-banner"></div>
    <div id="fleet-grid" class="fleet-grid"></div>
  </div>

  <!-- SINGLE NODE CONSOLE VIEW -->
  <div id="view-node" style="display:none;">
    <div class="telemetry">
      <div class="metric-card">
        <div class="metric-head"><span id="lbl-cpu">CPU</span><span id="cpu-cores">--</span></div>
        <div class="metric-val" id="cpu-val">--%</div>
        <div class="bar-track"><div id="cpu-bar" class="bar-fill"></div></div>
        <div class="metric-sub" id="cpu-load">Load: --</div>
      </div>
      <div class="metric-card">
        <div class="metric-head"><span id="lbl-ram">RAM</span><span id="ram-pct">--%</span></div>
        <div class="metric-val" id="ram-val">-- / -- GiB</div>
        <div class="bar-track"><div id="ram-bar" class="bar-fill"></div></div>
        <div class="metric-sub" id="ram-avail">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-head"><span id="gpu-name">NVIDIA GB10</span><span id="gpu-temp">-- C</span></div>
        <div class="metric-val" id="gpu-val">--%</div>
        <div class="bar-track"><div id="gpu-bar" class="bar-fill gpu"></div></div>
        <div class="metric-sub" id="gpu-extra">--</div>
      </div>
      <div class="metric-card">
        <div class="metric-head"><span id="lbl-disk">Storage (/)</span><span id="disk-pct">--%</span></div>
        <div class="metric-val" id="disk-val">-- GB</div>
        <div class="bar-track"><div id="disk-bar" class="bar-fill"></div></div>
        <div class="metric-sub" id="disk-total">--</div>
      </div>
    </div>

    <nav class="nav-bar" style="justify-content:space-between;">
      <div class="btn-row">
        <button id="tab-btn-demos" class="nav-tab active" onclick="setTab('demos')">
          <span id="nav-demos-lbl">Demos</span><span id="nav-demos-cnt" class="nav-count">0/4</span>
        </button>
        <button id="tab-btn-services" class="nav-tab" onclick="setTab('services')">
          <span id="nav-services-lbl">Servicios</span><span id="nav-services-cnt" class="nav-count">0/0</span>
        </button>
        <button id="tab-btn-telemetry" class="nav-tab" onclick="setTab('telemetry')">
          <span id="nav-telemetry-lbl">Telemetria</span><span id="nav-gpu-cnt" class="nav-count">0 GPU</span>
        </button>
      </div>
      <div class="btn-row">
        <button id="btn-node-deploy" class="solid" style="display:none;" onclick="openNodeDeployModal()"></button>
        <button id="btn-locate-node" onclick="locateNodeAction()"></button>
        <button id="btn-static-ip" onclick="networkModeAction('static')"></button>
        <button id="btn-dhcp-ip" onclick="networkModeAction('dhcp')"></button>
        <button id="btn-free-gpu" class="primary" onclick="freeGpuAction()"></button>
        <button id="btn-prune-docker" class="warn" onclick="pruneDockerAction()"></button>
        <button id="btn-reboot" onclick="powerAction('reboot')"></button>
        <button id="btn-poweroff" class="danger" onclick="powerAction('poweroff')"></button>
      </div>
    </nav>

    <section id="tab-demos" class="tab-pane active">
      <div id="node-token-panel" class="token-banner"></div>
      <p id="demos-note" class="section-note"></p>
      <div id="demo-grid" class="demo-grid"></div>
      <div class="panel" style="margin-top:16px;">
        <div class="panel-head">
          <h3 id="lbl-demo-term">Salida de terminal (demo en ejecucion)</h3>
          <div class="btn-row">
            <span id="demo-term-state" class="port-lbl">[IDLE]</span>
            <button id="btn-copy-demo-log" onclick="copyLogBox('demo-term-box','btn-copy-demo-log')"></button>
          </div>
        </div>
        <pre id="demo-term-box" style="max-height:220px;">--</pre>
      </div>
    </section>

    <section id="tab-services" class="tab-pane">
      <p id="services-note" class="section-note"></p>
      <div class="svc-toolbar">
        <div class="svc-filters">
          <button id="sf-custom" class="svc-filter active" onclick="setSvcFilter('custom')"></button>
          <button id="sf-active" class="svc-filter" onclick="setSvcFilter('active')"></button>
          <button id="sf-inactive" class="svc-filter" onclick="setSvcFilter('inactive')"></button>
          <button id="sf-all" class="svc-filter" onclick="setSvcFilter('all')"></button>
        </div>
        <div class="svc-controls-right">
          <select id="svc-sort" class="svc-select" onchange="setSvcSort(this.value)">
            <option value="ram" id="opt-sort-ram"></option>
            <option value="cpu" id="opt-sort-cpu"></option>
            <option value="active" id="opt-sort-active"></option>
            <option value="name" id="opt-sort-name"></option>
          </select>
          <select id="svc-view" class="svc-select" onchange="setSvcView(this.value)">
            <option value="cards" id="opt-view-cards"></option>
            <option value="table" id="opt-view-table"></option>
          </select>
          <input id="svc-search" class="svc-search" type="text" oninput="onSvcSearch(this.value)">
        </div>
      </div>
      <div id="svc-grid" class="svc-grid"></div>
      <div id="svc-table-wrap" class="svc-table-panel" style="display:none;">
        <table class="ptable">
          <thead><tr><th>UNIT</th><th>STATE</th><th>PID</th><th>RAM</th><th>CPU%</th><th>DESCRIPTION</th><th>ACTIONS</th></tr></thead>
          <tbody id="svc-table-body"></tbody>
        </table>
      </div>
    </section>

    <section id="tab-telemetry" class="tab-pane">
      <div class="charts-grid">
        <div class="chart-card"><div class="chart-head"><span class="chart-title" id="ch-cpu-lbl"></span><span class="chart-live" id="ch-cpu-val" style="color:#38bdf8;">0%</span></div><svg id="svg-cpu" class="chart-svg" viewBox="0 0 360 115" preserveAspectRatio="none"></svg></div>
        <div class="chart-card"><div class="chart-head"><span class="chart-title" id="ch-gpu-lbl"></span><span class="chart-live" id="ch-gpu-val" style="color:#76b900;">0%</span></div><svg id="svg-gpu" class="chart-svg" viewBox="0 0 360 115" preserveAspectRatio="none"></svg></div>
        <div class="chart-card"><div class="chart-head"><span class="chart-title" id="ch-ram-lbl"></span><span class="chart-live" id="ch-ram-val" style="color:#2dd4bf;">0%</span></div><svg id="svg-ram" class="chart-svg" viewBox="0 0 360 115" preserveAspectRatio="none"></svg></div>
        <div class="chart-card"><div class="chart-head"><span class="chart-title" id="ch-temp-lbl"></span><span class="chart-live" id="ch-temp-val" style="color:#f59e0b;">--</span></div><svg id="svg-temp" class="chart-svg" viewBox="0 0 360 115" preserveAspectRatio="none"></svg></div>
        <div class="chart-card"><div class="chart-head"><span class="chart-title" id="ch-net-lbl"></span><span class="chart-live" id="ch-net-val" style="color:#a855f7;">0 / 0</span></div><svg id="svg-net" class="chart-svg" viewBox="0 0 360 115" preserveAspectRatio="none"></svg></div>
        <div class="chart-card">
          <div class="chart-head"><span class="chart-title" id="ch-io-lbl"></span><span class="chart-live" id="ch-io-val">--</span></div>
          <div class="mem-stack"><div id="seg-used" class="mem-seg-used"></div><div id="seg-cache" class="mem-seg-cache"></div></div>
          <div class="mem-legend" id="mem-legend"></div>
          <div class="svc-telem" id="hw-extra" style="margin-top:10px;">--</div>
        </div>
      </div>
      <div class="panel" style="margin-bottom:16px;">
        <div class="panel-head"><h3 id="lbl-cores"></h3><span id="cores-load" class="port-lbl">--</span></div>
        <div id="cores-grid" class="cores-grid"></div>
      </div>
      <div class="two-col">
        <div class="panel"><div class="panel-head"><h3 id="lbl-top-procs"></h3></div><table class="ptable"><thead><tr><th>PID</th><th>USER</th><th>CPU%</th><th>RAM</th><th>CMD</th></tr></thead><tbody id="top-procs-body"></tbody></table></div>
        <div class="panel"><div class="panel-head"><h3 id="lbl-gpu-procs"></h3></div><div id="gpu-procs" class="gpu-list"></div><div class="panel-head" style="margin-top:10px;"><h3 id="lbl-ports"></h3></div><div id="ports-wrap" class="ports-wrap"></div></div>
      </div>
      <div class="panel">
        <div class="panel-head">
          <h3 id="lbl-live-log"></h3>
          <div class="btn-row">
            <button id="btn-copy-live-log" onclick="copyLogBox('log-box','btn-copy-live-log')"></button>
            <button id="btn-refresh" onclick="refresh()"></button>
          </div>
        </div>
        <pre id="log-box">--</pre>
      </div>
    </section>
  </div>
</div>

<!-- ENROLLMENT MODAL -->
<div id="enroll-modal" class="modal-bg">
  <div class="modal">
    <div class="panel-head">
      <h2 id="m-title" style="margin:0;">Enrolar dispositivo ZGX</h2>
      <button onclick="closeEnrollModal()">X</button>
    </div>
    <div id="enroll-note" class="deploy-note">Solo prueba la conexion y registra el equipo en la flota. No despliega ni reinicia demos automaticamente; usa el boton Desplegar en el portal cuando quieras instalar o actualizar.</div>
    <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center;flex-wrap:wrap;">
      <input id="scan-cidr" type="text" value="" placeholder="Subred auto (ej. 192.168.1.0/24)" style="flex:1;min-width:190px;background:#080d14;border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;">
      <button id="btn-scan" class="solid" type="button" onclick="scanNetwork()"></button>
    </div>
    <div id="scan-filters" class="scan-filters">
      <button id="sf-scan-new" type="button" class="scan-chip active" onclick="setScanFilter('new')"></button>
      <button id="sf-scan-zgx" type="button" class="scan-chip" onclick="setScanFilter('zgx')"></button>
      <button id="sf-scan-all" type="button" class="scan-chip" onclick="setScanFilter('all')"></button>
      <span id="scan-summary" class="port-lbl" style="margin-left:auto;"></span>
    </div>
    <div id="scan-results" class="scan-list"><div id="scan-hint" style="padding:10px;color:var(--muted);font-size:12px;">Pulsa "Buscar Nanos en la red" para descubrir equipos ZGX automaticamente.</div></div>
    <div class="btn-row" style="margin:8px 0;">
      <button id="btn-scan-select" type="button" onclick="toggleScanSelection()" disabled></button>
      <button id="btn-scan-verify" type="button" onclick="verifyScannedCredentials()" disabled></button>
      <button id="btn-scan-add" class="solid" type="button" onclick="addSelectedOrVerifiedHosts()" disabled></button>
      <button id="btn-scan-stop" type="button" onclick="scanCancelRequested = true" style="display:none;"></button>
    </div>
    <div id="scan-verify-note" class="deploy-note"></div>
    <div id="scan-verify-progress" role="status" aria-live="polite"></div>
    <div class="form-grid">
      <div class="field"><label>IP / Host</label><input id="en-host" type="text" placeholder="192.168.1.xxx"></div>
      <div class="field"><label>SSH Port</label><input id="en-ssh-port" type="number" value="22"></div>
      <div class="field"><label>Agent Port</label><input id="en-port" type="number" value="18888"></div>
      <div class="field"><label id="lbl-en-alias">Alias</label><input id="en-alias" type="text" placeholder="Nano-Lab-2"></div>
      <div class="field"><label id="lbl-en-tag">Etiqueta</label><input id="en-tag" type="text" value="ZGX-NANO"></div>
      <div class="field"><label id="lbl-en-role">Rol</label><select id="en-role"><option value="node">Node (Agente)</option><option value="hub">Hub (Principal)</option></select></div>
      <div class="field"><label>SSH User</label><input id="en-user" type="text" value="nvidia"></div>
      <div class="field"><label for="en-pass">SSH Password</label><div class="password-row"><input id="en-pass" type="password" placeholder="Vacio: usar contrasena guardada"><button type="button" class="password-toggle" aria-controls="en-pass" aria-pressed="false" onclick="togglePassword(this)">Mostrar</button></div></div>
    </div>
    <div class="btn-row" style="margin-bottom:10px;"><button id="btn-save-ssh-default" type="button" onclick="saveDefaultSshCredentials()"></button><span id="ssh-default-status" role="status" aria-live="polite"></span></div>
    <div id="auto-note" class="deploy-note"></div>
    <div class="btn-row" style="margin-bottom:10px;">
      <button id="btn-auto-start" class="primary" type="button" onclick="startAutoDeployment()"></button>
      <button id="btn-auto-cancel" type="button" onclick="cancelAutoDeployment()" style="display:none;"></button>
    </div>
    <pre id="auto-log" role="status" aria-live="polite" style="max-height:180px;display:none;"></pre>
    <div id="enroll-prog-wrap" style="display:none;margin-bottom:10px;">
      <div class="panel-head" style="margin-bottom:4px;">
        <span id="enroll-phase" class="port-lbl">Iniciando...</span>
        <span id="enroll-elapsed" class="port-lbl">0s</span>
      </div>
      <div class="bar-track"><div id="enroll-bar" class="bar-fill"></div></div>
    </div>
    <pre id="enroll-log" style="max-height:160px;margin-bottom:12px;display:none;"></pre>
    <div class="btn-row" style="justify-content:flex-end;">
      <button onclick="closeEnrollModal()" id="btn-en-cancel">Cancelar</button>
      <button class="solid" id="btn-en-submit" onclick="submitEnroll()">Enrolar y probar conexion</button>
    </div>
  </div>
</div>

<!-- DEPLOY MODAL -->
<div id="deploy-modal" class="modal-bg">
  <div class="modal">
    <div class="panel-head">
      <h2 id="dep-title" style="margin:0;">Desplegar en dispositivo</h2>
      <button onclick="closeDeployModal()">X</button>
    </div>
    <div id="dep-target" class="deploy-note">--</div>
    <div class="form-grid">
      <div class="field"><label>SSH User</label><input id="dep-ssh-user" type="text" value="nvidia"></div>
      <div class="field"><label>SSH Port</label><input id="dep-ssh-port" type="number" value="22"></div>
      <div class="field"><label for="dep-ssh-pass">SSH Password</label><div class="password-row"><input id="dep-ssh-pass" type="password" placeholder="Vacio: usar contrasena guardada"><button type="button" class="password-toggle" aria-controls="dep-ssh-pass" aria-pressed="false" onclick="togglePassword(this)">Mostrar</button></div></div>
    </div>
    <div class="panel-head" style="margin-bottom:6px;"><h3 id="dep-sel-lbl">Componentes a desplegar</h3></div>
    <div id="dep-list" class="deploy-list"></div>
    <div id="dep-source-wrap" class="field" style="display:none;margin-bottom:12px;">
      <label id="dep-source-label" for="dep-source">Origen de las demos</label>
      <select id="dep-source"></select>
      <div id="dep-source-note" class="port-lbl"></div>
    </div>
    <div id="deploy-prog-wrap" style="display:none;margin-bottom:10px;">
      <div class="panel-head" style="margin-bottom:4px;">
        <span id="deploy-phase" class="port-lbl">Iniciando...</span>
        <span id="deploy-elapsed" class="port-lbl">0s</span>
      </div>
      <div class="bar-track"><div id="deploy-bar" class="bar-fill"></div></div>
    </div>
    <pre id="deploy-log" style="max-height:180px;margin-bottom:12px;display:none;"></pre>
    <div id="dep-nano-status" class="deploy-note" role="status" aria-live="polite" style="display:none;"></div>
    <div class="btn-row" style="justify-content:flex-end;">
      <button onclick="closeDeployModal()" id="btn-dep-cancel">Cerrar</button>
      <button class="solid" id="btn-dep-submit" onclick="submitDeploy()">Desplegar seleccion</button>
    </div>
  </div>
</div>

<!-- USERS MODAL -->
<div id="users-modal" class="modal-bg">
  <div class="modal" style="max-width:560px;">
    <div class="panel-head">
      <h2 id="u-title" style="margin:0;">Gestion de Usuarios</h2>
      <button onclick="closeUsersModal()">X</button>
    </div>
    <div id="users-list" class="gpu-list" style="margin-bottom:14px;"></div>
    <div class="form-grid">
      <div class="field"><label>Usuario</label><input id="u-new-name" type="text" placeholder="nuevo_usuario"></div>
      <div class="field"><label for="u-new-pass">Contrasena</label><div class="password-row"><input id="u-new-pass" type="password" placeholder="Minimo 4 caracteres"><button type="button" class="password-toggle" aria-controls="u-new-pass" aria-pressed="false" onclick="togglePassword(this)">Mostrar</button></div></div>
    </div>
    <div class="btn-row" style="justify-content:flex-end;">
      <button onclick="closeUsersModal()">Cerrar</button>
      <button class="solid" onclick="addUserAction()">Anadir usuario</button>
    </div>
  </div>
</div>

<script>
const I18N = {
  es: {
    subtitle: "Consola de flota y control HP ZGX",
    backFleet: "<- Flota",
    claimMaster: "Reclamar Master (P2P)",
    masterSyncFailed: "Master activo. No se pudo sincronizar con:",
    usersBtn: "Usuarios",
    logout: "Salir",
    fleetTitle: "Unidades HP ZGX registradas",
    fleetSub: "Selecciona una unidad para gestionar sus demos, servicios y telemetria, o enrola una nueva sin autodespliegue.",
    addNode: "+ Enrolar ZGX",
    enterNode: "Entrar",
    cachedMonitoring: "Ver última instantánea",
    removeNode: "Quitar",
    deployBtn: "Desplegar",
    enrollTitle: "Enrolar dispositivo ZGX",
    enrollNote: "Solo prueba la conexion y registra el equipo en la flota. No despliega ni reinicia demos automaticamente; usa el boton Desplegar en el portal cuando quieras instalar o actualizar.",
    enrollSubmit: "Enrolar y probar conexion",
    deployTitle: "Desplegar en dispositivo",
    deploySelLbl: "Componentes a desplegar",
    deployAgentName: "Agente Fleet Console (zgx-launcher.service)",
    deployAgentDesc: "Instala o actualiza launcher_web.py, sudoers y systemd en el dispositivo.",
    deployPortalOnly: "Solo portal",
    deployFury: "Demos Fury",
    deployNano: "Demos Nano ZGX",
    deploySourceOnly: "Solo copiar repositorio",
    deploySourceOnlyDesc: "Copia las fuentes al equipo sin instalar ni reiniciar el portal o las demos.",
    deployFuryDesc: "Instala Fury y prepara sus dependencias y modelos. No instala el portal.",
    deployNanoDesc: "Prepara Nano Collective y sus modelos en segundo plano. No instala el portal ni Partner Kit.",
    deploySource: "Origen del repositorio",
    deployNoSource: "No hay un origen disponible. Actualiza el portal del equipo de origen.",
    deploySubmit: "Desplegar seleccion",
    nanoBackground: "Preparacion Nano en segundo plano",
    nanoStatusError: "No se puede consultar la preparacion Nano",
    showPassword: "Mostrar", hidePassword: "Ocultar",
    closeBtn: "Cerrar",
    cancelBtn: "Cancelar",
    scanBtn: "Buscar Nanos en la red",
    scanCidrPh: "Subred auto (ej. 192.168.1.0/24)",
    scanFilterNew: "Nuevas / Candidatas",
    scanFilterZgx: "Todas las ZGX",
    scanFilterAll: "Toda la red",
    scanEmptyNew: "No hay Nanos nuevas pendientes de enrolar en esta subred.",
    scanShowEnrolledBtn: "Ver ZGX ya enroladas",
    scanBadgeLocal: "ESTE EQUIPO",
    scanBadgeEnrolled: "YA EN FLOTA",
    scanBadgeHint: "POSIBLE NANO",
    scanBadgeLinux: "LINUX SSH",
    scanBadgeOther: "OTRO EQUIPO",
    autoStart: "Buscar, enrolar y desplegar todas las Nano",
    autoCancel: "Detener seguimiento",
    autoNote: "Busca equipos ARM64 con GB10, instala requisitos, portal, fuentes y demos. Puede descargar modelos y tardar horas. No interrumpe demos activas ni sobrescribe portales modificados. Detener seguimiento no detiene instalaciones remotas.",
    scanning: "Buscando Nanos en la red...",
    scanSelect: "Seleccionar visibles", scanClear: "Deseleccionar visibles",
    scanVerify: "Probar SSH seleccionadas",
    scanVerifyNote: "Filtra automaticamente impresoras, Windows y otros equipos, y verifica hardware GB10 por SSH si hay contrasena indicada o predeterminada. Marca las Nanos nuevas y pulsa «Enrolar seleccionadas».",
    scanAdd: "Enrolar seleccionadas", scanAdding: "Enrolando...", scanAdded: "Añadido a la flota",
    scanAlreadyAdded: "Ya en la flota o portal local", scanAddFinished: "Añadidos / omitidos / errores",
    scanEnrollStatusLost: "No se pudo confirmar el registro. Revisa la flota antes de reintentar.",
    scanVerifyRequired: "Selecciona al menos un equipo e introduce usuario y puerto SSH validos.",
    scanNeedPassHint: "Indica o guarda la contrasena SSH abajo para verificar e incorporar equipos nuevos en 1 clic.",
    saveSshDefault: "Guardar contrasena predeterminada", sshDefaultSaved: "Contrasena predeterminada guardada para este usuario.",
    scanTesting: "Probando SSH...", scanVerified: "SSH + HW verificado",
    scanNotZgx: "SSH OK (no es ZGX)",
    scanProgress: "Comprobados", scanConnectionError: "Error de conexion con el portal.",
    scanStop: "Detener pruebas pendientes", scanCancelled: "Prueba detenida.",
    stopActiveDemo: "Apagar demo activa",
    noActiveDemo: "[IDLE] Sin demo activa",
    cpuTitle: "Carga CPU", ramTitle: "RAM Unificada", diskTitle: "Almacenamiento (/)",
    avail: "Disponibles", free: "libres", used: "Usado",
    navDemos: "Demos", navServices: "Servicios", navTelemetry: "Telemetria",
    demosNote: "Demos ZGX Partner Kit (una activa a la vez; comparten Qwen 35B y puertos internos).",
    servicesNote: "Servicios systemd de la Nano ZGX. Ordenables por RAM, CPU o estado.",
    gpuProcs: "Procesos activos en GPU", noGpuProcs: "Sin procesos activos en GPU",
    liveLog: "Log de ejecucion en vivo", demoTerm: "Salida de terminal (demo en ejecucion)",
    noLogs: "Sin logs todavia.", refresh: "Refrescar", open: "Abrir",
    start: "Iniciar", restart: "Reiniciar", stop: "Apagar",
    activate: "Activar", deactivate: "Desactivar",
    modelsLoaded: "Modelos cargados", modelsNone: "Modelos cargados: 0", ramCgroup: "RAM CGroup",
    starting: "Iniciando", stopping: "Deteniendo", errOp: "Operacion fallida",
    chCpu: "CPU % (2m)", chGpu: "GPU % (2m)", chRam: "RAM % (2m)",
    chTemp: "Temperatura GPU (C) y Potencia (W)", chNet: "Red RX / TX (KB/s)", chIo: "Memoria, NVMe I/O y Clocks",
    coresTitle: "Uso por nucleo CPU", topProcs: "Top procesos del sistema", portsMap: "Mapa de puertos",
    memUsed: "Activa", memCache: "Cache/Buffers", memFree: "Libre", swap: "Swap",
    sfCustom: "IA y Custom", sfActive: "Activos", sfInactive: "Inactivos", sfAll: "Todos",
    svcSearchPh: "Buscar servicio (ej. ollama, ssh, docker)...",
    sortRam: "Orden: Mayor RAM", sortCpu: "Orden: Mayor CPU", sortActive: "Orden: Activos primero", sortName: "Orden: Nombre (A-Z)",
    viewCards: "Vista: Tarjetas", viewTable: "Vista: Tabla compacta",
    pushUpdate: "Actualizar Flota", startAllNano: "Levantar Nano Collective", reorganizeFleet: "Reorganizar Red", locateNode: "Localizar (ventilador)", staticIp: "IP Fija", dhcpIp: "DHCP", freeGpu: "Liberar GPU", pruneDocker: "Limpiar Docker",
    rebootHost: "Reiniciar equipo", poweroffHost: "Apagar equipo", lowDisk: "[WARN] Poco espacio libre",
    fleetTokTitle: "Contador global de tokens y ahorro en Euros (Todas las unidades HW)",
    nodeTokTitle: "Medidor de tokens y ahorro en Euros (Este dispositivo)",
    tokTotal: "Tokens totales", tokIn: "Entrada", tokOut: "Salida",
    eurSaved: "Equivalente API Cloud (EUR)", eurRateNote: "Ref. API Cloud",
    resetCounters: "Reiniciar contadores", confirmResetTok: "Reiniciar contadores de tokens de este dispositivo?",
    confirmResetFleetTok: "Reiniciar contadores de tokens de TODA la flota?",
    tokensLbl: "Tokens", apiEqLbl: "Equiv. API",
    copyLog: "Copiar log", copiedLog: "[OK] Copiado",
    openFree: "Free Mode", openStage: "Presenter Mode", switchingMode: "Cambiando modo...",
  },
  en: {
    subtitle: "HP ZGX fleet and node console",
    backFleet: "<- Fleet",
    claimMaster: "Claim Master (P2P)",
    masterSyncFailed: "Master active. Could not synchronize with:",
    usersBtn: "Users",
    logout: "Logout",
    fleetTitle: "Enrolled HP ZGX units",
    fleetSub: "Select a unit to manage demos, services, and telemetry, or enroll a new one without auto-deploy.",
    addNode: "+ Enroll ZGX",
    enterNode: "Manage",
    cachedMonitoring: "View last snapshot",
    removeNode: "Remove",
    deployBtn: "Deploy",
    enrollTitle: "Enroll ZGX device",
    enrollNote: "Tests connection and registers the device in the fleet only. Nothing is auto-deployed or restarted; use the Deploy button in the portal when you want to install or update.",
    enrollSubmit: "Enroll & Test Connection",
    deployTitle: "Deploy to device",
    deploySelLbl: "Components to deploy",
    deployAgentName: "Fleet Console Agent (zgx-launcher.service)",
    deployAgentDesc: "Installs or updates launcher_web.py, sudoers and systemd on the target device.",
    deployPortalOnly: "Portal only",
    deployFury: "Fury demos",
    deployNano: "Nano ZGX demos",
    deploySourceOnly: "Copy repository only",
    deploySourceOnlyDesc: "Copies the sources without installing or restarting the portal or demos.",
    deployFuryDesc: "Installs Fury and prepares dependencies and models. Does not install the portal.",
    deployNanoDesc: "Prepares Nano Collective and its models in the background. Does not install the portal or Partner Kit.",
    deploySource: "Repository source",
    deployNoSource: "No source is available. Update the portal on the source device.",
    deploySubmit: "Deploy selected",
    nanoBackground: "Nano preparation in the background",
    nanoStatusError: "Cannot check Nano preparation",
    showPassword: "Show", hidePassword: "Hide",
    closeBtn: "Close",
    cancelBtn: "Cancel",
    scanBtn: "Find Nanos on network",
    scanCidrPh: "Auto subnet (e.g. 192.168.1.0/24)",
    scanFilterNew: "New / Candidates",
    scanFilterZgx: "All ZGX",
    scanFilterAll: "All network",
    scanEmptyNew: "No new unenrolled Nanos found on this subnet.",
    scanShowEnrolledBtn: "Show enrolled ZGX",
    scanBadgeLocal: "THIS DEVICE",
    scanBadgeEnrolled: "IN FLEET",
    scanBadgeHint: "POSSIBLE NANO",
    scanBadgeLinux: "LINUX SSH",
    scanBadgeOther: "OTHER HOST",
    autoStart: "Find, enroll and deploy all Nano devices",
    autoCancel: "Stop monitoring",
    autoNote: "Finds ARM64 GB10 devices and installs prerequisites, portal, sources and demos. Model downloads may take hours. Active demos and modified portals are not interrupted. Stopping monitoring does not stop remote installations.",
    scanning: "Finding Nanos on network...",
    scanSelect: "Select visible", scanClear: "Deselect visible",
    scanVerify: "Test SSH on selected",
    scanVerifyNote: "Automatically filters out printers, Windows PCs and unrelated devices, and verifies GB10 hardware over SSH when a password or saved default is available. Check new Nanos and click «Enroll selected».",
    scanAdd: "Enroll selected", scanAdding: "Enrolling...", scanAdded: "Added to fleet",
    scanAlreadyAdded: "Already in fleet or local portal", scanAddFinished: "Added / skipped / errors",
    scanEnrollStatusLost: "Enrollment could not be confirmed. Check the fleet before retrying.",
    scanVerifyRequired: "Select at least one host and enter a valid SSH username and port.",
    scanNeedPassHint: "Enter or save the SSH password below to verify and enroll new devices in 1 click.",
    saveSshDefault: "Save default password", sshDefaultSaved: "Default password saved for this user.",
    scanTesting: "Testing SSH...", scanVerified: "SSH + HW verified",
    scanNotZgx: "SSH OK (not ZGX)",
    scanProgress: "Checked", scanConnectionError: "Could not connect to the portal.",
    scanStop: "Stop pending tests", scanCancelled: "Testing stopped.",
    stopActiveDemo: "Stop active demo",
    noActiveDemo: "[IDLE] No active demo",
    cpuTitle: "CPU Load", ramTitle: "Unified RAM", diskTitle: "Storage (/)",
    avail: "Available", free: "free", used: "Used",
    navDemos: "Demos", navServices: "Services", navTelemetry: "Telemetry",
    demosNote: "ZGX Partner Kit demos (one active at a time; they share Qwen 35B and internal ports).",
    servicesNote: "Systemd services on ZGX Nano. Sortable by RAM, CPU, or state.",
    gpuProcs: "Active GPU Processes", noGpuProcs: "No active GPU processes",
    liveLog: "Live execution log", demoTerm: "Terminal output (running demo)",
    noLogs: "No logs yet.", refresh: "Refresh", open: "Open",
    start: "Start", restart: "Restart", stop: "Stop",
    activate: "Enable", deactivate: "Disable",
    modelsLoaded: "Loaded models", modelsNone: "Loaded models: 0", ramCgroup: "CGroup RAM",
    starting: "Starting", stopping: "Stopping", errOp: "Operation failed",
    chCpu: "CPU % (2m)", chGpu: "GPU % (2m)", chRam: "RAM % (2m)",
    chTemp: "GPU Temp (C) & Power (W)", chNet: "Network RX / TX (KB/s)", chIo: "Memory, NVMe I/O & Clocks",
    coresTitle: "Per-core CPU usage", topProcs: "Top system processes", portsMap: "Listening ports map",
    memUsed: "Active", memCache: "Cache/Buffers", memFree: "Free", swap: "Swap",
    sfCustom: "AI & Custom", sfActive: "Active", sfInactive: "Inactive", sfAll: "All",
    svcSearchPh: "Search service (e.g. ollama, ssh, docker)...",
    sortRam: "Sort: Highest RAM", sortCpu: "Sort: Highest CPU", sortActive: "Sort: Active first", sortName: "Sort: Name (A-Z)",
    viewCards: "View: Cards", viewTable: "View: Compact table",
    pushUpdate: "Push Fleet Update", startAllNano: "Start All Nano Collective", reorganizeFleet: "Reorganize Network", locateNode: "Locate (fan burst)", staticIp: "Static IP", dhcpIp: "DHCP", freeGpu: "Free GPU", pruneDocker: "Prune Docker",
    rebootHost: "Reboot host", poweroffHost: "Power off", lowDisk: "[WARN] Low disk space",
    fleetTokTitle: "Global fleet token meter & Euro savings (All HW units)",
    nodeTokTitle: "Token meter & Euro savings (This device)",
    tokTotal: "Total tokens", tokIn: "Input", tokOut: "Output",
    eurSaved: "Cloud API Equivalent (EUR)", eurRateNote: "Cloud API ref.",
    resetCounters: "Reset counters", confirmResetTok: "Reset token counters for this device?",
    confirmResetFleetTok: "Reset token counters for the ENTIRE fleet?",
    tokensLbl: "Tokens", apiEqLbl: "API Eq.",
    copyLog: "Copy log", copiedLog: "[OK] Copied",
    openFree: "Free Mode", openStage: "Presenter Mode", switchingMode: "Switching mode...",
  }
};

const THUMBS = {
  ollama: `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#101b27"/><circle cx="96" cy="84" r="38" fill="#101b27" stroke="#e2e8f0" stroke-width="2.5"/><circle cx="84" cy="78" r="4.5" fill="#e2e8f0"/><circle cx="108" cy="78" r="4.5" fill="#e2e8f0"/><path d="M88 94 Q96 101 104 94" fill="none" stroke="#e2e8f0" stroke-width="2.5" stroke-linecap="round"/><rect x="164" y="42" width="222" height="80" rx="8" fill="#0b131c" stroke="#283c54"/><rect x="180" y="56" width="90" height="8" rx="3" fill="#76b900"/><rect x="180" y="74" width="188" height="7" rx="3" fill="#293f59"/></svg>`,
  comfyui: `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#0d1a14"/><rect x="34" y="48" width="84" height="62" rx="7" fill="#122118" stroke="#76b900" stroke-width="2"/><rect x="168" y="34" width="90" height="88" rx="7" fill="#111d2b" stroke="#38bdf8" stroke-width="2"/><rect x="304" y="50" width="82" height="60" rx="7" fill="#1a1c2e" stroke="#a855f7" stroke-width="2"/><path d="M118 76 C143 76 143 60 168 60" fill="none" stroke="#76b900" stroke-width="2.5"/><path d="M258 80 C281 80 281 80 304 80" fill="none" stroke="#38bdf8" stroke-width="2.5"/></svg>`,
  "sd-webui": `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#1b1412"/><rect x="38" y="34" width="140" height="92" rx="8" fill="#151b26" stroke="#f59e0b" stroke-width="2"/><circle cx="88" cy="72" r="20" fill="#f59e0b" opacity="0.35"/><rect x="206" y="42" width="174" height="14" rx="4" fill="#232c3d"/><rect x="206" y="68" width="134" height="10" rx="4" fill="#f59e0b" opacity="0.7"/></svg>`,
  "hermes-voice": `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#0d1724"/><g transform="translate(110,40)"><rect x="0" y="32" width="10" height="24" rx="5" fill="#38bdf8"/><rect x="20" y="14" width="10" height="60" rx="5" fill="#2dd4bf"/><rect x="40" y="0" width="10" height="88" rx="5" fill="#56d364"/><rect x="60" y="20" width="10" height="48" rx="5" fill="#38bdf8"/></g></svg>`,
  "zgx-watchdog": `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#131822"/><polyline points="28,96 105,96 128,52 152,120 176,72 198,96 390,96" fill="none" stroke="#56d364" stroke-width="3"/></svg>`,
  docker: `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#0b1929"/><g fill="#0096d6" stroke="#38bdf8" stroke-width="1.5"><rect x="120" y="70" width="28" height="22" rx="3"/><rect x="154" y="70" width="28" height="22" rx="3"/><rect x="188" y="70" width="28" height="22" rx="3"/></g></svg>`,
  systemd: `<svg viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg"><rect width="420" height="160" fill="#0c131d"/><rect x="52" y="42" width="316" height="76" rx="8" fill="#111a27" stroke="#263850"/><circle cx="96" cy="80" r="18" fill="#152436" stroke="#38bdf8" stroke-width="2"/></svg>`,
  campus: `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#0b2239"/><rect x="24" y="42" width="128" height="106" rx="8" fill="#101e2e" stroke="#284666"/><rect x="34" y="54" width="60" height="8" rx="3" fill="#0096d6"/><circle cx="206" cy="70" r="14" fill="#132940" stroke="#0096d6" stroke-width="2"/><circle cx="252" cy="118" r="13" fill="#132940" stroke="#56d364" stroke-width="2"/></svg>`,
  atlas: `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#0f292b"/><rect x="26" y="42" width="160" height="104" rx="8" fill="#0e1b24" stroke="#224554"/><polyline points="40,126 70,108 102,114 134,82 170,62" fill="none" stroke="#2dd4bf" stroke-width="3"/><g transform="translate(214,66)"><rect x="0" y="20" width="6" height="24" rx="3" fill="#2dd4bf"/><rect x="12" y="8" width="6" height="48" rx="3" fill="#38bdf8"/><rect x="24" y="0" width="6" height="64" rx="3" fill="#56d364"/></g></svg>`,
  guard: `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#281629"/><path d="M92 44 L138 60 V96 C138 124 118 142 92 152 C66 142 46 124 46 96 V60 Z" fill="#19162b" stroke="#a855f7" stroke-width="2.5"/><path d="M76 98 L88 110 L110 82" fill="none" stroke="#56d364" stroke-width="3.5"/></svg>`,
  vision: `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#172812"/><rect x="52" y="64" width="78" height="68" rx="4" fill="rgba(118,185,0,0.10)" stroke="#76b900" stroke-width="2"/><rect x="164" y="76" width="96" height="58" rx="4" fill="rgba(0,150,214,0.10)" stroke="#38bdf8" stroke-width="2"/></svg>`,
  "nano-collective": `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#0d1e2b"/><circle cx="96" cy="90" r="24" fill="#132d42" stroke="#0096d6" stroke-width="2"/><circle cx="160" cy="56" r="20" fill="#132d42" stroke="#56d364" stroke-width="2"/><circle cx="224" cy="90" r="24" fill="#132d42" stroke="#38bdf8" stroke-width="2"/><circle cx="160" cy="126" r="20" fill="#132d42" stroke="#a855f7" stroke-width="2"/><line x1="116" y1="78" x2="142" y2="64" stroke="#38bdf8" stroke-width="1.5"/><line x1="178" y1="64" x2="204" y2="78" stroke="#56d364" stroke-width="1.5"/><line x1="116" y1="102" x2="142" y2="118" stroke="#a855f7" stroke-width="1.5"/><line x1="178" y1="118" x2="204" y2="102" stroke="#0096d6" stroke-width="1.5"/></svg>`,
  "cern-lhc": `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#081421"/><circle cx="160" cy="90" r="64" fill="none" stroke="#1e3a5f" stroke-width="1.5" stroke-dasharray="5 4"/><circle cx="160" cy="90" r="44" fill="none" stroke="#0096d6" stroke-width="1.8" opacity="0.7"/><circle cx="160" cy="90" r="22" fill="none" stroke="#38bdf8" stroke-width="1.5"/><circle cx="160" cy="90" r="5" fill="#76b900"/><path d="M160 90 Q205 55 248 44" fill="none" stroke="#56d364" stroke-width="2"/><path d="M160 90 Q210 112 250 138" fill="none" stroke="#38bdf8" stroke-width="2"/><path d="M160 90 Q112 62 72 48" fill="none" stroke="#a855f7" stroke-width="2"/><path d="M160 90 Q108 118 68 134" fill="none" stroke="#f59e0b" stroke-width="1.8"/></svg>`,
  fury: `<svg viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"><rect width="320" height="180" fill="#1b1026"/><rect x="36" y="38" width="248" height="104" rx="10" fill="#120c1c" stroke="#a855f7" stroke-width="2"/><rect x="56" y="58" width="90" height="12" rx="4" fill="#a855f7"/><rect x="56" y="82" width="160" height="8" rx="3" fill="#3b2759"/><rect x="56" y="98" width="120" height="8" rx="3" fill="#0096d6"/><circle cx="242" cy="90" r="24" fill="#1c1430" stroke="#76b900" stroke-width="2.5"/></svg>`
};

let currentLang = localStorage.getItem("zgx_lang") || "es";
let currentTab = localStorage.getItem("zgx_tab") || "demos";
let svcFilter = localStorage.getItem("zgx_svc_filter") || "custom";
let svcSort = localStorage.getItem("zgx_svc_sort") || "ram";
let svcView = localStorage.getItem("zgx_svc_view") || "table";
let svcQuery = "";
let selectedNodeId = localStorage.getItem("zgx_node") || "";
let selectedNodeHost = window.location.hostname;
let currentRole = "hub";
let lastFleetData = null;
let fleetNodesById = {};
let lastNodeData = null;
let busy = false;

function t(k) { return (I18N[currentLang] && I18N[currentLang][k]) || I18N.es[k] || k; }

function togglePassword(button) {
  const input = document.getElementById(button.getAttribute("aria-controls"));
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  button.setAttribute("aria-pressed", String(visible));
  button.textContent = t(visible ? "hidePassword" : "showPassword");
}

function resetPasswordField(id) {
  const input = document.getElementById(id);
  const button = document.querySelector(`.password-toggle[aria-controls="${id}"]`);
  input.value = "";
  input.type = "password";
  button.setAttribute("aria-pressed", "false");
  button.textContent = t("showPassword");
}

function setLang(lang) {
  currentLang = lang === "en" ? "en" : "es";
  localStorage.setItem("zgx_lang", currentLang);
  document.getElementById("lang-es").classList.toggle("active", currentLang === "es");
  document.getElementById("lang-en").classList.toggle("active", currentLang === "en");
  applyStaticLabels();
  if (lastNodeData && selectedNodeId) renderNodeData(lastNodeData);
  else if (lastFleetData) renderFleet(lastFleetData);
}

function setTab(tab) {
  currentTab = ["demos", "services", "telemetry"].includes(tab) ? tab : "demos";
  localStorage.setItem("zgx_tab", currentTab);
  for (const name of ["demos", "services", "telemetry"]) {
    document.getElementById("tab-btn-" + name).classList.toggle("active", name === currentTab);
    document.getElementById("tab-" + name).classList.toggle("active", name === currentTab);
  }
}

function setSvcFilter(f) {
  svcFilter = f; localStorage.setItem("zgx_svc_filter", f);
  for (const k of ["custom", "active", "inactive", "all"]) document.getElementById("sf-" + k).classList.toggle("active", k === f);
  if (lastNodeData) renderNodeData(lastNodeData);
}
function setSvcSort(s) { svcSort = s; localStorage.setItem("zgx_svc_sort", s); document.getElementById("svc-sort").value = s; if (lastNodeData) renderNodeData(lastNodeData); }
function setSvcView(v) { svcView = v; localStorage.setItem("zgx_svc_view", v); document.getElementById("svc-view").value = v; if (lastNodeData) renderNodeData(lastNodeData); }
function onSvcSearch(q) { svcQuery = (q || "").toLowerCase().trim(); if (lastNodeData) renderNodeData(lastNodeData); }

function applyStaticLabels() {
  document.querySelectorAll(".password-toggle").forEach(button => {
    button.textContent = t(button.getAttribute("aria-pressed") === "true" ? "hidePassword" : "showPassword");
  });
  document.getElementById("btn-back-fleet").textContent = t("backFleet");
  document.getElementById("btn-claim-master").textContent = t("claimMaster");
  document.getElementById("btn-users").textContent = t("usersBtn");
  document.getElementById("btn-logout").textContent = t("logout");
  document.getElementById("fleet-title").textContent = t("fleetTitle");
  document.getElementById("fleet-sub").textContent = t("fleetSub");
  document.getElementById("btn-add-node").textContent = t("addNode");
  document.getElementById("btn-scan").textContent = t("scanBtn");
  document.getElementById("scan-cidr").placeholder = t("scanCidrPh");
  document.getElementById("btn-scan-select").textContent = t("scanSelect");
  document.getElementById("btn-scan-verify").textContent = t("scanVerify");
  updateScanFilterChips();
  updateScanSelection();
  document.getElementById("btn-scan-stop").textContent = t("scanStop");
  document.getElementById("btn-save-ssh-default").textContent = t("saveSshDefault");
  document.getElementById("btn-auto-start").textContent = t("autoStart");
  document.getElementById("btn-auto-cancel").textContent = t("autoCancel");
  document.getElementById("auto-note").textContent = t("autoNote");
  document.getElementById("scan-verify-note").textContent = t("scanVerifyNote");
  document.getElementById("btn-stop-all").textContent = t("stopActiveDemo");
  document.getElementById("lbl-cpu").textContent = t("cpuTitle");
  document.getElementById("lbl-ram").textContent = t("ramTitle");
  document.getElementById("lbl-disk").textContent = t("diskTitle");
  document.getElementById("nav-demos-lbl").textContent = t("navDemos");
  document.getElementById("nav-services-lbl").textContent = t("navServices");
  document.getElementById("nav-telemetry-lbl").textContent = t("navTelemetry");
  document.getElementById("demos-note").textContent = t("demosNote");
  document.getElementById("services-note").textContent = t("servicesNote");
  document.getElementById("lbl-gpu-procs").textContent = t("gpuProcs");
  document.getElementById("lbl-live-log").textContent = t("liveLog");
  document.getElementById("lbl-demo-term").textContent = t("demoTerm");
  document.getElementById("btn-refresh").textContent = t("refresh");
  document.getElementById("btn-copy-demo-log").textContent = t("copyLog");
  document.getElementById("btn-copy-live-log").textContent = t("copyLog");
  document.getElementById("ch-cpu-lbl").textContent = t("chCpu");
  document.getElementById("ch-gpu-lbl").textContent = t("chGpu");
  document.getElementById("ch-ram-lbl").textContent = t("chRam");
  document.getElementById("ch-temp-lbl").textContent = t("chTemp");
  document.getElementById("ch-net-lbl").textContent = t("chNet");
  document.getElementById("ch-io-lbl").textContent = t("chIo");
  document.getElementById("lbl-cores").textContent = t("coresTitle");
  document.getElementById("lbl-top-procs").textContent = t("topProcs");
  document.getElementById("lbl-ports").textContent = t("portsMap");
  document.getElementById("sf-custom").textContent = t("sfCustom");
  document.getElementById("sf-active").textContent = t("sfActive");
  document.getElementById("sf-inactive").textContent = t("sfInactive");
  document.getElementById("sf-all").textContent = t("sfAll");
  document.getElementById("svc-search").placeholder = t("svcSearchPh");
  document.getElementById("opt-sort-ram").textContent = t("sortRam");
  document.getElementById("opt-sort-cpu").textContent = t("sortCpu");
  document.getElementById("opt-sort-active").textContent = t("sortActive");
  document.getElementById("opt-sort-name").textContent = t("sortName");
  document.getElementById("opt-view-cards").textContent = t("viewCards");
  document.getElementById("opt-view-table").textContent = t("viewTable");
  document.getElementById("btn-start-all-nano").textContent = t("startAllNano");
  document.getElementById("btn-reorganize-fleet").textContent = t("reorganizeFleet");
  document.getElementById("btn-push-update").textContent = t("pushUpdate");
  document.getElementById("btn-locate-node").textContent = t("locateNode");
  document.getElementById("btn-static-ip").textContent = t("staticIp");
  document.getElementById("btn-dhcp-ip").textContent = t("dhcpIp");
  document.getElementById("btn-free-gpu").textContent = t("freeGpu");
  document.getElementById("btn-prune-docker").textContent = t("pruneDocker");
  document.getElementById("btn-reboot").textContent = t("rebootHost");
  document.getElementById("btn-poweroff").textContent = t("poweroffHost");
  document.getElementById("m-title").textContent = t("enrollTitle");
  document.getElementById("enroll-note").textContent = t("enrollNote");
  document.getElementById("btn-en-submit").textContent = t("enrollSubmit");
  document.getElementById("btn-en-cancel").textContent = t("cancelBtn");
  document.getElementById("dep-title").textContent = t("deployTitle");
  document.getElementById("dep-sel-lbl").textContent = t("deploySelLbl");
  document.getElementById("btn-dep-cancel").textContent = t("closeBtn");
  document.getElementById("btn-dep-submit").textContent = t("deploySubmit");
  document.getElementById("btn-node-deploy").textContent = t("deployBtn");
}

async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { window.location.href = "/login"; return {}; }
  return await res.json();
}

async function doLogout() { await api("POST", "/api/logout"); window.location.href = "/login"; }

function showFleet() {
  selectedNodeId = "";
  localStorage.removeItem("zgx_node");
  lastNodeData = null;
  refresh();
}

function enterNode(id, host) {
  selectedNodeId = id;
  selectedNodeHost = host === "127.0.0.1" ? window.location.hostname : host;
  localStorage.setItem("zgx_node", id);
  refresh();
}

async function removeNode(id) {
  if (!confirm("Remove this node from fleet?")) return;
  await api("POST", "/api/fleet/remove/" + encodeURIComponent(id));
  await refresh();
}

async function toggleLocalRole() {
  if (currentRole === "node") return claimMaster();
  const nextRole = currentRole === "hub" ? "node" : "hub";
  if (!confirm(`Switch this unit role to ${nextRole.toUpperCase()}?`)) return;
  const result = await api("POST", "/api/role", { role: nextRole });
  if (!result.ok) { alert(result.error || t("errOp")); return; }
  if (nextRole === "node") enterNode("local", "127.0.0.1");
  else showFleet();
}

async function claimMaster() {
  if (!confirm("Reclamar el rol Master (Hub) en este equipo?")) return;
  const r = await api("POST", "/api/fleet/claim-master");
  if (r.ok) {
    currentRole = "hub";
    showFleet();
    if (r.sync_failed?.length) alert(t("masterSyncFailed") + " " + r.sync_failed.join(", "));
  } else {
    alert(r.error || t("errOp"));
  }
}

async function openUsersModal() {
  document.getElementById("users-modal").classList.add("open");
  await loadUsers();
}
function closeUsersModal() {
  resetPasswordField("u-new-pass");
  document.getElementById("users-modal").classList.remove("open");
}

async function loadUsers() {
  const r = await api("GET", "/api/users");
  const box = document.getElementById("users-list");
  box.innerHTML = (r.users || []).map(u => `
    <div class="gpu-proc" style="align-items:center;">
      <span><strong>${u.username}</strong> (${u.created_at})</span>
      <div class="btn-row">
        <button class="warn" onclick="changePassPrompt('${u.username}')">Pass</button>
        <button class="danger" onclick="removeUserAction('${u.username}')">${t("removeNode")}</button>
      </div>
    </div>
  `).join("");
}

async function addUserAction() {
  const u = document.getElementById("u-new-name").value;
  const p = document.getElementById("u-new-pass").value;
  const r = await api("POST", "/api/users/add", { username: u, password: p });
  if (!r.ok) { alert(r.error || t("errOp")); return; }
  document.getElementById("u-new-name").value = "";
  document.getElementById("u-new-pass").value = "";
  await loadUsers();
}

async function changePassPrompt(username) {
  const p = prompt(`Nueva contrasena para ${username}:`);
  if (!p) return;
  const r = await api("POST", "/api/users/password", { username, password: p });
  if (!r.ok) alert(r.error || t("errOp"));
  else alert("Contrasena actualizada.");
}

async function removeUserAction(username) {
  if (!confirm(`Eliminar usuario ${username}?`)) return;
  const r = await api("POST", "/api/users/remove", { username });
  if (!r.ok) alert(r.error || t("errOp"));
  await loadUsers();
}

let enrollTimer = null;
let deployTimer = null;
let nanoSetupTimer = null;
let deployRunning = false, nanoSetupRunning = false;
let autoTimer = null;
let deployTargetNodeId = "";
let scannedHosts = [], scanTestingInProgress = false, scanCancelRequested = false;
let scanAddingInProgress = false, scanInProgress = false;
let scanFilterMode = "new", scanSubnetLabel = "";

function isHostNewCandidate(host) {
  return !!host.zgx_candidate && !host.enrolled && !host.is_local;
}
function isHostZgxFamily(host) {
  return !!host.zgx_candidate || !!host.enrolled || !!host.is_local;
}
function matchesScanFilter(host, mode = scanFilterMode) {
  if (mode === "new") return isHostNewCandidate(host);
  if (mode === "zgx") return isHostZgxFamily(host);
  return true;
}

function updateScanFilterChips() {
  const newCount = scannedHosts.filter(it => isHostNewCandidate(it.host)).length;
  const zgxCount = scannedHosts.filter(it => isHostZgxFamily(it.host)).length;
  const allCount = scannedHosts.length;
  const setChip = (id, key, count, active) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = allCount ? `${t(key)} (${count})` : t(key);
    el.classList.toggle("active", active);
  };
  setChip("sf-scan-new", "scanFilterNew", newCount, scanFilterMode === "new");
  setChip("sf-scan-zgx", "scanFilterZgx", zgxCount, scanFilterMode === "zgx");
  setChip("sf-scan-all", "scanFilterAll", allCount, scanFilterMode === "all");
  const summary = document.getElementById("scan-summary");
  if (summary) summary.textContent = scanSubnetLabel ? `Subred: ${scanSubnetLabel}` : "";
}

function setScanFilter(mode) {
  scanFilterMode = ["new", "zgx", "all"].includes(mode) ? mode : "new";
  renderScannedHosts();
}

async function openEnrollModal() {
  document.getElementById("enroll-modal").classList.add("open");
  await pollEnrollStatus();
  await pollAutoStatus();
  if (!scanInProgress && !scanTestingInProgress && !scanAddingInProgress && !scannedHosts.length) {
    scanNetwork();
  }
}
function closeEnrollModal() {
  scanCancelRequested = true;
  resetPasswordField("en-pass");
  document.getElementById("enroll-modal").classList.remove("open");
  if (enrollTimer) { clearInterval(enrollTimer); enrollTimer = null; }
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
}

async function pollAutoStatus() {
  const state = await api("GET", "/api/fleet/auto/status");
  const log = document.getElementById("auto-log");
  log.style.display = state.log && state.log.length ? "block" : "none";
  log.textContent = (state.log || []).join("\n");
  log.scrollTop = log.scrollHeight;
  document.getElementById("btn-auto-start").disabled = !!state.running;
  document.getElementById("btn-auto-cancel").style.display = state.running ? "" : "none";
  if (state.running && !autoTimer) autoTimer = setInterval(pollAutoStatus, 3000);
  if (!state.running && autoTimer) { clearInterval(autoTimer); autoTimer = null; refresh(); }
}

async function startAutoDeployment() {
  const result = await api("POST", "/api/fleet/auto", {
    cidr: document.getElementById("scan-cidr").value,
    ssh_user: document.getElementById("en-user").value,
    ssh_port: Number(document.getElementById("en-ssh-port").value),
    ssh_pass: document.getElementById("en-pass").value,
  });
  if (!result.ok) { alert(result.error || t("errOp")); return; }
  document.getElementById("en-pass").value = "";
  await pollAutoStatus();
}

async function cancelAutoDeployment() {
  const result = await api("POST", "/api/fleet/auto/cancel", {});
  if (!result.ok) alert(result.error || t("errOp"));
  await pollAutoStatus();
}

async function saveDefaultSshCredentials() {
  const status = document.getElementById("ssh-default-status");
  status.textContent = "";
  try {
    const result = await api("POST", "/api/fleet/ssh-default", {
      ssh_user: document.getElementById("en-user").value.trim(),
      ssh_pass: document.getElementById("en-pass").value,
    });
    status.textContent = result.ok ? t("sshDefaultSaved") : (result.error || t("errOp"));
  } catch (error) {
    status.textContent = t("scanConnectionError");
  }
}

function makeScanBadge(text, cls) {
  const b = document.createElement("span");
  b.className = "scan-badge" + (cls ? " " + cls : "");
  b.textContent = text;
  return b;
}

function renderScannedHosts() {
  const box = document.getElementById("scan-results");
  updateScanFilterChips();
  if (!scannedHosts.length) {
    updateScanSelection();
    return;
  }
  box.replaceChildren();
  const visible = scannedHosts.filter(it => matchesScanFilter(it.host));
  if (!visible.length && scanFilterMode === "new") {
    const empty = document.createElement("div");
    empty.style.cssText = "padding:12px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;";
    const msg = document.createElement("span");
    msg.textContent = t("scanEmptyNew");
    empty.append(msg);
    const zgxCount = scannedHosts.filter(it => isHostZgxFamily(it.host)).length;
    if (zgxCount) {
      const showBtn = document.createElement("button");
      showBtn.type = "button";
      showBtn.textContent = `${t("scanShowEnrolledBtn")} (${zgxCount})`;
      showBtn.addEventListener("click", () => setScanFilter("zgx"));
      empty.append(showBtn);
    }
    box.append(empty);
    updateScanSelection();
    return;
  }
  for (const item of visible) {
    const { host } = item;
    const row = document.createElement("div");
    row.className = "scan-row" + ((host.enrolled || host.is_local || !host.zgx_candidate) ? " scan-dim" : "");
    row.addEventListener("click", () => pickHost(host.ip, host.hostname || host.ip, host.hw_model || ""));
    const left = document.createElement("span");
    left.style.cssText = "display:flex;align-items:center;gap:6px;flex-wrap:wrap;";
    if (item.checkbox) left.append(item.checkbox);
    const ip = document.createElement("strong");
    ip.textContent = host.ip;
    left.append(ip);
    if (host.hostname) {
      const hn = document.createElement("span");
      hn.style.color = "#cde4f7";
      hn.textContent = `(${host.hostname})`;
      left.append(hn);
    }
    if (host.gpu) {
      const gpu = document.createElement("span");
      gpu.style.color = "var(--muted)";
      gpu.textContent = `· ${host.gpu}`;
      left.append(gpu);
    }

    const details = document.createElement("span");
    details.style.cssText = "display:flex;align-items:center;gap:4px;flex-wrap:wrap;";
    if (host.hw_model === "ZGX-NANO" || host.kind === "nano") details.append(makeScanBadge("ZGX-NANO", "nano"));
    else if (host.hw_model === "ZGX-FURY" || host.kind === "fury") details.append(makeScanBadge("ZGX-FURY", "fury"));
    else if (host.kind === "nano-hint") details.append(makeScanBadge(t("scanBadgeHint"), "nano"));
    else if (host.zgx_candidate) details.append(makeScanBadge(t("scanBadgeLinux"), ""));
    else details.append(makeScanBadge(t("scanBadgeOther"), "enrolled"));

    if (host.is_local) details.append(makeScanBadge(t("scanBadgeLocal"), "enrolled"));
    else if (host.enrolled) details.append(makeScanBadge(t("scanBadgeEnrolled"), "enrolled"));
    else {
      if (host.agent) details.append(makeScanBadge("PORTAL:18888", "ok"));
      if (item.verified) details.append(makeScanBadge("SSH OK", "ok"));
      else if (host.ssh) details.append(makeScanBadge(`SSH:${Number(document.getElementById("en-ssh-port").value || 22)}`, ""));
    }

    row.append(left, details, item.status);
    box.append(row);
  }
  updateScanSelection();
}

async function scanNetwork() {
  if (scanInProgress || scanTestingInProgress || scanAddingInProgress) return;
  const btn = document.getElementById("btn-scan");
  const box = document.getElementById("scan-results");
  const user = document.getElementById("en-user").value.trim() || "nvidia";
  const password = document.getElementById("en-pass").value;
  const port = Number(document.getElementById("en-ssh-port").value || 22);
  scanInProgress = true;
  scannedHosts = [];
  scanSubnetLabel = "";
  updateScanFilterChips();
  updateScanSelection();
  document.getElementById("scan-verify-progress").textContent = "";
  btn.disabled = true; btn.textContent = t("scanning");
  box.textContent = t("scanning");
  try {
    const r = await api("POST", "/api/fleet/scan", {
      cidr: document.getElementById("scan-cidr").value,
      ssh_port: port,
      ssh_user: user,
      ssh_pass: password,
      verify_candidates: true,
    });
    if (!r.ok) { box.textContent = r.error || t("errOp"); return; }
    scanSubnetLabel = r.subnet || "";
    if (!r.hosts.length) { box.textContent = `No SSH/ZGX hosts found in ${r.subnet}`; updateScanFilterChips(); return; }
    for (const host of r.hosts) {
      const canEnroll = !host.enrolled && !host.is_local && !!host.ssh;
      let checkbox = null;
      if (canEnroll) {
        checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = isHostNewCandidate(host) && (host.kind === "nano" || host.kind === "fury" || host.kind === "zgx" || host.kind === "nano-hint" || !!host.ssh_verified);
        checkbox.setAttribute("aria-label", `${t("scanSelect")} ${host.ip}`);
        checkbox.addEventListener("click", event => event.stopPropagation());
        checkbox.addEventListener("change", updateScanSelection);
      }
      const status = document.createElement("span");
      status.setAttribute("aria-live", "polite");
      status.style.fontSize = "11px";
      const verified = host.ssh_verified ? { user, port } : null;
      if (host.ssh_error && isHostNewCandidate(host)) status.textContent = host.ssh_error;
      scannedHosts.push({ host, checkbox, status, verified });
    }
    const newCandidates = scannedHosts.filter(it => isHostNewCandidate(it.host));
    if (newCandidates.length === 1) {
      const h = newCandidates[0].host;
      pickHost(h.ip, h.hostname || h.ip, h.hw_model || "ZGX-NANO");
    }
    if (newCandidates.length && newCandidates.every(it => !it.verified) && !password && !r.has_default_ssh) {
      document.getElementById("scan-verify-progress").textContent = t("scanNeedPassHint");
    }
    renderScannedHosts();
  } catch (error) {
    box.textContent = t("scanConnectionError");
  } finally {
    scanInProgress = false;
    btn.disabled = false; btn.textContent = t("scanBtn");
    updateScanSelection();
  }
}

function updateScanSelection() {
  const visibleBoxes = scannedHosts
    .filter(item => item.checkbox && matchesScanFilter(item.host) && !item.host.enrolled)
    .map(item => item.checkbox);
  const checkedVisible = visibleBoxes.filter(box => box.checked).length;
  const actionable = scannedHosts.filter(
    item => !item.host.enrolled && !item.host.is_local && ((item.checkbox && item.checkbox.checked) || item.verified)
  );
  const checkedOnly = scannedHosts.filter(item => !item.host.enrolled && !item.host.is_local && item.checkbox && item.checkbox.checked);
  const enrollCount = checkedOnly.length || actionable.length;
  const busy = scanInProgress || scanTestingInProgress || scanAddingInProgress;
  const add = document.getElementById("btn-scan-add");
  add.disabled = busy || !enrollCount;
  add.textContent = `${t("scanAdd")}${enrollCount ? ` (${enrollCount})` : ""}`;
  document.getElementById("btn-scan-select").disabled = busy || !visibleBoxes.length;
  document.getElementById("btn-scan-select").textContent = t(visibleBoxes.length && checkedVisible === visibleBoxes.length ? "scanClear" : "scanSelect");
  document.getElementById("btn-scan-verify").disabled = busy || !checkedOnly.length;
}

function toggleScanSelection() {
  const boxes = scannedHosts
    .filter(item => item.checkbox && matchesScanFilter(item.host) && !item.host.enrolled)
    .map(item => item.checkbox);
  const select = boxes.some(box => !box.checked);
  for (const box of boxes) box.checked = select;
  updateScanSelection();
}

async function verifyScannedCredentials() {
  if (scanInProgress || scanTestingInProgress || scanAddingInProgress) return;
  const selected = scannedHosts.filter(item => item.checkbox && item.checkbox.checked && !item.host.enrolled);
  const user = document.getElementById("en-user").value.trim();
  const password = document.getElementById("en-pass").value;
  const port = Number(document.getElementById("en-ssh-port").value);
  if (!selected.length || !user || !Number.isInteger(port) || port < 1 || port > 65535) {
    alert(t("scanVerifyRequired")); return;
  }
  scanTestingInProgress = true;
  scanCancelRequested = false;
  updateScanSelection();
  document.getElementById("btn-scan").disabled = true;
  document.getElementById("btn-en-submit").disabled = true;
  document.getElementById("btn-scan-stop").style.display = "";
  for (const item of scannedHosts) if (item.checkbox) item.checkbox.disabled = true;
  const progress = document.getElementById("scan-verify-progress");
  let next = 0, completed = 0;
  progress.textContent = `${t("scanProgress")}: 0/${selected.length}`;
  async function worker() {
    while (!scanCancelRequested && next < selected.length) {
      const item = selected[next++];
      item.status.textContent = t("scanTesting");
      try {
        const result = await api("POST", "/api/fleet/verify-ssh", {
          host: item.host.ip, ssh_user: user, ssh_pass: password, ssh_port: port,
        });
        if (result.ok) {
          item.verified = { user, port };
          if (result.hostname && !item.host.hostname) item.host.hostname = result.hostname;
          if (result.gpu) item.host.gpu = result.gpu;
          if (result.hw_model) item.host.hw_model = result.hw_model;
          if (result.is_nano) { item.host.kind = "nano"; item.host.zgx_candidate = true; }
          else if (result.is_fury) { item.host.kind = "fury"; item.host.zgx_candidate = true; }
          else if (result.is_zgx === false && result.arch) {
            item.host.kind = "non-zgx";
            item.host.zgx_candidate = false;
            if (item.checkbox) item.checkbox.checked = false;
          }
          item.status.textContent = (result.is_zgx === false && result.arch) ? t("scanNotZgx") : t("scanVerified");
        } else {
          item.verified = null;
          item.status.textContent = result.error || t("errOp");
        }
      } catch (error) {
        item.status.textContent = t("scanConnectionError");
      }
      progress.textContent = `${t("scanProgress")}: ${++completed}/${selected.length}`;
    }
  }
  try {
    await Promise.all(Array.from({length: Math.min(4, selected.length)}, () => worker()));
    if (scanCancelRequested) progress.textContent += ` ${t("scanCancelled")}`;
  } finally {
    scanTestingInProgress = false;
    for (const item of scannedHosts) if (item.checkbox) item.checkbox.disabled = false;
    document.getElementById("btn-scan").disabled = false;
    document.getElementById("btn-en-submit").disabled = false;
    document.getElementById("btn-scan-stop").style.display = "none";
    renderScannedHosts();
    await pollEnrollStatus();
  }
}

async function addSelectedOrVerifiedHosts() {
  if (scanInProgress || scanTestingInProgress || scanAddingInProgress) return;
  const checkedItems = scannedHosts.filter(item => item.checkbox && item.checkbox.checked && !item.host.enrolled && !item.host.is_local);
  const targetItems = checkedItems.length
    ? checkedItems
    : scannedHosts.filter(item => item.verified && !item.host.enrolled && !item.host.is_local);
  if (!targetItems.length) return;

  const user = document.getElementById("en-user").value.trim() || "nvidia";
  const password = document.getElementById("en-pass").value;
  const port = Number(document.getElementById("en-ssh-port").value || 22);

  scanAddingInProgress = true;
  updateScanSelection();
  document.getElementById("btn-scan").disabled = true;
  document.getElementById("btn-en-submit").disabled = true;
  const progress = document.getElementById("scan-verify-progress");
  let added = 0, skipped = 0, errors = 0;
  try {
    for (const item of targetItems) {
      progress.textContent = `${t("scanAdding")} ${added + skipped + errors + 1}/${targetItems.length}`;
      if (!item.verified) {
        item.status.textContent = t("scanTesting");
        const vr = await api("POST", "/api/fleet/verify-ssh", {
          host: item.host.ip, ssh_user: user, ssh_pass: password, ssh_port: port,
        });
        if (!vr.ok) {
          item.status.textContent = vr.error || t("errOp");
          errors++;
          continue;
        }
        item.verified = { user, port };
        if (vr.hostname && !item.host.hostname) item.host.hostname = vr.hostname;
        if (vr.gpu) item.host.gpu = vr.gpu;
        if (vr.hw_model) item.host.hw_model = vr.hw_model;
      }
      item.status.textContent = t("scanAdding");
      try {
        const { user: vUser, port: vPort } = item.verified;
        const result = await api("POST", "/api/fleet/enroll", {
          host: item.host.ip, alias: item.host.hostname || item.host.ip,
          tag: item.host.hw_model || "ZGX-NANO", port: 18888, role: "node",
          ssh_user: vUser, ssh_port: vPort, verified_ssh: true,
        });
        if (!result.ok) throw new Error(result.error || t("errOp"));
        if (result.skipped) {
          item.host.enrolled = true;
          if (item.checkbox) item.checkbox.checked = false;
          item.status.textContent = t("scanAlreadyAdded");
          skipped++;
          continue;
        }
        let state;
        do {
          state = await api("GET", "/api/fleet/enroll/status");
          if (!result.job_id || state.job_id !== result.job_id) throw new Error(t("scanEnrollStatusLost"));
          if (state.running) await new Promise(resolve => setTimeout(resolve, 800));
        } while (state.running);
        if (!state.ok) throw new Error(state.error || t("scanEnrollStatusLost"));
        item.host.enrolled = true;
        if (item.checkbox) item.checkbox.checked = false;
        item.status.textContent = t("scanAdded");
        added++;
      } catch (error) {
        item.status.textContent = error.message || t("scanConnectionError");
        errors++;
      }
    }
  } finally {
    scanAddingInProgress = false;
    progress.textContent = `${t("scanAddFinished")}: ${added} / ${skipped} / ${errors}`;
    document.getElementById("btn-scan").disabled = false;
    document.getElementById("btn-en-submit").disabled = false;
    renderScannedHosts();
    if (added) refresh();
    await pollEnrollStatus();
  }
}

async function addVerifiedHosts() {
  return addSelectedOrVerifiedHosts();
}

function pickHost(ip, name, hwModel) {
  document.getElementById("en-host").value = ip;
  document.getElementById("en-alias").value = name || ip;
  if (hwModel) document.getElementById("en-tag").value = hwModel;
}

async function pollEnrollStatus() {
  const st = await api("GET", "/api/fleet/enroll/status");
  if (!st || st.phase === undefined) return;
  const btn = document.getElementById("btn-en-submit");
  const scanBtn = document.getElementById("btn-scan");
  const wrap = document.getElementById("enroll-prog-wrap");
  const log = document.getElementById("enroll-log");
  if (st.running || st.log) {
    wrap.style.display = "block";
    log.style.display = "block";
    const pct = Math.round(((st.step || 0) / (st.total_steps || 3)) * 100);
    document.getElementById("enroll-phase").textContent = `Paso ${st.step}/${st.total_steps}: ${st.phase}`;
    document.getElementById("enroll-elapsed").textContent = `${st.elapsed_s || 0}s`;
    const bar = document.getElementById("enroll-bar");
    bar.style.width = pct + "%";
    bar.className = "bar-fill" + (st.ok === false ? " crit" : (st.ok === true ? " gpu" : ""));
    log.textContent = st.log || "";
    log.scrollTop = log.scrollHeight;
  } else {
    wrap.style.display = "none";
    log.style.display = "none";
  }
  btn.disabled = !!st.running || scanInProgress || scanTestingInProgress || scanAddingInProgress;
  scanBtn.disabled = !!st.running || scanInProgress || scanTestingInProgress || scanAddingInProgress;
  if (st.running) {
    if (!enrollTimer) enrollTimer = setInterval(pollEnrollStatus, 800);
  } else {
    if (enrollTimer) { clearInterval(enrollTimer); enrollTimer = null; }
    if (st.ok === true) refresh();
  }
}

async function submitEnroll() {
  const btn = document.getElementById("btn-en-submit");
  btn.disabled = true;
  const r = await api("POST", "/api/fleet/enroll", {
    host: document.getElementById("en-host").value,
    ssh_port: parseInt(document.getElementById("en-ssh-port").value || "22", 10),
    port: parseInt(document.getElementById("en-port").value || "18888", 10),
    alias: document.getElementById("en-alias").value,
    tag: document.getElementById("en-tag").value,
    role: document.getElementById("en-role").value,
    ssh_user: document.getElementById("en-user").value,
    ssh_pass: document.getElementById("en-pass").value,
  });
  if (!r.ok) {
    alert(r.error || "Error al iniciar enrolamiento");
    await pollEnrollStatus();
    return;
  }
  document.getElementById("en-pass").value = "";
  const enrolledIp = document.getElementById("en-host").value.trim();
  const matched = scannedHosts.find(it => it.host.ip === enrolledIp);
  if (matched) {
    matched.host.enrolled = true;
    if (matched.checkbox) matched.checkbox.checked = false;
    renderScannedHosts();
  }
  await pollEnrollStatus();
}

function openNodeDeployModal() {
  if (selectedNodeId && selectedNodeId !== "local") openDeployModal(selectedNodeId);
}

async function openDeployModal(nodeId) {
  let node = fleetNodesById[nodeId];
  if (!node) {
    const f = await api("GET", "/api/fleet");
    if (f && f.nodes) {
      f.nodes.forEach(n => { fleetNodesById[n.id] = n; });
      node = fleetNodesById[nodeId];
    }
  }
  if (!node || node.is_local) {
    alert("Selecciona un nodo remoto para desplegar.");
    return;
  }
  deployTargetNodeId = nodeId;
  nanoSetupRunning = false;
  document.getElementById("dep-nano-status").style.display = "none";
  document.getElementById("dep-target").textContent = `${node.alias || node.host} (${node.host}:${node.port || 18888}) [${node.hw_model || "ZGX"}]`;
  document.getElementById("dep-ssh-user").value = node.ssh_user || "nvidia";
  document.getElementById("dep-ssh-port").value = node.ssh_port || 22;
  resetPasswordField("dep-ssh-pass");

  const listEl = document.getElementById("dep-list");
  listEl.innerHTML = `<div style="padding:10px;color:var(--muted);font-size:12px;">Cargando catalogo...</div>`;
  document.getElementById("deploy-modal").classList.add("open");

  const cat = await api("GET", "/api/fleet/deploy/catalog");
  const choices = [
    ["portal", "deployPortalOnly", "deployAgentDesc"],
    ["fury-showcase", "deployFury", "deployFuryDesc"],
    ["nano-collective", "deployNano", "deployNanoDesc"],
    ["source-only", "deploySourceOnly", "deploySourceOnlyDesc"],
  ];
  listEl.innerHTML = choices.map(([value, name, desc], index) => `
    <label class="deploy-item">
      <input type="radio" name="deploy-choice" value="${value}" ${index === 0 ? "checked" : ""} onchange="updateDeploySource()">
      <div>
        <strong>${t(name)}</strong>
        <span>${t(desc)}</span>
      </div>
    </label>`).join("");
  deploySources = cat.sources || {};
  deploySourceErrors = cat.source_errors || [];
  updateDeploySource();
  await pollDeployStatus();
  await pollNanoSetupStatus();
  if (deployTargetNodeId === nodeId && document.getElementById("deploy-modal").classList.contains("open") && !nanoSetupTimer)
    nanoSetupTimer = setInterval(pollNanoSetupStatus, 10000);
}

let deploySources = {}, deploySourceErrors = [];
function updateDeploySource() {
  const choice = document.querySelector('input[name="deploy-choice"]:checked').value;
  const wrap = document.getElementById("dep-source-wrap");
  const select = document.getElementById("dep-source");
  wrap.style.display = "block";
  document.getElementById("dep-source-label").textContent = t("deploySource");
  select.replaceChildren();
  for (const source of (deploySources[choice === "source-only" ? "portal" : choice] || [])) {
    if (source.id === deployTargetNodeId) continue;
    const option = document.createElement("option");
    option.value = source.id;
    option.textContent = source.name;
    select.appendChild(option);
  }
  document.getElementById("dep-source-note").textContent = select.options.length
    ? "" : [t("deployNoSource"), ...deploySourceErrors].join(" ");
}

function closeDeployModal() {
  resetPasswordField("dep-ssh-pass");
  document.getElementById("deploy-modal").classList.remove("open");
  if (deployTimer) { clearInterval(deployTimer); deployTimer = null; }
  if (nanoSetupTimer) { clearInterval(nanoSetupTimer); nanoSetupTimer = null; }
}

async function pollNanoSetupStatus() {
  const nodeId = deployTargetNodeId;
  const note = document.getElementById("dep-nano-status");
  const params = new URLSearchParams({
    node_id: nodeId,
    ssh_user: document.getElementById("dep-ssh-user").value,
    ssh_port: document.getElementById("dep-ssh-port").value,
  });
  try {
    const result = await api("GET", "/api/fleet/deploy/nano-status?" + params);
    if (nodeId !== deployTargetNodeId || !document.getElementById("deploy-modal").classList.contains("open")) return;
    nanoSetupRunning = result.ok && /^(RUNNING:|QUEUED)/.test(result.status || "");
    note.textContent = result.ok ? (result.status ? `${t("nanoBackground")}: ${result.status}` : "")
      : `${t("nanoStatusError")}: ${result.error || "Error"}`;
    note.style.display = note.textContent ? "block" : "none";
  } catch (error) {
    note.textContent = `${t("nanoStatusError")}: ${error.message}`;
    note.style.display = "block";
  }
  document.getElementById("btn-dep-submit").disabled = deployRunning || nanoSetupRunning;
}

async function pollDeployStatus() {
  const st = await api("GET", "/api/fleet/deploy/status");
  if (!st || st.phase === undefined) return;
  const btn = document.getElementById("btn-dep-submit");
  const wrap = document.getElementById("deploy-prog-wrap");
  const log = document.getElementById("deploy-log");
  const relevant = st.running || (st.log && (!deployTargetNodeId || st.node_id === deployTargetNodeId));
  if (relevant) {
    wrap.style.display = "block";
    log.style.display = "block";
    const completedSteps = st.ok === true ? st.step : Math.max(0, (st.step || 0) - 1);
    const pct = Math.round((completedSteps / (st.total_steps || 3)) * 100);
    document.getElementById("deploy-phase").textContent = `Paso ${st.step}/${st.total_steps}: ${st.phase}`;
    document.getElementById("deploy-elapsed").textContent = `${st.elapsed_s || 0}s`;
    const bar = document.getElementById("deploy-bar");
    bar.style.width = pct + "%";
    bar.className = "bar-fill" + (st.ok === false ? " crit" : (st.ok === true ? " gpu" : ""));
    log.textContent = st.log || "";
    log.scrollTop = log.scrollHeight;
  } else {
    wrap.style.display = "none";
    log.style.display = "none";
  }
  deployRunning = !!st.running;
  btn.disabled = deployRunning || nanoSetupRunning;
  if (st.running) {
    if (!deployTimer) deployTimer = setInterval(pollDeployStatus, 800);
  } else {
    if (deployTimer) { clearInterval(deployTimer); deployTimer = null; }
    if (st.phase === "Background" && st.node_id === deployTargetNodeId) await pollNanoSetupStatus();
    if (st.ok === true) refresh();
  }
}

async function submitDeploy() {
  if (!deployTargetNodeId) return;
  const choice = document.querySelector('input[name="deploy-choice"]:checked').value;
  const deployAgent = choice === "portal";
  const copyOnly = choice === "source-only";
  const deployDemos = deployAgent || copyOnly ? [] : [choice];
  const sourceId = document.getElementById("dep-source").value;
  if (!sourceId) { alert(t("deployNoSource")); return; }
  const btn = document.getElementById("btn-dep-submit");
  btn.disabled = true;
  deployRunning = true;
  let r;
  try {
    r = await api("POST", "/api/fleet/deploy", {
      node_id: deployTargetNodeId,
      ssh_user: document.getElementById("dep-ssh-user").value,
      ssh_port: parseInt(document.getElementById("dep-ssh-port").value || "22", 10),
      ssh_pass: document.getElementById("dep-ssh-pass").value,
      deploy_agent: deployAgent,
      deploy_demos: deployDemos,
      copy_only: copyOnly,
      source_id: sourceId,
    });
  } catch (error) {
    deployRunning = false;
    btn.disabled = nanoSetupRunning;
    alert(error.message);
    return;
  }
  if (!r.ok) {
    deployRunning = false;
    alert(r.error || "Error al iniciar despliegue");
    await pollDeployStatus();
    return;
  }
  document.getElementById("dep-ssh-pass").value = "";
  await pollDeployStatus();
}

async function svcAction(unit, action) {
  busy = true;
  try {
    const r = await api("POST", `/api/node/${selectedNodeId}/service/${encodeURIComponent(unit)}/${action}`);
    if (!r.ok) alert(r.error || t("errOp"));
  } finally { busy = false; await refresh(); }
}

let furyUnifiedMode = localStorage.getItem("zgx_fury_unified_mode") || "fury-collective";

async function toggleDemo(key, enable) {
  busy = true;
  try {
    const r = enable ? await api("POST", `/api/node/${selectedNodeId}/start/${key}`) : await api("POST", `/api/node/${selectedNodeId}/stop`);
    if (!r.ok) alert(r.error || t("errOp"));
  } finally { busy = false; await refresh(); }
}

async function selectFuryUnifiedMode(targetKey) {
  furyUnifiedMode = targetKey;
  localStorage.setItem("zgx_fury_unified_mode", targetKey);
  const activeKey = (lastNodeData && lastNodeData.active && lastNodeData.active.key) || "";
  if ((activeKey === "fury-collective" || activeKey === "fury-arena") && activeKey !== targetKey) {
    await toggleDemo(targetKey, true);
    return;
  }
  if (lastNodeData) renderNodeData(lastNodeData);
}

async function stopDemo() {
  busy = true;
  try { await api("POST", `/api/node/${selectedNodeId}/stop`); }
  finally { busy = false; await refresh(); }
}

async function openNanoMode(mode) {
  const popup = window.open("about:blank", "_blank");
  if (popup && !popup.closed) {
    try {
      popup.document.title = t("switchingMode");
      popup.document.body.style.cssText = "background:#080d14;color:#e6edf3;font-family:sans-serif;padding:24px;";
      popup.document.body.textContent = t("switchingMode");
    } catch (_) {}
  }
  busy = true;
  try {
    const r = await api("POST", `/api/node/${selectedNodeId}/nano-mode/${encodeURIComponent(mode)}`);
    if (!r.ok) {
      if (popup && !popup.closed) popup.close();
      alert(r.error || t("errOp"));
      return;
    }
    const targetUrl = "http://" + selectedNodeHost + ":18808" + (mode === "presenter" ? "/stage" : "/free");
    if (popup && !popup.closed) {
      popup.opener = null;
      popup.location.href = targetUrl;
    } else {
      window.open(targetUrl, "_blank", "noopener");
    }
  } catch (err) {
    if (popup && !popup.closed) popup.close();
    alert(t("errOp"));
  } finally {
    busy = false;
    await refresh();
  }
}

async function startAllNanoCollective() {
  if (!confirm("Arrancar Nano Collective en todas las NanoZGX registradas?")) return;
  const btn = document.getElementById("btn-start-all-nano");
  btn.disabled = true;
  try {
    const r = await api("POST", "/api/fleet/start-nano-collective", {});
    if (!r.ok) { alert(r.error || t("errOp")); return; }
    const okTxt = (r.started && r.started.length) ? ("Iniciadas: " + r.started.join(", ")) : "Ninguna iniciada";
    const errTxt = (r.failed && r.failed.length) ? ("\nFallos: " + r.failed.map(f => `${f.host} (${f.error})`).join("; ")) : "";
    alert(okTxt + errTxt);
    await refresh();
  } finally { btn.disabled = false; }
}

async function reorganizeFleet() {
  const btn = document.getElementById("btn-reorganize-fleet");
  btn.disabled = true;
  try {
    const r = await api("POST", "/api/fleet/reorganize", {});
    if (!r.ok) { alert(r.error || t("errOp")); return; }
    const upd = (r.updated && r.updated.length) ? r.updated.map(u => `${u[0]} -> ${u[1]}`).join(", ") : "Sin cambios de IP";
    alert(`Red reorganizada (rol: ${r.role}).\nActualizaciones: ${upd}`);
    await refresh();
  } finally { btn.disabled = false; }
}

async function pushFleetUpdate() {
  const btn = document.getElementById("btn-push-update");
  btn.disabled = true;
  try {
    const r = await api("POST", "/api/fleet/push-update");
    const lines = (r.results || []).map(x => `${x.alias || x.host}: ${x.ok ? "OK" : "ERROR (" + x.error + ")"}`);
    alert(lines.length ? lines.join("\n") : "Sin nodos remotos en la flota.");
  } finally {
    btn.disabled = false;
    await refresh();
  }
}

async function locateNodeAction() {
  const r = await api("POST", `/api/node/${selectedNodeId}/locate`);
  if (!r.ok) { alert(r.error || t("errOp")); return; }
  alert(`Rafaga de ventilador iniciada en ${r.hostname || selectedNodeHost} (${r.duration_s || 8}s).`);
}

async function networkModeAction(mode) {
  let payload = { mode };
  if (mode === "static") {
    const curIp = selectedNodeHost && selectedNodeHost !== "localhost" ? `${selectedNodeHost}/24` : "";
    const ipCidr = prompt("IP fija con mascara (ej. 192.168.1.104/24):", curIp);
    if (!ipCidr) return;
    const gw = prompt("Gateway (dejar vacio para usar el actual):", "");
    payload.ip_cidr = ipCidr.trim();
    if (gw && gw.trim()) payload.gateway = gw.trim();
  } else {
    if (!confirm(`Pasar la interfaz activa de ${selectedNodeHost} a DHCP?`)) return;
  }
  const r = await api("POST", `/api/node/${selectedNodeId}/network/mode`, payload);
  if (!r.ok) { alert(r.error || t("errOp")); return; }
  alert(mode === "static"
    ? `IP fija aplicada en ${r.device}: ${r.ip_cidr} (GW ${r.gateway}).`
    : `Interfaz ${r.device} pasada a DHCP.`);
}

async function freeGpuAction() {
  if (!confirm("Detener ollama, comfyui, sd-webui y zgx-watchdog en este nodo para liberar la GPU?")) return;
  busy = true;
  try {
    const r = await api("POST", `/api/node/${selectedNodeId}/free-gpu`);
    if (!r.ok) alert(r.error || t("errOp"));
  } finally { busy = false; await refresh(); }
}

async function pruneDockerAction() {
  if (!confirm("Ejecutar 'docker system prune -af --volumes' para borrar imagenes, contenedores parados y volumenes Docker sin uso?")) return;
  busy = true;
  try {
    const r = await api("POST", `/api/node/${selectedNodeId}/prune-docker`);
    alert(r.ok ? ("Docker limpiado:\n" + (r.output || "OK")) : ("Error: " + r.error));
  } finally { busy = false; await refresh(); }
}

async function powerAction(kind) {
  if (!confirm(`Confirmar ${kind.toUpperCase()} en el nodo ${selectedNodeHost}?`)) return;
  await api("POST", `/api/node/${selectedNodeId}/power/${kind}`);
  alert(`Comando ${kind} enviado.`);
}

function fmtUptime(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return (d ? d + "d " : "") + h + "h " + m + "m";
}
function setBar(elId, pct, isGpu) {
  const el = document.getElementById(elId), v = Math.max(0, Math.min(100, pct || 0));
  el.style.width = v + "%"; el.className = "bar-fill" + (isGpu ? " gpu" : "");
  if (v >= 90) el.classList.add("crit"); else if (v >= 75) el.classList.add("warn");
}
function drawChart(svgId, seriesList, maxVal) {
  const svg = document.getElementById(svgId), W = 360, H = 115, pad = 6;
  const grid = [25, 50, 75].map(p => `<line x1="0" y1="${H - (p/100)*(H-pad*2)-pad}" x2="${W}" y2="${H - (p/100)*(H-pad*2)-pad}" stroke="#141e2b" stroke-width="1"/>`).join("");
  let paths = "";
  for (const s of seriesList) {
    const vals = s.values || []; if (vals.length < 2) continue;
    const peak = maxVal || Math.max(10, ...vals) * 1.15;
    const pts = vals.map((v, i) => [((i / Math.max(1, vals.length - 1)) * (W - 4) + 2).toFixed(1), (H - (Math.min(peak, Math.max(0, v)) / peak) * (H - pad * 2) - pad).toFixed(1)]);
    const lineD = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0] + "," + p[1]).join(" ");
    if (s.fill) paths += `<path d="${lineD} L${pts[pts.length - 1][0]},${H} L${pts[0][0]},${H} Z" fill="${s.fill}" opacity="0.16"/>`;
    paths += `<path d="${lineD}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
  }
  svg.innerHTML = grid + paths;
}

function copyLogBox(boxId, btnId) {
  const el = document.getElementById(boxId);
  const btn = document.getElementById(btnId);
  if (!el) return;
  const text = el.textContent || "";
  const done = () => {
    if (!btn) return;
    btn.textContent = t("copiedLog");
    setTimeout(() => { btn.textContent = t("copyLog"); }, 2000);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e) {}
  document.body.removeChild(ta);
  done();
}

function fmtTokens(n) {
  const v = Number(n || 0);
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "k";
  return String(v);
}

function fmtEur(v) {
  const n = Number(v || 0);
  return "\u20AC" + (n < 1 ? n.toFixed(4) : n.toFixed(2));
}

async function resetFleetTokens() {
  if (!confirm(t("confirmResetFleetTok"))) return;
  await api("POST", "/api/fleet/tokens/reset");
  await refresh();
}

async function resetNodeTokens() {
  if (!confirm(t("confirmResetTok"))) return;
  await api("POST", `/api/node/${selectedNodeId}/tokens/reset`);
  await refresh();
}

async function saveTokenRates(prefix) {
  const inEl = document.getElementById(prefix + "-rate-in");
  const outEl = document.getElementById(prefix + "-rate-out");
  const inVal = parseFloat(inEl ? inEl.value : "2.76");
  const outVal = parseFloat(outEl ? outEl.value : "13.80");
  if (!Number.isFinite(inVal) || !Number.isFinite(outVal) || inVal < 0 || outVal < 0) return;
  await api("POST", "/api/tokens/rates", { input_eur_per_1m: inVal, output_eur_per_1m: outVal });
  await refresh();
}

function renderTokenBanner(elId, tokData, titleText, onResetFnName, extraDemoLabels) {
  const el = document.getElementById(elId);
  if (!el) return;
  const tot = (tokData && tokData.total) || { input_tokens: 0, output_tokens: 0, total_tokens: 0, eur_saved: 0, tok_per_s: 0 };
  const rates = (tokData && tokData.rates) || { input_eur_per_1m: 2.76, output_eur_per_1m: 13.80 };
  const byDemo = (tokData && tokData.by_demo) || {};
  const demoKeys = Object.keys(byDemo).sort();
  const chips = demoKeys.map(k => {
    const dv = byDemo[k] || {};
    const label = (extraDemoLabels && extraDemoLabels[k]) || k;
    return `<div class="token-chip"><span>${label}:</span><strong>${fmtTokens(dv.total_tokens || 0)}</strong><span class="eur-tag">${fmtEur(dv.eur_saved || 0)}</span></div>`;
  }).join("");
  const activeFocused = document.activeElement && (document.activeElement.id === elId + "-rate-in" || document.activeElement.id === elId + "-rate-out");
  if (!activeFocused) {
    el.innerHTML = `
      <div class="token-banner-head">
        <h3>${titleText}</h3>
        <div class="btn-row">
          <label class="port-lbl" style="display:flex;align-items:center;gap:4px;">${t("eurRateNote")} In €<input id="${elId}-rate-in" type="number" min="0" step="0.1" value="${Number(rates.input_eur_per_1m).toFixed(2)}" style="width:62px;background:#080d14;border:1px solid #1b2d42;color:#e6edf3;border-radius:5px;padding:3px 5px;font-size:11px;"></label>
          <label class="port-lbl" style="display:flex;align-items:center;gap:4px;">Out €<input id="${elId}-rate-out" type="number" min="0" step="0.1" value="${Number(rates.output_eur_per_1m).toFixed(2)}" style="width:62px;background:#080d14;border:1px solid #1b2d42;color:#e6edf3;border-radius:5px;padding:3px 5px;font-size:11px;"></label>
          <button onclick="saveTokenRates('${elId}')">OK</button>
          <button onclick="${onResetFnName}()">${t("resetCounters")}</button>
        </div>
      </div>
      <div class="token-kpis">
        <div class="token-kpi">
          <div class="token-kpi-lbl">${t("tokTotal")}</div>
          <div class="token-kpi-val">${(tot.total_tokens || 0).toLocaleString()}</div>
          <div class="token-kpi-sub">${t("tokIn")}: ${(tot.input_tokens || 0).toLocaleString()} | ${t("tokOut")}: ${(tot.output_tokens || 0).toLocaleString()}</div>
        </div>
        <div class="token-kpi">
          <div class="token-kpi-lbl">${t("eurSaved")}</div>
          <div class="token-kpi-val eur">${fmtEur(tot.eur_saved || 0)}</div>
          <div class="token-kpi-sub">${t("tokensLbl")}: ${fmtTokens(tot.total_tokens || 0)}${tot.tok_per_s ? " | " + tot.tok_per_s + " tok/s" : ""}</div>
        </div>
      </div>
      <div class="token-demo-strip">${chips}</div>`;
  }
}

function renderFleet(data) {
  currentRole = data.role || "hub";
  fleetNodesById = {};
  (data.nodes || []).forEach(n => { fleetNodesById[n.id] = n; });
  document.getElementById("view-fleet").style.display = "block";
  document.getElementById("view-node").style.display = "none";
  document.getElementById("btn-back-fleet").style.display = "none";
  document.getElementById("btn-claim-master").style.display = currentRole === "node" ? "inline-flex" : "none";
  document.getElementById("btn-stop-all").style.display = "none";
  document.getElementById("host-title").textContent = "HP ZGX - Fleet Console";
  document.getElementById("host-sub").textContent = t("subtitle") + " | Role: " + currentRole.toUpperCase();
  document.getElementById("btn-role-toggle").textContent = "Role: " + currentRole.toUpperCase();
  const gs = document.getElementById("global-status");
  const onlineCnt = (data.nodes || []).filter(n => n.online).length;
  gs.textContent = `[ONLINE] ${onlineCnt}/${(data.nodes || []).length} ZGX`;
  gs.className = "pill ready";

  renderTokenBanner("fleet-token-panel", data.fleet_tokens, t("fleetTokTitle"), "resetFleetTokens", {
    campus: "CampusCompass", atlas: "Atlas Voice", guard: "Beat the Guard",
    vision: "Observatory", "nano-collective": "Nano Collective", fury: "Fury Showcase"
  });

  document.getElementById("fleet-grid").innerHTML = (data.nodes || []).map(n => {
    const act = n.active || {};
    const demoStr = act.key ? `[${(act.status || "").toUpperCase()}] ${act.key}` : "[IDLE]";
    const ramStr = n.ram && n.ram.total_gib ? `${n.ram.used_gib}/${n.ram.total_gib} GiB` : "--";
    const diskStr = n.disk && n.disk.free_gb !== undefined ? `${n.disk.free_gb} GB ${t("free")}` : "--";
    const gpuStr = n.gpu && n.gpu.util_pct !== null ? `${n.gpu.util_pct}% (${n.gpu.temp_c ?? "--"} C)` : "N/D";
    const nTok = (n.tokens && n.tokens.total) || { total_tokens: 0, input_tokens: 0, output_tokens: 0, eur_saved: 0 };
    return `
      <div class="fleet-card ${n.online ? "online" : "offline"}">
        <div class="fleet-head">
          <div>
            <h2 style="margin:0 0 3px;font-size:17px;">${n.alias} <span style="font-size:12px;color:#79c0ff;">[${n.hw_model || "ZGX"}]</span></h2>
            <div class="port-lbl">${n.is_local ? "localhost" : n.host}:${n.port} | ${n.tag} (${(n.role || "node").toUpperCase()})</div>
          </div>
          <span class="pill ${n.online ? "ready" : "idle"}" title="${n.observed_at ? new Date(n.observed_at * 1000).toLocaleString() : ""}">${n.online ? "[ONLINE]" : (n.observed_at ? "[OFFLINE / CACHE]" : "[OFFLINE]")}</span>
        </div>
        <div class="fleet-metrics">
          <div>CPU: ${n.cpu_pct ?? 0}%</div>
          <div>GPU: ${gpuStr}</div>
          <div>RAM: ${ramStr}</div>
          <div>Disk: ${diskStr}</div>
        </div>
        <div class="demo-tok-bar" style="margin-bottom:10px;">
          <span>${t("tokensLbl")}: <strong>${(nTok.total_tokens || 0).toLocaleString()}</strong> (${fmtTokens(nTok.input_tokens || 0)}/${fmtTokens(nTok.output_tokens || 0)})</span>
          <span class="eur-tag">${t("apiEqLbl")}: ${fmtEur(nTok.eur_saved || 0)}</span>
        </div>
        <div class="demo-footer">
          <span class="port-lbl">Demo: ${demoStr} | Inst: ${n.demos_installed ?? 0}/${n.demos_total ?? 0}</span>
          <div class="btn-row">
            ${!n.is_local ? `<button class="primary" onclick="openDeployModal('${n.id}')">${t("deployBtn")}</button><button class="danger" onclick="removeNode('${n.id}')">${t("removeNode")}</button>` : ""}
            ${!n.online && n.observed_at
              ? `<a class="btn" href="/api/node/${encodeURIComponent(n.id)}/last-status" target="_blank" rel="noopener">${t("cachedMonitoring")}</a>`
              : `<button class="solid" ${!n.online ? "disabled" : ""} onclick="enterNode('${n.id}', '${n.host}')">${t("enterNode")}</button>`}
          </div>
        </div>
      </div>`;
  }).join("");
}

function renderNodeData(data) {
  const active = data.active, sys = data.system, hist = data.history || [];
  document.getElementById("view-fleet").style.display = "none";
  document.getElementById("view-node").style.display = "block";
  document.getElementById("btn-back-fleet").style.display = "inline-flex";
  document.getElementById("btn-claim-master").style.display = currentRole === "node" ? "inline-flex" : "none";
  document.getElementById("btn-node-deploy").style.display = (selectedNodeId && selectedNodeId !== "local") ? "inline-flex" : "none";
  document.getElementById("btn-stop-all").style.display = "inline-flex";

  document.getElementById("host-title").textContent = "HP ZGX Nano (" + (sys.hostname || "zgx") + ")";
  document.getElementById("host-sub").textContent = "Uptime: " + fmtUptime(sys.uptime_s || 0) + " | Host: " + selectedNodeHost;

  const gs = document.getElementById("global-status");
  if (active.key) { gs.textContent = `[${active.status.toUpperCase()}] ${active.key} (${active.uptime_s}s)`; gs.className = "pill " + (active.status === "ready" ? "ready" : "starting"); }
  else { gs.textContent = t("noActiveDemo"); gs.className = "pill idle"; }
  document.getElementById("btn-stop-all").disabled = !active.key;

  const errBox = document.getElementById("err-box");
  if (active.last_error) { errBox.style.display = "block"; errBox.textContent = "[ERROR] " + active.last_error; }
  else errBox.style.display = "none";

  const cpu = sys.cpu || {};
  document.getElementById("cpu-cores").textContent = (cpu.cores || "--") + " cores";
  document.getElementById("cpu-val").textContent = (cpu.pct ?? 0) + "%";
  document.getElementById("cpu-load").textContent = `Load: ${cpu.load1} / ${cpu.load5} / ${cpu.load15}`;
  setBar("cpu-bar", cpu.pct || 0, false);

  const ram = sys.ram || {};
  document.getElementById("ram-pct").textContent = (ram.pct ?? 0) + "%";
  document.getElementById("ram-val").textContent = `${ram.used_gib} / ${ram.total_gib} GiB`;
  document.getElementById("ram-avail").textContent = `${t("avail")}: ${ram.avail_gib} GiB`;
  setBar("ram-bar", ram.pct || 0, false);

  const gpu = sys.gpu || {};
  document.getElementById("gpu-name").textContent = gpu.name || "NVIDIA GB10";
  document.getElementById("gpu-temp").textContent = gpu.temp_c !== null ? gpu.temp_c + " C" : "-- C";
  document.getElementById("gpu-val").textContent = gpu.util_pct !== null ? gpu.util_pct + "%" : "N/D";
  document.getElementById("gpu-extra").textContent = `Driver: ${gpu.driver || "--"} | ${gpu.power_w ?? "--"} W`;
  setBar("gpu-bar", gpu.util_pct || 0, true);

  const disk = sys.disk || {};
  document.getElementById("disk-pct").textContent = (disk.pct ?? 0) + "%";
  document.getElementById("disk-val").textContent = `${disk.free_gb} GB ${t("free")}`;
  const lowD = (disk.free_gb || 0) < 60 || (disk.pct || 0) >= 85;
  document.getElementById("disk-total").textContent = (lowD ? t("lowDisk") + " | " : "") + `${t("used")}: ${disk.used_gb} / ${disk.total_gb} GB`;
  setBar("disk-bar", disk.pct || 0, false);

  const demoTotal = Object.keys(data.demos || {}).length;
  document.getElementById("nav-demos-cnt").textContent = (active.key ? 1 : 0) + "/" + demoTotal;
  const svcList = data.services || [];
  document.getElementById("nav-services-cnt").textContent = svcList.filter(s => s.active_state === "active").length + "/" + svcList.length;
  document.getElementById("nav-gpu-cnt").textContent = (sys.gpu_apps ? sys.gpu_apps.length : 0) + " GPU";

  document.getElementById("ch-cpu-val").textContent = (cpu.pct ?? 0) + "%";
  drawChart("svg-cpu", [{ values: hist.map(h => h.cpu), color: "#38bdf8", fill: "#0096d6" }], 100);
  document.getElementById("ch-gpu-val").textContent = (gpu.util_pct ?? 0) + "%";
  drawChart("svg-gpu", [{ values: hist.map(h => h.gpu), color: "#76b900", fill: "#76b900" }], 100);
  document.getElementById("ch-ram-val").textContent = `${ram.pct ?? 0}% (${ram.used_gib} GiB)`;
  drawChart("svg-ram", [{ values: hist.map(h => h.ram), color: "#2dd4bf", fill: "#2dd4bf" }], 100);
  document.getElementById("ch-temp-val").textContent = `${gpu.temp_c ?? "--"} C / ${gpu.power_w ?? "--"} W`;
  drawChart("svg-temp", [{ values: hist.map(h => h.temp), color: "#f59e0b", fill: "#f59e0b" }, { values: hist.map(h => h.power), color: "#f85149", fill: null }], 100);
  const net = sys.net || {};
  document.getElementById("ch-net-val").textContent = `RX ${net.rx_kbps ?? 0} / TX ${net.tx_kbps ?? 0} KB/s`;
  drawChart("svg-net", [{ values: hist.map(h => h.rx), color: "#a855f7", fill: "#a855f7" }, { values: hist.map(h => h.tx), color: "#38bdf8", fill: null }], Math.max(50, ...hist.map(h => Math.max(h.rx || 0, h.tx || 0))));

  const dio = sys.disk_io || {};
  document.getElementById("ch-io-val").textContent = `R ${dio.read_mbs ?? 0} / W ${dio.write_mbs ?? 0} MB/s`;
  const totalG = ram.total_gib || 1, usedPct = Math.min(100, (ram.used_gib / totalG) * 100), cachePct = Math.min(100 - usedPct, ((ram.cached_gib || 0) / totalG) * 100);
  document.getElementById("seg-used").style.width = usedPct.toFixed(1) + "%";
  document.getElementById("seg-cache").style.width = cachePct.toFixed(1) + "%";
  document.getElementById("mem-legend").innerHTML = `<span><span class="dot" style="background:#0096d6"></span>${t("memUsed")}: ${ram.used_gib} GiB</span><span><span class="dot" style="background:#2dd4bf"></span>${t("memCache")}: ${ram.cached_gib} GiB</span><span>${t("swap")}: ${ram.swap_used_gib}/${ram.swap_total_gib} GiB</span>`;
  document.getElementById("hw-extra").textContent = `SM Clock: ${gpu.sm_mhz ?? "--"} MHz | PState: ${gpu.pstate || "--"} | Net: RX ${net.rx_gb ?? 0} GB / TX ${net.tx_gb ?? 0} GB`;

  document.getElementById("cores-load").textContent = `Load: ${cpu.load1} / ${cpu.load5} / ${cpu.load15}`;
  document.getElementById("cores-grid").innerHTML = (cpu.per_core || []).map((v, idx) => `<div class="core-item"><div class="core-lbl">C${idx}</div><div class="core-val">${v}%</div><div class="core-bar"><div class="core-fill" style="width:${Math.min(100, v)}%"></div></div></div>`).join("");
  document.getElementById("top-procs-body").innerHTML = (sys.top_procs || []).map(p => `<tr><td>${p.pid}</td><td>${p.user}</td><td>${p.cpu}%</td><td>${p.rss_mb} MB</td><td>${p.cmd}</td></tr>`).join("");
  document.getElementById("ports-wrap").innerHTML = (sys.ports_map || []).map(pm => `<span class="port-chip ${pm.open ? "open" : ""}">:${pm.port} ${pm.label}</span>`).join("");
  const gpBox = document.getElementById("gpu-procs");
  gpBox.innerHTML = (sys.gpu_apps && sys.gpu_apps.length) ? sys.gpu_apps.map(a => `<div class="gpu-proc"><span>PID ${a.pid} - ${a.name}</span><span>${a.mem}</span></div>`).join("") : `<div class="gpu-proc"><span>${t("noGpuProcs")}</span><span>0 MiB</span></div>`;

  // Services
  const svcGrid = document.getElementById("svc-grid"), svcTableWrap = document.getElementById("svc-table-wrap"), svcTableBody = document.getElementById("svc-table-body");
  svcGrid.innerHTML = ""; svcTableBody.innerHTML = "";
  const filteredSvcs = svcList.filter(s => {
    if (svcFilter === "custom" && !s.is_custom) return false;
    if (svcFilter === "active" && s.active_state !== "active") return false;
    if (svcFilter === "inactive" && s.active_state === "active") return false;
    if (svcQuery && !(s.unit + " " + s.name + " " + s.desc).toLowerCase().includes(svcQuery)) return false;
    return true;
  });
  filteredSvcs.sort((a, b) => {
    if (svcSort === "ram") return (b.memory_gib - a.memory_gib) || (b.cpu_pct - a.cpu_pct) || a.unit.localeCompare(b.unit);
    if (svcSort === "cpu") return (b.cpu_pct - a.cpu_pct) || (b.memory_gib - a.memory_gib) || a.unit.localeCompare(b.unit);
    if (svcSort === "active") return ((a.active_state === "active" ? 0 : 1) - (b.active_state === "active" ? 0 : 1)) || (b.memory_gib - a.memory_gib);
    return a.unit.localeCompare(b.unit);
  });

  if (svcView === "table") {
    svcGrid.style.display = "none"; svcTableWrap.style.display = "block";
    svcTableBody.innerHTML = filteredSvcs.map(s => {
      const isUp = s.active_state === "active", openHref = s.open_url ? s.open_url.replace("{host}", selectedNodeHost) : "";
      return `<tr><td><strong>${s.name}</strong></td><td style="color:${isUp ? "#56d364" : "#8b9cb0"};">[${(s.active_state || "off").toUpperCase()}] ${s.sub_state}</td><td>${s.main_pid || "-"}</td><td>${s.memory_gib || 0} GiB</td><td>${s.cpu_pct || 0}%</td><td style="color:#8b9cb0;max-width:240px;">${s.desc}</td><td><div class="btn-row">${isUp && openHref ? `<a class="btn open" href="${openHref}" target="_blank" rel="noopener">${t("open")}</a>` : ""}<button class="primary" ${isUp ? "disabled" : ""} onclick="svcAction('${s.unit}','start')">${t("start")}</button><button class="warn" onclick="svcAction('${s.unit}','restart')">${t("restart")}</button>${!s.protected_stop ? `<button class="danger" ${!isUp ? "disabled" : ""} onclick="svcAction('${s.unit}','stop')">${t("stop")}</button>` : ""}</div></td></tr>`;
    }).join("");
  } else {
    svcGrid.style.display = "grid"; svcTableWrap.style.display = "none";
    svcGrid.innerHTML = filteredSvcs.map(s => {
      const isUp = s.active_state === "active", openHref = s.open_url ? s.open_url.replace("{host}", selectedNodeHost) : "";
      return `<div class="demo-card ${isUp ? "svc-active" : ""}"><div class="thumb">${THUMBS[s.thumb] || THUMBS.systemd}<span class="thumb-tag">${s.port ? "SYSTEMD :" + s.port : "SYSTEMD"}</span><span class="thumb-state ${isUp ? "ready" : ""}">${isUp ? "[ACTIVE]" : "[OFF]"}</span></div><div class="demo-body"><div><h2>${s.name}</h2><p>${s.desc}</p></div><div class="svc-telem">PID: ${s.main_pid || "-"} | RAM: ${s.memory_gib || 0} GiB | CPU: ${s.cpu_pct || 0}%</div><div class="demo-footer"><span class="port-lbl">${s.unit}</span><div class="btn-row">${isUp && openHref ? `<a class="btn open" href="${openHref}" target="_blank" rel="noopener">${t("open")}</a>` : ""}<button class="primary" ${isUp ? "disabled" : ""} onclick="svcAction('${s.unit}','start')">${t("start")}</button><button class="warn" onclick="svcAction('${s.unit}','restart')">${t("restart")}</button>${!s.protected_stop ? `<button class="danger" ${!isUp ? "disabled" : ""} onclick="svcAction('${s.unit}','stop')">${t("stop")}</button>` : ""}</div></div></div></div>`;
    }).join("");
  }

  // Demos
  const tokData = data.tokens || { total: {}, by_demo: {}, rates: {} };
  const demoNames = {};
  for (const [k, d] of Object.entries(data.demos || {})) demoNames[k] = d.name;
  renderTokenBanner("node-token-panel", tokData, t("nodeTokTitle"), "resetNodeTokens", demoNames);

  const grid = document.getElementById("demo-grid");
  grid.innerHTML = "";
  const demosMap = data.demos || {};
  const hasUnifiedFury = Boolean(demosMap["fury-collective"] && demosMap["fury-arena"]);
  for (const [key, d] of Object.entries(demosMap)) {
    if (hasUnifiedFury && key === "fury-arena") continue;
    if (hasUnifiedFury && key === "fury-collective") {
      const isFuryActive = active.key === "fury-collective" || active.key === "fury-arena";
      if (isFuryActive) furyUnifiedMode = active.key;
      const modeKey = (furyUnifiedMode === "fury-arena") ? "fury-arena" : "fury-collective";
      const modeDemo = demosMap[modeKey];
      const isReady = isFuryActive && active.status === "ready";
      const badgeCls = isReady ? "ready" : (isFuryActive ? "starting" : "");
      const readyTag = active.key === "fury-arena" ? "[READY - ARENA 24x]" : "[READY - ALL-IN-ONE]";
      const badgeTxt = !modeDemo.installed ? "[NOT INSTALLED]" : (isReady ? readyTag : (isFuryActive ? `[STARTING ${active.uptime_s}s]` : "[OFF]"));
      const desc = currentLang === "en" ? (modeDemo.desc_en || modeDemo.desc_es) : (modeDemo.desc_es || modeDemo.desc_en);
      const tCol = (tokData.by_demo && tokData.by_demo["fury-collective"]) || { total_tokens: 0, input_tokens: 0, output_tokens: 0, eur_saved: 0 };
      const tArena = (tokData.by_demo && tokData.by_demo["fury-arena"]) || { total_tokens: 0, input_tokens: 0, output_tokens: 0, eur_saved: 0 };
      const sumTok = (tCol.total_tokens || 0) + (tArena.total_tokens || 0);
      const sumIn = (tCol.input_tokens || 0) + (tArena.input_tokens || 0);
      const sumOut = (tCol.output_tokens || 0) + (tArena.output_tokens || 0);
      const sumEur = (tCol.eur_saved || 0) + (tArena.eur_saved || 0);
      const baseHostUrl = "http://" + selectedNodeHost + ":" + modeDemo.port;
      const actionBtns = isReady
        ? `<a class="btn ${active.key === "fury-collective" ? "open" : ""}" href="${baseHostUrl}/" target="_blank" rel="noopener">Stage (/)</a><a class="btn ${active.key === "fury-arena" ? "open" : ""}" href="${baseHostUrl}/experience/arena" target="_blank" rel="noopener">Arena QR</a>`
        : "";
      const modeTag = modeKey === "fury-arena" ? "ARENA 24x QR · 115 GiB" : "ALL-IN-ONE V3+ · 235 GiB";
      const colLbl = currentLang === "en" ? "Full Stack" : "Stack Completo";
      const arenaLbl = "Arena 24x QR";
      const card = document.createElement("div");
      card.className = "demo-card" + (isFuryActive ? " active" : "");
      if (!modeDemo.installed) card.style.opacity = "0.62";
      card.innerHTML = `<div class="thumb">${THUMBS[modeKey] || THUMBS["fury-collective"] || ""}<span class="thumb-tag">${modeTag}</span><span class="thumb-state ${badgeCls}">${badgeTxt}</span></div><div class="demo-body"><div><h2>Fury Collective & Arena</h2><p>${desc}</p><div class="mode-switch"><button type="button" class="mode-pill ${modeKey === "fury-collective" ? "selected" : ""}" onclick="selectFuryUnifiedMode('fury-collective')"><span>${colLbl}</span><small>5 seqs · 235 GiB</small></button><button type="button" class="mode-pill arena ${modeKey === "fury-arena" ? "selected" : ""}" onclick="selectFuryUnifiedMode('fury-arena')"><span>${arenaLbl}</span><small>24 seqs · 115 GiB</small></button></div><div class="demo-tok-bar"><span>${t("tokensLbl")}: <strong>${sumTok.toLocaleString()}</strong> (${fmtTokens(sumIn)}/${fmtTokens(sumOut)})</span><span class="eur-tag">${fmtEur(sumEur)}</span></div></div><div class="demo-footer"><span class="port-lbl">:${modeDemo.port} ${!modeDemo.installed ? "| N/A" : ""}</span><div class="btn-row">${actionBtns}<label class="switch"><input type="checkbox" ${isFuryActive ? "checked" : ""} ${!modeDemo.installed ? "disabled" : ""} onchange="toggleDemo('${modeKey}', this.checked)"><span class="slider"></span></label></div></div></div>`;
      grid.appendChild(card);
      continue;
    }
    const isActive = active.key === key || (key === "cern-lhc" && Boolean(d.port_listening));
    const isReady = (active.key === key && active.status === "ready") || (key === "cern-lhc" && Boolean(d.port_listening));
    const badgeCls = isReady ? "ready" : (isActive ? "starting" : "");
    const readyTag = key === "nano-collective" && d.nano_mode ? (d.nano_mode === "presenter" ? "[READY - STAGE]" : "[READY - FREE]") : "[READY]";
    const badgeTxt = !d.installed ? "[NOT INSTALLED]" : (isReady ? readyTag : (isActive ? `[STARTING ${active.uptime_s}s]` : "[OFF]"));
    const desc = currentLang === "en" ? (d.desc_en || d.desc_es) : (d.desc_es || d.desc_en);
    const url = "http://" + selectedNodeHost + ":" + d.port + d.route;
    const actionBtns = isReady
      ? (key === "nano-collective"
          ? `<button class="${d.nano_mode === "free" ? "open" : ""}" onclick="openNanoMode('free')">${t("openFree")}</button><button class="${d.nano_mode === "presenter" ? "open" : "primary"}" onclick="openNanoMode('presenter')">${t("openStage")}</button>`
          : (key === "cern-lhc"
              ? `<a class="btn open" href="${url}" target="_blank" rel="noopener">Live Trigger</a><a class="btn primary" href="http://${selectedNodeHost}:${d.port}/tracking.html" target="_blank" rel="noopener">3D TrackML</a>`
              : `<a class="btn open" href="${url}" target="_blank" rel="noopener">${t("open")}</a>`))
      : "";
    const dTok = (tokData.by_demo && (tokData.by_demo[key] || (key === "cern-lhc" ? tokData.by_demo["ollama-cern"] : null))) || { total_tokens: 0, input_tokens: 0, output_tokens: 0, eur_saved: 0 };
    const card = document.createElement("div");
    card.className = "demo-card" + (isActive ? " active" : "");
    if (!d.installed) card.style.opacity = "0.62";
    card.innerHTML = `<div class="thumb">${THUMBS[key] || ""}<span class="thumb-tag">${d.tag}</span><span class="thumb-state ${badgeCls}">${badgeTxt}</span></div><div class="demo-body"><div><h2>${d.name}</h2><p>${desc}</p><div class="demo-tok-bar"><span>${t("tokensLbl")}: <strong>${(dTok.total_tokens || 0).toLocaleString()}</strong> (${fmtTokens(dTok.input_tokens || 0)}/${fmtTokens(dTok.output_tokens || 0)})</span><span class="eur-tag">${fmtEur(dTok.eur_saved || 0)}</span></div></div><div class="demo-footer"><span class="port-lbl">:${d.port} ${!d.installed ? "| N/A" : ""}</span><div class="btn-row">${actionBtns}<label class="switch"><input type="checkbox" ${isActive ? "checked" : ""} ${!d.installed ? "disabled" : ""} onchange="toggleDemo('${key}', this.checked)"><span class="slider"></span></label></div></div></div>`;
    grid.appendChild(card);
  }

  const termState = active.key
    ? `[${active.status.toUpperCase()}] ${active.key} (${active.uptime_s}s)`
    : (data.install_active ? `[INSTALLING] ${data.install_summary || "setup"}` : "[IDLE]");
  document.getElementById("demo-term-state").textContent = termState;
  for (const id of ["log-box", "demo-term-box"]) {
    const el = document.getElementById(id), wasAtBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 30;
    el.textContent = data.log || t("noLogs");
    if (wasAtBottom) el.scrollTop = el.scrollHeight;
  }
}

async function refresh() {
  if (busy) return;
  try {
    if (selectedNodeId && selectedNodeId !== "local" && !fleetNodesById[selectedNodeId]) {
      const fleet = await api("GET", "/api/fleet");
      if (!fleet || !fleet.nodes) return;
      currentRole = fleet.role || currentRole;
      fleet.nodes.forEach(node => { fleetNodesById[node.id] = node; });
      const selected = fleetNodesById[selectedNodeId];
      if (!selected) { showFleet(); return; }
      selectedNodeHost = selected.host === "127.0.0.1" ? window.location.hostname : selected.host;
    }
    if (!selectedNodeId) {
      const f = await api("GET", "/api/fleet");
      if (!f || !f.nodes) return;
      lastFleetData = f;
      renderFleet(f);
      return;
    }
    const data = await api("GET", `/api/node/${selectedNodeId}/status`);
    if (data && data.system) {
      if (selectedNodeId === "local") currentRole = data.role || currentRole;
      lastNodeData = data;
      renderNodeData(data);
    }
  } catch (e) { console.error(e); }
}

setLang(currentLang);
setTab(currentTab);
setSvcFilter(svcFilter);
setSvcSort(svcSort);
setSvcView(svcView);
refresh();
setInterval(refresh, 2500);
</script>
</body>
</html>
"""


LIVE_WALLBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HP ZGX Fleet · Live Activity &amp; ROI Wallboard</title>
<style>
  :root {
    --bg: #05080d; --surface: #0b121c; --surface-2: #0f1927; --border: #1b283a;
    --text: #e6edf3; --muted: #8b9bb0; --hp-blue: #0096d6; --nv-green: #76b900;
    --ok: #3fb950; --warn: #d29922; --err: #f85149; --purple: #a855f7;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: radial-gradient(circle at top right, #0b1929 0%, var(--bg) 55%); color: var(--text); }
  .wrap { max-width: 1640px; margin: 0 auto; padding: 18px 22px 28px; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap; border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 18px; }
  .brand { display: flex; align-items: center; gap: 12px; }
  .badge { background: linear-gradient(135deg, var(--hp-blue), #005b8e); color: #fff; font-weight: 800; font-size: 13px; padding: 7px 12px; border-radius: 7px; letter-spacing: 0.05em; }
  h1 { margin: 0; font-size: 20px; font-weight: 700; }
  .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .pill { font-size: 11px; font-weight: 700; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); color: #56d364; border-color: rgba(63,185,80,0.45); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  select, a.btn, button.btn-reset { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 7px; padding: 6px 11px; font-size: 12px; font-weight: 600; text-decoration: none; cursor: pointer; }
  a.btn:hover, select:hover { border-color: var(--hp-blue); }
  button.btn-reset { border-color: rgba(248,81,73,0.45); color: #ffa198; }
  button.btn-reset:hover { border-color: var(--err); background: rgba(248,81,73,0.12); }

  .kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(220px, 1fr)); gap: 14px; margin-bottom: 16px; }
  @media (max-width: 1100px) { .kpi-grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 640px) { .kpi-grid { grid-template-columns: 1fr; } }
  .kpi { background: linear-gradient(180deg, var(--surface-2) 0%, var(--surface) 100%); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; position: relative; overflow: hidden; }
  .kpi.accent-blue { border-top: 3px solid var(--hp-blue); }
  .kpi.accent-green { border-top: 3px solid var(--nv-green); }
  .kpi.accent-purple { border-top: 3px solid var(--purple); }
  .kpi.accent-cyan { border-top: 3px solid #38bdf8; }
  .kpi-lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); font-weight: 700; }
  .kpi-val { font-size: 32px; font-weight: 800; margin: 8px 0 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: -0.02em; }
  .kpi-sub { font-size: 12px; color: #b7c5d6; display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .kpi-tag { font-size: 11px; font-weight: 700; color: #76b900; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }
  @media (max-width: 980px) { .charts-row { grid-template-columns: 1fr; } }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; gap: 8px; }
  .panel-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 700; }
  .panel-live { font-size: 14px; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #cde4f7; }
  svg.chart { width: 100%; height: 130px; display: block; background: #070b11; border: 1px solid #182334; border-radius: 8px; }

  .main-row { display: grid; grid-template-columns: 1.5fr 1fr; gap: 14px; }
  @media (max-width: 1100px) { .main-row { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th { text-align: left; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); padding: 8px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 9px 8px; border-bottom: 1px solid #131d2b; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  td.name-col { font-family: inherit; font-weight: 600; }
  .st-badge { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px; border: 1px solid var(--border); }
  .st-on { color: #56d364; border-color: rgba(63,185,80,0.45); background: rgba(63,185,80,0.08); }
  .st-off { color: var(--err); border-color: rgba(248,81,73,0.45); background: rgba(248,81,73,0.08); }
  .bar-bg { height: 6px; background: #141f2e; border-radius: 99px; overflow: hidden; margin-top: 4px; }
  .bar-fill { height: 100%; background: linear-gradient(90deg, var(--hp-blue), var(--nv-green)); }
  .demo-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #131d2b; gap: 10px; font-size: 12.5px; }
  .demo-row:last-child { border-bottom: 0; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="badge">HP ZGX LIVE</div>
      <div>
        <h1>Parque HW &amp; Demos · Telemetria y ROI en Tiempo Real</h1>
        <div class="sub" id="wb-sub">Conectando con la flota...</div>
      </div>
    </div>
    <div class="controls">
      <label style="font-size:11px;color:var(--muted);">Ponderacion anual:
        <select id="annual-horizon" onchange="renderLast()">
          <option value="2000">Jornada laboral (8h x 250d = 2.000 h/ano)</option>
          <option value="3600">Uso extendido (12h x 300d = 3.600 h/ano)</option>
          <option value="8760" selected>Operacion 24/7 (24h x 365d = 8.760 h/ano)</option>
        </select>
      </label>
      <span class="pill" id="live-clock">[LIVE · 2s]</span>
      <button class="btn-reset" onclick="resetAllFleetTokens()">Poner a cero (Flota)</button>
      <a class="btn" href="/">Volver al Portal</a>
    </div>
  </header>

  <section class="kpi-grid">
    <div class="kpi accent-blue">
      <div class="kpi-lbl">Consumo de Tokens (Flota)</div>
      <div class="kpi-val" id="kpi-tokens">0</div>
      <div class="kpi-sub">
        <span id="kpi-tok-io">In: 0 | Out: 0</span>
        <span class="kpi-tag" id="kpi-tok-rate">0.0 tok/s</span>
      </div>
    </div>
    <div class="kpi accent-green">
      <div class="kpi-lbl">Equivalente API Cloud (EUR)</div>
      <div class="kpi-val" style="color:#76b900;" id="kpi-eur">EUR 0.00</div>
      <div class="kpi-sub">
        <span id="kpi-eur-ref">Ref. In/Out API</span>
        <span class="kpi-tag" id="kpi-eur-rate">EUR 0.00/h</span>
      </div>
    </div>
    <div class="kpi accent-purple">
      <div class="kpi-lbl">Proyeccion Anual Ponderada (1 Ano)</div>
      <div class="kpi-val" style="color:#d8b4fe;" id="kpi-annual-eur">EUR 0</div>
      <div class="kpi-sub">
        <span id="kpi-annual-tok">0 M tok/ano</span>
        <span class="kpi-tag" id="kpi-annual-mode">[BASE]</span>
      </div>
    </div>
    <div class="kpi accent-cyan">
      <div class="kpi-lbl">Potencia de Procesado &amp; Concurrencia</div>
      <div class="kpi-val" style="color:#38bdf8;" id="kpi-power">0 W</div>
      <div class="kpi-sub">
        <span id="kpi-gpu-ram">GPU: 0% | RAM: 0 GiB</span>
        <span class="kpi-tag" id="kpi-users">0 usuarios activos</span>
      </div>
    </div>
  </section>

  <section class="charts-row">
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Velocidad de Procesado (Tokens / seg)</span>
        <span class="panel-live" id="lbl-chart-tok">0.0 tok/s</span>
      </div>
      <svg class="chart" id="chart-tok" viewBox="0 0 600 130" preserveAspectRatio="none"></svg>
    </div>
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Potencia GPU Flota (W) &amp; Carga GPU (%)</span>
        <span class="panel-live" id="lbl-chart-pwr">0 W | 0%</span>
      </div>
      <svg class="chart" id="chart-pwr" viewBox="0 0 600 130" preserveAspectRatio="none"></svg>
    </div>
  </section>

  <section class="main-row">
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Parque de Hardware en Tiempo Real</span>
        <span class="panel-live" id="lbl-hw-count">0/0 online</span>
      </div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Unidad HW</th>
              <th>Estado</th>
              <th>Demo Activa</th>
              <th>Usuarios</th>
              <th>GPU / Temp / W</th>
              <th>RAM</th>
              <th>Tokens / EUR</th>
            </tr>
          </thead>
          <tbody id="hw-tbody"></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head">
        <span class="panel-title">Actividad por Demo (Tokens &amp; EUR)</span>
        <span class="panel-live" id="lbl-demo-count">0 demos</span>
      </div>
      <div id="demo-list"></div>
    </div>
  </section>

  <section class="panel" style="margin-top:14px;">
    <div class="panel-head">
      <span class="panel-title">Consola de Procesamientos en Tiempo Real (Flota)</span>
      <span class="panel-live" id="lbl-console-count">0 eventos</span>
    </div>
    <pre id="live-console" style="margin:0;background:#05080d;border:1px solid #182334;border-radius:8px;padding:10px 12px;height:185px;overflow-y:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;line-height:1.45;color:#b7c5d6;">[INFO] Esperando actividad en tiempo real de la flota...</pre>
  </section>
</div>

<script>
const tokHist = [];
const pwrHist = [];
const MAX_PTS = 60;
let prevSample = null;
let emaTokPerSec = 0;
let emaEurPerSec = 0;
let lastObservedTokPerSec = 0;
let lastObservedEurPerSec = 0;
let lastFleetPayload = null;
const consoleLines = [];
const seenEventKeys = new Set();
const prevNodeState = {};

function appendConsoleLine(tsStr, tag, nodeName, msg) {
  const line = `[${tsStr}] [${tag}] [${nodeName}] ${msg}`;
  consoleLines.push(line);
  if (consoleLines.length > 120) consoleLines.shift();
  const el = document.getElementById("live-console");
  if (el) {
    const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 28;
    el.textContent = consoleLines.join("\\n");
    if (atBottom) el.scrollTop = el.scrollHeight;
  }
  const cnt = document.getElementById("lbl-console-count");
  if (cnt) cnt.textContent = `${consoleLines.length} eventos`;
}

function fmtNum(n) { return Math.round(n || 0).toLocaleString(); }
function fmtEur(v) {
  const n = Number(v || 0);
  if (n >= 1000) return "EUR " + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (n >= 10) return "EUR " + n.toFixed(2);
  return "EUR " + n.toFixed(4);
}
function fmtCompactTok(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " k";
  return String(Math.round(n));
}

function drawSpark(svgId, values, color, maxFloor) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const w = 600, h = 130, pad = 8;
  const arr = values.length ? values : [0];
  const maxVal = Math.max(maxFloor, ...arr) * 1.12;
  const pts = arr.map((v, i) => {
    const x = arr.length === 1 ? w : (i / (arr.length - 1)) * w;
    const y = h - pad - (Math.min(v, maxVal) / maxVal) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const area = `0,${h} ` + pts.join(" ") + ` ${w},${h}`;
  svg.innerHTML = `<polygon points="${area}" fill="${color}" fill-opacity="0.14"/><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2.5"/>`;
}

function renderWallboard(data) {
  lastFleetPayload = data;
  const nodes = data.nodes || [];
  const tok = data.fleet_tokens || data.tokens || {};
  const tot = tok.total || { input_tokens: 0, output_tokens: 0, total_tokens: 0, eur_saved: 0 };
  const now = Date.now() / 1000;

  if (prevSample) {
    const dt = Math.max(0.5, now - prevSample.ts);
    const dTok = Math.max(0, (tot.total_tokens || 0) - prevSample.tokens);
    const dEur = Math.max(0, (tot.eur_saved || 0) - prevSample.eur);
    const instTok = dTok / dt;
    const instEur = dEur / dt;
    emaTokPerSec = emaTokPerSec * 0.55 + instTok * 0.45;
    emaEurPerSec = emaEurPerSec * 0.55 + instEur * 0.45;
    if (instTok > 0.05) {
      lastObservedTokPerSec = emaTokPerSec;
      lastObservedEurPerSec = emaEurPerSec;
    }
  }
  prevSample = { ts: now, tokens: tot.total_tokens || 0, eur: tot.eur_saved || 0 };

  let onlineCount = 0;
  let totalWatts = 0;
  let gpuSum = 0;
  let gpuCount = 0;
  let ramUsed = 0;
  let ramTotal = 0;
  let totalUsers = 0;
  let totalConns = 0;
  let activeDemoCount = 0;
  let totalUptimeSec = 0;
  let reportedTokPerSec = 0;

  const tbody = document.getElementById("hw-tbody");
  tbody.innerHTML = "";
  for (const n of nodes) {
    if (n.online) onlineCount++;
    const gpu = n.gpu || {};
    const ram = n.ram || {};
    const act = n.active || {};
    const cu = n.concurrent_users || { unique_clients: 0, connections: 0 };
    const nTok = (n.tokens && n.tokens.total) || { total_tokens: 0, eur_saved: 0 };
    if (n.online) {
      if (typeof gpu.power_w === "number") totalWatts += gpu.power_w;
      if (typeof gpu.util_pct === "number") { gpuSum += gpu.util_pct; gpuCount++; }
      ramUsed += Number(ram.used_gib || 0);
      ramTotal += Number(ram.total_gib || 0);
      totalUsers += Number(cu.unique_clients || 0);
      totalConns += Number(cu.connections || 0);
      reportedTokPerSec += Number((nTok && nTok.tok_per_s) || 0);
      if (act.key) {
        activeDemoCount++;
        totalUptimeSec += Number(act.uptime_s || 0);
      }
    }
    const gUtil = typeof gpu.util_pct === "number" ? `${gpu.util_pct.toFixed(0)}%` : "N/D";
    const gTemp = typeof gpu.temp_c === "number" ? `${gpu.temp_c.toFixed(0)}C` : "-";
    const gPwr = typeof gpu.power_w === "number" ? `${gpu.power_w.toFixed(0)}W` : "-";
    const demoStr = act.key ? `${act.key} (${act.status || "run"})` : "[IDLE]";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="name-col">${n.alias || n.host}<div style="font-size:11px;color:var(--muted);">${n.hw_model || "ZGX"} · ${n.host}</div></td>
      <td><span class="st-badge ${n.online ? "st-on" : "st-off"}">${n.online ? "[ONLINE]" : "[OFFLINE]"}</span></td>
      <td>${demoStr}</td>
      <td>${cu.unique_clients || 0} (${cu.connections || 0} conn)</td>
      <td>${gUtil} · ${gTemp} · ${gPwr}</td>
      <td>${Number(ram.used_gib || 0).toFixed(1)} / ${Number(ram.total_gib || 0).toFixed(0)} GiB</td>
      <td>${fmtCompactTok(nTok.total_tokens || 0)}<div style="font-size:11px;color:#76b900;">${fmtEur(nTok.eur_saved || 0)}</div></td>
    `;
    tbody.appendChild(tr);
  }

  const avgGpu = gpuCount ? (gpuSum / gpuCount) : 0;
  const displayTokPerSec = Math.max(emaTokPerSec, reportedTokPerSec);
  const horizonHours = Number(document.getElementById("annual-horizon").value || 8760);
  const baseHours = Math.max(1, totalUptimeSec / 3600, 1);
  const liveMode = displayTokPerSec >= 0.1;
  const rateTokSec = liveMode ? displayTokPerSec : (lastObservedTokPerSec > 0.1 ? lastObservedTokPerSec : (tot.total_tokens || 0) / (baseHours * 3600));
  const rateEurSec = (emaEurPerSec > 0 && emaTokPerSec >= 0.1) ? emaEurPerSec : (liveMode ? (rateTokSec * 6.0 / 1e6) : (lastObservedEurPerSec > 0 ? lastObservedEurPerSec : (tot.eur_saved || 0) / (baseHours * 3600)));

  const annualTok = rateTokSec * 3600 * horizonHours;
  const annualEur = rateEurSec * 3600 * horizonHours;

  document.getElementById("kpi-tokens").textContent = fmtNum(tot.total_tokens || 0);
  document.getElementById("kpi-tok-io").textContent = `In: ${fmtCompactTok(tot.input_tokens || 0)} | Out: ${fmtCompactTok(tot.output_tokens || 0)}`;
  document.getElementById("kpi-tok-rate").textContent = `${displayTokPerSec.toFixed(1)} tok/s`;

  document.getElementById("kpi-eur").textContent = fmtEur(tot.eur_saved || 0);
  const rates = tok.rates || {};
  document.getElementById("kpi-eur-ref").textContent = `Ref In ${Number(rates.in_per_1m || 2.76).toFixed(2)} / Out ${Number(rates.out_per_1m || 13.80).toFixed(2)} EUR/1M`;
  document.getElementById("kpi-eur-rate").textContent = `${fmtEur(rateEurSec * 3600)}/h`;

  document.getElementById("kpi-annual-eur").textContent = fmtEur(annualEur);
  document.getElementById("kpi-annual-tok").textContent = `${fmtCompactTok(annualTok)} tok/ano (${horizonHours}h)`;
  document.getElementById("kpi-annual-mode").textContent = liveMode ? "[RITMO EN VIVO]" : "[PONDERADO]";

  document.getElementById("kpi-power").textContent = `${totalWatts.toFixed(0)} W`;
  document.getElementById("kpi-gpu-ram").textContent = `GPU: ${avgGpu.toFixed(0)}% | RAM: ${ramUsed.toFixed(0)}/${ramTotal.toFixed(0)} GiB`;
  document.getElementById("kpi-users").textContent = `${totalUsers} usuarios (${totalConns} conn) · ${activeDemoCount} demos`;

  document.getElementById("lbl-hw-count").textContent = `${onlineCount}/${nodes.length} online`;
  document.getElementById("wb-sub").textContent = `Flota activa: ${onlineCount}/${nodes.length} unidades · ${activeDemoCount} demos ejecutandose`;
  document.getElementById("live-clock").textContent = `[LIVE · ${new Date().toLocaleTimeString()}]`;

  tokHist.push(displayTokPerSec);
  if (tokHist.length > MAX_PTS) tokHist.shift();
  pwrHist.push(totalWatts);
  if (pwrHist.length > MAX_PTS) pwrHist.shift();
  document.getElementById("lbl-chart-tok").textContent = `${displayTokPerSec.toFixed(1)} tok/s`;
  document.getElementById("lbl-chart-pwr").textContent = `${totalWatts.toFixed(0)} W | ${avgGpu.toFixed(0)}% GPU`;
  drawSpark("chart-tok", tokHist, "#38bdf8", 10);
  drawSpark("chart-pwr", pwrHist, "#76b900", 50);

  const byDemo = Object.entries(tok.by_demo || {}).sort((a, b) => (b[1].total_tokens || 0) - (a[1].total_tokens || 0));
  const maxDemoTok = Math.max(1, ...byDemo.map(([, v]) => v.total_tokens || 0));
  document.getElementById("lbl-demo-count").textContent = `${byDemo.length} fuentes`;
  const demoList = document.getElementById("demo-list");
  for (const n of nodes) {
    const nName = n.alias || n.host;
    for (const ev of (n.recent_events || [])) {
      const key = `${nName}|${ev.ts}|${ev.kind}|${ev.source}|${ev.message}`;
      if (!seenEventKeys.has(key)) {
        seenEventKeys.add(key);
        appendConsoleLine(ev.time || new Date().toLocaleTimeString(), ev.kind || "INFO", `${nName}/${ev.source}`, ev.message);
      }
    }
    const actKey = (n.active && n.active.key) || "idle";
    const actSt = (n.active && n.active.status) || "idle";
    const cu = n.concurrent_users || { unique_clients: 0, connections: 0 };
    const nTok = (n.tokens && n.tokens.total && n.tokens.total.total_tokens) || 0;
    const gUtil = (n.gpu && typeof n.gpu.util_pct === "number") ? n.gpu.util_pct : 0;
    const gPwr = (n.gpu && typeof n.gpu.power_w === "number") ? n.gpu.power_w : 0;
    const prev = prevNodeState[nName];
    const nowStr = new Date().toLocaleTimeString();
    if (!prev) {
      if (n.online) {
        appendConsoleLine(nowStr, "SYNC", nName, `Online · ${n.hw_model || "ZGX"} · demo=${actKey} (${actSt}) · GPU ${gUtil}% ${gPwr}W`);
      }
    } else {
      if (prev.actKey !== actKey || prev.actSt !== actSt) {
        appendConsoleLine(nowStr, "DEMO", nName, `Estado cambiado: ${prev.actKey}(${prev.actSt}) -> ${actKey}(${actSt})`);
      }
      if (nTok > prev.tokens) {
        appendConsoleLine(nowStr, "TOKENS", nName, `+${nTok - prev.tokens} tokens procesados (total ${nTok.toLocaleString()})`);
      }
      if (cu.connections !== prev.conns) {
        appendConsoleLine(nowStr, "NET", nName, `Conexiones concurrentes: ${cu.unique_clients} usuarios / ${cu.connections} conn TCP`);
      }
      if (Math.abs(gUtil - prev.gUtil) >= 10 || Math.abs(gPwr - prev.gPwr) >= 20) {
        appendConsoleLine(nowStr, "GPU", nName, `Carga GPU ${gUtil.toFixed(0)}% · Potencia ${gPwr.toFixed(0)} W`);
      }
    }
    prevNodeState[nName] = { actKey, actSt, tokens: nTok, conns: cu.connections || 0, gUtil, gPwr };
  }
  demoList.innerHTML = "";
  for (const [dk, dv] of byDemo) {
    const pct = Math.round(((dv.total_tokens || 0) / maxDemoTok) * 100);
    const row = document.createElement("div");
    row.className = "demo-row";
    row.innerHTML = `
      <div style="flex:1;">
        <div style="display:flex;justify-content:space-between;">
          <strong>${dk}</strong>
          <span style="font-family:monospace;">${fmtNum(dv.total_tokens || 0)} tok · <span style="color:#76b900;">${fmtEur(dv.eur_saved || 0)}</span></span>
        </div>
        <div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div>
      </div>
    `;
    demoList.appendChild(row);
  }
}

function renderLast() {
  if (lastFleetPayload) renderWallboard(lastFleetPayload);
}

async function resetAllFleetTokens() {
  if (!confirm("Poner a cero los contadores de tokens y euros de TODA la flota?")) return;
  try {
    const r = await fetch("/api/fleet/tokens/reset", { method: "POST" });
    if (r.status === 401) { window.location.href = "/login"; return; }
    const res = await r.json();
    tokHist.length = 0;
    prevSample = null;
    emaTokPerSec = 0;
    emaEurPerSec = 0;
    lastObservedTokPerSec = 0;
    lastObservedEurPerSec = 0;
    await poll();
    if (res && res.failed && res.failed.length) {
      alert("Contadores reiniciados. Unidades offline no alcanzadas: " + res.failed.join(", "));
    }
  } catch (e) { console.error(e); }
}

async function poll() {
  try {
    const r = await fetch("/api/fleet");
    if (r.status === 401) { window.location.href = "/login"; return; }
    const data = await r.json();
    if (data && data.nodes) renderWallboard(data);
  } catch (e) { console.error(e); }
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _is_authed(self) -> bool:
        cfg = load_config()
        token = self.headers.get("X-ZGX-Token", "")
        if token and hmac.compare_digest(token, cfg["node_token"]):
            return True
        if check_session_cookie(self.headers.get("Cookie", "")):
            return True
        return False

    def _read_json(self) -> dict:
        try:
            ln = int(self.headers.get("Content-Length", "0") or 0)
            if ln <= 0:
                return {}
            return json.loads(self.rfile.read(ln).decode("utf-8"))
        except Exception:
            return {}

    def _send_json(self, code: int, payload: dict, extra_headers: list = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/login":
            self._send_html(LOGIN_HTML)
            return
        if self.path == "/api/node/probe":
            with TELEM_LOCK:
                sys_s = dict(LATEST_SYS)
            gpu_n = sys_s.get("gpu", {}).get("name", "")
            hw = sys_s.get("hw") or detect_hw_profile(gpu_n)
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "hostname": socket.gethostname(),
                "hw_model": hw.get("hw_model", ""),
                "arch": hw.get("arch", ""),
                "gpu": gpu_n,
                **_identity_payload(),
            })
            return
        if self.path == "/api/tokens/report":
            peer_ip = self.client_address[0] if self.client_address else ""
            if peer_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or self._is_authed():
                self._send_json(HTTPStatus.OK, record_token_report(self._read_json()))
            else:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return
        if not self._is_authed():
            if self.path.startswith("/api/"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            else:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        if self.path == "/" or self.path == "/index.html":
            self._send_html(HTML_PAGE)
        elif self.path in ("/live", "/wallboard"):
            self._send_html(LIVE_WALLBOARD_HTML)
        elif self.path == "/api/status":
            self._send_json(HTTPStatus.OK, get_status())
        elif self.path == "/api/role":
            cfg = load_config()
            self._send_json(HTTPStatus.OK, {
                "ok": True,
                "role": cfg["role"],
                "id": cfg.get("node_id", ""),
                "hub_epoch": int(cfg.get("hub_epoch", 0)),
                "hostname": socket.gethostname(),
            })
        elif self.path == "/api/fleet":
            self._send_json(HTTPStatus.OK, get_fleet_status())
        elif self.path == "/api/fleet/enroll/status":
            self._send_json(HTTPStatus.OK, get_enroll_status())
        elif self.path == "/api/fleet/deploy/catalog":
            self._send_json(HTTPStatus.OK, get_deploy_catalog())
        elif self.path == "/api/fleet/deploy/local-catalog":
            self._send_json(HTTPStatus.OK, get_local_deploy_catalog())
        elif self.path.startswith("/api/fleet/deploy/archive/"):
            key = self.path.rsplit("/", 1)[-1]
            try:
                if key not in _deploy_package_sources():
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Opcion no disponible."})
                    return
                archive = _build_repository_tarball(_repository_root())
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                return
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(archive.stat().st_size))
                self.end_headers()
                with archive.open("rb") as file:
                    shutil.copyfileobj(file, self.wfile)
            finally:
                archive.unlink(missing_ok=True)
        elif self.path == "/api/fleet/deploy/status":
            self._send_json(HTTPStatus.OK, get_deploy_status())
        elif self.path.startswith("/api/fleet/deploy/nano-status?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._send_json(HTTPStatus.OK, get_nano_setup_status(
                query.get("node_id", [""])[0], query.get("ssh_user", [""])[0],
                query.get("ssh_port", [""])[0]))
        elif self.path == "/api/fleet/auto/status":
            self._send_json(HTTPStatus.OK, get_auto_status())
        elif self.path == "/api/users":
            self._send_json(HTTPStatus.OK, {"ok": True, "users": list_users()})
        elif self.path.startswith("/api/node/"):
            rest = self.path.split("/api/node/", 1)[1]
            node_id, _, sub = rest.partition("/")
            self._send_json(HTTPStatus.OK, proxy_node_request(node_id, "/" + sub, method="GET"))
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if self.path == "/api/login":
            body = self._read_json()
            u = (body.get("username") or "").strip()
            p = body.get("password") or ""
            if verify_credentials(u, p):
                self._send_json(HTTPStatus.OK, {"ok": True}, [("Set-Cookie", make_session_cookie(u))])
            else:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Usuario o contrasena incorrectos."})
            return
        if self.path == "/api/logout":
            self._send_json(HTTPStatus.OK, {"ok": True}, [("Set-Cookie", "zgx_session=; Path=/; Max-Age=0")])
            return
        if self.path == "/api/tokens/report":
            peer_ip = self.client_address[0] if self.client_address else ""
            if peer_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or self._is_authed():
                self._send_json(HTTPStatus.OK, record_token_report(self._read_json()))
            else:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return
        if not self._is_authed():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

        if self.path == "/api/fleet/scan":
            body = self._read_json()
            self._send_json(
                HTTPStatus.OK,
                scan_local_network(
                    cidr_override=body.get("cidr", ""),
                    ssh_port=body.get("ssh_port", 22),
                    ssh_user=body.get("ssh_user", ""),
                    ssh_pass=body.get("ssh_pass", ""),
                    verify_candidates=bool(body.get("verify_candidates", False)),
                ),
            )
        elif self.path == "/api/monitoring/export":
            self._send_json(HTTPStatus.OK, export_monitoring(self._read_json()))
        elif self.path == "/api/fleet/verify-ssh":
            self._send_json(HTTPStatus.OK, verify_ssh_credentials(self._read_json()))
        elif self.path == "/api/fleet/ssh-default":
            self._send_json(HTTPStatus.OK, save_default_ssh_credentials(self._read_json()))
        elif self.path == "/api/fleet/enroll":
            self._send_json(HTTPStatus.OK, enroll_node(self._read_json()))
        elif self.path == "/api/fleet/deploy":
            self._send_json(HTTPStatus.OK, deploy_to_node(self._read_json()))
        elif self.path == "/api/fleet/auto":
            self._send_json(HTTPStatus.OK, start_auto_deployment(self._read_json()))
        elif self.path == "/api/fleet/auto/cancel":
            _auto_update(message="Cancelacion solicitada.", cancel=True)
            self._send_json(HTTPStatus.OK, {"ok": True})
        elif self.path == "/api/fleet/sync":
            self._send_json(HTTPStatus.OK, apply_fleet_sync(self._read_json()))
        elif self.path == "/api/fleet/announce":
            self._send_json(HTTPStatus.OK, handle_fleet_announce(self._read_json()))
        elif self.path == "/api/fleet/claim-master":
            self._send_json(HTTPStatus.OK, claim_master())
        elif self.path == "/api/fleet/push-update":
            self._send_json(HTTPStatus.OK, push_update_fleet())
        elif self.path == "/api/fleet/reorganize":
            self._send_json(HTTPStatus.OK, reorganize_fleet_action())
        elif self.path == "/api/fleet/start-nano-collective":
            self._send_json(HTTPStatus.OK, start_all_nano_collective_action())
        elif self.path == "/api/node/self-update":
            self._send_json(HTTPStatus.OK, self_update_node(self._read_json()))
        elif self.path == "/api/free-gpu":
            self._send_json(HTTPStatus.OK, free_gpu_action())
        elif self.path == "/api/locate":
            self._send_json(HTTPStatus.OK, locate_node_action())
        elif self.path == "/api/network/mode":
            self._send_json(HTTPStatus.OK, set_network_mode_action(self._read_json()))
        elif self.path == "/api/prune-docker":
            self._send_json(HTTPStatus.OK, prune_docker_action())
        elif self.path.startswith("/api/power/"):
            self._send_json(HTTPStatus.OK, power_action(self.path.split("/api/power/", 1)[1].strip("/")))
        elif self.path.startswith("/api/fleet/remove/"):
            nid = urllib.parse.unquote(self.path.split("/api/fleet/remove/", 1)[1].strip("/"))
            self._send_json(HTTPStatus.OK, remove_fleet_node(nid))
        elif self.path == "/api/users/add":
            b = self._read_json()
            self._send_json(HTTPStatus.OK, add_user(b.get("username", ""), b.get("password", "")))
        elif self.path == "/api/users/password":
            b = self._read_json()
            self._send_json(HTTPStatus.OK, change_user_password(b.get("username", ""), b.get("password", "")))
        elif self.path == "/api/users/remove":
            b = self._read_json()
            self._send_json(HTTPStatus.OK, remove_user(b.get("username", "")))
        elif self.path == "/api/role":
            body = self._read_json()
            self._send_json(HTTPStatus.OK, set_local_role(body.get("role", "")))
        elif self.path.startswith("/api/node/"):
            rest = self.path.split("/api/node/", 1)[1]
            node_id, _, sub = rest.partition("/")
            self._send_json(HTTPStatus.OK, proxy_node_request(node_id, "/" + sub, method="POST", body=self._read_json()))
        elif self.path.startswith("/api/service/"):
            parts = self.path.split("/api/service/", 1)[1].strip("/").split("/")
            if len(parts) == 2:
                self._send_json(HTTPStatus.OK, control_service(urllib.parse.unquote(parts[0]), parts[1]))
            else:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid route"})
        elif self.path.startswith("/api/start/"):
            key = self.path.split("/api/start/", 1)[1].strip("/")
            self._send_json(HTTPStatus.OK, start_demo(key))
        elif self.path == "/api/stop":
            self._send_json(HTTPStatus.OK, stop_demo())
        elif self.path.startswith("/api/nano-mode/"):
            mode = self.path.split("/api/nano-mode/", 1)[1].strip("/")
            self._send_json(HTTPStatus.OK, switch_nano_mode(mode))
        elif self.path == "/api/tokens/reset":
            self._send_json(HTTPStatus.OK, reset_token_ledger())
        elif self.path == "/api/fleet/tokens/reset":
            res = reset_token_ledger()
            fleet_nodes = load_fleet()
            ok_nodes = ["local"]
            failed_nodes = []

            def _reset_remote(n):
                try:
                    _node_http_json(n["host"], n.get("port", 18888), "/api/tokens/reset", n.get("token", ""), method="POST", timeout=2.0)
                    return n.get("alias") or n["host"], True
                except Exception:
                    return n.get("alias") or n["host"], False

            if fleet_nodes:
                with ThreadPoolExecutor(max_workers=min(12, len(fleet_nodes))) as ex:
                    for name, ok_flag in ex.map(_reset_remote, fleet_nodes):
                        (ok_nodes if ok_flag else failed_nodes).append(name)
            res["reset_ok"] = ok_nodes
            res["failed"] = failed_nodes
            self._send_json(HTTPStatus.OK, res)
        elif self.path == "/api/tokens/rates":
            b = self._read_json()
            self._send_json(HTTPStatus.OK, update_token_rates(b.get("input_eur_per_1m", 2.76), b.get("output_eur_per_1m", 13.80)))
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})


def main():
    def handle_exit(signum, frame):
        with STATE_LOCK:
            _stop_current_locked()
        raise SystemExit(0)

    load_config()
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    def _boot_reconcile():
        time.sleep(3)
        try:
            reorganize_fleet_action()
        except Exception as exc:
            print(f"[WARN] Reorganizacion inicial de red: {exc}", flush=True)

    threading.Thread(target=telemetry_loop, daemon=True).start()
    threading.Thread(target=discovery_loop, daemon=True).start()
    threading.Thread(target=_boot_reconcile, daemon=True).start()
    threading.Thread(target=fleet_sync_loop, daemon=True).start()
    threading.Thread(target=fleet_resilience_loop, daemon=True).start()
    threading.Thread(target=fleet_monitoring_loop, daemon=True).start()
    resume_auto_deployment()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"[INFO] ZGX Fleet Console listening on http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

