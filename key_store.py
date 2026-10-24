"""Save an explicitly submitted key in macOS Keychain, without network access."""

import getpass
import hmac
import re
import shlex
import subprocess
import sys
import threading


SERVICE = "com.local-dino.mubit-api-key"
_KEY = re.compile(r"mbt_[A-Za-z0-9_-]{8,2044}\Z")
_SAVE_LOCK = threading.Lock()
_TIMEOUT_SECONDS = 10


def save_key(payload):
    """Store only an exact key payload, then confirm the value by reading it back.

    The caller must enforce local same-origin access and an explicit user action.
    Neither subprocess receives the secret in its arguments or environment. All
    command output is captured and every failure returns a fixed public message.
    """
    if (not isinstance(payload, dict) or set(payload) != {"key"}
            or not isinstance(payload["key"], str) or not _KEY.fullmatch(payload["key"])):
        return {"ok": False, "message": "Enter the full Mubit key shown when it was created."}
    if sys.platform != "darwin":
        return {"ok": False, "message": "Private key saving requires macOS Keychain."}
    if not _SAVE_LOCK.acquire(blocking=False):
        return {"ok": False, "message": "Another key save is in progress. Wait for it to finish."}

    key = payload["key"]
    command = None
    written = None
    verified = None
    try:
        account = getpass.getuser()
        # security's interactive command parser receives the secret through a
        # pipe, so it never appears in process arguments, a file or a shell.
        command = " ".join(shlex.quote(value) for value in [
            "add-generic-password", "-U", "-a", account, "-s", SERVICE, "-w", key,
        ]) + "\n"
        written = subprocess.run(
            ["/usr/bin/security", "-i"], input=command.encode(),
            capture_output=True, timeout=_TIMEOUT_SECONDS, check=False, shell=False,
        )
        if written.returncode != 0:
            return {"ok": False, "message": "Keychain did not confirm the save. Please try again."}
        verified = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", account,
             "-s", SERVICE, "-w"],
            capture_output=True, timeout=_TIMEOUT_SECONDS, check=False, shell=False,
        )
        matches = (verified.returncode == 0 and isinstance(verified.stdout, bytes)
                   and hmac.compare_digest(verified.stdout.rstrip(b"\n"), key.encode()))
        if not matches:
            return {"ok": False, "message": "The saved key could not be confirmed. Please try again."}
        return {"ok": True, "message": "Key saved in macOS Keychain."}
    except Exception:
        # Timeout and command errors can include input or output containing the
        # key. Do not log, return or re-raise those exceptions.
        return {"ok": False, "message": "The saved key could not be confirmed. Please try again."}
    finally:
        key = command = written = verified = None
        _SAVE_LOCK.release()
