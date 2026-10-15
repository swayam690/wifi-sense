"""Data models for WiFi Sense."""
from pydantic import BaseModel, Field, root_validator
from dataclasses import dataclass, field
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum
import json
import uuid

# Pydantic v1 compatibility for model_dump
if not hasattr(BaseModel, "model_dump"):
    def _model_dump(self, mode: str = "python", **kwargs):
        if mode == "json":
            return json.loads(self.json(**kwargs))
        return self.dict(**kwargs)
    BaseModel.model_dump = _model_dump


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower:
                    return member
        return None


class FeatureStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXPERIMENTAL = "experimental"
    UNVALIDATED = "unvalidated"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower:
                    return member
        return None


class ExperimentPhase(str, Enum):
    IDLE = "idle"
    BASELINE = "baseline"
    MOVEMENT = "movement"
    CUSTOM = "custom"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower:
                    return member
        return None


class SignalSample(BaseModel):
    timestamp: datetime
    rssi_dbm: Optional[float] = None
    noise_dbm: Optional[float] = None
    link_quality: Optional[int] = None
    tx_bitrate: Optional[float] = None
    rx_bitrate: Optional[float] = None
    frequency_mhz: Optional[int] = None
    missing: bool = False
    phase: ExperimentPhase = ExperimentPhase.IDLE
    annotation: Optional[str] = None


class WiFiStatus(BaseModel):
    interface: Optional[str] = None
    driver: Optional[str] = None
    ssid: Optional[str] = None
    bssid: Optional[str] = None
    frequency_mhz: Optional[int] = None
    channel: Optional[int] = None
    signal_dbm: Optional[float] = None
    noise_dbm: Optional[float] = None
    link_quality: Optional[int] = None
    tx_bitrate: Optional[float] = None
    rx_bitrate: Optional[float] = None
    connection_status: ConnectionStatus = ConnectionStatus.UNKNOWN
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    last_update: Optional[datetime] = None
    error: Optional[str] = None


class ExperimentConfig(BaseModel):
    sampling_interval_ms: int = Field(default=1000, ge=200, le=30000, description="Sampling interval in milliseconds")
    interval_ms: Optional[int] = None
    phase: ExperimentPhase = ExperimentPhase.BASELINE
    annotation: Optional[str] = None

    @root_validator(pre=True)
    def normalize_interval(cls, values):
        if isinstance(values, dict):
            if "interval_ms" in values and values["interval_ms"]:
                values["sampling_interval_ms"] = values["interval_ms"]
        return values


class ExperimentStats(BaseModel):
    session_id: str
    started_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    sample_count: int = 0
    missing_count: int = 0
    min_rssi: Optional[float] = None
    max_rssi: Optional[float] = None
    mean_rssi: Optional[float] = None
    std_rssi: Optional[float] = None
    current_phase: ExperimentPhase = ExperimentPhase.IDLE
    is_running: bool = False


class AnnotationRequest(BaseModel):
    text: str
    phase: ExperimentPhase = ExperimentPhase.CUSTOM
    session_id: Optional[str] = None
    timestamp: Optional[Any] = None


class HardwareCapability(BaseModel):
    name: str
    available: bool
    detail: str


class HardwareDiagnostics(BaseModel):
    adapter_name: Optional[str] = None
    driver: Optional[str] = None
    pci_id: Optional[str] = None
    interface: Optional[str] = None
    monitor_mode_supported: bool = False
    csi_available: bool = False
    capabilities: List[HardwareCapability] = []
    notes: List[str] = []


class FeatureCard(BaseModel):
    name: str
    status: FeatureStatus
    description: str
    detail: str
    requirements: str


class SignalAnalysis(BaseModel):
    timestamps: List[str] = []
    raw_rssi: List[Optional[float]] = []
    smoothed_rssi: List[Optional[float]] = []
    detrended: List[Optional[float]] = []
    rolling_variance: List[Optional[float]] = []
    change_points: List[int] = []
    analysis_notes: List[str] = []
