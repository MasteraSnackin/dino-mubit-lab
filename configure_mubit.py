#!/usr/bin/env python3
"""User-run, private credential entry. Never accepts a key on the command line."""
import argparse
import getpass
import hmac
import json
import re
import shlex
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICE = "com.local-dino.mubit-api-key"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-only", action="store_true", help="Save a key before checking the available models")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        raise SystemExit("Open Configure Mubit.command in Terminal to enter the key privately.")
    print("Configure Mubit for the local dinosaur game")
    print("The key will be stored in macOS Keychain, not in the game files.")
    print("This setup does not make a model call or start gameplay.")
    model = None
    if not args.key_only:
        model = input("Exact model ID from Mubit Connect: ").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model) or "mbt_" in model:
            raise SystemExit("Enter a model ID, not an API key.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("Paste the Mubit inference key (hidden): ").strip()
        except getpass.GetPassWarning:
            raise SystemExit("Hidden input is unavailable. Use a normal Terminal window; nothing was saved.")
    if not re.fullmatch(r"mbt_[A-Za-z0-9_-]{8,2044}", key):
        raise SystemExit("That does not look like a Mubit key. Nothing was saved.")
    # Feed the key over stdin so it does not appear in process arguments.
    command = " ".join(shlex.quote(value) for value in [
        "add-generic-password", "-U", "-a", getpass.getuser(),
        "-s", SERVICE, "-w", key,
    ]) + "\n"
    result = subprocess.run(["/usr/bin/security", "-i"],
                            input=command.encode(), capture_output=True)
    command = None
    verified = subprocess.run([
        "/usr/bin/security", "find-generic-password", "-a", getpass.getuser(),
        "-s", SERVICE, "-w",
    ], capture_output=True)
    matches = verified.returncode == 0 and hmac.compare_digest(verified.stdout.rstrip(b"\n"), key.encode())
    key = None
    result = None
    verified = None
    if not matches:
        raise SystemExit("Keychain did not save the key. No model request was sent.")
    if model:
        settings = ROOT / "mubit-settings.json"
        settings.write_text(json.dumps({"model": model}, indent=2) + "\n")
        settings.chmod(0o600)
    print("Key saved in Keychain. Reload the game and select Check connection.")
    print("Connection checks list available models; Play and Review send game data to Mubit.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
