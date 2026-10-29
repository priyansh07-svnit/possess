# possess.sh — haunted terminal shell integration.
# Source this from your .bashrc and/or .zshrc:
#
#   [ -f "$HOME/.local/share/possess/possess.sh" ] && . "$HOME/.local/share/possess/possess.sh"
#
# Provides the `possess` command (on|off|status|haunt N|ofrenda [--lang es])
# and, after every command, lets a local ghost whisper about what just ran.

# Resolve the directory this file lives in (works when symlinked/copied).
POSSESS_DIR="${POSSESS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)}"
POSSESS_CLI="$POSSESS_DIR/possess"

# Detect a working Python interpreter (python3 preferred; fall back to python,
# e.g. some Windows builds ship a store stub as python3).
POSSESS_PYTHON=""
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" 2>/dev/null; then
    POSSESS_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    POSSESS_PYTHON=python
fi
export POSSESS_PYTHON

# Load persisted state (enabled / haunt level) into this shell.
_possess_load_state() {
    POSSESS_ENABLED=0
    POSSESS_HAUNT=2
    local state_file="$HOME/.local/share/possess/state"
    if [ -f "$state_file" ]; then
        # shellcheck disable=SC1090
        . "$state_file" 2>/dev/null || true
    fi
    : "${POSSESS_ENABLED:=0}"
    : "${POSSESS_HAUNT:=2}"
    export POSSESS_ENABLED POSSESS_HAUNT
}

# The `possess` command, implemented as a shell function so `on`/`off`/`haunt`
# take effect immediately in the interactive shell (environment variables).
possess() {
    if [ ! -x "$POSSESS_CLI" ]; then
        echo "possess: CLI not found at $POSSESS_CLI" >&2
        return 1
    fi
    local sub="$1"; shift || true
    case "$sub" in
        on)
            "$POSSESS_CLI" on "$@" >/dev/null 2>&1
            _possess_load_state
            POSSESS_ENABLED=1
            export POSSESS_ENABLED
            ;;
        off)
            "$POSSESS_CLI" off "$@" >/dev/null 2>&1
            _possess_load_state
            POSSESS_ENABLED=0
            export POSSESS_ENABLED
            ;;
        status)
            "$POSSESS_CLI" status "$@"
            ;;
        haunt)
            "$POSSESS_CLI" haunt "$@" >/dev/null 2>&1
            _possess_load_state
            ;;
        ofrenda)
            "$POSSESS_CLI" ofrenda "$@"
            ;;
        *)
            "$POSSESS_CLI" "$sub" "$@"
            ;;
    esac
}

_possess_load_state

# --- ANSI styling -----------------------------------------------------------
POSSESS_GLYPH="👻"
_possess_dim() { printf '\e[2m\e[3m%s\e[0m' "$*"; }

# --- Event capture ----------------------------------------------------------
# POSSESS_PREEXEC_COMMAND holds the command currently running so we can compute
# its exit code and duration once it finishes.

# bash: DEBUG trap runs before each command; $BASH_COMMAND is the command text.
if [ -n "${BASH_VERSION:-}" ]; then
    possess_preexec_time=0
    # NOTE: use ${var:-value} so the trap does not clobber a command that is
    # already being processed (the DEBUG trap fires before *every* command,
    # including the ones inside _possess_send_event).
    trap 'POSSESS_PREEXEC_COMMAND="${POSSESS_PREEXEC_COMMAND:-$BASH_COMMAND}"; possess_preexec_time="${possess_preexec_time:-$(date +%s.%N 2>/dev/null || echo 0)}"' DEBUG
fi

# zsh: preexec() runs before each command with the command string as $1.
if [ -n "${ZSH_VERSION:-}" ]; then
    preexec() {
        POSSESS_PREEXEC_COMMAND="$1"
        possess_preexec_time="$EPOCHREALTIME"
    }
fi

# Send the just-finished command to the daemon as a JSON event over the Unix
# socket. Single Python process, fire-and-forget (backgrounded): the prompt is
# never blocked waiting on the model.
_possess_send_event() {
    [ "$POSSESS_ENABLED" = "1" ] || return 0
    [ -n "$POSSESS_PREEXEC_COMMAND" ] || return 0
    # Skip empty / whitespace-only commands.
    case "$POSSESS_PREEXEC_COMMAND" in
        *[![:space:]]*) ;;
        *) return 0 ;;
    esac

    local code="${1:-0}"
    local end_time
    end_time="$(date +%s.%N 2>/dev/null || echo "${EPOCHREALTIME:-0}")"
    local start_time="${possess_preexec_time:-0}"
    local duration
    duration="$(awk -v s="$start_time" -v e="$end_time" 'BEGIN{printf "%.2f", e-s}' 2>/dev/null || echo 0)"
    local cwd
    cwd="$(pwd 2>/dev/null || echo)"

    export POSSESS_CMD="$POSSESS_PREEXEC_COMMAND"
    export POSSESS_CODE="$code"
    export POSSESS_DUR="$duration"
    export POSSESS_CWD="$cwd"
    export POSSESS_DIR="$POSSESS_DIR"

    # One Python process: build JSON, connect to the daemon, send, close.
    # Uses the same transport abstraction as the daemon (Unix socket, or TCP
    # localhost where AF_UNIX is unavailable). Backgrounded so it cannot slow
    # the prompt.
    (
        "$POSSESS_PYTHON" - <<'PY' >/dev/null 2>&1 &
import json, os, socket, sys
sys.path.insert(0, os.environ.get("POSSESS_DIR", ""))
import possess_daemon as PD  # noqa: E402
cmd = os.environ.get("POSSESS_CMD", "")
code = int(os.environ.get("POSSESS_CODE", "0"))
dur = float(os.environ.get("POSSESS_DUR", "0") or 0)
cwd = os.environ.get("POSSESS_CWD", "")
if not cmd:
    sys.exit(0)
event = {"command": cmd, "exit_code": code, "cwd": cwd, "duration": dur}
try:
    s = PD.client_socket()
    s.settimeout(0.5)
    s.connect(PD.socket_address())
    s.sendall((json.dumps(event) + "\n").encode("utf-8"))
    try:
        s.shutdown(socket.SHUT_WR)
    except Exception:
        pass
    s.close()
except Exception:
    sys.exit(0)
PY
        disown 2>/dev/null || true
    )

    POSSESS_PREEXEC_COMMAND=""
}

# Print a pending whisper (produced by the daemon for an earlier command) at
# the top of the next prompt: dimmed/italic, ghost glyph, then clear it.
_possess_print_whisper() {
    local whisper_file="$HOME/.local/share/possess/last_whisper"
    [ -f "$whisper_file" ] || return 0
    local whisper
    whisper="$(cat "$whisper_file" 2>/dev/null)"
    rm -f "$whisper_file" 2>/dev/null
    [ -n "$whisper" ] || return 0
    if [ -z "${POSSESS_PRINTING_WHISPER:-}" ]; then
        POSSESS_PRINTING_WHISPER=1
        printf '\n'
        _possess_dim "${POSSESS_GLYPH} ${whisper}"
        printf '\n'
        POSSESS_PRINTING_WHISPER=""
    fi
}

# --- Hooks ------------------------------------------------------------------
if [ -n "${BASH_VERSION:-}" ]; then
    possess_prompt_command() {
        local code=$?
        _possess_print_whisper
        _possess_send_event "$code"
    }
    if [ -z "${PROMPT_COMMAND:-}" ]; then
        PROMPT_COMMAND='possess_prompt_command'
    else
        PROMPT_COMMAND='possess_prompt_command; '"$PROMPT_COMMAND"
    fi
fi

if [ -n "${ZSH_VERSION:-}" ]; then
    precmd() {
        local code=$?
        _possess_print_whisper
        _possess_send_event "$code"
    }
fi

# Safety: never let a possess error break the shell.
return 0 2>/dev/null || true
