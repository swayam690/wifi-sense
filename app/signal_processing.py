"""Signal processing for WiFi Sense RSSI time-series."""
import numpy as np
from typing import List, Optional, Tuple
from app.models import SignalSample, SignalAnalysis


def compute_statistics(samples: List[SignalSample]) -> dict:
    """Compute min, max, mean, std of RSSI values, excluding missing samples."""
    values = [s.rssi_dbm for s in samples if s.rssi_dbm is not None and not s.missing]
    if not values:
        return {"min": None, "max": None, "mean": None, "std": None, "count": 0, "missing": sum(1 for s in samples if s.missing)}
    arr = np.array(values, dtype=np.float64)
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "count": len(values),
        "missing": sum(1 for s in samples if s.missing)
    }


def smooth_signal(values: List[Optional[float]], window_size: int = 5) -> List[Optional[float]]:
    """Apply moving average smoothing. Handle None values by forward-filling then smoothing."""
    if not values or window_size < 1:
        return values
    # Forward fill None values
    filled = []
    last_val = None
    for v in values:
        if v is not None:
            last_val = v
        filled.append(last_val)
    
    if all(v is None for v in filled):
        return values
    
    arr = np.array([v if v is not None else 0.0 for v in filled], dtype=np.float64)
    if len(arr) < window_size:
        window_size = len(arr)
    kernel = np.ones(window_size) / window_size
    smoothed = np.convolve(arr, kernel, mode='same')
    return [float(s) if values[i] is not None else None for i, s in enumerate(smoothed)]


def detrend_signal(values: List[Optional[float]]) -> List[Optional[float]]:
    """Remove linear trend from signal. Returns residuals."""
    valid_indices = [i for i, v in enumerate(values) if v is not None]
    if len(valid_indices) < 2:
        return values
    valid_values = [values[i] for i in valid_indices]
    x = np.array(valid_indices, dtype=np.float64)
    y = np.array(valid_values, dtype=np.float64)
    coeffs = np.polyfit(x, y, 1)
    trend = np.polyval(coeffs, x)
    residuals = y - trend
    result = [None] * len(values)
    for idx, vi in enumerate(valid_indices):
        result[vi] = float(residuals[idx])
    return result


def rolling_variance(values: List[Optional[float]], window_size: int = 10) -> List[Optional[float]]:
    """Compute rolling variance of the signal."""
    if not values or window_size < 2:
        return [None] * len(values)
    filled = []
    last_val = None
    for v in values:
        if v is not None:
            last_val = v
        filled.append(last_val)
    
    result = [None] * len(values)
    for i in range(len(filled)):
        start = max(0, i - window_size + 1)
        window = [v for v in filled[start:i+1] if v is not None]
        if len(window) >= 2:
            result[i] = float(np.var(window))
    return result


def detect_changes(values: List[Optional[float]], threshold_factor: float = 2.0) -> List[int]:
    """Detect change points where the signal deviates significantly from local mean.
    Returns indices of detected change points.
    Uses a simple approach: flag points where the absolute deviation from
    a rolling mean exceeds threshold_factor * rolling_std.
    """
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(valid) < 10:
        return []
    
    indices, vals = zip(*valid)
    arr = np.array(vals, dtype=np.float64)
    window = min(10, len(arr) // 2)
    if window < 3:
        return []
    
    change_points = []
    for i in range(window, len(arr)):
        local_window = arr[max(0, i-window):i]
        local_mean = np.mean(local_window)
        local_std = np.std(local_window)
        if local_std > 0 and abs(arr[i] - local_mean) > threshold_factor * local_std:
            change_points.append(indices[i])
    
    return change_points


def analyze_signal(samples: List[SignalSample]) -> SignalAnalysis:
    """Run full signal analysis pipeline on collected samples."""
    timestamps = [s.timestamp.isoformat() for s in samples]
    raw_rssi = [s.rssi_dbm for s in samples]
    
    smoothed = smooth_signal(raw_rssi, window_size=5)
    detrended = detrend_signal(raw_rssi)
    variance = rolling_variance(raw_rssi, window_size=10)
    changes = detect_changes(raw_rssi)
    
    notes = []
    stats = compute_statistics(samples)
    if stats["count"] > 0:
        notes.append(f"Analyzed {stats['count']} valid samples (RSSI range: {stats['min']:.1f} to {stats['max']:.1f} dBm).")
        if stats["std"] is not None and stats["std"] < 1.0:
            notes.append("Signal is very stable — environmental changes may not be detectable at this resolution.")
        if stats["std"] is not None and stats["std"] > 3.0:
            notes.append("Notable signal variance detected — could indicate environmental changes or interference.")
    if stats["missing"] > 0:
        notes.append(f"{stats['missing']} samples were missing (collection failed or timed out).")
    if changes:
        notes.append(f"{len(changes)} potential change point(s) detected. These may correlate with movement or environmental changes, but are not proof of presence.")
    else:
        notes.append("No significant change points detected in the signal.")
    notes.append("Note: RSSI is a coarse measurement. Small fluctuations are normal and may not indicate meaningful environmental changes.")
    
    return SignalAnalysis(
        timestamps=timestamps,
        raw_rssi=raw_rssi,
        smoothed_rssi=smoothed,
        detrended=detrended,
        rolling_variance=variance,
        change_points=changes,
        analysis_notes=notes
    )
