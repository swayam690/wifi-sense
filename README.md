# WiFi Sense 📡

A local, non-root, scientific instrumentation application for experimental Wi-Fi time-series signal sensing on Linux (Parrot OS / Debian).

Designed specifically for exploring ambient radio frequency fluctuations, movement correlations, and hardware diagnostics without requiring root privileges, external hardware, or paid cloud APIs.

---

## ⚠️ Hardware Limitations & Academic Honesty

- **Target Adapter**: Intel Centrino Wireless-N 2230 (`8086:0887`), driven by the standard upstream Linux kernel `iwlwifi` driver.
- **Signal Metric**: Operates strictly on **RSSI** (Received Signal Strength Indication, measured in dBm) and link metadata polled via `nmcli` and `iw dev <iface> link`.
- **CSI (Channel State Information)**: **Unavailable**. The standard `iwlwifi` kernel driver does **NOT** expose OFDM subcarrier channel matrices. While monitor mode is supported by the physical interface, packet capture in monitor mode does not yield CSI without out-of-tree custom research kernels/firmware (e.g., Linux 802.11n CSI Tool on legacy Linux 3.x/4.x).
- **Presence, Breathing & Heartbeat**: Marked as **Unvalidated** or **Unavailable**. Detecting fine sub-millimeter chest displacement (~0.2–0.4 Hz) or heartbeats via Wi-Fi requires high-resolution subcarrier phase data (>100 Hz CSI). Presenting standard RSSI fluctuations as heartbeat or vital sign detection is scientifically invalid and strictly avoided in this application.

---

## Features

1. **Dark Scientific Dashboard**:
   - High-contrast, responsive interface inspired by laboratory test instruments.
   - Zero external CDN or NPM dependencies (completely self-contained HTML/CSS/JS).
2. **Live Signal Stream**:
   - Real-time time-series chart of RSSI (dBm) drawn on HTML5 Canvas.
   - Dual-channel telemetry: WebSocket stream (`/ws`) with automatic HTTP polling fallback.
3. **Controlled Experiment Manager**:
   - Configurable sampling intervals (200ms to 5000ms).
   - Multi-phase experimentation (`Baseline`, `Movement`, `Custom`) to correlate physical actions against RF measurements.
   - Timestamped user annotations mapped directly to sample timestamps.
4. **Statistical Analysis & Signal Processing**:
   - NumPy-powered processing pipeline:
     - Moving average smoothing with forward-fill.
     - Linear detrending (polynomial fit residual analysis).
     - Rolling sample variance.
     - Dynamic threshold change-point detection.
5. **Session Data Export**:
   - Full CSV export with timestamps, RSSI, noise, bitrates, phase labels, and annotations.
6. **Hardware Diagnostics Page**:
   - Automatic interface discovery, PCI ID extraction, driver inspection, and RF capability reporting.

---

## Architecture & File Structure

```text
wifiseethrough/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application, REST endpoints, WebSocket
│   ├── models.py            # Pydantic schemas, Enums, Dataclasses
│   ├── collector.py         # Non-root WiFi interface discovery & telemetry (iw/nmcli)
│   ├── signal_processing.py # NumPy statistical & signal analysis pipeline
│   ├── experiment.py        # Experiment session runner & CSV generator
│   └── frontend/
│       └── index.html       # Standalone dark-theme dashboard UI
├── tests/
│   ├── __init__.py
│   ├── test_collector.py
│   ├── test_signal_processing.py
│   ├── test_experiment.py
│   └── test_api.py
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Setup & Installation

### Prerequisites
- Linux OS (tested on Parrot OS / Debian 12)
- Python 3.11+
- Wireless networking tools: `iw`, `nmcli`, `iwconfig` (standard on Parrot OS)

### 1. Set Up Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

*(Optional: If virtualenv creation is skipped, the app also runs using Debian system python packages: `python3-fastapi`, `python3-uvicorn`, `python3-numpy`, `python3-pydantic`).*

### 2. Run the Application
```bash
source venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open your browser to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## Running Tests

Execute the automated test suite:
```bash
source venv/bin/activate
pytest tests/ -v
```

---

## REST API Overview

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Dashboard User Interface |
| `GET` | `/api/status` | Active Wi-Fi interface, SSID, BSSID, RSSI, freq, bitrates |
| `GET` | `/api/signal/current` | Most recent collected sample |
| `GET` | `/api/signal/history?last_n=300` | Historical signal time series |
| `POST` | `/api/experiment/start` | Start logging session (`sampling_interval_ms`, `phase`) |
| `POST` | `/api/experiment/stop` | End session and compute summary metrics |
| `GET` | `/api/experiment/stats` | Session metrics (mean, std dev, min, max, missing) |
| `GET` | `/api/experiment/export` | Download session telemetry as CSV |
| `POST` | `/api/experiment/annotate` | Log an event annotation (e.g. "Subject walked past") |
| `POST` | `/api/experiment/phase` | Switch phase (`baseline` vs `movement`) |
| `GET` | `/api/analysis` | Run detrending, smoothing, variance, & change point detection |
| `GET` | `/api/diagnostics` | Hardware report (PCI ID, driver, monitor mode, CSI status) |
| `GET` | `/api/features` | Research feature feasibility cards |
| `WS` | `/ws` | Real-time WebSocket telemetry stream |

---

## Experimental Protocol Guide

To test whether ambient movement correlates with RSSI changes:
1. Ensure the laptop is stationary on a desk connected to the 2.4 GHz network (`Airtel_sand_4040`).
2. Set Sampling Interval to `1000ms` (or `500ms`).
3. Click **Start Experiment** with Phase set to **Baseline**. Remain motionless or keep the room empty for 60 seconds.
4. Switch Phase to **Movement** and click **Add Annotation** with "Walking in Line of Sight". Walk across the direct line between the router and laptop.
5. Click **Add Annotation** with "Subject seated".
6. Click **Stop Experiment** and open **Signal Analysis** to inspect rolling variance and detected change points.
7. Click **Export CSV** to download raw data for external evaluation.
