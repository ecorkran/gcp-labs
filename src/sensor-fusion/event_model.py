"""
RiverPulse Event Data Model

All sensor events flow through a common schema so the correlation
engine can match them regardless of source modality.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EventState(str, Enum):
    RAW = "raw"
    PENDING = "pending_correlation"
    CORRELATED = "correlated"
    ASSESSED = "assessed"
    ALERTED = "alerted"
    ARCHIVED = "archived"


class Modality(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    ENVIRONMENTAL = "environmental"
    SENSOR = "sensor"  # numeric readings (cfs, stage height, etc.)


@dataclass
class RawEvent:
    """A single sensor event from one modality."""
    gauge_id: str
    modality: str           # Modality enum value
    timestamp: str          # ISO format, from the sensor
    source_uri: str         # GCS URI for media, or "inline" for sensor data
    state: str = EventState.RAW
    classification: dict = field(default_factory=dict)  # Results from single-modal processing
    metadata: dict = field(default_factory=dict)         # Sensor-specific metadata
    event_id: str = ""      # Set by Firestore
    received_at: str = ""   # Server receive time
    
    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["received_at"]:
            d["received_at"] = datetime.now(timezone.utc).isoformat()
        return d


@dataclass
class CorrelatedEvent:
    """Multiple raw events matched within a time window."""
    gauge_id: str
    correlation_window_sec: float   # How wide the match window was
    raw_event_ids: list             # Firestore IDs of constituent raw events
    modalities_present: list        # Which modalities are represented
    earliest_timestamp: str
    latest_timestamp: str
    state: str = EventState.CORRELATED
    fusion_assessment: dict = field(default_factory=dict)
    alert_triggered: bool = False
    event_id: str = ""
    created_at: str = ""
    
    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["created_at"]:
            d["created_at"] = datetime.now(timezone.utc).isoformat()
        return d