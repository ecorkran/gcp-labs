"""
RiverPulse Audio Classifier

Classifies ambient audio from gauge microphones using Gemini's
native audio understanding. Returns structured event classifications.
"""
import json
import os
from datetime import datetime, timezone

from google import genai
from google.genai import types
from google.cloud import storage, firestore


PROJECT_ID = os.popen("gcloud config get-value project").read().strip()

gemini_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
)

db = firestore.Client()


AUDIO_SYSTEM_INSTRUCTION = """You are an environmental audio analyst for the RiverPulse monitoring system.
You classify ambient audio captured by field sensors near rivers and remote locations.

Analyze the audio and respond with valid JSON matching this schema:
{
    "primary_sound": "flowing_water" | "wave_impact" | "rain" | "thunder" | "wind" | "boat_engine" |
                     "human_voice" | "crowd_activity" | "machinery" | "silence" | "unknown",
    "confidence": 0.0 to 1.0,
    "all_detected_sounds": [
        {"sound": "name", "confidence": 0.0-1.0, "approximate_timing": "description"}
    ],
    "water_flow_estimate": "none" | "low" | "moderate" | "high" | "extreme",
    "weather_indicators": ["rain", "thunder", "wind", "clear"],
    "human_activity": true | false,
    "threat_detected": true | false,
    "threat_type": null | "boat_intrusion" | "human_presence" | "other",
    "overall_environment": "One sentence describing the acoustic environment",
    "alert_recommended": true | false,
    "alert_reason": null | "reason for alert"
}

Key classification rules:
- Wave impact: Low-frequency burst with broadband decay, periodic rhythm.
- Boat engines: Low-frequency sustained drone, 60-240 Hz dominant, constant.
- Crowd/beach activity: Broadband mid-frequency noise, intermittent voice patterns.
- Flowing water: Broadband noise, relatively constant, low-to-mid frequency.
- Rain: High-frequency broadband noise, more stochastic than water flow.
- Thunder: Low-frequency rumble, 20-100 Hz, intermittent.
- Human voice: Formant patterns in 300-3400 Hz range, intermittent.

Note: These are synthetic waveforms for testing, not real environmental audio.
Classify based on the acoustic patterns you detect."""


def classify_audio(audio_uri: str, gauge_id: str = "unknown",
                   context: str = None) -> dict:
    """
    Classify audio from a GCS URI using Gemini.
    
    Args:
        audio_uri: GCS URI of the audio file
        gauge_id: Gauge identifier for context
        context: Optional additional context (location, recent events, etc.)
    
    Returns:
        Parsed JSON classification
    """
    # Load audio from GCS
    parts = audio_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    audio_bytes = blob.download_as_bytes()

    # Determine MIME type
    ext = blob_name.rsplit(".", 1)[-1].lower()
    mime_map = {
        "wav": "audio/wav",
        "mp3": "audio/mp3",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "m4a": "audio/m4a",
    }
    mime_type = mime_map.get(ext, "audio/wav")

    # Build prompt
    prompt_text = f"Gauge: {gauge_id}\n"
    if context:
        prompt_text += f"Context: {context}\n"
    prompt_text += "\nClassify the environmental audio captured by this gauge sensor."

    # Build multimodal content: audio + text
    contents = [
        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
        types.Part.from_text(text=prompt_text),
    ]

    # Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=AUDIO_SYSTEM_INSTRUCTION,
            temperature=0.2,
            max_output_tokens=2048,
            response_mime_type="application/json",
        ),
    )

    try:
        classification = json.loads(response.text)
    except json.JSONDecodeError:
        classification = {
            "primary_sound": "unknown",
            "confidence": 0.0,
            "overall_environment": "Failed to parse response",
            "raw_response": response.text,
        }

    return classification


def store_audio_event(gauge_id: str, audio_uri: str, classification: dict) -> str:
    """Store audio classification in Firestore and optionally trigger alerts."""
    doc_data = {
        "gaugeId": gauge_id,
        "audioUri": audio_uri,
        "classification": classification,
        "model": "gemini-3.0-flash",
        "timestamp": datetime.now(timezone.utc),
        "alertTriggered": classification.get("alert_recommended", False),
    }

    _, doc_ref = db.collection("audio-events").add(doc_data)

    # Update gauge with latest audio event
    db.collection("gauges").document(gauge_id).set({
        "latestAudio": {
            "primarySound": classification.get("primary_sound", "unknown"),
            "threatDetected": classification.get("threat_detected", False),
            "environment": classification.get("overall_environment", ""),
            "recordedAt": datetime.now(timezone.utc),
        }
    }, merge=True)

    return doc_ref.id


# --- Main: CLI usage ---
if __name__ == "__main__":
    import sys

    BUCKET = f"riverpulse-demo-riverpulse-data"
    
    # Default: classify all audio files in the bucket
    if len(sys.argv) >= 2:
        # Single file mode
        audio_uri = sys.argv[1]
        gauge_id = sys.argv[2] if len(sys.argv) >= 3 else "gauge-001"
        
        print(f"Classifying: {audio_uri}")
        classification = classify_audio(audio_uri, gauge_id=gauge_id)
        print(json.dumps(classification, indent=2))
        
        doc_id = store_audio_event(gauge_id, audio_uri, classification)
        print(f"\nStored: {doc_id}")
    else:
        # Batch mode: classify all audio in bucket
        gcs_client = storage.Client()
        bucket = gcs_client.bucket(f"{BUCKET}")
        blobs = bucket.list_blobs(prefix="audio/")
        
        audio_exts = {".wav", ".mp3", ".flac", ".ogg"}
        
        for blob in blobs:
            ext = "." + blob.name.rsplit(".", 1)[-1].lower() if "." in blob.name else ""
            if ext not in audio_exts:
                continue
            
            audio_uri = f"gs://{BUCKET}/{blob.name}"
            filename = blob.name.split("/")[-1]
            gauge_id = "-".join(filename.split("-")[:2]) if "-" in filename else "unknown"
            
            print(f"\n{'=' * 60}")
            print(f"File: {filename}")
            print(f"Gauge: {gauge_id}")
            
            try:
                classification = classify_audio(audio_uri, gauge_id=gauge_id)
                
                primary = classification.get("primary_sound", "unknown")
                confidence = classification.get("confidence", 0)
                threat = classification.get("threat_detected", False)
                env = classification.get("overall_environment", "")
                
                print(f"Primary: {primary} ({confidence:.0%})")
                print(f"Threat:  {threat}")
                print(f"Env:     {env}")
                
                if classification.get("alert_recommended"):
                    print(f"⚠ ALERT: {classification.get('alert_reason', 'unknown')}")
                
                doc_id = store_audio_event(gauge_id, audio_uri, classification)
                print(f"Stored:  {doc_id}")
                
            except Exception as e:
                print(f"ERROR: {e}")
        
        print(f"\n{'=' * 60}")
        print("Batch classification complete")
