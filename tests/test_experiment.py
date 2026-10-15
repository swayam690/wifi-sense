"""Tests for WiFi Sense experiment session manager."""
import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock
from app.models import (
    ExperimentConfig, ExperimentPhase, WiFiStatus, ConnectionStatus
)
from app.experiment import ExperimentSession


def _mock_wifi_status(rssi=-50.0):
    """Create a mock WiFi status."""
    return WiFiStatus(
        interface="wlan0",
        ssid="TestNetwork",
        bssid="AA:BB:CC:DD:EE:FF",
        frequency_mhz=2452,
        channel=9,
        signal_dbm=rssi,
        connection_status=ConnectionStatus.CONNECTED,
        last_update=datetime.now(timezone.utc),
    )


class TestExperimentSession:
    def test_initial_state(self):
        session = ExperimentSession()
        assert not session.is_running
        assert session.samples == []
        assert session.current_phase == ExperimentPhase.IDLE

    def test_start_and_stop(self):
        async def _run():
            session = ExperimentSession()
            config = ExperimentConfig(sampling_interval_ms=200)
            collector = lambda: _mock_wifi_status()

            await session.start(config, collector)
            assert session.is_running
            assert session.started_at is not None

            # Let it collect a few samples
            await asyncio.sleep(0.7)

            await session.stop()
            assert not session.is_running
            assert session.stopped_at is not None
            assert len(session.samples) >= 2
        asyncio.run(_run())

    def test_double_start(self):
        async def _run():
            session = ExperimentSession()
            config = ExperimentConfig(sampling_interval_ms=500)
            collector = lambda: _mock_wifi_status()

            await session.start(config, collector)
            # Second start should be no-op
            await session.start(config, collector)
            assert session.is_running
            await session.stop()
        asyncio.run(_run())

    def test_stats_empty(self):
        session = ExperimentSession()
        stats = session.get_stats()
        assert stats.sample_count == 0
        assert stats.is_running is False

    def test_add_annotation(self):
        session = ExperimentSession()
        session.is_running = True
        session.add_annotation("Test event")
        assert len(session.annotations) == 1
        assert session.annotations[0]["text"] == "Test event"

    def test_set_phase(self):
        session = ExperimentSession()
        session.set_phase(ExperimentPhase.MOVEMENT, "Started moving")
        assert session.current_phase == ExperimentPhase.MOVEMENT
        assert len(session.annotations) == 1

    def test_csv_export_empty(self):
        session = ExperimentSession()
        csv_data = session.export_csv()
        lines = csv_data.strip().split("\n")
        assert len(lines) == 1  # Header only

    def test_csv_export_with_data(self):
        async def _run():
            session = ExperimentSession()
            config = ExperimentConfig(sampling_interval_ms=200)
            collector = lambda: _mock_wifi_status(-55)

            await session.start(config, collector)
            await asyncio.sleep(0.5)
            await session.stop()

            csv_data = session.export_csv()
            lines = csv_data.strip().split("\n")
            assert len(lines) > 1  # Header + data rows
            assert "rssi_dbm" in lines[0]
            assert "timestamp" in lines[0]
        asyncio.run(_run())

    def test_get_history_limit(self):
        session = ExperimentSession()
        from app.models import SignalSample
        for i in range(20):
            session.samples.append(SignalSample(
                timestamp=datetime.now(timezone.utc),
                rssi_dbm=-50.0 - i,
                phase=ExperimentPhase.BASELINE
            ))
        assert len(session.get_history(last_n=5)) == 5
        assert len(session.get_history(last_n=100)) == 20
        assert len(session.get_history()) == 20

    def test_collector_failure_records_missing(self):
        """When the collector raises an exception, a missing sample is recorded."""
        async def _run():
            session = ExperimentSession()

            def failing_collector():
                raise RuntimeError("No WiFi")

            config = ExperimentConfig(sampling_interval_ms=200)
            await session.start(config, failing_collector)
            await asyncio.sleep(0.5)
            await session.stop()

            assert len(session.samples) > 0
            assert all(s.missing for s in session.samples)
        asyncio.run(_run())

