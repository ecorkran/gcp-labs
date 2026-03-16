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
#BUCKET = f"{PROJECT_ID}-riverpulse-data"
BUCKET = f"riverpulse-data-riverpulse-demo"

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
