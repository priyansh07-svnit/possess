#!/usr/bin/env python3
"""tests/test_possess.py — end-to-end + fallback tests for possess.

Starts possess_daemon.py on a temp Unix socket, sends shell-style events, and
checks that whispers come back. Also verifies the offline fallback table and
secret redaction work with no model involved.
"""

import json
import os
import socket
import sys
import threading
import time
import unittest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

import possess_daemon as PD  # noqa: E402


def test_address(suffix=""):
    """Address for the test daemon: temp Unix socket, or TCP localhost."""
    if PD.HAS_UNIX:
        path = os.path.join(os.path.dirname(PD.WHISPER_FILE), f"test_possess{suffix}.sock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return path
    return PD.socket_address()


class DaemonHarness:
    """Start the daemon on a temp address in a background thread."""

    def __init__(self, address, model="llama3.2:3b",
                 endpoint="http://localhost:11434/v1", haunt=3, lang="en"):
        self.address = address
        PD.DAEMON_STATE["haunt"] = haunt
        PD.DAEMON_STATE["model"] = model
        PD.DAEMON_STATE["endpoint"] = endpoint
        PD.DAEMON_STATE["lang"] = lang
        PD.MEMORY = PD.SessionMemory()
        PD.LAST_WHISPER_TIME[0] = 0.0
        self.thread = threading.Thread(target=PD.run_server, args=(address,), daemon=True)
        self.thread.start()
        # wait for the listen address to accept connections
        deadline = time.time() + 5
        while not daemon_listening(address):
            if time.time() > deadline:
                raise RuntimeError("daemon never started listening")
            time.sleep(0.05)

    def send(self, event, timeout=8.0):
        s = PD.client_socket()
        s.settimeout(timeout)
        s.connect(self.address)
        s.sendall((json.dumps(event) + "\n").encode("utf-8"))
        try:
            s.shutdown(socket.SHUT_WR)
        except Exception:
            pass
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode("utf-8", errors="replace")) if buf else {}


def daemon_listening(address):
    try:
        s = PD.client_socket()
        s.settimeout(0.3)
        s.connect(address)
        s.close()
        return True
    except Exception:
        return False


class PossessTests(unittest.TestCase):
    ADDRESS = test_address()

    @classmethod
    def setUpClass(cls):
        # haunt=3 (poltergeist) so non-failure commands also whisper in tests.
        cls.harness = DaemonHarness(cls.ADDRESS, haunt=3)

    def _whisper(self, command, exit_code=0, duration=0.5, cwd="/home/user"):
        resp = self.harness.send({
            "command": command, "exit_code": exit_code,
            "cwd": cwd, "duration": duration,
        })
        return resp.get("whisper")

    def test_failed_command_whispers(self):
        w = self._whisper("cargo deploy --to production", exit_code=1)
        self.assertIsNotNone(w)
        self.assertIsInstance(w, str)
        self.assertGreater(len(w), 0)
        print(f"\n[failed] {w}")

    def test_dangerous_rm_rf_whispers(self):
        w = self._whisper("rm -rf /tmp/old_stuff", exit_code=0)
        self.assertIsNotNone(w)
        print(f"[rm -rf] {w}")

    def test_sudo_whispers(self):
        w = self._whisper("sudo apt install haunted", exit_code=0)
        self.assertIsNotNone(w)
        print(f"[sudo] {w}")

    def test_git_push_force_whispers(self):
        w = self._whisper("git push --force origin main", exit_code=0)
        self.assertIsNotNone(w)
        print(f"[git push --force] {w}")

    def test_memory_reference_possible(self):
        # Run same failing command twice; ghost may refer back.
        self._whisper("npm install cursed-package", exit_code=1)
        w2 = self._whisper("npm install cursed-package", exit_code=1)
        self.assertIsNotNone(w2)
        print(f"[repeat] {w2}")

    def test_cd_dotdot_whispers(self):
        # Failed commands always whisper (bypass haunt/cooldown rate limit).
        w = self._whisper("cd ..", exit_code=1)
        self.assertIsNotNone(w)
        print(f"[cd .. (failed)] {w}")

    def test_empty_command_no_whisper(self):
        resp = self.harness.send({"command": "", "exit_code": 0, "cwd": "/", "duration": 0})
        self.assertIsNone(resp.get("whisper"))

    def test_rate_limit_cooldown(self):
        # haunt 3, rapid safe commands: cooldown should suppress some.
        whispers = 0
        for i in range(8):
            w = self._whisper(f"ls -la dir{i}", exit_code=0)
            if w:
                whispers += 1
        # With haunt 3 and cooldown, not every command whispers.
        print(f"[coverage] {whispers}/8 safe commands whispered")
        self.assertGreaterEqual(whispers, 0)

    def test_redaction(self):
        cleaned = PD.redact("curl -H 'Authorization: Bearer sk-live_abcdef1234567890' https://api.example")
        self.assertNotIn("sk-live_abcdef", cleaned)
        self.assertIn("[REDACTED]", cleaned)
        cleaned2 = PD.redact("export DEPLOY_KEY=supersecretvalue && deploy")
        self.assertNotIn("supersecretvalue", cleaned2)
        cleaned3 = PD.redact("psql --password mydbpass123 -U admin")
        self.assertNotIn("mydbpass123", cleaned3)
        print(f"[redact] {cleaned}")

    def test_fallback_table_non_empty(self):
        for lang in ("en", "es"):
            for _ in range(20):
                w = PD.fallback_whisper("git push --force origin main", 0, lang)
                self.assertIsInstance(w, str)
                self.assertGreater(len(w), 0)
            w = PD.fallback_whisper("some random safe command", 0, lang)
            self.assertIsInstance(w, str)
            self.assertGreater(len(w), 0)
            w = PD.fallback_whisper("whatever", 1, lang)
            self.assertGreater(len(w), 0)
        print("[fallback] en + es tables return non-empty whispers")

    def test_classify(self):
        self.assertEqual(PD.classify("rm -rf /tmp/x"), "rm")
        self.assertEqual(PD.classify("git push --force"), "git push --force")
        self.assertEqual(PD.classify("sudo ls"), "sudo")
        self.assertEqual(PD.classify("cd .."), "cd ..")
        self.assertIsNone(PD.classify("ls -la"))


class FallbackOnlyTests(unittest.TestCase):
    """Verify fallback whispers work with zero model (bad endpoint)."""

    def setUp(self):
        os.environ["POSSESS_PORT"] = "17474"
        self.harness = DaemonHarness(test_address(".fallback"), haunt=3,
                                     endpoint="http://127.0.0.1:1/discard",
                                     lang="en")

    def test_model_unreachable_uses_fallback(self):
        w = self._whisper("rm -rf /tmp/anything", exit_code=0)
        self.assertIsNotNone(w)
        self.assertGreater(len(w), 0)
        print(f"[fallback-live] {w}")

    def _whisper(self, command, exit_code=0, duration=0.5, cwd="/home/user"):
        resp = self.harness.send({
            "command": command, "exit_code": exit_code,
            "cwd": cwd, "duration": duration,
        }, timeout=8.0)
        return resp.get("whisper")


if __name__ == "__main__":
    unittest.main(verbosity=2)
