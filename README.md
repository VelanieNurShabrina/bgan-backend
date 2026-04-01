# BGAN M2M Backend Monitoring System

This project is a backend system for monitoring BGAN M2M communication using AT Commands via Telnet connection.

---

## Features

### Signal Monitoring

* Detect signal strength using `AT_ISIG`
* Store signal data into SQLite database
* Provide signal history for visualization (chart)

### Satellite Monitoring

* Detect current satellite using `AT_ISATCUR`
* Map satellite ID into readable satellite name

### Device Monitoring

* Retrieve IMEI using `AT+CGSN`
* Retrieve IMSI using `AT+CIMI`

### Network Monitoring

* Detect network registration status using `AT+CREG`
* Convert status into human-readable format (home, roaming, searching, etc.)

### APN Configuration

* Retrieve APN profiles using `AT+CGDCONT?`
* Configure APN with authentication (APN, username, password)

### PDP Monitoring

* Detect PDP status using `AT+CGACT?`
* Activate PDP connection
* Deactivate PDP connection
* Retrieve IP address using `AT+CGPADDR`
* Validate IP (ensure not `0.0.0.0`)

---

## System Features

* Telnet-based communication with BGAN modem
* Auto reconnect mechanism for Telnet connection
* Command retry mechanism for reliability
* Response parsing & filtering
* Caching system to prevent UI flickering
* SQLite database for signal history logging

---

## Tech Stack

* Python
* Flask
* Telnet (via `telnetlib`)
* SQLite

---

## Project Structure

* `isat_m2m.py` → Main backend service (API + logic)
* `signal_history.db` → SQLite database (auto-generated)

---

## How to Run

```bash
pip install -r requirements.txt
python isat_m2m.py
```

---

## PDP Validation Logic

A PDP connection is considered **SUCCESS** if:

* PDP is active (`AT+CGACT = 1`)
* A valid IP address is assigned
* The IP is not `0.0.0.0`

A PDP connection is considered **FAILED** if:

* PDP is not active
* No IP is assigned
* IP remains `0.0.0.0` after retries

---

## Notes

* Signal data is stored locally in SQLite database (`signal_history.db`)
* Caching is used to stabilize frontend display
* PDP activation uses retry mechanism to ensure IP assignment
* Designed to integrate with React-based monitoring dashboard

---

## Author

Velanie Nur Shabrina
