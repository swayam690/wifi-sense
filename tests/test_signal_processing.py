"""Tests for WiFi Sense signal processing module."""
import pytest
import numpy as np
from datetime import datetime, timezone
from app.models import SignalSample, ExperimentPhase
from app.signal_processing import (
    compute_statistics,
    smooth_signal,
    detrend_signal,
    rolling_variance,
    detect_changes,
    analyze_signal,
)


def _make_sample(rssi: float = -50.0, missing: bool = False) -> SignalSample:
    """Helper to create a SignalSample with a given RSSI."""
    return SignalSample(
        timestamp=datetime.now(timezone.utc),
        rssi_dbm=None if missing else rssi,
        missing=missing,
        phase=ExperimentPhase.BASELINE,
    )


class TestComputeStatistics:
    def test_basic_stats(self):
        samples = [_make_sample(r) for r in [-50, -55, -45, -60, -40]]
        stats = compute_statistics(samples)
        assert stats["min"] == -60.0
        assert stats["max"] == -40.0
        assert abs(stats["mean"] - (-50.0)) < 0.01
        assert stats["count"] == 5
        assert stats["missing"] == 0

    def test_empty_samples(self):
        stats = compute_statistics([])
        assert stats["min"] is None
        assert stats["max"] is None
        assert stats["mean"] is None
        assert stats["count"] == 0

    def test_all_missing(self):
        samples = [_make_sample(missing=True) for _ in range(5)]
        stats = compute_statistics(samples)
        assert stats["count"] == 0
        assert stats["missing"] == 5

    def test_mixed_missing(self):
        samples = [_make_sample(-50), _make_sample(missing=True), _make_sample(-60)]
        stats = compute_statistics(samples)
        assert stats["count"] == 2
        assert stats["missing"] == 1
        assert stats["min"] == -60.0
        assert stats["max"] == -50.0

    def test_single_sample(self):
        samples = [_make_sample(-42)]
        stats = compute_statistics(samples)
        assert stats["count"] == 1
        assert stats["mean"] == -42.0
        assert stats["std"] == 0.0


class TestSmoothSignal:
    def test_basic_smoothing(self):
        values = [-50.0, -52.0, -48.0, -51.0, -49.0]
        result = smooth_signal(values, window_size=3)
        assert len(result) == 5
        assert all(v is not None for v in result)

    def test_with_none_values(self):
        values = [-50.0, None, -48.0, None, -49.0]
        result = smooth_signal(values, window_size=3)
        assert len(result) == 5
        # None positions should remain None
        assert result[1] is None
        assert result[3] is None

    def test_empty_input(self):
        assert smooth_signal([], 3) == []

    def test_window_larger_than_data(self):
        values = [-50.0, -52.0]
        result = smooth_signal(values, window_size=10)
        assert len(result) == 2

    def test_all_none(self):
        values = [None, None, None]
        result = smooth_signal(values, window_size=3)
        assert result == [None, None, None]


class TestDetrendSignal:
    def test_removes_linear_trend(self):
        # Create data with a clear linear trend
        values = [float(-50 + i) for i in range(20)]
        result = detrend_signal(values)
        # After detrending, residuals should be near zero
        valid = [v for v in result if v is not None]
        assert all(abs(v) < 0.01 for v in valid)

    def test_too_few_points(self):
        values = [-50.0]
        result = detrend_signal(values)
        assert result == values

    def test_with_none(self):
        values = [-50.0, None, -48.0, None, -46.0]
        result = detrend_signal(values)
        assert len(result) == 5
        assert result[1] is None
        assert result[3] is None


class TestRollingVariance:
    def test_constant_signal(self):
        values = [-50.0] * 20
        result = rolling_variance(values, window_size=5)
        # Constant signal should have zero variance
        valid = [v for v in result if v is not None]
        assert all(v == 0.0 for v in valid)

    def test_varying_signal(self):
        values = [-50.0, -55.0, -45.0, -60.0, -40.0] * 4
        result = rolling_variance(values, window_size=5)
        assert len(result) == 20
        # Should have some non-zero variance values
        valid = [v for v in result if v is not None]
        assert any(v > 0 for v in valid)

    def test_empty(self):
        result = rolling_variance([], 5)
        assert result == []

    def test_short_window(self):
        result = rolling_variance([-50.0], window_size=1)
        assert result == [None]


class TestDetectChanges:
    def test_stable_signal(self):
        values = [-50.0 + np.random.normal(0, 0.1) for _ in range(50)]
        changes = detect_changes(values, threshold_factor=3.0)
        # Very stable signal should have few or no change points
        assert len(changes) < 5

    def test_abrupt_change(self):
        values = [-50.0] * 25 + [-70.0] * 25
        changes = detect_changes(values, threshold_factor=2.0)
        # Should detect change near index 25
        assert len(changes) > 0
        assert any(20 <= cp <= 30 for cp in changes)

    def test_too_few_samples(self):
        values = [-50.0] * 5
        changes = detect_changes(values, threshold_factor=2.0)
        assert changes == []

    def test_with_none(self):
        values = [-50.0] * 10 + [None] * 5 + [-50.0] * 10
        changes = detect_changes(values, threshold_factor=2.0)
        # Should handle None values without crashing
        assert isinstance(changes, list)


class TestAnalyzeSignal:
    def test_full_analysis(self):
        samples = [_make_sample(-50 + i * 0.5) for i in range(30)]
        analysis = analyze_signal(samples)
        assert len(analysis.timestamps) == 30
        assert len(analysis.raw_rssi) == 30
        assert len(analysis.smoothed_rssi) == 30
        assert len(analysis.detrended) == 30
        assert len(analysis.rolling_variance) == 30
        assert isinstance(analysis.change_points, list)
        assert len(analysis.analysis_notes) > 0

    def test_analysis_notes_content(self):
        samples = [_make_sample(-50) for _ in range(20)]
        samples[5] = _make_sample(missing=True)
        analysis = analyze_signal(samples)
        notes_text = " ".join(analysis.analysis_notes)
        assert "missing" in notes_text.lower() or "sample" in notes_text.lower()
