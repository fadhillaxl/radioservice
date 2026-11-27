#!/usr/bin/env python3
"""
Web UI & API Server untuk Monitoring dan Konfigurasi Icom IC-9700 (Hamlib rigctld).
Menggunakan Python 3 Standard Library agar ringan dan bebas dependensi eksternal.
"""

import glob
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CONFIG_PATH = os.environ.get("IC9700_CONFIG_PATH", "/config/settings.json")
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_HOST = os.environ.get("WEB_BIND", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))


def detect_default_serial_port() -> str:
    """Deteksi otomatis symlink Port A IC-9700 dengan fallback ke /dev/ttyUSB0."""
    env_port = os.environ.get("RIG_PORT", "").strip()
    if env_port and os.path.exists(env_port):
        return env_port

    matches = sorted(glob.glob("/dev/serial/by-id/*IC-9700*A-if00-port0"))
    if matches:
        return matches[0]

    if os.path.exists("/dev/icom-ic9700-cat"):
        return "/dev/icom-ic9700-cat"

    if os.path.exists("/dev/ttyUSB0"):
        return "/dev/ttyUSB0"

    return "/dev/ttyUSB0"


DEFAULT_SETTINGS = {
    "rig_model": int(os.environ.get("RIG_MODEL", "3081")),
    "rig_baud": int(os.environ.get("RIG_BAUD", "115200")),
    "rig_port": os.environ.get("RIG_PORT", "") or "auto",
    "civ_addr": os.environ.get("CIV_ADDR", "0xA2"),
    "rigctld_bind": os.environ.get("RIGCTLD_BIND", "0.0.0.0"),
    "rigctld_port": int(os.environ.get("RIGCTLD_PORT", "4532")),
    "extra_args": os.environ.get("RIGCTLD_EXTRA_ARGS", ""),
}


class RigManager:
    """Mengelola proses rigctld, koneksi socket TCP ke rigctld, dan konfigurasi."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rig_socket_lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.logs = deque(maxlen=150)
        self.settings = self.load_settings()
        self.active_serial_port = ""
        self.started_at: float | None = None

    def log(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self.logs.append(line)

    def load_settings(self) -> dict:
        cfg = dict(DEFAULT_SETTINGS)
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
            except Exception as exc:
                print(f"[WARN] Gagal membaca {CONFIG_PATH}: {exc}", flush=True)
        return cfg

    def save_settings(self, new_settings: dict) -> dict:
        with self.lock:
            updated = dict(self.settings)
            if "rig_model" in new_settings:
                updated["rig_model"] = int(new_settings["rig_model"])
            if "rig_baud" in new_settings:
                updated["rig_baud"] = int(new_settings["rig_baud"])
            if "rig_port" in new_settings:
                updated["rig_port"] = str(new_settings["rig_port"]).strip() or "auto"
            if "civ_addr" in new_settings:
                updated["civ_addr"] = str(new_settings["civ_addr"]).strip()
            if "rigctld_bind" in new_settings:
                updated["rigctld_bind"] = str(new_settings["rigctld_bind"]).strip() or "0.0.0.0"
            if "rigctld_port" in new_settings:
                updated["rigctld_port"] = int(new_settings["rigctld_port"])
            if "extra_args" in new_settings:
                updated["extra_args"] = str(new_settings["extra_args"]).strip()

            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2)

            self.settings = updated
            self.log(f"Konfigurasi disimpan ke {CONFIG_PATH}")
            return dict(self.settings)

    def resolve_serial_port(self) -> str:
        port_cfg = self.settings.get("rig_port", "auto")
        if port_cfg and port_cfg != "auto":
            return port_cfg
        return detect_default_serial_port()

    def build_rigctld_cmd(self) -> list[str]:
        serial_port = self.resolve_serial_port()
        self.active_serial_port = serial_port
        cmd = [
            "rigctld",
            "-m", str(self.settings["rig_model"]),
            "-r", serial_port,
            "-s", str(self.settings["rig_baud"]),
            "-T", str(self.settings["rigctld_bind"]),
            "-t", str(self.settings["rigctld_port"]),
        ]
        civ_addr = str(self.settings.get("civ_addr", "")).strip()
        if civ_addr and civ_addr.lower() != "default":
            cmd.extend(["-c", civ_addr])
        extra = str(self.settings.get("extra_args", "")).strip()
        if extra:
            cmd.extend(shlex.split(extra))
        return cmd

    def _consume_output(self, pipe):
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                self.log(f"[rigctld] {line.rstrip()}")
        except Exception:
            pass

    def start_rigctld(self) -> tuple[bool, str]:
        with self.lock:
            self._stop_unlocked()
            cmd = self.build_rigctld_cmd()
            if not os.path.exists(self.active_serial_port):
                msg = f"Port serial '{self.active_serial_port}' tidak ditemukan di /dev."
                self.log(f"[WARN] {msg}")
                return False, msg

            self.log(f"Menjalankan: {' '.join(cmd)}")
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                self.started_at = time.time()
                t = threading.Thread(
                    target=self._consume_output,
                    args=(self.process.stdout,),
                    daemon=True,
                )
                t.start()
            except FileNotFoundError:
                msg = "Binary 'rigctld' tidak ditemukan. Pastikan libhamlib-utils terinstal."
                self.log(f"[ERROR] {msg}")
                return False, msg
            except Exception as exc:
                msg = f"Gagal menjalankan rigctld: {exc}"
                self.log(f"[ERROR] {msg}")
                return False, msg

        time.sleep(0.4)
        if self.is_running():
            return True, f"rigctld berjalan (PID {self.process.pid}) pada port {self.settings['rigctld_port']}"
        return False, "rigctld berhenti sesaat setelah dijalankan. Periksa log dan koneksi USB radio."

    def _stop_unlocked(self):
        if self.process and self.process.poll() is None:
            self.log(f"Menghentikan rigctld (PID {self.process.pid})...")
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            except Exception:
                pass
        self.process = None
        self.started_at = None

    def stop_rigctld(self):
        with self.lock:
            self._stop_unlocked()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def test_vfo_cli(self, custom_settings: dict | None = None) -> dict:
        """Menjalankan tes `rigctl f` secara langsung (saat rigctld dihentikan sementara atau via TCP)."""
        cfg = dict(self.settings)
        if custom_settings:
            cfg.update(custom_settings)

        # Jika rigctld sedang jalan dan setting sama, uji lewat TCP rigctld agar tidak bentrok port serial
        if self.is_running() and not custom_settings:
            resp = self.query_rigctld("f")
            ok = resp.isdigit()
            return {
                "ok": ok,
                "method": "rigctld-tcp",
                "output": resp,
                "frequency_hz": int(resp) if ok else None,
            }

        port = cfg.get("rig_port", "auto")
        if not port or port == "auto":
            port = detect_default_serial_port()

        cmd = [
            "rigctl",
            "-m", str(cfg.get("rig_model", 3081)),
            "-r", str(port),
            "-s", str(cfg.get("rig_baud", 115200)),
        ]
        civ_addr = str(cfg.get("civ_addr", "")).strip()
        if civ_addr and civ_addr.lower() != "default":
            cmd.extend(["-c", civ_addr])
        cmd.append("f")

        was_running = self.is_running()
        if was_running:
            self.stop_rigctld()

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            out = (proc.stdout + "\n" + proc.stderr).strip()
            ok = proc.returncode == 0 and out.splitlines()[-1].strip().isdigit()
            freq = int(out.splitlines()[-1].strip()) if ok else None
            return {
                "ok": ok,
                "method": "rigctl-serial",
                "command": " ".join(cmd),
                "output": out,
                "frequency_hz": freq,
            }
        except Exception as exc:
            return {
                "ok": False,
                "method": "rigctl-serial",
                "command": " ".join(cmd),
                "output": str(exc),
                "frequency_hz": None,
            }
        finally:
            if was_running:
                self.start_rigctld()

    def query_rigctld(self, command: str, timeout: float = 1.5) -> str:
        """Kirim perintah ke rigctld via TCP 127.0.0.1:<port>."""
        port = int(self.settings.get("rigctld_port", 4532))
        with self.rig_socket_lock:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall((command.strip() + "\n").encode("ascii"))
                chunks = []
                while True:
                    try:
                        data = sock.recv(4096)
                        if not data:
                            break
                        chunks.append(data.decode("utf-8", errors="replace"))
                        joined = "".join(chunks)
                        # Kebanyakan perintah rigctld selesai dalam 1-2 baris atau RPRT
                        if joined.endswith("\n"):
                            # Untuk perintah 'm' (mode + passband), rigctld mengirim 2 baris
                            if command.strip() == "m" and joined.count("\n") < 2 and "RPRT" not in joined:
                                continue
                            break
                    except socket.timeout:
                        break
                return "".join(chunks).strip()

    def get_radio_telemetry(self) -> dict:
        """Membaca status VFO, frekuensi, mode, PTT, S-meter, power, AF, SQL dari rigctld."""
        telemetry = {
            "connected": False,
            "frequency_hz": None,
            "mode": None,
            "passband_hz": None,
            "vfo": None,
            "ptt": 0,
            "strength_db": None,
            "rfpower": None,
            "af_gain": None,
            "squelch": None,
            "swr": None,
            "error": None,
        }
        try:
            freq_raw = self.query_rigctld("f")
            if freq_raw.startswith("RPRT"):
                telemetry["error"] = f"rigctld mengembalikan {freq_raw}"
                return telemetry

            telemetry["connected"] = True
            if freq_raw.lstrip("-").isdigit():
                telemetry["frequency_hz"] = int(freq_raw)

            mode_raw = self.query_rigctld("m")
            mode_lines = [ln.strip() for ln in mode_raw.splitlines() if ln.strip()]
            if mode_lines and not mode_lines[0].startswith("RPRT"):
                telemetry["mode"] = mode_lines[0]
                if len(mode_lines) > 1 and mode_lines[1].lstrip("-").isdigit():
                    telemetry["passband_hz"] = int(mode_lines[1])

            vfo_raw = self.query_rigctld("v")
            if vfo_raw and not vfo_raw.startswith("RPRT"):
                telemetry["vfo"] = vfo_raw.splitlines()[0].strip()

            ptt_raw = self.query_rigctld("t")
            if ptt_raw and ptt_raw.splitlines()[0].strip().isdigit():
                telemetry["ptt"] = int(ptt_raw.splitlines()[0].strip())

            def read_float(cmd: str) -> float | None:
                raw = self.query_rigctld(cmd)
                first = raw.splitlines()[0].strip() if raw else ""
                try:
                    return float(first)
                except ValueError:
                    return None

            telemetry["strength_db"] = read_float("l STRENGTH")
            telemetry["rfpower"] = read_float("l RFPOWER")
            telemetry["af_gain"] = read_float("l AF")
            telemetry["squelch"] = read_float("l SQL")
            telemetry["swr"] = read_float("l SWR")
        except Exception as exc:
            telemetry["connected"] = False
            telemetry["error"] = str(exc)

        return telemetry


def get_hardware_status() -> dict:
    """Mendeteksi port serial, kartu ALSA, dan perangkat USB IC-9700."""
    # 1. Serial ports
    serial_ports = []
    seen = set()
    for pattern in [
        "/dev/serial/by-id/*",
        "/dev/icom-ic9700-*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    ]:
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            serial_ports.append({
                "path": path,
                "realpath": real,
                "is_ic9700_port_a": bool(re.search(r"IC-9700.*A-if00-port0|icom-ic9700-cat", path)),
            })
            seen.add(path)

    # 2. ALSA cards
    alsa_cards = []
    if os.path.exists("/proc/asound/cards"):
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8") as f:
                content = f.read()
            for line in content.splitlines():
                m = re.match(r"^\s*(\d+)\s+\[([^\]]+)\]:\s+(.+)$", line)
                if m:
                    card_num, card_id, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
                    alsa_cards.append({
                        "card_num": int(card_num),
                        "card_id": card_id,
                        "description": desc,
                        "device": f"plughw:{card_num},0",
                        "is_codec": ("codec" in desc.lower() or "usb-audio" in desc.lower()),
                    })
        except Exception:
            pass

    # 3. USB VID:PID detection
    usb_uart_present = False
    usb_audio_present = False
    usb_devices = []
    try:
        proc = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=2)
        for line in proc.stdout.splitlines():
            if "10c4:ea60" in line.lower():
                usb_uart_present = True
                usb_devices.append(line.strip())
            elif "08bb:2901" in line.lower():
                usb_audio_present = True
                usb_devices.append(line.strip())
    except Exception:
        pass

    return {
        "serial_ports": serial_ports,
        "detected_port_a": detect_default_serial_port(),
        "alsa_cards": alsa_cards,
        "usb_uart_10c4_ea60": usb_uart_present,
        "usb_audio_08bb_2901": usb_audio_present,
        "usb_devices": usb_devices,
    }


MANAGER = RigManager()


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Kurangi spam log untuk polling /api/status
        if "/api/status" not in self.path:
            super().log_message(fmt, *args)

    def _send_json(self, data: dict, status: int = HTTPStatus.OK):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            index_file = os.path.join(STATIC_DIR, "index.html")
            try:
                with open(index_file, "rb") as f:
                    content = f.read()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND, "index.html tidak ditemukan")
            return

        if path == "/api/status":
            hw = get_hardware_status()
            running = MANAGER.is_running()
            telemetry = MANAGER.get_radio_telemetry() if running else {
                "connected": False,
                "error": "Proses rigctld sedang tidak berjalan",
            }
            self._send_json({
                "service": {
                    "running": running,
                    "pid": MANAGER.process.pid if running and MANAGER.process else None,
                    "active_serial_port": MANAGER.active_serial_port or MANAGER.resolve_serial_port(),
                    "uptime_sec": int(time.time() - MANAGER.started_at) if (running and MANAGER.started_at) else 0,
                },
                "settings": MANAGER.settings,
                "radio": telemetry,
                "hardware": hw,
                "logs": list(MANAGER.logs)[-50:],
            })
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint tidak ditemukan")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            payload = self._read_json_body()
        except Exception as exc:
            self._send_json({"ok": False, "error": f"JSON tidak valid: {exc}"}, HTTPStatus.BAD_REQUEST)
            return

        # 1. Kontrol radio langsung (Frekuensi, Mode, VFO, PTT, Level, Raw Command)
        if path == "/api/radio/control":
            action = payload.get("action")
            try:
                if action == "set_freq":
                    freq_hz = int(payload["frequency_hz"])
                    resp = MANAGER.query_rigctld(f"F {freq_hz}")
                    ok = "RPRT 0" in resp or resp == ""
                    self._send_json({"ok": ok, "response": resp, "frequency_hz": freq_hz})
                    return

                if action == "set_mode":
                    mode = str(payload["mode"]).upper().strip()
                    passband = int(payload.get("passband_hz", 0))
                    if not re.match(r"^[A-Z0-9]+$", mode):
                        raise ValueError("Nama mode tidak valid")
                    resp = MANAGER.query_rigctld(f"M {mode} {passband}")
                    ok = "RPRT 0" in resp or resp == ""
                    self._send_json({"ok": ok, "response": resp})
                    return

                if action == "set_vfo":
                    vfo = str(payload["vfo"]).strip()
                    if not re.match(r"^[A-Za-z0-9]+$", vfo):
                        raise ValueError("Nama VFO tidak valid")
                    resp = MANAGER.query_rigctld(f"V {vfo}")
                    ok = "RPRT 0" in resp or resp == ""
                    self._send_json({"ok": ok, "response": resp})
                    return

                if action == "set_ptt":
                    ptt = 1 if int(payload.get("ptt", 0)) else 0
                    resp = MANAGER.query_rigctld(f"T {ptt}")
                    ok = "RPRT 0" in resp or resp == ""
                    self._send_json({"ok": ok, "response": resp, "ptt": ptt})
                    return

                if action == "set_level":
                    level_name = str(payload["level"]).upper().strip()
                    if level_name not in ("RFPOWER", "AF", "SQL", "MICGAIN", "RF"):
                        raise ValueError(f"Level '{level_name}' tidak didukung")
                    val = float(payload["value"])
                    val = max(0.0, min(1.0, val))
                    resp = MANAGER.query_rigctld(f"L {level_name} {val:.3f}")
                    ok = "RPRT 0" in resp or resp == ""
                    self._send_json({"ok": ok, "response": resp})
                    return

                if action == "raw":
                    cmd = str(payload.get("command", "")).strip()
                    if not cmd:
                        raise ValueError("Perintah kosong")
                    resp = MANAGER.query_rigctld(cmd)
                    self._send_json({"ok": True, "response": resp})
                    return

                self._send_json({"ok": False, "error": f"Action '{action}' tidak dikenal"}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # 2. Update settings & opsional restart rigctld
        if path == "/api/settings":
            try:
                saved = MANAGER.save_settings(payload)
                restart = bool(payload.get("restart", True))
                msg = "Pengaturan disimpan."
                ok = True
                if restart:
                    ok, rmsg = MANAGER.start_rigctld()
                    msg = f"{msg} {rmsg}"
                self._send_json({"ok": ok, "message": msg, "settings": saved})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        # 3. Kontrol proses rigctld (start / stop / restart / test_vfo)
        if path == "/api/service":
            action = payload.get("action", "restart")
            if action in ("start", "restart"):
                ok, msg = MANAGER.start_rigctld()
                self._send_json({"ok": ok, "message": msg})
                return
            if action == "stop":
                MANAGER.stop_rigctld()
                self._send_json({"ok": True, "message": "rigctld dihentikan."})
                return
            if action == "test_vfo":
                res = MANAGER.test_vfo_cli(payload.get("settings"))
                self._send_json(res)
                return

            self._send_json({"ok": False, "error": f"Action '{action}' tidak dikenal"}, HTTPStatus.BAD_REQUEST)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint tidak ditemukan")


def main():
    MANAGER.log("Memulai supervisor rigctld & Web UI Icom IC-9700...")
    MANAGER.start_rigctld()

    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), RequestHandler)
    MANAGER.log(f"Web UI Monitoring & Settings aktif di http://{WEB_HOST}:{WEB_PORT}")

    def handle_sig(signum, _frame):
        MANAGER.log(f"Menerima sinyal {signum}, menghentikan layanan...")
        MANAGER.stop_rigctld()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_sig)
    signal.signal(signal.SIGINT, handle_sig)

    try:
        server.serve_forever()
    finally:
        MANAGER.stop_rigctld()


if __name__ == "__main__":
    main()
