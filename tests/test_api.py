"""Tests for WiFi Sense API routes."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.models import (
    WiFiStatus, ConnectionStatus, HardwareDiagnostics, HardwareCapability
)


@pytest.fixture
def client():
    """Create a test client with mocked collector."""
    mock_status = WiFiStatus(
        interface="wlan0",
        driver="iwlwifi",
        ssid="TestNetwork",
        bssid="AA:BB:CC:DD:EE:FF",
        frequency_mhz=2452,
        channel=9,
        signal_dbm=-52.0,
        connection_status=ConnectionStatus.CONNECTED,
        ip_address="192.168.1.100",
        last_update=datetime.now(timezone.utc),
    )

    mock_diag = HardwareDiagnostics(
        adapter_name="Intel Centrino Wireless-N 2230",
        driver="iwlwifi",
        pci_id="8086:0887",
        interface="wlan0",
        monitor_mode_supported=True,
        csi_available=False,
        capabilities=[
            HardwareCapability(name="RSSI Monitoring", available=True, detail="Available via nmcli/iw"),
            HardwareCapability(name="CSI Capture", available=False, detail="Not supported by iwlwifi driver"),
        ],
        notes=["This Intel adapter does not expose CSI data."]
    )

    with patch("app.main.get_wifi_status", return_value=mock_status):
        with patch("app.main.get_hardware_diagnostics", return_value=mock_diag):
            from app.main import app, session
            # Reset session state
            session.is_running = False
            session.samples = []
            session.annotations = []
            yield TestClient(app)


class TestStatusAPI:
    def test_get_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["interface"] == "wlan0"
        assert data["ssid"] == "TestNetwork"
        assert data["signal_dbm"] == -52.0

    def test_get_signal_current(self, client):
        resp = client.get("/api/signal/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "rssi_dbm" in data or "timestamp" in data

    def test_get_signal_history_empty(self, client):
        resp = client.get("/api/signal/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["samples"] == []


class TestExperimentAPI:
    def test_start_experiment(self, client):
        resp = client.post("/api/experiment/start", json={
            "sampling_interval_ms": 1000,
            "phase": "baseline"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "session_id" in data

        # Stop it for cleanup
        client.post("/api/experiment/stop")

    def test_start_experiment_frontend_payload(self, client):
        resp = client.post("/api/experiment/start", json={
            "interval_ms": 500,
            "phase": "Baseline"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["sampling_interval_ms"] == 500
        assert data["phase"] == "baseline"

        # Change phase while running using title-case
        phase_resp = client.post("/api/experiment/phase", json={"phase": "Movement"})
        assert phase_resp.status_code == 200
        assert phase_resp.json()["phase"] == "movement"

        # Add annotation while running
        ann_resp = client.post("/api/experiment/annotate", json={
            "text": "Subject walking",
            "phase": "Movement",
            "session_id": data["session_id"]
        })
        assert ann_resp.status_code == 200

        # Stop it for cleanup
        stop_resp = client.post("/api/experiment/stop")
        assert stop_resp.status_code == 200

    def test_phase_staged_before_start(self, client):
        resp = client.post("/api/experiment/phase", json={"phase": "Movement"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "phase_staged"
        assert resp.json()["phase"] == "movement"

    def test_favicon(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code == 204

    def test_stop_without_start(self, client):
        resp = client.post("/api/experiment/stop")
        assert resp.status_code == 409

    def test_double_start(self, client):
        client.post("/api/experiment/start", json={"sampling_interval_ms": 1000})
        resp = client.post("/api/experiment/start", json={"sampling_interval_ms": 1000})
        assert resp.status_code == 409
        client.post("/api/experiment/stop")

    def test_get_stats(self, client):
        resp = client.get("/api/experiment/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "is_running" in data

    def test_export_empty(self, client):
        resp = client.get("/api/experiment/export")
        assert resp.status_code == 404

    def test_annotate_without_experiment(self, client):
        resp = client.post("/api/experiment/annotate", json={
            "text": "test annotation",
            "phase": "custom"
        })
        assert resp.status_code == 409


class TestDiagnosticsAPI:
    def test_get_diagnostics(self, client):
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["driver"] == "iwlwifi"
        assert data["csi_available"] is False

    def test_get_features(self, client):
        resp = client.get("/api/features")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 4
        # Check that unavailable features are properly marked
        names = [f["name"] for f in data]
        assert "Breathing Detection" in names
        breathing = next(f for f in data if f["name"] == "Breathing Detection")
        assert breathing["status"] == "unavailable"


class TestAnalysisAPI:
    def test_analysis_insufficient_data(self, client):
        resp = client.get("/api/analysis")
        assert resp.status_code == 404
