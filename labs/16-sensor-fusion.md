# Lab 16: Multi-Modal Sensor Fusion — Correlated Event Pipeline

# Overview

**Time:** 75–105 minutes  
**Prerequisites:** Labs 13–15 completed (Vision API, Gemini multimodal, audio classification). Firestore, Pub/Sub, Cloud Storage, Cloud Run all working.

###### New Skills
* Temporal event correlation across sensor modalities
* Multi-modal fusion prompt design (image + audio + sensor data → unified assessment)
* Event state machine (pending → correlated → assessed → alerted)
* Pub/Sub fan-out for parallel processing with downstream aggregation

---

## Concepts (5 minutes)

- **Sensor Fusion:** Combining data from multiple sensor types to produce a higher-confidence assessment than any single sensor alone. A camera sees a vessel entering a restricted zone. The microphone hears no engine noise. Environmental sensors read normal conditions. Fused assessment: kayak or swimmer, not a motorized intrusion.
- **Temporal Correlation:** Events from different sensors that occur within a time window belong to the same real-world event. Camera triggers at 14:03:12, audio clip starts at 14:03:10, environmental snapshot at 14:03:15 — these are one event, not three.
- **Event State Machine:** Raw sensor signals flow through states: `raw` → `pending_correlation` → `correlated` → `assessed` → `alerted` (or `archived`). Each state transition adds context. This prevents alert fatigue (single-sensor false positives) and enables rich multi-modal analysis.
- **Fan-Out / Fan-In:** Pub/Sub delivers the same event to multiple independent processors (fan-out). A downstream aggregator collects results from all processors and correlates them (fan-in). This is the architecture Labs 1–15 have been building toward.

Labs 13–15 each process one modality in isolation. That's useful for development and testing, but in production, the power is in combining them. A boat engine classification from audio alone might be 75% confidence. Add a camera frame showing a vessel in a restricted zone, and confidence jumps to 95%. Add environmental context (low-visibility conditions, night, no permitted vessels scheduled), and it becomes a high-priority alert.

Single-sensor systems produce too many false positives. Fusion eliminates them. This is the architecture behind commercial surf and coastal monitoring platforms: multi-sensor AI that detects only what matters — vessels, swimmers, wave conditions, and hazards.

```
[Camera triggers]──────────► [Vision API] ──► [Firestore: raw-events]
                                                       |
[Audio clip captured]──────► [Gemini audio] ──► [Firestore: raw-events]
                                                       |
[Environmental snapshot]───► [Cloud Run API] ──► [Firestore: raw-events]
                                                       |
                                                       v
                                              [Correlation Engine]
                                              (time window matching)
                                                       |
                                                       v
                                              [Firestore: correlated-events]
                                                       |
                                                       v
                                              [Gemini: multi-modal fusion prompt]
                                              (image + audio + env → unified assessment)
                                                       |
                                                       v
                                              [Firestore: assessed-events]
                                                       |
                                          ┌────────────┴────────────┐
                                          v                         v
                                   [Pub/Sub: alerts]         [Archive / analytics]
                                   (if threat detected)
```

---

## Setup

```bash
# All APIs should be enabled from prior labs. Verify:
gcloud services list --enabled --filter="name:aiplatform OR name:vision OR name:firestore"

mkdir -p ~/sensor-fusion
cd ~/sensor-fusion

python3 -m venv venv
source venv/bin/activate

pip install google-genai google-cloud-vision google-cloud-storage google-cloud-firestore google-cloud-pubsub Pillow numpy
```

---

## Step 1: Define the Event Data Model

Create `event_model.py` — the shared schema for all event types:
```python
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
```

---

## Step 2: Simulate Multi-Modal Event Ingestion

Create `simulate_events.py` — generates a realistic burst of sensor events as if a real-world event just happened:
```python
"""
Simulate a multi-modal event: camera + audio + environmental sensors
all trigger within a short time window, as they would in the field.
"""
import json
import os
from datetime import datetime, timezone, timedelta

from google.cloud import firestore, storage

from event_model import RawEvent, Modality, EventState


PROJECT_ID = os.popen("gcloud config get-value project").read().strip()
BUCKET = f"{PROJECT_ID}-riverpulse-data"

db = firestore.Client()


def simulate_normal_event(gauge_id: str = "gauge-001"):
    """
    Simulate: surf activity detected near gauge.
    Camera, microphone, and environmental sensors all trigger.
    """
    base_time = datetime.now(timezone.utc)
    events = []

    # 1. Camera frame (triggers first — motion detection)
    image_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.IMAGE,
        timestamp=(base_time + timedelta(seconds=0)).isoformat(),
        source_uri=f"gs://{BUCKET}/images/gauge-001-clear_flow.png",
        classification={
            "source": "vision_api",
            "labels": ["surfing", "wave", "ocean", "outdoor", "sport"],
            "top_label": "surfing",
            "top_confidence": 0.82,
            "objects": [{"name": "person", "score": 0.78, "bounds": {"x_min": 0.3, "y_min": 0.2, "x_max": 0.7, "y_max": 0.8}}],
            "safe_search": {"adult": 1, "violence": 1},
        },
        metadata={"resolution": "640x480", "camera_angle": "north", "motion_trigger": True},
    )
    events.append(image_event)

    # 2. Audio clip (captured concurrently — ambient mic)
    audio_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.AUDIO,
        timestamp=(base_time + timedelta(seconds=2)).isoformat(),
        source_uri=f"gs://{BUCKET}/audio/gauge-001-quiet_ambient.wav",
        classification={
            "source": "gemini_audio",
            "primary_sound": "quiet_ambient",
            "confidence": 0.85,
            "threat_detected": False,
            "water_flow_estimate": "moderate",
            "human_activity": False,
        },
        metadata={"duration_sec": 5.0, "sample_rate": 16000, "format": "wav"},
    )
    events.append(audio_event)

    # 3. Environmental sensor snapshot
    env_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.ENVIRONMENTAL,
        timestamp=(base_time + timedelta(seconds=1)).isoformat(),
        source_uri="inline",
        classification={
            "source": "onboard_sensors",
        },
        metadata={
            "air_temp_f": 42.5,
            "humidity_pct": 67.2,
            "air_quality_aqi": 28,
            "barometric_pressure_hpa": 1013.2,
            "light_level_lux": 1200,
            "time_of_day": "afternoon",
        },
    )
    events.append(env_event)

    # 4. Numeric sensor reading (flow data)
    sensor_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.SENSOR,
        timestamp=(base_time + timedelta(seconds=0.5)).isoformat(),
        source_uri="inline",
        classification={
            "source": "gauge_sensor",
            "cfs": 920,
            "stage_height": 4.5,
            "water_temp_f": 48,
            "condition": "optimal",
        },
        metadata={"reading_type": "flow_reading"},
    )
    events.append(sensor_event)

    # Store all raw events in Firestore
    stored_ids = []
    for event in events:
        event.state = EventState.PENDING
        doc_ref = db.collection("raw-events").add(event.to_dict())
        event_id = doc_ref[1].id
        event.event_id = event_id
        stored_ids.append(event_id)
        print(f"  Stored {event.modality:15s} event: {event_id}")

    return events, stored_ids


def simulate_threat_event(gauge_id: str = "gauge-002"):
    """
    Simulate: potential boat intrusion event.
    Camera sees vessel, audio detects engine noise,
    environmental normal, sensor reads normal flow.
    """
    base_time = datetime.now(timezone.utc)
    events = []

    image_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.IMAGE,
        timestamp=(base_time + timedelta(seconds=0)).isoformat(),
        source_uri=f"gs://{BUCKET}/images/gauge-002-flow-high-kermits.jpg",
        classification={
            "source": "vision_api",
            "labels": ["boat", "vessel", "water", "outdoor", "watercraft"],
            "top_label": "boat",
            "top_confidence": 0.71,
            "objects": [{"name": "boat", "score": 0.68, "bounds": {"x_min": 0.4, "y_min": 0.1, "x_max": 0.6, "y_max": 0.9}}],
        },
        metadata={"resolution": "640x480", "motion_trigger": True},
    )
    events.append(image_event)

    audio_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.AUDIO,
        timestamp=(base_time + timedelta(seconds=1)).isoformat(),
        source_uri=f"gs://{BUCKET}/audio/gauge-001-boat_engine.wav",
        classification={
            "source": "gemini_audio",
            "primary_sound": "boat_engine",
            "confidence": 0.78,
            "threat_detected": True,
            "threat_type": "boat_intrusion",
            "human_activity": True,
            "alert_recommended": True,
        },
        metadata={"duration_sec": 2.0, "sample_rate": 16000},
    )
    events.append(audio_event)

    env_event = RawEvent(
        gauge_id=gauge_id,
        modality=Modality.ENVIRONMENTAL,
        timestamp=(base_time + timedelta(seconds=0.5)).isoformat(),
        source_uri="inline",
        classification={"source": "onboard_sensors"},
        metadata={
            "air_temp_f": 28.1,
            "humidity_pct": 45.0,
            "light_level_lux": 50,
            "time_of_day": "dusk",
        },
    )
    events.append(env_event)

    stored_ids = []
    for event in events:
        event.state = EventState.PENDING
        doc_ref = db.collection("raw-events").add(event.to_dict())
        event.event_id = doc_ref[1].id
        stored_ids.append(event.event_id)
        print(f"  Stored {event.modality:15s} event: {event.event_id}")

    return events, stored_ids


if __name__ == "__main__":
    print("=== Simulating Normal Surf Event (gauge-001) ===")
    normal_events, normal_ids = simulate_normal_event()

    print(f"\n=== Simulating Threat Event (gauge-002) ===")
    threat_events, threat_ids = simulate_threat_event()

    print(f"\nStored {len(normal_ids) + len(threat_ids)} raw events total")
    print(f"Normal event IDs: {normal_ids}")
    print(f"Threat event IDs: {threat_ids}")
```

Run it:
```bash
python simulate_events.py
```

Check Firestore Console → `raw-events` collection. You should see 7 documents spanning two simulated incidents.

---

## Step 3: Build the Correlation Engine

Create `correlator.py` — matches raw events from the same gauge within a time window:
```python
"""
Event Correlation Engine

Scans pending raw events, groups them by gauge + time window,
and creates correlated event documents containing all modalities.
"""
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from google.cloud import firestore

from event_model import EventState, CorrelatedEvent


db = firestore.Client()

# Correlation window: events within this many seconds are considered
# part of the same real-world event
CORRELATION_WINDOW_SEC = 30.0


def correlate_pending_events() -> list:
    """
    Find all pending raw events, group by gauge + time window,
    create correlated events.
    """
    # Fetch all pending events
    query = db.collection("raw-events") \
        .where("state", "==", EventState.PENDING) \
        .order_by("timestamp")
    
    pending = []
    for doc in query.stream():
        d = doc.to_dict()
        d["_doc_id"] = doc.id
        pending.append(d)
    
    if not pending:
        print("No pending events to correlate")
        return []

    print(f"Found {len(pending)} pending events")

    # Group by gauge_id
    by_gauge = defaultdict(list)
    for event in pending:
        by_gauge[event["gauge_id"]].append(event)

    correlated_events = []

    for gauge_id, events in by_gauge.items():
        # Sort by timestamp
        events.sort(key=lambda e: e["timestamp"])
        
        # Sliding window grouping
        groups = []
        current_group = [events[0]]

        for event in events[1:]:
            t_current = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            t_first = datetime.fromisoformat(current_group[0]["timestamp"].replace("Z", "+00:00"))
            
            if (t_current - t_first).total_seconds() <= CORRELATION_WINDOW_SEC:
                current_group.append(event)
            else:
                groups.append(current_group)
                current_group = [event]
        
        groups.append(current_group)  # Don't forget the last group

        # Create correlated events from groups with multiple modalities
        for group in groups:
            modalities = list(set(e["modality"] for e in group))
            raw_ids = [e["_doc_id"] for e in group]
            timestamps = [e["timestamp"] for e in group]

            correlated = CorrelatedEvent(
                gauge_id=gauge_id,
                correlation_window_sec=CORRELATION_WINDOW_SEC,
                raw_event_ids=raw_ids,
                modalities_present=modalities,
                earliest_timestamp=min(timestamps),
                latest_timestamp=max(timestamps),
            )

            # Store correlated event
            doc_ref = db.collection("correlated-events").add(correlated.to_dict())
            correlated.event_id = doc_ref[1].id

            # Update raw events to CORRELATED state
            batch = db.batch()
            for raw_id in raw_ids:
                ref = db.collection("raw-events").document(raw_id)
                batch.update(ref, {
                    "state": EventState.CORRELATED,
                    "correlated_event_id": correlated.event_id,
                })
            batch.commit()

            print(f"  Correlated: {correlated.event_id} "
                  f"({len(raw_ids)} events, modalities: {modalities})")
            
            correlated_events.append(correlated)

    return correlated_events


if __name__ == "__main__":
    print("=== Running Correlation Engine ===")
    results = correlate_pending_events()
    print(f"\nCreated {len(results)} correlated events")
```

Run the correlator:
```bash
python correlator.py
```

This will probably require you to create an index in FireStore.  Any error related to this will provide you with a link to click and easily create the index.  Follow it.

Then it should find the 7 pending events, group them into 2 correlated events (one per gauge), and update all raw events to `correlated` state.

---

## Step 4: Multi-Modal Fusion Assessment

Create `fusion_assessor.py` — the crown jewel. Takes a correlated event, loads all constituent data, and sends a single multi-modal prompt to Gemini:
```python
"""
Multi-Modal Fusion Assessor

Takes a correlated event (image + audio + environmental + sensor),
loads all data, and sends a single multi-modal Gemini prompt
for unified assessment.
"""
import json
import os
from datetime import datetime, timezone

from google import genai
from google.genai import types
from google.cloud import firestore, storage

from event_model import EventState


PROJECT_ID = os.popen("gcloud config get-value project").read().strip()

gemini_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
)

db = firestore.Client()
gcs_client = storage.Client()


FUSION_SYSTEM_INSTRUCTION = """You are a multi-modal event analyst for an environmental monitoring system.
You receive data from multiple sensor types captured during the same time window at the same location.
Your job is to fuse all available signals into a single unified assessment.

Respond with valid JSON:
{
    "event_type": "surf_activity" | "human_presence" | "boat_intrusion" | "vessel_detected" |
                  "environmental_anomaly" | "equipment_issue" | "weather_event" | "routine" | "unknown",
    "severity": "none" | "low" | "medium" | "high" | "critical",
    "confidence": 0.0 to 1.0,
    "summary": "One clear sentence describing what happened",
    "detailed_analysis": "3-5 sentences integrating ALL sensor modalities. Explain what each sensor contributed and how they corroborate or contradict each other.",
    "modality_contributions": {
        "image": "What the camera showed and how it influenced the assessment",
        "audio": "What the microphone captured and how it influenced the assessment",
        "environmental": "What environmental sensors indicated",
        "sensor": "What numeric readings showed (if present)"
    },
    "fusion_rationale": "Why combining these signals changes the assessment vs any single sensor alone",
    "recommended_actions": ["list of specific actions"],
    "alert_priority": "none" | "low" | "routine_review" | "immediate" | "emergency",
    "false_positive_risk": "low" | "medium" | "high",
    "additional_data_needed": ["list of data that would improve confidence, if any"]
}

Fusion rules:
- Single-modality detections have inherently lower confidence than multi-modal corroboration.
- Image + audio agreement on threat = high confidence.
- Image shows threat but audio is quiet = medium confidence, could be distance or timing.
- Audio detects threat but image is empty = could be off-camera, medium confidence.
- Environmental anomalies (unusual temp, air quality) can elevate severity of other detections.
- Night/dusk + human presence + remote location = elevated threat level.
- Always explain HOW the sensors corroborate or conflict."""


def load_media_bytes(gcs_uri: str) -> tuple:
    """Load bytes from GCS. Returns (bytes, mime_type) or (None, None)."""
    if gcs_uri == "inline" or not gcs_uri.startswith("gs://"):
        return None, None

    try:
        parts = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = gcs_client.bucket(parts[0])
        blob = bucket.blob(parts[1])
        data = blob.download_as_bytes()
        
        ext = parts[1].rsplit(".", 1)[-1].lower()
        mime_map = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "wav": "audio/wav", "mp3": "audio/mp3", "flac": "audio/flac",
        }
        return data, mime_map.get(ext, "application/octet-stream")
    except Exception as e:
        print(f"  Warning: could not load {gcs_uri}: {e}")
        return None, None


def assess_correlated_event(correlated_event_id: str) -> dict:
    """
    Load a correlated event, fetch all constituent raw events,
    build a multi-modal prompt, and get a unified assessment.
    """
    # Load the correlated event
    corr_doc = db.collection("correlated-events").document(correlated_event_id).get()
    if not corr_doc.exists:
        raise ValueError(f"Correlated event {correlated_event_id} not found")
    
    corr = corr_doc.to_dict()
    gauge_id = corr["gauge_id"]
    raw_ids = corr["raw_event_ids"]

    print(f"Assessing correlated event: {correlated_event_id}")
    print(f"  Gauge: {gauge_id}")
    print(f"  Modalities: {corr['modalities_present']}")
    print(f"  Raw events: {len(raw_ids)}")

    # Load all raw events
    raw_events = []
    for raw_id in raw_ids:
        doc = db.collection("raw-events").document(raw_id).get()
        if doc.exists:
            raw_events.append(doc.to_dict())

    # Build multi-modal content parts for Gemini
    content_parts = []
    context_text = f"Location: Gauge {gauge_id}\n"
    context_text += f"Time window: {corr['earliest_timestamp']} to {corr['latest_timestamp']}\n"
    context_text += f"Modalities captured: {', '.join(corr['modalities_present'])}\n\n"

    for raw in raw_events:
        modality = raw["modality"]
        uri = raw["source_uri"]

        if modality == "image":
            media_bytes, mime_type = load_media_bytes(uri)
            if media_bytes and mime_type.startswith("image"):
                content_parts.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))
                context_text += f"IMAGE: Camera frame from {raw['timestamp']}.\n"
                context_text += f"  Vision API labels: {raw['classification'].get('labels', [])}\n"
                context_text += f"  Objects detected: {raw['classification'].get('objects', [])}\n\n"

        elif modality == "audio":
            media_bytes, mime_type = load_media_bytes(uri)
            if media_bytes and mime_type.startswith("audio"):
                content_parts.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))
                context_text += f"AUDIO: Microphone clip from {raw['timestamp']}.\n"
                context_text += f"  Audio classification: {json.dumps(raw['classification'], default=str)}\n\n"

        elif modality == "environmental":
            context_text += f"ENVIRONMENTAL: Sensors at {raw['timestamp']}.\n"
            for key, val in raw.get("metadata", {}).items():
                context_text += f"  {key}: {val}\n"
            context_text += "\n"

        elif modality == "sensor":
            context_text += f"SENSOR: Readings at {raw['timestamp']}.\n"
            for key, val in raw.get("classification", {}).items():
                if key != "source":
                    context_text += f"  {key}: {val}\n"
            context_text += "\n"

    context_text += "Provide your multi-modal fusion assessment as JSON."
    content_parts.append(types.Part.from_text(text=context_text))

    # Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=content_parts,
        config=types.GenerateContentConfig(
            system_instruction=FUSION_SYSTEM_INSTRUCTION,
            temperature=0.2,
            max_output_tokens=4096,
        ),
    )

    try:
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        assessment = json.loads(text.strip())
    except json.JSONDecodeError:
        assessment = {
            "event_type": "unknown",
            "severity": "unknown",
            "summary": "Failed to parse fusion assessment",
            "raw_response": response.text,
        }

    # Store assessment back on the correlated event
    db.collection("correlated-events").document(correlated_event_id).update({
        "fusion_assessment": assessment,
        "state": EventState.ASSESSED,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "model": "gemini-2.5-flash",
    })

    # Update raw events to assessed state
    batch = db.batch()
    for raw_id in raw_ids:
        batch.update(db.collection("raw-events").document(raw_id), {
            "state": EventState.ASSESSED,
        })
    batch.commit()

    # Check if alert should be triggered
    alert_priority = assessment.get("alert_priority", "none")
    if alert_priority in ("immediate", "emergency"):
        print(f"  ⚠ ALERT TRIGGERED: {assessment.get('summary', 'unknown')}")
        # In production: publish to Pub/Sub alerts topic
        db.collection("correlated-events").document(correlated_event_id).update({
            "state": EventState.ALERTED,
            "alert_triggered": True,
        })

    return assessment


# --- Main ---
if __name__ == "__main__":
    # Assess all correlated events that haven't been assessed yet
    query = db.collection("correlated-events") \
        .where("state", "==", EventState.CORRELATED)
    
    for doc in query.stream():
        print(f"\n{'=' * 70}")
        assessment = assess_correlated_event(doc.id)
        print(f"\n  Event type: {assessment.get('event_type')}")
        print(f"  Severity:   {assessment.get('severity')}")
        print(f"  Confidence: {assessment.get('confidence')}")
        print(f"  Summary:    {assessment.get('summary')}")
        print(f"  Alert:      {assessment.get('alert_priority')}")
        print(f"\n  Fusion rationale: {assessment.get('fusion_rationale', 'N/A')}")
```

Run the full pipeline:
```bash
python fusion_assessor.py
```

You should see the fusion assessor load each correlated event, send multi-modal content (image + audio + text context) to Gemini, and get back a unified assessment that explicitly references how the different modalities corroborate or contradict each other.

---

## Step 5: Run the Complete Pipeline End-to-End

Create `run_pipeline.py` — orchestrates all three stages:
```python
"""
End-to-end sensor fusion pipeline.
Simulates events → correlates → assesses → reports.
"""
from simulate_events import simulate_normal_event, simulate_threat_event
from correlator import correlate_pending_events
from fusion_assessor import assess_correlated_event

from google.cloud import firestore
from event_model import EventState

db = firestore.Client()


def run_pipeline():
    print("=" * 70)
    print("STAGE 1: Event Ingestion")
    print("=" * 70)
    
    print("\n--- Normal Surf Event (gauge-001) ---")
    _, normal_ids = simulate_normal_event("gauge-001")

    print("\n--- Threat Event (gauge-002) ---")
    _, threat_ids = simulate_threat_event("gauge-002")

    print(f"\nIngested {len(normal_ids) + len(threat_ids)} raw events")

    print("\n" + "=" * 70)
    print("STAGE 2: Temporal Correlation")
    print("=" * 70)
    
    correlated = correlate_pending_events()
    print(f"\nCreated {len(correlated)} correlated events")

    print("\n" + "=" * 70)
    print("STAGE 3: Multi-Modal Fusion Assessment")
    print("=" * 70)
    
    results = []
    query = db.collection("correlated-events") \
        .where("state", "==", EventState.CORRELATED)
    
    for doc in query.stream():
        assessment = assess_correlated_event(doc.id)
        results.append({
            "event_id": doc.id,
            "type": assessment.get("event_type"),
            "severity": assessment.get("severity"),
            "confidence": assessment.get("confidence"),
            "summary": assessment.get("summary"),
            "alert": assessment.get("alert_priority"),
        })

    print("\n" + "=" * 70)
    print("PIPELINE RESULTS")
    print("=" * 70)
    
    for r in results:
        alert_marker = " ⚠ ALERT" if r["alert"] in ("immediate", "emergency") else ""
        print(f"\n  [{r['severity'].upper():8s}] {r['type']}{alert_marker}")
        print(f"  Confidence: {r['confidence']}")
        print(f"  {r['summary']}")

    print(f"\nTotal events processed: {len(normal_ids) + len(threat_ids)} raw → "
          f"{len(correlated)} correlated → {len(results)} assessed")


if __name__ == "__main__":
    run_pipeline()
```

Run it:
```bash
python run_pipeline.py
```

---

## Step 6: Verify in Firestore

Open Cloud Console → Firestore and explore the three collections:

1. **`raw-events`** — Individual sensor signals with state progression (raw → pending → correlated → assessed)
2. **`correlated-events`** — Grouped multi-modal events with the full `fusion_assessment` JSON from Gemini
3. Check the `modality_contributions` field in the assessment — this shows what each sensor type contributed

Compare the normal surf event assessment (should be low severity, routine) with the threat event assessment (should be high severity with alert).

---

## The Pattern Generalizes

| RiverPulse Fusion Component | Surf Monitoring Equivalent |
|---|---|
| Camera frame + Vision API labels | Beach/break camera + object detection (surfers, vessels, crowds) |
| Audio clip + Gemini classification | Hydrophone / surface mic + acoustic classification (wave impact, engines) |
| Environmental snapshot (temp, humidity) | Weather station (wind speed/direction, air temp, humidity) |
| Numeric gauge readings (cfs, stage) | Buoy telemetry (wave height, period, water temp, tide) |
| 30-second correlation window | Configurable per-location event window |
| Gemini fusion prompt | Same — multi-modal Gemini assessment |
| Alert pipeline → Pub/Sub | Alert pipeline → lifeguard / harbor patrol notification |

---

## Cleanup

```bash
rm -rf ~/sensor-fusion

# Delete Firestore collections
# Console → Firestore → raw-events, correlated-events → Delete
```

---

## Discussion Points for Interviews

- "Single-sensor detections produce too many false positives. The correlation engine groups events from the same gauge within a time window, then the fusion assessor sends ALL modalities — camera frame, audio clip, environmental data, sensor readings — to Gemini in a single prompt. The model reasons across all signals and produces a unified assessment with an explicit confidence score and fusion rationale."

- "The state machine tracks events through their lifecycle: raw → pending → correlated → assessed → alerted. This gives us auditability — you can trace any alert back through every processing step to the original sensor signals. For safety and regulatory use cases, that chain of evidence matters."

- "Fusion fundamentally changes the confidence calculation. Audio alone detecting a boat engine might be 75% — it could be a distant vessel outside the restricted zone. Add a camera frame showing a vessel in frame, and you're at 95%. Add environmental context — low visibility, dusk, no permitted vessels scheduled — and it's a critical alert. No single sensor gets you there."

- "The architecture is fan-out / fan-in. Pub/Sub delivers events to independent processors for each modality. The correlation engine aggregates results downstream. Each processor can fail independently without blocking the others. If audio classification is slow, the image and environmental data still correlate — they just get assessed with fewer modalities."

---

## Learning Summary

This lab combined multiple sensor readings into an event window and uses Gemini inference to correlate the modalities and produce a unified assessment with higher confidence than is possible when processing single events.  We increased our max tokens return in order to allow for the more complete responses.

Oyr event state machine tracks event states and adds context, preventing single-sensor false positives and improving multi-modal analysis results.

With some combinations fusion actually lowered confidence when modalities conflicted.  This is event and sensor dependent.  We also encountered errors in some cases when specifying response mime type as "application/json" which caused unexpected characters and required some additional processing / fence-stripping.

---

## Next Lab

This concludes gcp-labs Series 2.  