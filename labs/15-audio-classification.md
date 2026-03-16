# Lab 15: Audio Classification — Ambient Sound Analysis

# Overview

**Time:** 60–90 minutes  
**Prerequisites:** Lab 14 completed (Gemini via `google-genai` SDK working), Cloud Storage bucket available.

###### New Skills
* Audio processing with Gemini (native audio understanding)
* Generating and working with audio test data (WAV files)
* Audio event classification pipeline (environmental sounds → categories)
* Cloud Function trigger: Cloud Storage upload → audio processing

---

## Concepts (5 minutes)

- **Gemini Audio Understanding:** Gemini 2.0+ models natively process audio input. You send audio bytes (WAV, MP3, FLAC, OGG) directly to the model alongside a text prompt. No separate transcription step needed — the model understands speech, music, environmental sounds, and ambient noise directly.
- **Audio Event Classification:** Categorizing sounds by type — flowing water, rain, thunder, vehicle engines, gunshots, animal calls, human speech. This is pattern recognition on audio waveforms, similar to image classification on pixels.
- **Acoustic Detection Pattern:** A field sensor captures ambient audio, classifies it, and reports events. For RiverPulse: water intensity as a flow proxy, rain detection, thunder (weather alerts). The same pattern applies to any remote monitoring domain — the only thing that changes is the system prompt.

```
[Field Microphone]
      |
      | audio clip upload (triggered by threshold / motion)
      v
[Cloud Storage: audio/]
      |
      | Cloud Function trigger (OBJECT_FINALIZE)
      v
[Gemini: audio + context prompt]
      |
      | structured classification
      v
[Firestore: audio-events]
      |
      | if critical event → [Pub/Sub: alerts]
      v
[Portal / notification pipeline]
```

**Why Gemini for audio instead of a dedicated model?**

You could deploy Whisper on Vertex AI for transcription, or train a custom audio classifier with TensorFlow. But Gemini offers a compelling alternative for v1 systems: it handles audio natively, can reason about what it hears (not just transcribe), and needs no model training or deployment. For a startup building an MVP, that's a massive time-to-value advantage. You can always swap in specialized models later when you have enough labeled data to justify the engineering investment.

---

## Setup

```bash
# Vertex AI should already be enabled from Lab 14
gcloud services list --enabled --filter="name:aiplatform"

# Create working directory
mkdir -p ~/riverpulse/audio-classifier
cd ~/riverpulse/audio-classifier

python3 -m venv .venv
source venv/bin/activate

pip install google-genai google-cloud-storage google-cloud-firestore numpy
```

---

## Step 1: Generate Sample Audio Files

We need audio samples that simulate what a field sensor would capture. We'll generate WAV files programmatically — different frequencies and patterns to represent different environmental sounds.

Create `generate_audio.py`:
```python
"""
Generate sample WAV audio files simulating field sensor captures.

These are synthetic waveforms, not real environmental recordings.
In production, you'd use actual field audio. For the lab, these
test the full pipeline: upload → classify → store.
"""
import struct
import wave
import math
import os
import random

OUTPUT_DIR = os.path.expanduser("~/audio-classifier/samples")


def generate_wav(filepath: str, duration: float, sample_rate: int = 16000,
                 frequencies: list = None, noise_level: float = 0.1,
                 amplitude: float = 0.5):
    """
    Generate a WAV file with mixed sine waves and noise.
    
    Args:
        filepath: Output file path
        duration_sec: Duration in seconds
        sample_rate: Samples per second (16kHz is good for speech/environmental)
        frequencies: List of (freq_hz, rel_amplitude) tuples
        noise_level: Random noise amplitude (0.0 to 1.0)
        amplitude: Overall amplitude scaling
    """
    if frequencies is None:
        frequencies = [(440, 1.0)]

    num_samples = int(duration * sample_rate)
    samples = []

    for i in range(num_samples):
        t = i / sample_rate
        value = 0.0

        # Mix sine waves
        for freq, rel_amp in frequencies:
            value += rel_amp * math.sin(2 * math.pi * freq * t)

        # Add noise
        value += noise_level * (random.random() * 2 - 1)

        # Normalize and scale
        value = max(-1.0, min(1.0, value * amplitude))
        samples.append(value)

    # Write WAV file (16-bit PCM)
    with wave.open(filepath, 'w') as wav:
        wav.setnchannels(1)          # Mono
        wav.setsampwidth(2)          # 16-bit
        wav.setframerate(sample_rate)

        for sample in samples:
            packed = struct.pack('<h', int(sample * 32767))
            wav.writeframes(packed)


def create_test_samples():
    """Create audio samples simulating different environmental sounds."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    samples = {
        # Flowing water: broadband noise with low-frequency emphasis
        "flowing_water_normal": {
            "duration": 5.0,
            "frequencies": [(80, 0.3), (160, 0.2), (320, 0.15), (640, 0.1)],
            "noise_level": 0.4,
            "amplitude": 0.3,
        },
        # Heavy water / rapids: more energy, wider frequency range
        "flowing_water_heavy": {
            "duration": 5.0,
            "frequencies": [(60, 0.5), (120, 0.4), (240, 0.3), (480, 0.25), (960, 0.2)],
            "noise_level": 0.6,
            "amplitude": 0.6,
        },
        # Rain: high-frequency noise bursts
        "rain": {
            "duration": 5.0,
            "frequencies": [(2000, 0.1), (4000, 0.15), (6000, 0.1)],
            "noise_level": 0.5,
            "amplitude": 0.3,
        },
        # Thunder: low-frequency rumble
        "thunder": {
            "duration": 3.0,
            "frequencies": [(30, 0.8), (60, 0.5), (90, 0.3)],
            "noise_level": 0.2,
            "amplitude": 0.7,
        },
        # Boat engine: low-frequency drone with harmonic content
        "boat_engine": {
            "duration": 5.0,
            "frequencies": [(80, 0.6), (160, 0.4), (240, 0.2)],
            "noise_level": 0.15,
            "amplitude": 0.5,
        },
        # Quiet ambient: near-silence with minor noise
        "quiet_ambient": {
            "duration": 5.0,
            "frequencies": [(200, 0.05)],
            "noise_level": 0.05,
            "amplitude": 0.1,
        },
        # Crowd/beach noise: broadband mid-frequency human activity
        "crowd_activity": {
            "duration": 4.0,
            "frequencies": [(300, 0.3), (600, 0.25), (900, 0.2), (1200, 0.15)],
            "noise_level": 0.4,
            "amplitude": 0.4,
        },
        # Wave impact: low-frequency burst with broadband decay
        "wave_impact": {
            "duration": 3.0,
            "frequencies": [(40, 0.7), (80, 0.5), (200, 0.3)],
            "noise_level": 0.5,
            "amplitude": 0.7,
        },
    }

    for name, params in samples.items():
        filepath = os.path.join(OUTPUT_DIR, f"gauge-001-{name}.wav")
        generate_wav(filepath, **params)
        
        # File size
        size_kb = os.path.getsize(filepath) / 1024
        print(f"Created: {filepath} ({size_kb:.0f} KB, {params['duration']}s)")

    print(f"\nGenerated {len(samples)} audio samples in {OUTPUT_DIR}")


if __name__ == "__main__":
    create_test_samples()
```

Generate the samples:
```bash
python generate_audio.py
```

Upload to Cloud Storage:
```bash
PROJECT_ID=$(gcloud config get-value project)
BUCKET="gs://${PROJECT_ID}-riverpulse-data"

gcloud storage cp ~/audio-classifier/samples/*.wav ${BUCKET}/audio/
gcloud storage ls ${BUCKET}/audio/
```

---

## Step 2: Audio Classification with Gemini

Create `audio_classifier.py`:
```python
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

    BUCKET = f"{PROJECT_ID}-riverpulse-data"
    
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
```

Run the batch classifier:
```bash
python audio_classifier.py
```

You should see each audio file classified with a primary sound type, confidence score, threat detection status, and environmental description. The synthetic audio won't produce perfect classifications (Gemini will note they're synthetic), but the pipeline demonstrates the full pattern.

---

## Step 3: Cloud Function Trigger — Auto-Classify on Upload

Create a Cloud Function that fires when audio is uploaded to Cloud Storage, classifies it, and stores the result.  We already built the full classifier module in `audio-classifier/audio_classifier.py`.  Copy and into the cloud function project and import it as we have done in Lab 13 and Lab 14.

```bash
mkdir -p ~/riverpulse/audio-function && cd $_
```

Use the assessor module provided with the lab in `audio-function/audio_classifier.py`.  It contains minor modifications to allow it to work in Cloud Functions.  Primarily this is an update to the environment variable retrieval for `PROJECT_ID`.
```python
# cannot use popen here
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.popen("gcloud config get-value project").read().strip()
```

Create `main.py`:
```python
"""
Cloud Function: Auto-classify audio uploads.

Triggered by OBJECT_FINALIZE on Cloud Storage.
Classifies the audio using Gemini via audio_classifier module,
stores result in Firestore.
"""
import functions_framework
from audio_classifier import classify_audio, store_audio_event

AUDIO_EXTENSIONS = {"wav", "mp3", "flac", "ogg", "m4a"}


@functions_framework.cloud_event
def process_audio(cloud_event):
    """Triggered by Cloud Storage OBJECT_FINALIZE."""
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    if not file_name.startswith("audio/"):
        print(f"Skipping non-audio path: {file_name}")
        return

    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in AUDIO_EXTENSIONS:
        print(f"Skipping non-audio file: {file_name}")
        return

    audio_uri = f"gs://{bucket_name}/{file_name}"
    base_name = file_name.split("/")[-1]
    gauge_id = "-".join(base_name.split("-")[:2]) if "-" in base_name else "unknown"

    print(f"Processing audio: {audio_uri}")

    try:
        classification = classify_audio(audio_uri, gauge_id=gauge_id)
        doc_id = store_audio_event(gauge_id, audio_uri, classification)
        print(f"Stored classification: {doc_id}")

        if classification.get("alert_recommended") or classification.get("threat_detected"):
            print(f"ALERT for {gauge_id}: {classification.get('alert_reason') or classification.get('summary', 'threat detected')}")
            # In production: publish to Pub/Sub alerts topic
            # publisher.publish(alerts_topic, json.dumps({...}).encode())

    except Exception as e:
        print(f"Classification error: {e}")
```

Create `requirements.txt`:
```
functions-framework==3.*
google-genai>=1.14.0
google-cloud-storage>=2.14.0
google-cloud-firestore>=2.14.0
```

Deploy the Cloud Function:
```bash
PROJECT_ID=$(gcloud config get-value project)
BUCKET="${PROJECT_ID}-riverpulse-data"

# Add required IAM role:
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gs-project-accounts.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"

# Deploy:
gcloud functions deploy audio-classifier \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=process_audio \
  --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
  --trigger-event-filters="bucket=${BUCKET}" \
  --memory=512MiB \
  --timeout=120s
```

Test by uploading a new audio file and verifying that it triggers our cloud function.
```bash
# Generate one more sample and upload it
cd ~/riverpulse/audio-classifier
python -c "
from generate_audio import generate_wav
generate_wav('/tmp/test-upload-.wav', 3.0, 
             frequencies=[(100, 0.9), (500, 0.7), (1000, 0.5)],
             noise_level=0.3, amplitude=0.8)
print('Generated test file')
"

gcloud storage cp /tmp/test-upload.wav gs://${BUCKET}/audio/gauge-002-test-event.wav

# Wait for the function to trigger (10-30 seconds)
sleep 15

# Check function logs
gcloud functions logs read audio-classifier --region=us-central1 --limit=10

# Check Firestore for the new classification
# Console → Firestore → audio-events → look for the newest document
```

---

## Step 4: Verify the Complete Pipeline

Check that everything flowed through:

```bash
# 1. Audio file is in Cloud Storage
gcloud storage ls gs://${BUCKET}/audio/

# 2. Cloud Function triggered and ran
gcloud functions logs read audio-classifier --region=us-central1 --limit=5

# 3. Classification is in Firestore
# Open Console → Firestore → audio-events
# Look for documents with trigger: "cloud_function_auto"

# 4. Gauge document updated
# Console → Firestore → gauges → gauge-002 → latestAudio field
```

---

## The Pattern Generalizes

The infrastructure built here applies directly to any remote acoustic monitoring domain. The only thing that changes between deployments is the system prompt and the alert thresholds:

| RiverPulse | Surf Monitoring |
|---|---|
| Flowing water intensity → flow proxy | Wave impact intensity → swell size proxy |
| Rain/thunder → weather alerts | Thunder → lightning closure alert |
| Boat engine → unauthorized access | Boat engine → jet ski intrusion in surf zone |
| Crowd activity → public safety check | Crowd activity → beach capacity monitoring |
| Quiet ambient → normal conditions | Silence → off-hours normal |
| Cloud Function auto-classify | Same: upload-triggered classification |
| Firestore event storage | Same: event storage + alert pipeline |

For a surf monitoring network: break cameras capture 5-second audio clips whenever motion is detected. The Cloud Function classifies each clip automatically — large wave impacts trigger swell alerts, unexpected boat engines in a surf zone trigger safety notifications, thunder triggers beach closure protocols. All built on the same upload → Cloud Function → Gemini → Firestore pipeline.

---

## Cost Analysis

Same Gemini pricing as Lab 14. Audio tokens are based on duration:
- **Audio tokenization:** ~32 tokens per second of audio
- A 5-second clip: ~160 input tokens + prompt tokens + output tokens
- **Per classification:** fraction of a cent

For RiverPulse: 100 gauges × 12 clips/day (every 2 hours) = 1,200 calls/day ≈ $2–3/month.

For a surf monitoring network: motion-triggered clips (not continuous). 50 events/day per camera × 20 break cameras = 1,000 calls/day ≈ $2–5/month. Still far cheaper than running a dedicated audio ML model on Vertex AI.

Cloud Function cost: 512MB × 120s timeout = negligible at this scale. Free tier covers it.

---

## Cleanup
Optional.  Recommended to keep through lab series.

```bash
# Delete the Cloud Function
gcloud functions delete audio-classifier --region=us-central1 --gen2 --quiet

# Remove audio from Cloud Storage
PROJECT_ID=$(gcloud config get-value project)
gcloud storage rm "gs://${PROJECT_ID}-riverpulse-data/audio/**" 2>/dev/null

# Delete Firestore collection
# Console → Firestore → audio-events → Delete collection

# Remove local files
rm -rf ~/audio-classifier ~/audio-function
```

---

## Discussion Points for Interviews

- "We use Gemini's native audio understanding rather than deploying a separate transcription model. For a startup MVP, this is a huge time-to-value win — no model training, no Vertex AI model deployment, no custom inference pipeline. You send audio bytes to the API and get structured classifications back."

- "The Cloud Function triggers automatically on audio upload to Cloud Storage. The gauge captures a clip, uploads it, and the classification happens without any polling or scheduling. The Eventarc trigger on OBJECT_FINALIZE is the same pattern we used for storage notifications in Lab 7."

- "The system prompt is what makes the same infrastructure work across domains. For river monitoring, 'threat_detected' means unauthorized boat access. For a surf break, it might mean thunder (lightning closure) or a jet ski in a restricted zone. You tune the classification vocabulary and alert thresholds in the prompt — no code changes needed."

- "The tradeoff with using Gemini for audio is latency. A Gemini API call takes 1–5 seconds. For time-critical events, you'd want on-device edge inference — a small audio classifier running on the sensor hardware itself. The cloud-side Gemini classification then confirms or enriches the edge detection. That edge-cloud hybrid is the production pattern for latency-sensitive use cases."

- "Audio + image + sensor fusion is the full picture. This lab handles audio. Lab 14 handles image + sensor data. In production, a single event might combine all three: the camera frame, the audio clip, and the environmental readings all go into one multimodal Gemini prompt for a unified assessment."

---

## Architecture Summary — Labs 1–15

```
[Gauge / Camera / Microphone]
      |
      |── MQTT ──────────────► [Compute Engine: MQTT Broker]
      |                              |── bridge ──► [Pub/Sub: sensor-events]
      |
      |── photo upload ──────► [Cloud Storage: images/]
      |                              |── API endpoint ──► [Vision API] ──► [Firestore]
      |                              |── API endpoint ──► [Gemini]    ──► [Firestore]
      |
      |── audio upload ──────► [Cloud Storage: audio/]
                                     |── Cloud Function ──► [Gemini]  ──► [Firestore]
                                     |── if threat ──► [Pub/Sub: alerts]

[Pub/Sub: sensor-events]
      |── push ──► [Cloud Run: riverpulse-api] ──► [Firestore + BigQuery]
      |── Eventarc ──► [Cloud Function: flood-evaluator] ──► [Pub/Sub: alerts]

[Monitoring] ──► [Cloud Monitoring dashboards + alerts]
[Secrets]   ──► [Secret Manager]
[CI/CD]     ──► [Cloud Build ──► Cloud Run deploy]
```

This is a complete multi-modal IoT monitoring platform. The same architecture applies to any remote sensing domain — swap the sensor type and system prompts, and the GCP infrastructure is identical.

---

## Learning Summary

This lab adds audio classification, first as a standalone function, then as a cloud function which triggers events on object finalize.  We set this during deployment of the audio classifier.  We only process and store (in FireStore) audio events -- the early returns skip everything else.

We found and debugged some issues.  For example, the audio_classifier on the cloud function threw errors if we specified MIME type application/json.  As always, we used logs and error outputs to find and fix these errors.

---

## Series Complete

You've built a functional multi-modal monitoring system on GCP:
- **Data ingestion:** MQTT → Pub/Sub → Cloud Run API
- **Storage:** Firestore (operational), BigQuery (analytics), Cloud Storage (files)
- **Processing:** Cloud Functions for event-driven logic
- **AI — Vision:** Cloud Vision API for fast image classification
- **AI — Multimodal:** Gemini for image + text reasoning
- **AI — Audio:** Gemini for environmental sound classification
- **Operations:** Secret Manager, Cloud Monitoring, CI/CD

You can sketch this architecture on a whiteboard and explain every arrow.
