#!/usr/bin/env python3
"""Unified HP ZGX Nano Fleet Hub & Node Console (single-package deployment)."""

import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import ipaddress
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import socket
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
LISTEN_HOST = os.environ.get("ZGX_LAUNCHER_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("ZGX_LAUNCHER_PORT", "18888"))

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

WATCH_PORTS = [
    (18888, "ZGX Console"),
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
        if os.environ.get("ZGX_ROLE") in ("hub", "node") and cfg["role"] != os.environ.get("ZGX_ROLE"):
            cfg["role"] = os.environ["ZGX_ROLE"]
            changed = True
        if "node_token" not in cfg or not cfg["node_token"]:
            cfg["node_token"] = os.environ.get("ZGX_NODE_TOKEN") or secrets.token_hex(24)
            changed = True
        if os.environ.get("ZGX_NODE_TOKEN") and cfg["node_token"] != os.environ["ZGX_NODE_TOKEN"]:
            cfg["node_token"] = os.environ["ZGX_NODE_TOKEN"]
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
    FLEET_FILE.write_text(json.dumps(nodes, indent=2), encoding="utf-8")


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
    meta = FURY_PROFILES.get(key) or (FURY_DEMO.get(key) if key == "fury" else DEMOS.get(key))
    if not meta:
        return {"ok": False, "key": key, "checks": [], "error": f"Unknown demo: {key}"}

    # 1. File / model prerequisites
    req_files = []
    if _is_fury_key(key):
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
    if _is_fury_key(key):
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
    return {
        "total": {
            "input_tokens": tot_in,
            "output_tokens": tot_out,
            "total_tokens": tot_in + tot_out,
            "eur_saved": _calc_eur(tot_in, tot_out, rates),
            "tok_per_s": _LIVE_TOK_S["val"],
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
    return {
        "role": cfg["role"],
        "active": active,
        "services": services_info,
        "demos": demos_info,
        "log": read_tail(LOG_FILE, 80),
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


def sync_fleet_to_peers() -> None:
    cfg = load_config()
    if cfg.get("role") != "hub":
        return
    nodes = load_fleet()
    local_ip = get_local_ip()
    self_peer = {
        "id": "hub-" + socket.gethostname(),
        "alias": socket.gethostname(),
        "tag": "HUB",
        "host": local_ip,
        "port": LISTEN_PORT,
        "token": cfg["node_token"],
        "role": "hub",
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    full_ring = [self_peer] + nodes
    users_snapshot = cfg.get("users", {})
    for n in nodes:
        if n.get("host") == local_ip:
            continue
        peers_for_target = [p for p in full_ring if p.get("host") != n.get("host")]
        try:
            _node_http_json(
                n["host"],
                n.get("port", 18888),
                "/api/fleet/sync",
                n.get("token", ""),
                method="POST",
                timeout=1.5,
                body={"peers": peers_for_target, "users": users_snapshot},
            )
        except Exception:
            pass


def apply_fleet_sync(payload: dict) -> dict:
    peers = payload.get("peers")
    if isinstance(peers, list):
        local_ip = get_local_ip()
        cleaned = [p for p in peers if isinstance(p, dict) and p.get("host") and p.get("host") != local_ip]
        save_fleet(cleaned)
    users = payload.get("users")
    if isinstance(users, dict) and users:
        cfg = load_config()
        cfg["users"] = users
        save_config(cfg)
    return {"ok": True}


def claim_master() -> dict:
    cfg = load_config()
    cfg["role"] = "hub"
    save_config(cfg)
    nodes = load_fleet()
    for n in nodes:
        if n.get("role") == "hub":
            try:
                _node_http_json(
                    n["host"], n.get("port", 18888), "/api/role", n.get("token", ""),
                    method="POST", timeout=2.0, body={"role": "node"}
                )
            except Exception:
                pass
            n["role"] = "node"
    save_fleet(nodes)
    sync_fleet_to_peers()
    return {"ok": True, "role": "hub"}


def get_fleet_status() -> dict:
    cfg = load_config()
    local_st = get_status()
    local_sys = local_st["system"]
    local_entry = {
        "id": "local",
        "alias": local_sys.get("hostname", "zgx-local"),
        "tag": "HUB + LOCAL",
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
            "ssh_user": n.get("ssh_user", "iosu"),
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
            "tokens": {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "eur_saved": 0.0}, "by_demo": {}},
        }
        try:
            st = _node_http_json(n["host"], n.get("port", 18888), "/api/status", n.get("token", ""), timeout=1.6)
            entry["online"] = True
            entry["agent_installed"] = True
            entry["latency_ms"] = int((time.time() - t0) * 1000)
            entry["role"] = st.get("role", "node")
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
            if isinstance(st.get("tokens"), dict):
                entry["tokens"] = st["tokens"]
        except Exception:
            pass
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


def scan_local_network(cidr_override: str = "") -> dict:
    cidrs = []
    if cidr_override:
        cidrs.append(cidr_override.strip())
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
    enrolled_ips = {n["host"] for n in load_fleet()}

    def check_ip(ip: str):
        ssh_up = port_open(22, host=ip, timeout=0.35)
        agent_up = port_open(18888, host=ip, timeout=0.35)
        if not ssh_up and not agent_up:
            return None
        hostname = ""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        hw_model = ""
        gpu_name = ""
        if agent_up:
            try:
                with urllib.request.urlopen(f"http://{ip}:18888/api/node/probe", timeout=0.45) as resp:
                    pr = json.load(resp)
                hw_model = pr.get("hw_model", "")
                gpu_name = pr.get("gpu", "")
                if not hostname:
                    hostname = pr.get("hostname", "")
            except Exception:
                pass
        return {
            "ip": ip,
            "hostname": hostname,
            "ssh": ssh_up,
            "agent": agent_up,
            "hw_model": hw_model,
            "gpu": gpu_name,
            "enrolled": ip in enrolled_ips,
        }

    found = []
    with ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(check_ip, all_ips):
            if res is not None:
                found.append(res)
    found.sort(key=lambda x: tuple(int(p) for p in x["ip"].split(".")))
    return {"ok": True, "subnet": str(target_net), "hosts": found}


def _pty_ssh_run(host: str, user: str, password: str, remote_cmd: str, stdin_data: bytes = b"", timeout: int = 45, ssh_port: int = 22) -> tuple:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(
            "ssh",
            [
                "ssh",
                "-p", str(ssh_port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=8",
                "-o", "PubkeyAuthentication=no",
                "-o", "NumberOfPasswordPrompts=1",
                f"{user}@{host}",
                remote_cmd,
            ],
        )
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
                    if stdin_data and not stdin_sent:
                        time.sleep(0.25)
                        os.write(fd, stdin_data)
                        stdin_sent = True
            else:
                if pw_sent and stdin_data and not stdin_sent:
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


def _ssh_run(host: str, user: str, password: str, remote_cmd: str, timeout: int = 45, ssh_port: int = 22) -> tuple:
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


def _ssh_upload_file(host: str, user: str, password: str, local_path: Path, remote_path: str, ssh_port: int = 22, timeout: int = 180) -> tuple:
    try:
        import paramiko
    except ImportError:
        raw = local_path.read_bytes()
        if len(raw) > 5 * 1024 * 1024:
            return False, "paramiko requerido para subir paquetes grandes."
        b64 = base64.b64encode(raw).decode("ascii")
        rc, out = _pty_ssh_run(
            host, user, password,
            f"mkdir -p \"$(dirname '{remote_path}')\" && echo '{b64}' | base64 -d > '{remote_path}' && echo UPLOAD_OK",
            timeout=timeout, ssh_port=ssh_port,
        )
        return (rc == 0 and "UPLOAD_OK" in out), out[-300:]

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
    ssh_user = (payload.get("ssh_user") or "iosu").strip()
    ssh_pass = payload.get("ssh_pass") or ""
    explicit_token = (payload.get("node_token") or "").strip()

    if not host:
        return {"ok": False, "error": "Host / IP requerida."}
    existing = next((n for n in load_fleet() if (n.get("host"), int(n.get("port", 18888))) == (host, port)), None)
    if not ssh_pass and not existing and not explicit_token:
        return {"ok": False, "error": "Contrasena SSH requerida para verificar y enrolar un dispositivo nuevo."}

    with ENROLL_LOCK:
        if ENROLL_JOB["running"]:
            return {"ok": False, "busy": True, "error": f"Ya hay un enrolamiento en curso hacia {ENROLL_JOB['host']}."}
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
    return {"ok": True, "started": True}


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


def _deploy_package_sources() -> dict:
    pkgs = {}
    if (FURY_DIR / "launch.sh").exists():
        pkgs["fury-showcase"] = {
            "key": "fury-showcase",
            "name": "HP ZGX Fury Showcase",
            "src_dir": FURY_DIR.resolve(),
            "remote_rel": "hp-zgx-fury-showcase",
            "verify_file": "launch.sh",
            "desc_es": "Codigo showcase Fury (UI i18n, Heimdall, ops y scripts; excluye venv/modelos pesados).",
            "desc_en": "Fury showcase code (i18n UI, Heimdall, ops and scripts; excludes heavy venv/models).",
        }
    if (NANO_COLLECTIVE_DIR / "launch.sh").exists():
        pkgs["nano-collective"] = {
            "key": "nano-collective",
            "name": "ZGX Nano Collective v3.2",
            "src_dir": NANO_COLLECTIVE_DIR.resolve(),
            "remote_rel": "hp-zgx-nano",
            "verify_file": "launch.sh",
            "desc_es": "Codigo Nano Collective v3.2 (UI i18n, Free/Stage y herramientas; excluye venv/modelos).",
            "desc_en": "Nano Collective v3.2 code (i18n UI, Free/Stage and tools; excludes venv/models).",
        }
    if (KIT_DIR / "portable.py").exists():
        pkgs["partner-kit"] = {
            "key": "partner-kit",
            "name": "ZGX Partner Kit",
            "src_dir": KIT_DIR.resolve(),
            "remote_rel": "zgx-partner-kit",
            "verify_file": "portable.py",
            "desc_es": "Scripts ZGX Partner Kit (portable.py, public_entry.py; excluye modelos/media).",
            "desc_en": "ZGX Partner Kit scripts (portable.py, public_entry.py; excludes models/media).",
        }
    return pkgs


def get_deploy_catalog() -> dict:
    pkgs = _deploy_package_sources()
    return {
        "ok": True,
        "agent": True,
        "demos": [
            {
                "key": p["key"],
                "name": p["name"],
                "desc_es": p["desc_es"],
                "desc_en": p["desc_en"],
            }
            for p in pkgs.values()
        ],
    }


def _build_demo_tarball(src_dir: Path, pkg_key: str) -> Path:
    exclude_dirs = {".venv", ".model-venv", ".cache", "models", "logs", "state", "data", "out", "run", "var", "__pycache__", ".git", "node_modules"}
    exclude_exts = (".gguf", ".safetensors", ".onnx", ".bin", ".pt", ".pth", ".ckpt", ".engine", ".tar.gz")
    fd, tmp_name = tempfile.mkstemp(prefix=f"zgx-deploy-{pkg_key}-", suffix=".tar.gz", dir="/tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    with tarfile.open(tmp_path, "w:gz") as tf:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = sorted(d for d in dirs if d not in exclude_dirs and not d.startswith(".pytest"))
            root_p = Path(root)
            for fn in sorted(files):
                if fn.endswith(exclude_exts):
                    continue
                fp = root_p / fn
                if fp.is_symlink() and not fp.exists():
                    continue
                if not fp.is_file():
                    continue
                try:
                    if fp.stat().st_size > 150 * 1024 * 1024:
                        continue
                    arc = str(fp.relative_to(src_dir))
                    tf.add(str(fp), arcname=arc, recursive=False)
                except OSError:
                    continue
    return tmp_path


def _run_deploy_job(node: dict, ssh_user: str, ssh_port: int, ssh_pass: str, deploy_agent: bool, deploy_demos: list) -> None:
    host = node["host"]
    port = int(node.get("port", 18888))
    pkgs = _deploy_package_sources()
    total_steps = 1 + (1 if deploy_agent else 0) + len(deploy_demos)
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
        _deploy_log("[OK] Conexion SSH verificada.")

        if deploy_agent:
            cur_step += 1
            _deploy_log("Inspeccionando configuracion existente del agente remoto...", step=cur_step, phase="Deploy Agent")
            inspect_py = """
import json, pathlib, re
home = pathlib.Path.home()
svc = pathlib.Path("/etc/systemd/system/zgx-launcher.service")
info = {
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
        if not info["role"] and c.get("role") in ("hub", "node"):
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

            _deploy_log(f"Subiendo launcher_web.py a {remote_script}...")
            _ssh_run(host, ssh_user, ssh_pass, f"mkdir -p '{remote_workdir}' '{remote_demos_root}'", timeout=15, ssh_port=ssh_port)
            ok_up, err_up = _ssh_upload_file(host, ssh_user, ssh_pass, Path(__file__), remote_script, ssh_port=ssh_port, timeout=60)
            if not ok_up:
                raise RuntimeError(f"Fallo subiendo launcher_web.py: {err_up}")
            rc_c, out_c = _ssh_run(host, ssh_user, ssh_pass, f"python3 -m py_compile '{remote_script}' && echo PY_OK", timeout=20, ssh_port=ssh_port)
            if rc_c != 0 or "PY_OK" not in out_c:
                raise RuntimeError(f"Fallo validando sintaxis en nodo remoto: {out_c[-300:]}")

            env_lines = [
                f"Environment=HOME=/home/{ssh_user}",
                "Environment=PYTHONUNBUFFERED=1",
                f"Environment=ZGX_DEMOS_ROOT={remote_demos_root}",
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
            pw_b64 = base64.b64encode((ssh_pass + "\n").encode("utf-8")).decode("ascii")
            sys_script = f"""set -e
umask 077
echo '{sudoers_b64}' | base64 -d > /tmp/zgx-launcher.sudoers
echo '{unit_b64}' | base64 -d > /tmp/zgx-launcher.service
PW=$(echo '{pw_b64}' | base64 -d)
printf '%s\\n' "$PW" | sudo -S chown root:root /tmp/zgx-launcher.sudoers /tmp/zgx-launcher.service
printf '%s\\n' "$PW" | sudo -S chmod 440 /tmp/zgx-launcher.sudoers
printf '%s\\n' "$PW" | sudo -S chmod 644 /tmp/zgx-launcher.service
printf '%s\\n' "$PW" | sudo -S visudo -cf /tmp/zgx-launcher.sudoers
printf '%s\\n' "$PW" | sudo -S mv /tmp/zgx-launcher.sudoers /etc/sudoers.d/zgx-launcher
printf '%s\\n' "$PW" | sudo -S mv /tmp/zgx-launcher.service /etc/systemd/system/zgx-launcher.service
printf '%s\\n' "$PW" | sudo -S systemctl daemon-reload
printf '%s\\n' "$PW" | sudo -S systemctl enable --now zgx-launcher.service
printf '%s\\n' "$PW" | sudo -S systemctl restart zgx-launcher.service
echo SYS_OK
"""
            sys_b64 = base64.b64encode(sys_script.encode("utf-8")).decode("ascii")
            _deploy_log("Configurando systemd zgx-launcher.service y reiniciando agente...")
            rc_s, out_s = _ssh_run(host, ssh_user, ssh_pass, f"echo '{sys_b64}' | base64 -d | bash -s", timeout=45, ssh_port=ssh_port)
            if rc_s != 0 or "SYS_OK" not in out_s:
                raise RuntimeError(f"Fallo configurando/reiniciando zgx-launcher.service (code {rc_s}): {out_s[-400:]}")

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
            meta = pkgs.get(pkg_key)
            if not meta:
                raise RuntimeError(f"Paquete de demo no disponible en este Hub: {pkg_key}")
            _deploy_log(f"Empaquetando demo '{meta['name']}' desde {meta['src_dir']}...", step=cur_step, phase=f"Deploy {pkg_key}")
            tar_path = _build_demo_tarball(meta["src_dir"], pkg_key)
            remote_tar = f"/tmp/zgx-deploy-{pkg_key}.tar.gz"
            try:
                size_mb = round(tar_path.stat().st_size / (1024 * 1024), 2)
                _deploy_log(f"Transfiriendo paquete {pkg_key} ({size_mb} MB) hacia {host}...")
                ok_up, err_up = _ssh_upload_file(host, ssh_user, ssh_pass, tar_path, remote_tar, ssh_port=ssh_port, timeout=300)
                if not ok_up:
                    raise RuntimeError(f"Fallo transfiriendo {pkg_key}: {err_up}")
            finally:
                try:
                    tar_path.unlink(missing_ok=True)
                except OSError:
                    pass

            rel = meta["remote_rel"]
            vfile = meta["verify_file"]
            extract_script = f"""set -e
DEST="$HOME/{rel}"
if [ -L "$DEST" ]; then
  DEST="$(readlink -f "$DEST")"
fi
mkdir -p "$DEST"
tar -xzf '{remote_tar}' -C "$DEST"
rm -f '{remote_tar}'
test -e "$DEST/{vfile}"
echo "DEPLOY_DEST=$DEST"
"""
            ex_b64 = base64.b64encode(extract_script.encode("utf-8")).decode("ascii")
            rc_e, out_e = _ssh_run(host, ssh_user, ssh_pass, f"echo '{ex_b64}' | base64 -d | bash -s", timeout=120, ssh_port=ssh_port)
            if rc_e != 0 or "DEPLOY_DEST=" not in out_e:
                raise RuntimeError(f"Fallo extrayendo {pkg_key} en {host}: {out_e[-400:]}")
            dest_line = next((ln.strip() for ln in out_e.splitlines() if ln.strip().startswith("DEPLOY_DEST=")), "")
            _deploy_log(f"[OK] Demo {pkg_key} desplegada ({dest_line}).")

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

    node = next((n for n in load_fleet() if n.get("id") == node_id), None)
    if not node:
        return {"ok": False, "error": "Nodo no encontrado en la flota."}
    ssh_user = (payload.get("ssh_user") or node.get("ssh_user") or "iosu").strip()
    ssh_port = int(payload.get("ssh_port") or node.get("ssh_port") or 22)
    if not ssh_pass:
        return {"ok": False, "error": "Contrasena SSH requerida para desplegar."}
    if not deploy_agent and not deploy_demos:
        return {"ok": False, "error": "Selecciona al menos un componente para desplegar (Agente o una Demo)."}

    pkgs = _deploy_package_sources()
    for dk in deploy_demos:
        if dk not in pkgs:
            return {"ok": False, "error": f"Demo no disponible en este Hub para desplegar: {dk}"}

    with DEPLOY_LOCK:
        if DEPLOY_JOB["running"]:
            return {"ok": False, "busy": True, "error": f"Ya hay un despliegue manual en curso hacia {DEPLOY_JOB['host']}."}
        DEPLOY_JOB["running"] = True
        DEPLOY_JOB["phase"] = "Starting"
        DEPLOY_JOB["step"] = 0
        DEPLOY_JOB["total_steps"] = 1 + (1 if deploy_agent else 0) + len(deploy_demos)
        DEPLOY_JOB["started_at"] = time.time()
        DEPLOY_JOB["ended_at"] = 0.0
        DEPLOY_JOB["node_id"] = node_id
        DEPLOY_JOB["host"] = f"{node['host']}:{ssh_port}"
        DEPLOY_JOB["ok"] = None
        DEPLOY_JOB["error"] = ""
        DEPLOY_JOB["log"] = [f"[{time.strftime('%H:%M:%S')}] Iniciando despliegue manual hacia {node.get('alias', node['host'])} ({node['host']}:{ssh_port})..."]

    threading.Thread(
        target=_run_deploy_job,
        args=(node, ssh_user, ssh_port, ssh_pass, deploy_agent, deploy_demos),
        daemon=True,
    ).start()
    return {"ok": True, "started": True}


def remove_fleet_node(node_id: str) -> dict:
    nodes = [n for n in load_fleet() if n["id"] != node_id]
    save_fleet(nodes)
    sync_fleet_to_peers()
    return {"ok": True}


def set_local_role(new_role: str) -> dict:
    if new_role not in ("hub", "node"):
        return {"ok": False, "error": "Rol invalido"}
    cfg = load_config()
    cfg["role"] = new_role
    save_config(cfg)
    return {"ok": True, "role": new_role}


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


def proxy_node_request(node_id: str, sub_path: str, method: str = "GET") -> dict:
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
    try:
        return _node_http_json(
            node["host"], node.get("port", 18888), "/api" + sub_path, node.get("token", ""), method=method, timeout=120.0
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
      <input id="p" type="password" autocomplete="current-password" required>
      <button type="submit">Entrar</button>
    </form>
  </div>
<script>
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
  .scan-list {
    max-height: 190px; overflow-y: auto; border: 1px solid #1b2738; border-radius: 8px;
    background: #070b10; margin-bottom: 14px;
  }
  .scan-row {
    display: flex; justify-content: space-between; align-items: center; padding: 8px 10px;
    border-bottom: 1px solid #121b28; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    cursor: pointer;
  }
  .scan-row:hover { background: #131e2c; }
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
  .deploy-item input[type="checkbox"] { margin-top: 3px; width: auto; accent-color: var(--hp-blue); }
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
    <div style="display:flex;gap:8px;margin-bottom:10px;align-items:center;flex-wrap:wrap;">
      <input id="scan-cidr" type="text" placeholder="Subred auto (ej. 192.168.1.0/24)" style="flex:1;min-width:180px;background:#080d14;border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;">
      <button id="btn-scan" class="primary" onclick="scanNetwork()"></button>
    </div>
    <div id="scan-results" class="scan-list"><div id="scan-hint" style="padding:10px;color:var(--muted);font-size:12px;">Pulsa "Escanear red local" para descubrir equipos con SSH (:22) o ZGX Agent (:18888).</div></div>
    <div class="form-grid">
      <div class="field"><label>IP / Host</label><input id="en-host" type="text" placeholder="192.168.1.xxx"></div>
      <div class="field"><label>SSH Port</label><input id="en-ssh-port" type="number" value="22"></div>
      <div class="field"><label>Agent Port</label><input id="en-port" type="number" value="18888"></div>
      <div class="field"><label id="lbl-en-alias">Alias</label><input id="en-alias" type="text" placeholder="Nano-Lab-2"></div>
      <div class="field"><label id="lbl-en-tag">Etiqueta</label><input id="en-tag" type="text" value="ZGX-NANO"></div>
      <div class="field"><label id="lbl-en-role">Rol</label><select id="en-role"><option value="node">Node (Agente)</option><option value="hub">Hub (Principal)</option></select></div>
      <div class="field"><label>SSH User</label><input id="en-user" type="text" value="iosu"></div>
      <div class="field"><label>SSH Password</label><input id="en-pass" type="password" placeholder="Password SSH (opcional si ya esta enrolado)"></div>
    </div>
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
      <div class="field"><label>SSH User</label><input id="dep-ssh-user" type="text" value="iosu"></div>
      <div class="field"><label>SSH Port</label><input id="dep-ssh-port" type="number" value="22"></div>
      <div class="field"><label>SSH Password</label><input id="dep-ssh-pass" type="password" placeholder="Password SSH para desplegar"></div>
    </div>
    <div class="panel-head" style="margin-bottom:6px;"><h3 id="dep-sel-lbl">Componentes a desplegar</h3></div>
    <div id="dep-list" class="deploy-list"></div>
    <div id="deploy-prog-wrap" style="display:none;margin-bottom:10px;">
      <div class="panel-head" style="margin-bottom:4px;">
        <span id="deploy-phase" class="port-lbl">Iniciando...</span>
        <span id="deploy-elapsed" class="port-lbl">0s</span>
      </div>
      <div class="bar-track"><div id="deploy-bar" class="bar-fill"></div></div>
    </div>
    <pre id="deploy-log" style="max-height:180px;margin-bottom:12px;display:none;"></pre>
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
      <div class="field"><label>Contrasena</label><input id="u-new-pass" type="password" placeholder="Minimo 4 caracteres"></div>
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
    usersBtn: "Usuarios",
    logout: "Salir",
    fleetTitle: "Unidades HP ZGX registradas",
    fleetSub: "Selecciona una unidad para gestionar sus demos, servicios y telemetria, o enrola una nueva sin autodespliegue.",
    addNode: "+ Enrolar ZGX",
    enterNode: "Entrar",
    removeNode: "Quitar",
    deployBtn: "Desplegar",
    enrollTitle: "Enrolar dispositivo ZGX",
    enrollNote: "Solo prueba la conexion y registra el equipo en la flota. No despliega ni reinicia demos automaticamente; usa el boton Desplegar en el portal cuando quieras instalar o actualizar.",
    enrollSubmit: "Enrolar y probar conexion",
    deployTitle: "Desplegar en dispositivo",
    deploySelLbl: "Componentes a desplegar",
    deployAgentName: "Agente Fleet Console (zgx-launcher.service)",
    deployAgentDesc: "Instala o actualiza launcher_web.py, sudoers y systemd en el dispositivo.",
    deploySubmit: "Desplegar seleccion",
    closeBtn: "Cerrar",
    cancelBtn: "Cancelar",
    scanBtn: "Escanear red local",
    scanning: "Escaneando red...",
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
    pushUpdate: "Actualizar Flota", freeGpu: "Liberar GPU", pruneDocker: "Limpiar Docker",
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
    usersBtn: "Users",
    logout: "Logout",
    fleetTitle: "Enrolled HP ZGX units",
    fleetSub: "Select a unit to manage demos, services, and telemetry, or enroll a new one without auto-deploy.",
    addNode: "+ Enroll ZGX",
    enterNode: "Manage",
    removeNode: "Remove",
    deployBtn: "Deploy",
    enrollTitle: "Enroll ZGX device",
    enrollNote: "Tests connection and registers the device in the fleet only. Nothing is auto-deployed or restarted; use the Deploy button in the portal when you want to install or update.",
    enrollSubmit: "Enroll & Test Connection",
    deployTitle: "Deploy to device",
    deploySelLbl: "Components to deploy",
    deployAgentName: "Fleet Console Agent (zgx-launcher.service)",
    deployAgentDesc: "Installs or updates launcher_web.py, sudoers and systemd on the target device.",
    deploySubmit: "Deploy selected",
    closeBtn: "Close",
    cancelBtn: "Cancel",
    scanBtn: "Scan local network",
    scanning: "Scanning network...",
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
    pushUpdate: "Push Fleet Update", freeGpu: "Free GPU", pruneDocker: "Prune Docker",
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
  document.getElementById("btn-back-fleet").textContent = t("backFleet");
  document.getElementById("btn-claim-master").textContent = t("claimMaster");
  document.getElementById("btn-users").textContent = t("usersBtn");
  document.getElementById("btn-logout").textContent = t("logout");
  document.getElementById("fleet-title").textContent = t("fleetTitle");
  document.getElementById("fleet-sub").textContent = t("fleetSub");
  document.getElementById("btn-add-node").textContent = t("addNode");
  document.getElementById("btn-scan").textContent = t("scanBtn");
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
  document.getElementById("btn-push-update").textContent = t("pushUpdate");
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
  const nextRole = currentRole === "hub" ? "node" : "hub";
  if (!confirm(`Switch this unit role to ${nextRole.toUpperCase()}?`)) return;
  await api("POST", "/api/role", { role: nextRole });
  if (nextRole === "node") enterNode("local", "127.0.0.1");
  else showFleet();
}

async function claimMaster() {
  if (!confirm("Reclamar el rol Master (Hub) en este equipo?")) return;
  const r = await api("POST", "/api/fleet/claim-master");
  if (r.ok) {
    currentRole = "hub";
    showFleet();
  } else {
    alert(r.error || t("errOp"));
  }
}

async function openUsersModal() {
  document.getElementById("users-modal").classList.add("open");
  await loadUsers();
}
function closeUsersModal() { document.getElementById("users-modal").classList.remove("open"); }

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
let deployTargetNodeId = "";

async function openEnrollModal() {
  document.getElementById("enroll-modal").classList.add("open");
  await pollEnrollStatus();
}
function closeEnrollModal() {
  document.getElementById("enroll-modal").classList.remove("open");
  if (enrollTimer) { clearInterval(enrollTimer); enrollTimer = null; }
}

async function scanNetwork() {
  const btn = document.getElementById("btn-scan");
  const box = document.getElementById("scan-results");
  btn.disabled = true; btn.textContent = t("scanning");
  box.innerHTML = `<div style="padding:10px;color:var(--muted);font-size:12px;">${t("scanning")}</div>`;
  try {
    const r = await api("POST", "/api/fleet/scan", { cidr: document.getElementById("scan-cidr").value });
    if (!r.ok) { box.innerHTML = `<div style="padding:10px;color:#ff7b72;font-size:12px;">${r.error}</div>`; return; }
    if (!r.hosts.length) { box.innerHTML = `<div style="padding:10px;color:var(--muted);font-size:12px;">No SSH/ZGX hosts found in ${r.subnet}</div>`; return; }
    box.innerHTML = r.hosts.map(h => `
      <div class="scan-row" onclick="pickHost('${h.ip}', '${h.hostname || h.ip}', '${h.hw_model || ''}')">
        <span><strong>${h.ip}</strong> ${h.hostname ? "(" + h.hostname + ")" : ""} ${h.gpu ? "- " + h.gpu : ""}</span>
        <span>${h.hw_model ? "[" + h.hw_model + "]" : ""} ${h.ssh ? "[SSH:22]" : ""} ${h.agent ? "[ZGX:18888]" : ""} ${h.enrolled ? "[ENROLLED]" : ""}</span>
      </div>
    `).join("");
  } finally {
    btn.disabled = false; btn.textContent = t("scanBtn");
  }
}

function pickHost(ip, name, hwModel) {
  document.getElementById("en-host").value = ip;
  if (!document.getElementById("en-alias").value) document.getElementById("en-alias").value = name;
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
  btn.disabled = !!st.running;
  scanBtn.disabled = !!st.running;
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
  document.getElementById("dep-target").textContent = `${node.alias || node.host} (${node.host}:${node.port || 18888}) [${node.hw_model || "ZGX"}]`;
  document.getElementById("dep-ssh-user").value = node.ssh_user || "iosu";
  document.getElementById("dep-ssh-port").value = node.ssh_port || 22;
  document.getElementById("dep-ssh-pass").value = "";

  const listEl = document.getElementById("dep-list");
  listEl.innerHTML = `<div style="padding:10px;color:var(--muted);font-size:12px;">Cargando catalogo...</div>`;
  document.getElementById("deploy-modal").classList.add("open");

  const cat = await api("GET", "/api/fleet/deploy/catalog");
  const agentChecked = (!node.online || node.agent_installed === false) ? "checked" : "";
  let html = `
    <label class="deploy-item">
      <input type="checkbox" id="dep-agent" ${agentChecked}>
      <div>
        <strong>${t("deployAgentName")}</strong>
        <span>${t("deployAgentDesc")}</span>
      </div>
    </label>`;
  for (const d of (cat.demos || [])) {
    const desc = currentLang === "en" ? (d.desc_en || d.desc_es) : (d.desc_es || d.desc_en);
    html += `
      <label class="deploy-item">
        <input type="checkbox" class="dep-demo-cb" value="${d.key}">
        <div>
          <strong>${d.name} (${d.key})</strong>
          <span>${desc || ""}</span>
        </div>
      </label>`;
  }
  listEl.innerHTML = html;
  await pollDeployStatus();
}

function closeDeployModal() {
  document.getElementById("deploy-modal").classList.remove("open");
  if (deployTimer) { clearInterval(deployTimer); deployTimer = null; }
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
    const pct = Math.round(((st.step || 0) / (st.total_steps || 3)) * 100);
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
  btn.disabled = !!st.running;
  if (st.running) {
    if (!deployTimer) deployTimer = setInterval(pollDeployStatus, 800);
  } else {
    if (deployTimer) { clearInterval(deployTimer); deployTimer = null; }
    if (st.ok === true) refresh();
  }
}

async function submitDeploy() {
  if (!deployTargetNodeId) return;
  const deployAgent = !!(document.getElementById("dep-agent") && document.getElementById("dep-agent").checked);
  const deployDemos = Array.from(document.querySelectorAll(".dep-demo-cb:checked")).map(cb => cb.value);
  const btn = document.getElementById("btn-dep-submit");
  btn.disabled = true;
  const r = await api("POST", "/api/fleet/deploy", {
    node_id: deployTargetNodeId,
    ssh_user: document.getElementById("dep-ssh-user").value,
    ssh_port: parseInt(document.getElementById("dep-ssh-port").value || "22", 10),
    ssh_pass: document.getElementById("dep-ssh-pass").value,
    deploy_agent: deployAgent,
    deploy_demos: deployDemos,
  });
  if (!r.ok) {
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

async function toggleDemo(key, enable) {
  busy = true;
  try {
    const r = enable ? await api("POST", `/api/node/${selectedNodeId}/start/${key}`) : await api("POST", `/api/node/${selectedNodeId}/stop`);
    if (!r.ok) alert(r.error || t("errOp"));
  } finally { busy = false; await refresh(); }
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
  document.getElementById("btn-claim-master").style.display = "none";
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
          <span class="pill ${n.online ? "ready" : "idle"}">${n.online ? "[ONLINE]" : "[OFFLINE]"}</span>
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
            <button class="solid" ${!n.online ? "disabled" : ""} onclick="enterNode('${n.id}', '${n.host}')">${t("enterNode")}</button>
          </div>
        </div>
      </div>`;
  }).join("");
}

function renderNodeData(data) {
  const active = data.active, sys = data.system, hist = data.history || [];
  document.getElementById("view-fleet").style.display = "none";
  document.getElementById("view-node").style.display = "block";
  document.getElementById("btn-back-fleet").style.display = currentRole === "hub" ? "inline-flex" : "none";
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
  for (const [key, d] of Object.entries(data.demos || {})) {
    const isActive = active.key === key, isReady = isActive && active.status === "ready";
    const badgeCls = isReady ? "ready" : (isActive ? "starting" : "");
    const readyTag = key === "nano-collective" && d.nano_mode ? (d.nano_mode === "presenter" ? "[READY - STAGE]" : "[READY - FREE]") : "[READY]";
    const badgeTxt = !d.installed ? "[NOT INSTALLED]" : (isReady ? readyTag : (isActive ? `[STARTING ${active.uptime_s}s]` : "[OFF]"));
    const desc = currentLang === "en" ? (d.desc_en || d.desc_es) : (d.desc_es || d.desc_en);
    const url = "http://" + selectedNodeHost + ":" + d.port + d.route;
    const actionBtns = isReady
      ? (key === "nano-collective"
          ? `<button class="${d.nano_mode === "free" ? "open" : ""}" onclick="openNanoMode('free')">${t("openFree")}</button><button class="${d.nano_mode === "presenter" ? "open" : "primary"}" onclick="openNanoMode('presenter')">${t("openStage")}</button>`
          : `<a class="btn open" href="${url}" target="_blank" rel="noopener">${t("open")}</a>`)
      : "";
    const dTok = (tokData.by_demo && tokData.by_demo[key]) || { total_tokens: 0, input_tokens: 0, output_tokens: 0, eur_saved: 0 };
    const card = document.createElement("div");
    card.className = "demo-card" + (isActive ? " active" : "");
    if (!d.installed) card.style.opacity = "0.62";
    card.innerHTML = `<div class="thumb">${THUMBS[key] || ""}<span class="thumb-tag">${d.tag}</span><span class="thumb-state ${badgeCls}">${badgeTxt}</span></div><div class="demo-body"><div><h2>${d.name}</h2><p>${desc}</p><div class="demo-tok-bar"><span>${t("tokensLbl")}: <strong>${(dTok.total_tokens || 0).toLocaleString()}</strong> (${fmtTokens(dTok.input_tokens || 0)}/${fmtTokens(dTok.output_tokens || 0)})</span><span class="eur-tag">${fmtEur(dTok.eur_saved || 0)}</span></div></div><div class="demo-footer"><span class="port-lbl">:${d.port} ${!d.installed ? "| N/A" : ""}</span><div class="btn-row">${actionBtns}<label class="switch"><input type="checkbox" ${isActive ? "checked" : ""} ${!d.installed ? "disabled" : ""} onchange="toggleDemo('${key}', this.checked)"><span class="slider"></span></label></div></div></div>`;
    grid.appendChild(card);
  }

  document.getElementById("demo-term-state").textContent = active.key ? `[${active.status.toUpperCase()}] ${active.key} (${active.uptime_s}s)` : "[IDLE]";
  for (const id of ["log-box", "demo-term-box"]) {
    const el = document.getElementById(id), wasAtBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 30;
    el.textContent = data.log || t("noLogs");
    if (wasAtBottom) el.scrollTop = el.scrollHeight;
  }
}

async function refresh() {
  if (busy) return;
  try {
    if (!selectedNodeId) {
      const f = await api("GET", "/api/fleet");
      if (!f || !f.nodes) return;
      currentRole = f.role || "hub";
      if (currentRole === "node") {
        selectedNodeId = "local";
        selectedNodeHost = window.location.hostname;
      } else {
        lastFleetData = f;
        renderFleet(f);
        return;
      }
    }
    const data = await api("GET", `/api/node/${selectedNodeId}/status`);
    if (data && data.system) {
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
            })
            return
        if not self._is_authed():
            if self.path.startswith("/api/"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            else:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/login")
                self.end_headers()
            return
        if self.path == "/" or self.path == "/index.html":
            self._send_html(HTML_PAGE)
        elif self.path == "/api/status":
            self._send_json(HTTPStatus.OK, get_status())
        elif self.path == "/api/fleet":
            self._send_json(HTTPStatus.OK, get_fleet_status())
        elif self.path == "/api/fleet/enroll/status":
            self._send_json(HTTPStatus.OK, get_enroll_status())
        elif self.path == "/api/fleet/deploy/catalog":
            self._send_json(HTTPStatus.OK, get_deploy_catalog())
        elif self.path == "/api/fleet/deploy/status":
            self._send_json(HTTPStatus.OK, get_deploy_status())
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
        if not self._is_authed():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return

        if self.path == "/api/fleet/scan":
            body = self._read_json()
            self._send_json(HTTPStatus.OK, scan_local_network(body.get("cidr", "")))
        elif self.path == "/api/fleet/enroll":
            self._send_json(HTTPStatus.OK, enroll_node(self._read_json()))
        elif self.path == "/api/fleet/deploy":
            self._send_json(HTTPStatus.OK, deploy_to_node(self._read_json()))
        elif self.path == "/api/fleet/sync":
            self._send_json(HTTPStatus.OK, apply_fleet_sync(self._read_json()))
        elif self.path == "/api/fleet/claim-master":
            self._send_json(HTTPStatus.OK, claim_master())
        elif self.path == "/api/fleet/push-update":
            self._send_json(HTTPStatus.OK, push_update_fleet())
        elif self.path == "/api/node/self-update":
            self._send_json(HTTPStatus.OK, self_update_node(self._read_json()))
        elif self.path == "/api/free-gpu":
            self._send_json(HTTPStatus.OK, free_gpu_action())
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
            self._send_json(HTTPStatus.OK, proxy_node_request(node_id, "/" + sub, method="POST"))
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
            for n in load_fleet():
                try:
                    _node_http_json(n["host"], n.get("port", 18888), "/api/tokens/reset", n.get("token", ""), method="POST", timeout=2.0)
                except Exception:
                    pass
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
    threading.Thread(target=telemetry_loop, daemon=True).start()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"[INFO] ZGX Fleet Console listening on http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

