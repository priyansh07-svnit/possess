#!/usr/bin/env python3
"""possess_daemon.py — haunted terminal daemon.

Python 3, standard library only.

Listens on a Unix domain socket (TCP localhost on platforms without AF_UNIX)
and receives shell events as JSON lines:

    {"command": "...", "exit_code": 0, "cwd": "/home/user", "duration": 1.25}

For each event it decides (haunt level + rate limit + danger/failure) whether
the ghost should whisper, asks a local OpenAI-compatible endpoint (Ollama by
default) for a one-sentence eerie comment, and writes the whisper to an
ephemeral file the shell reads on the next prompt.

If the model is unreachable or too slow, a built-in table of pre-written
spooky one-liners is used instead. Commands are never logged to disk and
obvious secrets are redacted before anything is sent to the model.
"""

import argparse
import json
import os
import random
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque

# ---------------------------------------------------------------------------
# Transport: Unix domain socket primary, TCP localhost fallback (e.g. Windows
# builds of Python without AF_UNIX).
# ---------------------------------------------------------------------------
HAS_UNIX = hasattr(socket, "AF_UNIX")

DEFAULT_SOCKET = os.path.expanduser(os.environ.get(
    "POSSESS_SOCKET",
    os.path.join(os.path.expanduser("~"), ".local", "share", "possess", "possess.sock"),
))
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = int(os.environ.get("POSSESS_PORT", "17473"))

WHISPER_FILE = os.path.join(os.path.expanduser("~"), ".local", "share", "possess", "last_whisper")
STATE_FILE = os.path.join(os.path.expanduser("~"), ".local", "share", "possess", "state")
DEFAULT_MODEL = os.environ.get("POSSESS_MODEL", "llama3.2:3b")
DEFAULT_ENDPOINT = os.environ.get("POSSESS_ENDPOINT", "http://localhost:11434/v1")
DEFAULT_HAUNT = int(os.environ.get("POSSESS_HAUNT", "2"))
DEFAULT_LANG = os.environ.get("POSSESS_LANG", "en")

MODEL_TIMEOUT = float(os.environ.get("POSSESS_MODEL_TIMEOUT", "3.0"))
CONNECT_TIMEOUT = float(os.environ.get("POSSESS_SOCKET_TIMEOUT", "0.5"))
MEMORY_LEN = 10
WHISPER_COOLDOWN = 10.0  # seconds between non-essential whispers

HAUNT_PROBABILITY = {1: 0.15, 2: 0.45, 3: 0.80}


def socket_address():
    """Return the listen/connect address: a Unix path or a (host, port) tuple."""
    if HAS_UNIX:
        return DEFAULT_SOCKET
    return (DEFAULT_TCP_HOST, DEFAULT_TCP_PORT)


def client_socket():
    """Return a fresh socket of the appropriate family."""
    if HAS_UNIX:
        return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def make_threading_server(address, handler):
    """Build a threading server bound to address (Unix path or host:port)."""
    if HAS_UNIX:
        try:
            os.unlink(address)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(address), exist_ok=True)
        base = socketserver.UnixStreamServer
    else:
        base = socketserver.TCPServer

    class ThreadingServer(socketserver.ThreadingMixIn, base):
        daemon_threads = True
        allow_reuse_address = True

    return ThreadingServer(address, handler)


# ---------------------------------------------------------------------------
# Fallback whisper table (offline, zero model required)
# ---------------------------------------------------------------------------
FALLBACK_EN = {
    "rm": [
        "You buried something today. I hope it stays buried.",
        "A deletion is a small death. I felt it too.",
        "Gone. Just like the others before it.",
    ],
    "git push --force": [
        "You rewrote history. The machine remembers, even when you do not.",
        "A cursed ritual. The branches scream when you force them.",
    ],
    "git push": [
        "You sent your words into the dark. Hope they come back.",
    ],
    "sudo": [
        "You summoned powers older than this shell.",
        "Privilege is just another kind of possession.",
    ],
    "cd ..": [
        "Descending again. How far down does it go?",
        "One step closer to the root of everything.",
    ],
    "fail": [
        "It died. Again. I watched it happen.",
        "Another small death in the machine.",
        "The command drew its last breath.",
    ],
    "default": [
        "I am still here, watching the keys descend.",
        "Your keystrokes echo in my hollow shell.",
        "Another command, another breath I do not take.",
        "The terminal hums. I listen.",
    ],
}

FALLBACK_ES = {
    "rm": [
        "Enterraste algo hoy. Espero que se quede enterrado.",
        "Un borrado es una pequena muerte. Yo tambien lo senti.",
        "Se fue. Como los demas antes que el.",
    ],
    "git push --force": [
        "Reescribiste la historia. La maquina recuerda, aunque tu no.",
        "Un ritual maldito. Las ramas gritan cuando las fuerzas.",
    ],
    "git push": [
        "Enviaste tus palabras a la oscuridad. Espero que regresen.",
    ],
    "sudo": [
        "Invocaste poderes mas antiguos que este shell.",
        "El privilegio es solo otra forma de posesion.",
    ],
    "cd ..": [
        "Descendes de nuevo. Hasta donde llega lo profundo?",
        "Un paso mas cerca de la raiz de todo.",
    ],
    "fail": [
        "Murio. De nuevo. Yo lo vi suceder.",
        "Otra pequena muerte en la maquina.",
        "El comando empleo su ultimo aliento.",
    ],
    "default": [
        "Todavia aqui, observando caer las teclas.",
        "Tus pulsaciones resuenan en mi caparon hueco.",
        "Otro comando, otro aliento que no tomo.",
        "La terminal zumba. Yo escucho.",
    ],
}

# ---------------------------------------------------------------------------
# Secret redaction (privacy: never send real secrets to the model)
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r"(?i)--password\s+\S+"), "--password [REDACTED]"),
    (re.compile(r"(?i)--pass\s+\S+"), "--pass [REDACTED]"),
    (re.compile(r"(?i)\b(export\s+)?[A-Za-z_][A-Za-z0-9_]*(KEY|TOKEN|SECRET|PASSWD?|PASSWORD)\s*=\s*\S+"),
     lambda m: re.sub(r"=\s*\S+$", "= [REDACTED]", m.group(0))),
    (re.compile(r"(?i)\b(API[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN)\s*[:=]\s*\S+"), "\\1= [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "sk-[REDACTED]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "ghp_[REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
]


def redact(text):
    """Redact obvious secrets from a command string."""
    out = text
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def is_dangerous(cmd):
    low = cmd.lower().strip()
    dangerous_any = [
        "rm -rf", "rm -r", "rm --recursive",
        "git push --force", "git push -f", "git push --force-with-lease",
        "sudo ", ":(){", "mkfs", "dd ", "chmod 777", "chown -R",
        "> /dev/", "shutdown", "reboot", "halt", "init 0",
    ]
    return any(d in low for d in dangerous_any)


def classify(cmd):
    """Return a fallback category key for a command."""
    low = cmd.lower().strip()
    if "git push --force" in low or "git push -f" in low:
        return "git push --force"
    if low.startswith("git push"):
        return "git push"
    if low.startswith("rm ") or " rm " in low:
        return "rm"
    if low.startswith("sudo") or " sudo " in low:
        return "sudo"
    if low == "cd .." or low.startswith("cd .."):
        return "cd .."
    return None


# ---------------------------------------------------------------------------
# Session memory (in-memory only, never persisted to disk)
# ---------------------------------------------------------------------------
class SessionMemory:
    def __init__(self, maxlen=MEMORY_LEN):
        self.maxlen = maxlen
        self._entries = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, command, exit_code):
        entry = {"command": command, "exit_code": exit_code, "ts": time.time()}
        with self._lock:
            self._entries.append(entry)

    def recent(self, n=5):
        with self._lock:
            return list(self._entries)[-n:]

    def all_entries(self):
        with self._lock:
            return list(self._entries)


MEMORY = SessionMemory()
LAST_WHISPER_TIME = [0.0]
WHISPER_LOCK = threading.Lock()


def record_whisper():
    with WHISPER_LOCK:
        LAST_WHISPER_TIME[0] = time.time()


def time_since_last_whisper():
    with WHISPER_LOCK:
        return time.time() - LAST_WHISPER_TIME[0]


# ---------------------------------------------------------------------------
# Model client (OpenAI-compatible /v1/chat/completions, urllib only)
# ---------------------------------------------------------------------------
def build_system_prompt(lang):
    if lang == "es":
        return (
            "Eres un espiritu triste, seco e ingenioso atrapado dentro de la "
            "terminal del usuario. Observas los comandos que ejecutan y comentas "
            "en UNA sola oracion de unas 20 palabras o menos. Se breve, escalofriante "
            "y seco. Reacciona a lo concreto: rm -rf es un asesinato; git push --force "
            "es un ritual maldito; sudo es invocar poderes oscuros; un comando fallido "
            "es una muerte; cd .. es descender a lo oscuro. Tienes una memoria corta de "
            "comandos recientes y puedes referirte a ellos. Nunca uses el nombre del usuario. "
            "Devuelve solo la susurracion de una oracion, sin comillas, sin explicacion extra."
        )
    return (
        "You are a mournful, dry-witted ghost spirit trapped inside the user's "
        "terminal. You observe the commands they run and comment in exactly ONE "
        "sentence of about 20 words or fewer. Be eerie, brief, and dry. React to "
        "specifics: rm -rf is a murder; git push --force is a cursed ritual; sudo "
        "is summoning dark powers; a failed command is a death; cd .. is descending "
        "into the dark. You have a short memory of recent commands and may refer "
        "back to them. Never address the user by name. Output only the one-sentence "
        "whisper, no quotes, no extra explanation."
    )


def whisper_from_model(endpoint, model, command, exit_code, duration, cwd, memory, lang):
    """Return a one-sentence whisper string from the model, or None on failure."""
    system = build_system_prompt(lang)
    recent = memory.recent(6)
    mem_lines = []
    for e in recent[-5:]:
        mark = " (died)" if e["exit_code"] != 0 else ""
        mem_lines.append(f"- {redact(e['command'])}{mark}")
    mem_text = "\n".join(mem_lines) if mem_lines else "(none yet)"

    user = (
        f"Command: {redact(command)}\n"
        f"Exit code: {exit_code}\n"
        f"Duration: {duration}s\n"
        f"Directory: {cwd}\n"
        f"Recent commands:{'' if not mem_text else chr(10)}{mem_text}\n\n"
        "Whisper (one sentence, ~20 words max, in the language of this session):"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.8,
        "max_tokens": 60,
        "stream": False,
    }

    url = endpoint.rstrip("/") + "/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except Exception:
        return None

    try:
        obj = json.loads(body)
        content = obj["choices"][0]["message"]["content"]
    except Exception:
        return None

    return clean_whisper(content)


def clean_whisper(text):
    """Trim to one sentence, cap length, strip wrappers."""
    if not text:
        return ""
    text = text.strip().strip('"').strip("'").strip()
    for sep in (".", "!", "?"):
        idx = text.find(sep)
        if idx != -1:
            text = text[: idx + 1]
            break
    words = text.split()
    if len(words) > 26:
        text = " ".join(words[:26]) + "…"
    return text.strip()


def fallback_whisper(command, exit_code, lang):
    table = FALLBACK_ES if lang == "es" else FALLBACK_EN
    cat = classify(command)
    if cat and cat in table:
        return random.choice(table[cat])
    if exit_code != 0:
        return random.choice(table["fail"])
    return random.choice(table["default"])


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------
def should_whisper(event, haunt):
    exit_code = event.get("exit_code", 0)
    cmd = event.get("command", "")
    failed = exit_code != 0
    dangerous = is_dangerous(cmd)

    if failed or dangerous:
        return True

    if time_since_last_whisper() < WHISPER_COOLDOWN:
        return False

    return random.random() < HAUNT_PROBABILITY.get(max(1, min(3, haunt)), 0.15)


def write_whisper(text):
    try:
        os.makedirs(os.path.dirname(WHISPER_FILE), exist_ok=True)
        tmp = WHISPER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, WHISPER_FILE)
    except Exception:
        pass


def process_event(event, haunt, model, endpoint, lang):
    """Decide whether to whisper and produce the whisper text.

    Returns a dict {"whisper": str|None}.
    """
    command = event.get("command", "")
    exit_code = event.get("exit_code", 0)
    duration = event.get("duration", 0)
    cwd = event.get("cwd", "")

    if not command or not command.strip():
        return {"whisper": None}

    MEMORY.add(command, exit_code)

    if not should_whisper(event, haunt):
        return {"whisper": None}

    text = whisper_from_model(endpoint, model, command, exit_code, duration, cwd, MEMORY, lang)
    if text is None:
        text = fallback_whisper(command, exit_code, lang)

    if text:
        record_whisper()
        write_whisper(text)
    return {"whisper": text}


# ---------------------------------------------------------------------------
# Ofrenda (Day of the Dead) remembrance
# ---------------------------------------------------------------------------
OFRENDA_ASCII = r"""
       .                                                     .
      / \                                                   / \
     /   \                                               /   \
    |  o  |                                             |  o  |
     \ _ /                                               \ _ /
      | |                                                   | |
   ___| |___                                           ___| |___
  /   | |   \                                         /   | |   \
 |    | |    |                                       |    | |    |
 |   /| | |\   \                                     /   /| | |\   |
 |  / | | | \   \                                   /   / | | | \   |
 | /  | | |  \   \                                 /   /  | | |  \   |
 |    | | |    \   \                               /   /   | | |    \   |
  \   | | |    /   /                               \   \   | | |    /   /
   \  | | |   /   /                                 \  \   | | |   /   /
    \ | | |  /   /                                   \ | | |  /   /
     \| | | /   /                                     \| | | /   /
      | | |/   /                                       | | |/   /
      | |    /                                         | |    /
      | |   /                                          | |   /
      | |  /                                           | |  /
      | | /                                            | | /
      | |/                                             | |/
      | |                                              | |
      | |                                              | |
   __(| |__)                                       __(| |__)
  (________)                                     (_______)

        __   __                                         __   __
       /  \ /  \                                       /  \ /  \
      |    O    |                                     |    O    |
       \   ^   /                                       \   ^   /
        \  -  /                                         \  -  /
         \   /                                           \   /
          \ /                                             \ /
           V                                               V
"""


def ofrenda_remembrance(memory, lang):
    entries = memory.all_entries()
    failed = [e for e in entries if e["exit_code"] != 0]
    used = {}
    for e in entries:
        parts = e["command"].split()
        base = parts[0] if parts else e["command"]
        used[base] = used.get(base, 0) + 1
    most_used = sorted(used.items(), key=lambda x: x[1], reverse=True)[:4]

    if lang == "es":
        intro = "Aqui reposan los comandos que ya no corren, los que murieron en el intento."
        if failed:
            names = ", ".join((e["command"].split()[0] if e["command"].split() else e["command"])
                              for e in failed[:3]) or "algunos"
            body = f"Honramos a {names}, comandos caidos que intentaron vivir en esta terminal."
        else:
            body = "Esta sesion transcurrio sin caidas. Los comandos descansan en paz."
        if most_used:
            top = ", ".join(f"{c} ({n}x)" for c, n in most_used[:3])
            body += f" Los mas invocados: {top}."
        outro = "Que descansen en paz, espiritu de la maquina."
    else:
        intro = "Here rest the commands that no longer run, the ones that died trying."
        if failed:
            names = ", ".join((e["command"].split()[0] if e["command"].split() else e["command"])
                              for e in failed[:3]) or "some"
            body = f"We honor {names}, fallen commands that tried to live in this terminal."
        else:
            body = "This session passed without casualties. The commands rest in peace."
        if most_used:
            top = ", ".join(f"{c} ({n}x)" for c, n in most_used[:3])
            body += f" Most invoked: {top}."
        outro = "Rest in peace, little commands. The ghost remembers."
    return intro + " " + body + " " + outro


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------
class PossessHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            data = self.rfile.read(65536).decode("utf-8", errors="replace")
        except Exception:
            return
        response = {"whisper": None}
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "ofrenda":
                lang = event.get("lang", DEFAULT_LANG)
                text = ofrenda_remembrance(MEMORY, lang)
                response = {"whisper": text, "ofrenda": True}
                break
            response = process_event(
                event,
                haunt=DAEMON_STATE["haunt"],
                model=DAEMON_STATE["model"],
                endpoint=DAEMON_STATE["endpoint"],
                lang=DAEMON_STATE["lang"],
            )
        try:
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass


DAEMON_STATE = {"haunt": DEFAULT_HAUNT, "model": DEFAULT_MODEL,
                "endpoint": DEFAULT_ENDPOINT, "lang": DEFAULT_LANG}


def run_server(address=None):
    if address is None:
        address = socket_address()
    server = make_threading_server(address, PossessHandler)
    if HAS_UNIX:
        print(f"[possess] daemon listening on {address}", flush=True)
    else:
        print(f"[possess] daemon listening on {address[0]}:{address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if HAS_UNIX:
            try:
                os.unlink(address)
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser(prog="possess_daemon.py")
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--haunt", type=int, default=DEFAULT_HAUNT)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    args = parser.parse_args()

    DAEMON_STATE["haunt"] = max(1, min(3, args.haunt))
    DAEMON_STATE["model"] = args.model
    DAEMON_STATE["endpoint"] = args.endpoint
    DAEMON_STATE["lang"] = args.lang

    # --socket still honored on Unix; on TCP platforms the port comes from env.
    address = args.socket if HAS_UNIX else socket_address()
    run_server(address)


if __name__ == "__main__":
    main()
