#!/usr/bin/env python3
from flask import Flask, jsonify, request
from flask_cors import CORS
import telnetlib
import threading
import time
import sqlite3
from datetime import datetime, timedelta


BGAN_IP = "192.168.0.2"
BGAN_PORT = 5454
TELNET_DEFAULT_TIMEOUT = 6.0
MIN_CMD_INTERVAL = 0.18
CMD_RETRY = 2
UNLOCK_CMD = "AT_iclck=AD,0,admin"
APP_HOST = "0.0.0.0"
APP_PORT = 5000
ACTIVE_CID = 1

# -----------------------------
# CACHE — to avoid flicker
# -----------------------------
CACHE = {
    "signal": None,
    "satellite_id": None,
    "satellite_name": None,
    "imei": None,
    "imsi": None,
    "network": None,
    "pdp_ip": None,
    "apn": []
}

def now_wib():
    return (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")

def get_db():
    conn = sqlite3.connect("signal_history.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal REAL,
            timestamp TEXT
        )
    """)
    conn.commit()


def _normalize_lines(raw_bytes):
    try:
        text = raw_bytes.decode(errors="ignore")
    except:
        text = str(raw_bytes)
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            lines.append(ln)
    return lines

class TelnetManager:
    def __init__(self, host, port, default_timeout=TELNET_DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.default_timeout = default_timeout
        self._lock = threading.Lock()
        self._tn = None
        self._last_cmd_ts = 0.0

    def _ensure_connected(self):
        if self._tn is not None:
            return
        tn = telnetlib.Telnet(self.host, self.port, timeout=self.default_timeout)
        time.sleep(0.06)
        self._tn = tn
        try:
            tn.write((UNLOCK_CMD + "\r\n").encode())
            time.sleep(0.06)
            _ = tn.read_very_eager()
        except:
            pass

    def _close(self):
        try:
            if self._tn:
                self._tn.close()
        finally:
            self._tn = None

    def send(self, cmd, expect_ok=True, timeout=None, min_interval=MIN_CMD_INTERVAL):
        if timeout is None: timeout = self.default_timeout
        cmd_str = cmd.strip()

        for attempt in range(CMD_RETRY + 1):
            with self._lock:
                elapsed = time.time() - self._last_cmd_ts
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)

                try:
                    if self._tn is None:
                        self._ensure_connected()
                except:
                    self._close()
                    continue

                try:
                    self._tn.write((cmd_str + "\r\n").encode())
                    deadline = time.time() + timeout
                    chunks = []

                    while time.time() < deadline:
                        try:
                            part = self._tn.read_very_eager()
                        except:
                            part = b""
                        if part:
                            chunks.append(part)
                            txt = b"".join(chunks).decode(errors="ignore").upper()
                            if expect_ok and ("OK" in txt or "ERROR" in txt):
                                break
                        else:
                            time.sleep(0.04)

                    raw_bytes = b"".join(chunks)
                    lines = _normalize_lines(raw_bytes)

                    filtered = []
                    cmd_upper = cmd_str.upper()
                    for ln in lines:
                        if ln.upper() == cmd_upper: continue
                        if ln.upper().startswith(cmd_upper): continue
                        filtered.append(ln)

                    self._last_cmd_ts = time.time()
                    return filtered
                except:
                    self._close()

        return [""]

_telnet_mgr = TelnetManager(BGAN_IP, BGAN_PORT)
app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {
        "origins": [
            "https://bgan-m2m-dashboard.vercel.app",
            "http://localhost:3000"
        ]
    }}
)


NETWORK_STATUS = {
    0: "Not registered",
    1: "Registered (home)",
    2: "Searching",
    3: "Registration denied",
    4: "Unknown",
    5: "Roaming",
}

@app.route("/api/m2m/signal")
def api_signal():
    raw = _telnet_mgr.send("AT_ISIG=1", timeout=5)

    strength = None
    for ln in raw:
        if "_ISIG" in ln.upper():
            try:
                token = ln.split(":", 1)[1].strip().split()[0]
                if not token.startswith("("):
                    strength = float(token)
            except:
                pass

    if strength is not None:
        CACHE["signal"] = strength

        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO signal_history (signal, timestamp) VALUES (?, ?)",
                (strength, now_wib())
            )
            conn.commit()
        except Exception as e:
            print("DB insert error:", e)

    return jsonify({
        "signal_strength": CACHE["signal"],
        "timestamp": now_wib()
    })


@app.route("/api/m2m/signal-history")
def api_signal_history():
    limit = int(request.args.get("limit",50))

    conn = get_db()
    rows = conn.execute(
        "SELECT signal, timestamp FROM signal_history ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()

    data = [
        {"signal": r ["signal"], "timestamp": r["timestamp"]}
        for r in reversed(rows)
    ]

    return jsonify(data)

@app.route("/api/m2m/satellite")
def api_satellite():
    raw = _telnet_mgr.send("AT_ISATCUR?", timeout=5)
    sid = None
    for ln in raw:
        if "_ISATCUR" in ln.upper() and ":" in ln:
            try:
                sid = int(ln.split(":", 1)[1].strip())
            except:
                pass
    SATELLITE_MAP = {
        1: "IOR (Indian Ocean West)",
        2: "IOR2 (Indian Ocean Central)",
        3: "IOE (Indian Ocean East)",
        4: "POR",
        5: "AOR",
    }
    if sid is not None:
        CACHE["satellite_id"] = sid
        CACHE["satellite_name"] = SATELLITE_MAP.get(sid, "-")

    return jsonify({
        "satellite_id": CACHE["satellite_id"],
        "satellite_name": CACHE["satellite_name"],
        "timestamp": now_wib()
    })

@app.route("/api/m2m/imei")
def api_imei():
    raw = _telnet_mgr.send("AT+CGSN", timeout=6)
    imei = None
    for ln in raw:
        if ln.isdigit() and len(ln) >= 10:
            imei = ln
    if imei: CACHE["imei"] = imei
    return jsonify({"imei": CACHE["imei"], "timestamp": now_wib()})

@app.route("/api/m2m/imsi")
def api_imsi():
    raw = _telnet_mgr.send("AT+CIMI", timeout=6)
    imsi = None
    for ln in raw:
        if ln.isdigit() and len(ln) >= 10:
            imsi = ln
    if imsi: CACHE["imsi"] = imsi
    return jsonify({"imsi": CACHE["imsi"], "timestamp": now_wib()})

@app.route("/api/m2m/network")
def api_network():
    raw = _telnet_mgr.send("AT+CREG?", timeout=5)
    stat = None
    for ln in raw:
        if "+CREG" in ln.upper():
            try:
                parts = ln.split(",")
                stat = int(parts[1].strip())
            except:
                pass
    label = f"{stat} - {NETWORK_STATUS.get(stat, 'Unknown')}" if stat is not None else CACHE["network"]
    if stat is not None:
        CACHE["network"] = label
    return jsonify({"status_text": CACHE["network"], "timestamp": now_wib()})

@app.route("/api/m2m/apn")
def api_apn():
    raw = _telnet_mgr.send("AT+CGDCONT?", timeout=6)
    profiles = []
    for ln in raw:
        if "+CGDCONT" not in ln: continue
        clean = ln.replace(" ", "")
        try:
            _, payload = clean.split(":", 1)
            parts = [p.replace('"', '') for p in payload.split(",")]
            profiles.append({"cid": parts[0], "type": parts[1], "apn": parts[2], "address": parts[3]})
        except:
            pass
    if profiles:
        CACHE["apn"] = profiles

    return jsonify({"profiles": CACHE["apn"], "timestamp": now_wib()})

@app.route("/api/m2m/pdp-status")
def api_pdp_status():
    # 1. Check PDP active
    raw = _telnet_mgr.send("AT+CGACT?", timeout=6)

    pdp_active = False
    for ln in raw:
        if "+CGACT" in ln.upper():
            try:
                cid, state = ln.split(":")[1].split(",")
                if int(cid.strip()) == ACTIVE_CID and int(state.strip()) == 1:
                    pdp_active = True
            except:
                pass

    ip = None

    # 2. If PDP active, retry CGPADDR (IMPORTANT)
    if pdp_active:
        for _ in range(3):  # retry max 3x
            res = _telnet_mgr.send(f"AT+CGPADDR={ACTIVE_CID}", timeout=6)
            for ln in res:
                if "+CGPADDR" in ln.upper():
                    parts = ln.replace('"', '').split(",")
                    if len(parts) > 1:
                        candidate = parts[1].strip()
                        if candidate and candidate != "0.0.0.0":
                            ip = candidate
                            break
            if ip:
                break
            time.sleep(1.0)  # kasih waktu modem assign IP

    # 3. Cache ONLY valid IP
    if ip:
        CACHE["pdp_ip"] = ip

    return jsonify({
        "pdp_active": pdp_active,
        "ip": CACHE.get("pdp_ip"),
        "timestamp": now_wib()
    })


@app.route("/api/m2m/pdp-activate")
def api_pdp_activate():
    # Turn off first (ensure clean restart)
    _telnet_mgr.send(f"AT+CGACT=0,{ACTIVE_CID}", timeout=6)
    time.sleep(0.8)

    # Activate
    _telnet_mgr.send(f"AT+CGACT=1,{ACTIVE_CID}", timeout=10)

    # Validate IP (not OK text)
    ip = None
    for _ in range(10):
        res = _telnet_mgr.send(f"AT+CGPADDR={ACTIVE_CID}", timeout=6)

        for ln in res:
            if "+CGPADDR" in ln.upper():
                parts = ln.split(",")
                if len(parts) >= 2:
                    candidate = parts[1].replace('"', '').strip()
                    if candidate and candidate != "0.0.0.0":
                        ip = candidate

        if ip:
            CACHE["pdp_ip"] = ip
            return jsonify({
                "success": True,
                "status": "active",
                "ip": ip,
                "auth_success": True,
                "timestamp": now_wib()
            })

        time.sleep(1)

    # Failed
    CACHE["pdp_ip"] = None
    return jsonify({
        "success": False,
        "status": "inactive",
        "ip": None,
        "auth_success": False,
        "message": "PDP activation failed (APN authentication error)",
        "timestamp": now_wib()
    }), 400


@app.route("/api/m2m/pdp-deactivate", methods=["POST"])
def api_pdp_deactivate():
    _telnet_mgr.send(f"AT+CGACT=0,{ACTIVE_CID}", timeout=6)

    # kosongkan IP biar UI tau PDP mati
    CACHE["pdp_ip"] = None

    return jsonify({
        "success": True,
        "status": "inactive",
        "ip": None,
        "timestamp": now_wib()
    })



@app.route("/api/m2m/apn", methods=["POST"])
def api_apn_set():
    data = request.json or {}
    apn = data.get("apn")
    user = data.get("user")
    password = data.get("pass")

    if not apn or not user or not password:
        return jsonify({"error": "apn, user, pass required"}), 400

    # === STEP 1: MATIKAN PDP DULU (important)
    _telnet_mgr.send(f"AT+CGACT=0,{ACTIVE_CID}", timeout=6)
    time.sleep(0.5)

    # === STEP 2: SET APN BARU (tanpa hidupkan PDP)
    cmd = f'AT+CGDCONT={ACTIVE_CID},"IP","{apn}",0.0.0.0,0,0,"{user}","{password}"'
    _telnet_mgr.send(cmd, timeout=8)

    # === Clear cache PDP, supaya UI tidak baca IP lama
    CACHE["pdp_ip"] = None

    return jsonify({
        "success": True,
        "saved": True,
        "message": "APN saved successfully. PDP is OFF. Press Activate PDP.",
        "timestamp": now_wib()
    }), 200

if __name__ == "__main__":
    print("BGAN M2M backend running...")
    init_db()
    app.run(host=APP_HOST, port=APP_PORT)
