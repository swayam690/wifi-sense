"""WiFi Sense — Main FastAPI application."""
import os
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    ExperimentConfig, AnnotationRequest, ExperimentPhase,
    FeatureCard, FeatureStatus
)
from app.collector import get_wifi_status, get_hardware_diagnostics
from app.experiment import ExperimentSession
from app.signal_processing import analyze_signal

app = FastAPI(title="WiFi Sense", version="1.0.0")

# Serve static frontend
FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Global experiment session
session = ExperimentSession()

# WebSocket connections for live updates
ws_clients: list[WebSocket] = []


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main dashboard page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>WiFi Sense</h1><p>Frontend not found. Place index.html in app/frontend/</p>")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Favicon endpoint to avoid 404s."""
    return PlainTextResponse("", status_code=204)


@app.get("/api/status")
async def api_status():
    """Get current WiFi connection status."""
    status = await asyncio.get_event_loop().run_in_executor(None, get_wifi_status)
    return status.model_dump(mode="json")


@app.get("/api/signal/current")
async def api_signal_current():
    """Get the most recent signal sample."""
    if session.samples:
        return session.samples[-1].model_dump(mode="json")
    # Return a fresh reading
    status = await asyncio.get_event_loop().run_in_executor(None, get_wifi_status)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rssi_dbm": status.signal_dbm,
        "connection_status": status.connection_status.value,
        "missing": status.signal_dbm is None
    }


@app.get("/api/signal/history")
async def api_signal_history(
    last_n: Optional[int] = Query(default=None, ge=1, le=36000),
    limit: Optional[int] = Query(default=None, ge=1, le=36000)
):
    """Get signal sample history."""
    n = limit if limit is not None else (last_n or 300)
    samples = session.get_history(last_n=n)
    return {
        "samples": [s.model_dump(mode="json") for s in samples],
        "count": len(samples)
    }


@app.post("/api/experiment/start")
async def api_experiment_start(config: Optional[ExperimentConfig] = None):
    """Start a new experiment session."""
    if config is None:
        config = ExperimentConfig()
    if session.is_running:
        return JSONResponse(
            status_code=409,
            content={"error": "Experiment already running", "session_id": session.session_id}
        )
    await session.start(config, get_wifi_status)
    return {
        "status": "started",
        "session_id": session.session_id,
        "sampling_interval_ms": config.sampling_interval_ms,
        "phase": config.phase.value
    }


@app.post("/api/experiment/stop")
async def api_experiment_stop():
    """Stop the current experiment session."""
    if not session.is_running:
        return JSONResponse(
            status_code=409,
            content={"error": "No experiment running"}
        )
    await session.stop()
    stats = session.get_stats()
    return {
        "status": "stopped",
        "stats": stats.model_dump(mode="json")
    }


@app.get("/api/experiment/stats")
async def api_experiment_stats():
    """Get current experiment statistics."""
    return session.get_stats().model_dump(mode="json")


@app.get("/api/experiment/export")
async def api_experiment_export():
    """Export experiment data as CSV."""
    if not session.samples:
        return JSONResponse(
            status_code=404,
            content={"error": "No data to export. Run an experiment first."}
        )
    csv_data = session.export_csv()
    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=wifisense_{session.session_id}.csv"}
    )


@app.post("/api/experiment/annotate")
async def api_experiment_annotate(req: AnnotationRequest):
    """Add an annotation to the current experiment."""
    if not session.is_running:
        return JSONResponse(
            status_code=409,
            content={"error": "No experiment running"}
        )
    session.add_annotation(req.text)
    if req.phase != session.current_phase:
        session.set_phase(req.phase)
    return {"status": "annotated", "text": req.text, "phase": req.phase.value}


@app.post("/api/experiment/phase")
async def api_experiment_phase(config: ExperimentConfig):
    """Change experiment phase."""
    if not session.is_running:
        session.current_phase = config.phase
        return {"status": "phase_staged", "phase": config.phase.value}
    session.set_phase(config.phase, config.annotation)
    return {"status": "phase_changed", "phase": config.phase.value}


@app.get("/api/analysis")
async def api_analysis():
    """Run signal analysis on collected data."""
    if len(session.samples) < 3:
        return JSONResponse(
            status_code=404,
            content={"error": "Not enough data for analysis. Collect at least 3 samples."}
        )
    analysis = analyze_signal(session.samples)
    return analysis.model_dump(mode="json")


@app.get("/api/diagnostics")
async def api_diagnostics():
    """Get hardware diagnostics and capability report."""
    diag = await asyncio.get_event_loop().run_in_executor(None, get_hardware_diagnostics)
    return diag.model_dump(mode="json")


@app.get("/api/features")
async def api_features():
    """Get feature status cards."""
    features = [
        FeatureCard(
            name="Signal Strength Monitoring",
            status=FeatureStatus.AVAILABLE,
            description="Real-time RSSI/signal strength collection and visualization.",
            detail="Uses nmcli/iw to poll signal strength at configurable intervals. Resolution is typically 1 dBm with update rates of 1-2 Hz depending on the driver.",
            requirements="Active WiFi connection"
        ),
        FeatureCard(
            name="Environmental Correlation",
            status=FeatureStatus.EXPERIMENTAL,
            description="Explore whether movement and environmental changes correlate with signal variations.",
            detail="RSSI may show small variations with large-scale movement near the signal path. This is an experimental observation tool — results depend heavily on environment, distance, and adapter sensitivity.",
            requirements="Active WiFi connection, controlled experiment"
        ),
        FeatureCard(
            name="Presence Detection",
            status=FeatureStatus.UNVALIDATED,
            description="Detecting whether a person is present in a room based on signal changes.",
            detail="RSSI-based presence detection has been explored in research but is unreliable with consumer hardware. This adapter does not provide the fine-grained CSI data that published methods require. Any observations here are unvalidated.",
            requirements="CSI-capable hardware (not available), validated algorithm"
        ),
        FeatureCard(
            name="Breathing Detection",
            status=FeatureStatus.UNAVAILABLE,
            description="Detecting breathing patterns through WiFi signal analysis.",
            detail="Breathing detection requires sub-millisecond CSI measurements to detect the ~0.3 Hz chest movement signal. The Intel Centrino Wireless-N 2230 with iwlwifi does not expose CSI data. This feature cannot work with the current hardware.",
            requirements="CSI-capable hardware with custom firmware (not available)"
        ),
        FeatureCard(
            name="Heartbeat Detection",
            status=FeatureStatus.UNAVAILABLE,
            description="Detecting heartbeat through WiFi signal analysis.",
            detail="Heartbeat detection via WiFi requires extremely sensitive phase measurements from CSI at high sampling rates (>100 Hz). This is a cutting-edge research topic even with specialized hardware. It is not possible with RSSI from a standard WiFi adapter.",
            requirements="Specialized CSI hardware, controlled RF environment, validated algorithm"
        ),
        FeatureCard(
            name="Through-Wall Detection",
            status=FeatureStatus.UNAVAILABLE,
            description="Detecting presence or movement through walls.",
            detail="Through-wall sensing in research uses multiple CSI-capable transceivers with known positions. Standard RSSI measurements do not provide the spatial or phase resolution needed. This feature is not achievable with the current setup.",
            requirements="Multiple CSI-capable transceivers, specialized setup"
        )
    ]
    return [f.model_dump(mode="json") for f in features]


@app.get("/api/annotations")
async def api_annotations():
    """Get all experiment annotations."""
    return {"annotations": session.annotations}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for live signal updates."""
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            # Send status update every second
            try:
                status = await asyncio.get_event_loop().run_in_executor(None, get_wifi_status)
                status_dict = status.model_dump(mode="json")
                now_iso = datetime.now(timezone.utc).isoformat()

                data = {
                    "type": "status",
                    "data": status_dict,
                    "link": status_dict,
                    "interface": status.interface,
                    "driver": status.driver,
                    "ssid": status.ssid,
                    "bssid": status.bssid,
                    "freq": status.frequency_mhz,
                    "frequency_mhz": status.frequency_mhz,
                    "channel": status.channel,
                    "rssi": status.signal_dbm,
                    "signal_dbm": status.signal_dbm,
                    "tx_bitrate": status.tx_bitrate,
                    "rx_bitrate": status.rx_bitrate,
                    "ip_address": status.ip_address,
                    "connection_status": status.connection_status.value,
                    "timestamp": now_iso
                }

                if session.is_running:
                    stats = session.get_stats()
                    stats_dict = stats.model_dump(mode="json")
                    data["experiment"] = stats_dict
                    data["stats"] = {
                        "count": stats.sample_count,
                        "missing": stats.missing_count,
                        "min": stats.min_rssi,
                        "max": stats.max_rssi,
                        "mean": stats.mean_rssi,
                        "std": stats.std_rssi,
                    }
                    if session.samples:
                        latest = session.samples[-1]
                        latest_dict = latest.model_dump(mode="json")
                        data["latest_sample"] = latest_dict
                        data["sample"] = {
                            "rssi": latest.rssi_dbm,
                            "rssi_dbm": latest.rssi_dbm,
                            "timestamp": latest.timestamp.isoformat(),
                            "phase": latest.phase.value,
                            "annotation": latest.annotation,
                            "missing": latest.missing
                        }
                else:
                    if status.signal_dbm is not None:
                        sample_obj = {
                            "rssi": status.signal_dbm,
                            "rssi_dbm": status.signal_dbm,
                            "timestamp": now_iso,
                            "phase": session.current_phase.value,
                            "annotation": None,
                            "missing": False
                        }
                        data["latest_sample"] = sample_obj
                        data["sample"] = sample_obj

                await ws.send_json(data)
            except Exception:
                pass
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)
