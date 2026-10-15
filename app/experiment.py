"""Experiment session manager for WiFi Sense."""
import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Callable

from app.models import (
    SignalSample, ExperimentStats, ExperimentPhase,
    ExperimentConfig, WiFiStatus
)
from app.signal_processing import compute_statistics


class ExperimentSession:
    """Manages a single experiment session with data collection."""
    
    def __init__(self):
        self.session_id: str = str(uuid.uuid4())[:8]
        self.samples: List[SignalSample] = []
        self.started_at: Optional[datetime] = None
        self.stopped_at: Optional[datetime] = None
        self.is_running: bool = False
        self.current_phase: ExperimentPhase = ExperimentPhase.IDLE
        self.sampling_interval_ms: int = 1000
        self._task: Optional[asyncio.Task] = None
        self._collector_fn: Optional[Callable] = None
        self._pending_annotation: Optional[str] = None
        self.annotations: List[dict] = []
        self.max_samples: int = 36000  # 10 hours at 1 sample/sec
    
    async def start(self, config: ExperimentConfig, collector_fn: Callable):
        """Start collecting samples."""
        if self.is_running:
            return
        self.session_id = str(uuid.uuid4())[:8]
        self.samples = []
        self.annotations = []
        self.started_at = datetime.now(timezone.utc)
        self.stopped_at = None
        self.is_running = True
        self.current_phase = config.phase
        self.sampling_interval_ms = config.sampling_interval_ms
        self._collector_fn = collector_fn
        if config.annotation:
            self._pending_annotation = config.annotation
        self._task = asyncio.create_task(self._collection_loop())
    
    async def stop(self):
        """Stop collecting samples."""
        self.is_running = False
        self.stopped_at = datetime.now(timezone.utc)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.current_phase = ExperimentPhase.IDLE
    
    def set_phase(self, phase: ExperimentPhase, annotation: Optional[str] = None):
        """Change the current experiment phase."""
        self.current_phase = phase
        if annotation:
            self.add_annotation(annotation)
    
    def add_annotation(self, text: str):
        """Add an event annotation at the current time."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "text": text,
            "phase": self.current_phase.value,
            "sample_index": len(self.samples)
        }
        self.annotations.append(entry)
        self._pending_annotation = text
    
    async def _collection_loop(self):
        """Main data collection loop."""
        while self.is_running:
            try:
                if len(self.samples) >= self.max_samples:
                    self.is_running = False
                    break
                
                sample = await self._collect_one_sample()
                self.samples.append(sample)
            except asyncio.CancelledError:
                break
            except Exception:
                # Record a missing sample on error
                self.samples.append(SignalSample(
                    timestamp=datetime.now(timezone.utc),
                    missing=True,
                    phase=self.current_phase
                ))
            
            await asyncio.sleep(self.sampling_interval_ms / 1000.0)
    
    async def _collect_one_sample(self) -> SignalSample:
        """Collect a single sample from the WiFi adapter."""
        if self._collector_fn is None:
            return SignalSample(
                timestamp=datetime.now(timezone.utc),
                missing=True,
                phase=self.current_phase
            )
        
        loop = asyncio.get_event_loop()
        status: WiFiStatus = await loop.run_in_executor(None, self._collector_fn)
        
        annotation = self._pending_annotation
        self._pending_annotation = None
        
        if status.signal_dbm is None and status.connection_status.value != "connected":
            return SignalSample(
                timestamp=datetime.now(timezone.utc),
                missing=True,
                phase=self.current_phase,
                annotation=annotation
            )
        
        return SignalSample(
            timestamp=datetime.now(timezone.utc),
            rssi_dbm=status.signal_dbm,
            noise_dbm=status.noise_dbm,
            link_quality=status.link_quality,
            tx_bitrate=status.tx_bitrate,
            rx_bitrate=status.rx_bitrate,
            frequency_mhz=status.frequency_mhz,
            missing=status.signal_dbm is None,
            phase=self.current_phase,
            annotation=annotation
        )
    
    def get_stats(self) -> ExperimentStats:
        """Compute current experiment statistics."""
        now = datetime.now(timezone.utc)
        duration = 0.0
        if self.started_at:
            end = self.stopped_at or now
            duration = (end - self.started_at).total_seconds()
        
        stats_data = compute_statistics(self.samples)
        
        return ExperimentStats(
            session_id=self.session_id,
            started_at=self.started_at,
            duration_seconds=round(duration, 1),
            sample_count=len(self.samples),
            missing_count=stats_data["missing"],
            min_rssi=stats_data["min"],
            max_rssi=stats_data["max"],
            mean_rssi=round(stats_data["mean"], 2) if stats_data["mean"] is not None else None,
            std_rssi=round(stats_data["std"], 3) if stats_data["std"] is not None else None,
            current_phase=self.current_phase,
            is_running=self.is_running
        )
    
    def export_csv(self) -> str:
        """Export samples as CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "timestamp", "rssi_dbm", "noise_dbm", "link_quality",
            "tx_bitrate_mbps", "rx_bitrate_mbps", "frequency_mhz",
            "missing", "phase", "annotation"
        ])
        for s in self.samples:
            writer.writerow([
                s.timestamp.isoformat(),
                s.rssi_dbm if s.rssi_dbm is not None else "",
                s.noise_dbm if s.noise_dbm is not None else "",
                s.link_quality if s.link_quality is not None else "",
                s.tx_bitrate if s.tx_bitrate is not None else "",
                s.rx_bitrate if s.rx_bitrate is not None else "",
                s.frequency_mhz if s.frequency_mhz is not None else "",
                s.missing,
                s.phase.value,
                s.annotation if s.annotation else ""
            ])
        return output.getvalue()
    
    def get_history(self, last_n: Optional[int] = None) -> List[SignalSample]:
        """Return sample history, optionally limited to last N samples."""
        if last_n is not None and last_n > 0:
            return self.samples[-last_n:]
        return list(self.samples)
