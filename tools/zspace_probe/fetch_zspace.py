"""
ZSpace Web API probe (LOCAL ONLY — credentials never leave this machine)

Usage:
  pip install requests
  python tools/zspace_probe/fetch_zspace.py

The script will prompt for username / password at runtime (or read from
environment variables ZSPACE_USER / ZSPACE_PASS). All API responses are
written to tools/zspace_probe/zspace_dumps/ as pretty-printed JSON.

After the script finishes, review the dumps, redact any token fields, and
share the redacted JSON with your assistant.
"""

from __future__ import annotations

import getpass
import json
import os
import ssl
import sys
import time
import urllib3
from pathlib import Path

import requests

# Silence the urllib3 InsecureRequestWarning so the console is not flooded.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://192-168-101-12.z423-8ou2.direct.zspace.link:5056"
DUMP_DIR = Path(__file__).parent / "zspace_dumps"
DUMP_DIR.mkdir(exist_ok=True)

SENSITIVE_KEYS = {
    "token", "rtoken", "atoken", "btoken",
    "password", "pwd", "passwd",
    "device_id", "rdeviceid", "rdevice", "ruser",
    "secret", "session", "cookie",
}


def redact(obj):
    """Replace values for sensitive keys with REDACTED placeholders."""
    if isinstance(obj, dict):
        return {
            k: ("<REDACTED>" if k.lower() in SENSITIVE_KEYS else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def save_dump(name: str, payload):
    out = DUMP_DIR / f"{name}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  -> {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


def dump_raw(name: str, response: requests.Response):
    try:
        body = response.json()
    except ValueError:
        body = {"_raw": response.text}
    save_dump(name, {
        "request": {
            "method": response.request.method,
            "url": response.request.url,
            "headers": {k: v for k, v in response.request.headers.items()
                        if k.lower() not in {"cookie", "authorization"}},
        },
        "status": response.status_code,
        "body": body,
    })


def main():
    user = os.environ.get("ZSPACE_USER")
    pwd = os.environ.get("ZSPACE_PASS")
    if not user:
        user = input("ZSpace username: ").strip()
    if not pwd:
        pwd = getpass.getpass("ZSpace password: ")

    session = requests.Session()
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (ZSpaceProbe/1.0)",
        "Referer": f"{BASE}/home/",
        "Origin": BASE,
    })

    print(f"\n[1/5] POST {BASE}/auth/login")
    r = session.post(f"{BASE}/auth/login", json={
        "username": user,
        "password": pwd,
    }, timeout=10)
    dump_raw("01_login", r)
    if r.json().get("code") not in {"N000000", "N000200", "00000", "0"}:
        print("Login failed — see 01_login.json. Adjust credentials and rerun.")
        return 1

    data = r.json().get("data") or {}
    token = data.get("token") or data.get("rtoken")
    device_id = data.get("device_id") or data.get("rdeviceid")
    rdevice = data.get("rdevice") or data.get("device") or "web"
    ruser = data.get("username") or data.get("ruser") or user

    print(f"\n[2/5] POST {BASE}/system/polling2")
    r = session.post(f"{BASE}/system/polling2",
                     json={},
                     headers={"rtoken": token, "rdeviceid": device_id,
                              "rdevice": rdevice, "ruser": ruser},
                     timeout=8)
    dump_raw("02_polling2", r)

    print(f"\n[3/5] POST {BASE}/system/home")
    r = session.post(f"{BASE}/system/home", json={},
                     headers={"rtoken": token, "rdeviceid": device_id,
                              "rdevice": rdevice, "ruser": ruser},
                     timeout=8)
    dump_raw("03_system_home", r)

    print(f"\n[4/5] POST {BASE}/device/loginlist")
    r = session.post(f"{BASE}/device/loginlist", json={},
                     headers={"rtoken": token, "rdeviceid": device_id,
                              "rdevice": rdevice, "ruser": ruser},
                     timeout=8)
    dump_raw("04_loginlist", r)

    print(f"\n[5/5] POST {BASE}/fan/set  (mode query, do NOT actually change)")
    # Many ZSpace fan APIs return current state if you send a read-only
    # payload. If your firmware rejects this, just delete this block.
    r = session.post(f"{BASE}/fan/set", json={"mode": "auto"},
                     headers={"rtoken": token, "rdeviceid": device_id,
                              "rdevice": rdevice, "ruser": ruser},
                     timeout=8)
    dump_raw("05_fan_set", r)

    print(f"\nAll responses written to {DUMP_DIR}")
    print("Next: open the JSON files, redact sensitive fields (tokens, device_id),")
    print("then share the redacted content with your assistant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
