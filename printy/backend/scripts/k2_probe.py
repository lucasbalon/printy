"""Checks what a Creality K2 exposes on the local network, read-only.

Usage (from printy/backend):
    ../../.venv/Scripts/python scripts/k2_probe.py 192.168.1.50

It never sends a print command: it only reads the WebSocket for a few seconds,
asks for the printer parameters, and tries the camera and Moonraker URLs.
Send the whole output back so the driver can be adjusted.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

LISTEN_S = 15
INTERESTING = ("state", "deviceState", "nozzleTemp", "bedTemp0", "printProgress", "printFileName", "printLeftTime", "model", "hostname")


async def probe_ws(host: str) -> None:
    print(f"\n== WebSocket ws://{host}:9999 ==")
    try:
        async with websockets.connect(f"ws://{host}:9999", open_timeout=5, ping_interval=None) as ws:
            print("connexion OK")
            await ws.send(json.dumps({"method": "get", "params": {"reqPrinterPara": 1}}))
            keys: set[str] = set()
            sample: dict = {}
            loop = asyncio.get_running_loop()
            end = loop.time() + LISTEN_S
            while loop.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=end - loop.time())
                except TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    print(f"  message non JSON : {str(raw)[:80]!r}")
                    continue
                if isinstance(msg, dict):
                    if msg.get("ModeCode") == "heart_beat":
                        await ws.send("ok")
                        continue
                    keys.update(msg)
                    sample.update(msg)
            print(f"{len(keys)} champs reçus en {LISTEN_S} s")
            print("champs utiles :", {k: sample.get(k) for k in INTERESTING})
            print("tous les champs :", sorted(keys))
    except Exception as exc:  # noqa: BLE001
        print(f"ÉCHEC : {exc!r}")


async def probe_http(host: str) -> None:
    checks = [
        ("Caméra (instantané)", f"http://{host}:8080/?action=snapshot"),
        ("Moonraker (envoi des fichiers)", f"http://{host}:7125/server/info"),
        ("Interface web", f"http://{host}/"),
        ("Fluidd / Mainsail", f"http://{host}:4408/"),
    ]
    async with httpx.AsyncClient(timeout=6) as http:
        for label, url in checks:
            print(f"\n== {label} : {url} ==")
            try:
                r = await http.get(url)
                ctype = r.headers.get("content-type", "")
                print(f"HTTP {r.status_code}, {ctype}, {len(r.content)} octets")
                if "json" in ctype:
                    print(r.text[:300])
            except Exception as exc:  # noqa: BLE001
                print(f"ÉCHEC : {exc!r}")


async def main(host: str) -> None:
    await probe_ws(host)
    await probe_http(host)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit("usage : python scripts/k2_probe.py <ip-de-la-k2>")
    asyncio.run(main(sys.argv[1]))
