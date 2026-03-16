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
            #response_mime_type="application/json",
        ),
    )

    print(f"Finish reason: {response.candidates[0].finish_reason if response.candidates else 'no candidates'}")
    print(f"Raw response: {response.text}")  # add this line
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
