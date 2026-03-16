# Lab 14: Gemini Multimodal — Text + Image Reasoning via Vertex AI

# Overview

**Time:** 60–90 minutes  
**Prerequisites:** Labs 1–5 completed, Lab 13 helpful but not required. Cloud Storage bucket with sample images.

###### New Skills
* Google Gen AI SDK (`google-genai`) with Vertex AI backend
* Gemini multimodal prompting (image + text → structured analysis)
* Structured output from LLMs (JSON mode)
* Integrating AI-generated assessments into the data pipeline

---

## Concepts (5 minutes)

- **Gemini:** Google's multimodal AI model family. Accepts text, images, audio, video as input. Returns text (and optionally images). Runs on Vertex AI or via the Gemini Developer API.
- **Google Gen AI SDK (`google-genai`):** The current unified Python SDK for Gemini. Replaces the deprecated `vertexai.generative_models` module. Works with both Vertex AI and the Gemini Developer API — same code, different client config.
- **Multimodal Prompting:** Sending mixed content types (a photo + text context + a question) in a single request. Gemini processes all modalities together, not sequentially.
- **Structured Output:** Prompting the model to return JSON or another structured format. Critical for pipeline integration — you need parseable data, not prose.
- **Vertex AI Backend:** Running Gemini through Vertex AI gives you enterprise features: VPC-SC, data residency, IAM, audit logging. Same models, different auth and billing path.

Lab 13 used the Vision API for fast, cheap label detection — "what objects are in this image?" This lab uses Gemini for *reasoning* — "given this photo AND these sensor readings, what's happening at this gauge and should we be concerned?"

The difference matters. Vision API returns labels: `["water", "river", "brown", "mud"]`. Gemini returns analysis: "Flow is 1,800 cfs with visible sediment plume extending downstream. Combined with the 340% increase from yesterday's baseline, this indicates a significant runoff event, likely from upstream snowmelt. Conditions are high and potentially hazardous for all craft."

For a surf monitoring system: this is the pattern where a camera frame + buoy sensor data + wind readings get combined into a single AI assessment. "Camera at Mavericks shows a clean 15-foot swell with offshore winds. Buoy data confirms 14-second period at 12 feet. Wind sensors read 8 knots offshore. Classification: excellent big-wave conditions, expert surfers only, strong current advisory."

```
[Photo from gauge camera]  +  [Recent readings from Firestore]
              |                            |
              v                            v
         [Gemini via Vertex AI — multimodal prompt]
                        |
                        v
         [Structured JSON assessment]
                        |
                        v
         [Firestore: ai-assessments]  +  [Alert pipeline if hazardous]
```

---

## Setup

```bash
# Enable Vertex AI API (may already be enabled)
gcloud services enable aiplatform.googleapis.com

# Verify
gcloud services list --enabled --filter="name:aiplatform"

# Create working directory
mkdir -p ~/riverpulse/gemini-multimodal
cd ~/riverpulse/gemini-multimodal

python3 -m venv venv
source venv/bin/activate

# Install the NEW Gen AI SDK — not the deprecated vertexai.generative_models
pip install google-genai google-cloud-firestore google-cloud-storage Pillow
```

**Important SDK note:** As of June 2025, `vertexai.generative_models` is deprecated. The replacement is `google-genai` (`from google import genai`). Same Gemini models, cleaner API, works with both Vertex AI and Gemini Developer API. All code in this lab uses the current SDK.

---

## Step 1: Test Gemini with a Simple Prompt

Create `test_gemini.py`:
```python
"""
Verify Gemini access via Vertex AI using the google-genai SDK.
"""
from google import genai
from google.genai import types
import os

# Initialize client for Vertex AI
# Picks up GOOGLE_CLOUD_PROJECT and region from environment or gcloud config
PROJECT_ID = os.popen("gcloud config get-value project").read().strip()

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
)

# Simple text prompt
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="What are three indicators of flood conditions on a river? Be concise.",
)

print(response.text)
```

Run it:
```bash
python test_gemini.py
```

You should get a concise response about flood indicators. If you get authentication errors, verify your gcloud credentials: `gcloud auth application-default login`.

---

## Step 2: Multimodal Prompt — Image + Context

Now the real thing. Send a gauge photo plus sensor context and get a structured assessment.

Create `assess_conditions.py`:
```python
"""
RiverPulse Condition Assessor

Sends gauge camera photos + recent sensor readings to Gemini
for multimodal condition assessment. Returns structured JSON.
"""
import json
import os
import base64
from datetime import datetime, timezone

from google import genai
from google.genai import types
from google.cloud import storage, firestore


PROJECT_ID = os.popen("gcloud config get-value project").read().strip()

# Initialize Gemini client via Vertex AI
gemini_client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
)

# Firestore client
db = firestore.Client()


# System instruction — defines the AI's role and output format
SYSTEM_INSTRUCTION = """You are a river conditions analyst for the RiverPulse monitoring system.
You assess gauge camera images combined with sensor data to determine current river conditions.

Always respond with valid JSON matching this exact schema:
{
    "condition": "low" | "runnable" | "optimal" | "high" | "flood" | "ice" | "debris" | "unknown",
    "confidence": 0.0 to 1.0,
    "hazard_level": "none" | "low" | "moderate" | "high" | "extreme",
    "summary": "One sentence describing current conditions",
    "details": "2-3 sentences with specific observations from the image and data",
    "recommendations": ["list", "of", "actionable", "recommendations"],
    "observations": {
        "water_color": "description",
        "water_level_visual": "description relative to banks",
        "debris_visible": true/false,
        "ice_visible": true/false,
        "visibility": "clear" | "poor" | "obscured"
    }
}

Base your assessment on BOTH the image and the sensor data provided.
If the image and sensor data conflict, note the discrepancy.
Be specific — reference actual cfs values and visual evidence."""


def load_image_from_gcs(gcs_uri: str) -> tuple[bytes, str]:
    """Load image bytes from a GCS URI."""
    # Parse gs://bucket/path
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    
    image_bytes = blob.download_as_bytes()
    
    # Determine mime type from extension
    ext = blob_name.rsplit(".", 1)[-1].lower()
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", 
                "gif": "image/gif", "webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/jpeg")
    
    return image_bytes, mime_type


def get_recent_readings(gauge_id: str, limit: int = 5) -> list:
    """Fetch recent sensor readings from Firestore for context."""
    query = db.collection("readings") \
        .where("gaugeId", "==", gauge_id) \
        .order_by("timestamp", direction=firestore.Query.DESCENDING) \
        .limit(limit)
    
    readings = []
    for doc in query.stream():
        d = doc.to_dict()
        readings.append({
            "timestamp": str(d.get("timestamp", "")),
            "cfs": d.get("cfs"),
            "stageHeight": d.get("stageHeight"),
            "waterTemp": d.get("waterTemp"),
            "condition": d.get("condition"),
        })
    
    return readings


def assess_gauge(gauge_id: str, image_uri: str, readings: list = None) -> dict:
    """
    Perform multimodal assessment of gauge conditions.
    
    Args:
        gauge_id: The gauge identifier
        image_uri: GCS URI of the camera image
        readings: Optional list of recent readings. If None, fetches from Firestore.
    
    Returns:
        Parsed JSON assessment from Gemini
    """
    # Load the image
    image_bytes, mime_type = load_image_from_gcs(image_uri)

    # Get recent readings if not provided
    if readings is None:
        readings = get_recent_readings(gauge_id)

    # Build the context text
    if readings:
        readings_text = json.dumps(readings, indent=2, default=str)
        context = f"""Gauge: {gauge_id}
Recent sensor readings (most recent first):
{readings_text}

Analyze the attached gauge camera image in combination with this sensor data.
Provide your assessment as JSON."""
    else:
        context = f"""Gauge: {gauge_id}
No recent sensor readings available.

Analyze the attached gauge camera image based on visual evidence only.
Provide your assessment as JSON."""

    # Build multimodal content: image + text
    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        types.Part.from_text(text=context),
    ]

    # Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.2,          # Low temperature for consistent, factual output
            max_output_tokens=2048,
            response_mime_type="application/json",  # Force JSON output
        ),
    )

    # Parse the response
    try:
        assessment = json.loads(response.text)
    except json.JSONDecodeError:
        # If JSON parsing fails, wrap the text response
        assessment = {
            "condition": "unknown",
            "confidence": 0.0,
            "hazard_level": "unknown",
            "summary": "Failed to parse structured response",
            "raw_response": response.text,
        }

    return assessment


def store_assessment(gauge_id: str, image_uri: str, assessment: dict) -> str:
    """Store the AI assessment in Firestore."""
    doc_data = {
        "gaugeId": gauge_id,
        "imageUri": image_uri,
        "assessment": assessment,
        "model": "gemini-2.5-flash",
        "timestamp": datetime.now(timezone.utc),
    }

    _, doc_ref = db.collection("ai-assessments").add(doc_data)

    # Update gauge document with latest AI assessment
    db.collection("gauges").document(gauge_id).set({
        "latestAssessment": {
            "condition": assessment.get("condition", "unknown"),
            "hazardLevel": assessment.get("hazard_level", "unknown"),
            "summary": assessment.get("summary", ""),
            "assessedAt": datetime.now(timezone.utc),
        }
    }, merge=True)

    return doc_ref.id


# --- Main: CLI usage ---
if __name__ == "__main__":
    import sys

    # Default test with sample image
    BUCKET = f"{PROJECT_ID}-riverpulse-data"
    
    gauge_id = "gauge-001"
    image_uri = f"gs://{BUCKET}/images/gauge-001-clear_flow.png"
    
    if len(sys.argv) >= 3:
        gauge_id = sys.argv[1]
        image_uri = sys.argv[2]

    print(f"Assessing: {gauge_id}")
    print(f"Image:     {image_uri}")
    print()

    # Provide sample readings if Firestore doesn't have any
    sample_readings = [
        {"timestamp": "2026-02-04T08:15:00Z", "cfs": 850, "stageHeight": 4.2, 
         "waterTemp": 52, "condition": "optimal"},
        {"timestamp": "2026-02-04T08:10:00Z", "cfs": 840, "stageHeight": 4.1, 
         "waterTemp": 52, "condition": "optimal"},
        {"timestamp": "2026-02-04T08:05:00Z", "cfs": 830, "stageHeight": 4.0, 
         "waterTemp": 51, "condition": "optimal"},
    ]

    # Try Firestore first, fall back to sample data
    readings = get_recent_readings(gauge_id)
    if not readings:
        print("No Firestore readings found — using sample data")
        readings = sample_readings

    assessment = assess_gauge(gauge_id, image_uri, readings=readings)

    print(json.dumps(assessment, indent=2))
    print()

    # Store in Firestore
    doc_id = store_assessment(gauge_id, image_uri, assessment)
    print(f"Stored assessment: {doc_id}")
```

Run it:
```bash
python assess_conditions.py
```

You should get a structured JSON assessment that combines visual analysis of the image with interpretation of the sensor readings. The response will reference both the visual appearance and the cfs values.

---

## Step 3: Add Assessment Endpoint to Cloud Run API

We already built the full assessor module in `gemini-multimodal/assess_conditions.py`. Copy it into the API project and import it — same pattern as Lab 13.

```bash
# Copy the assessor module into the API project
cp ~/riverpulse/gemini-multimodal/assess_conditions.py ~/riverpulse/riverpulse-api/assess_conditions.py
```

Add `google-genai` to `requirements.txt`:
```
google-genai>=1.14.0
```

Now add these routes to the existing `riverpulse-api` `main.py`:

```python
# Add to imports at top of main.py
from assess_conditions import assess_gauge, store_assessment

# ============ AI ASSESSMENT ============
@app.route('/assess', methods=['POST'])
def assess_conditions_endpoint():
    """
    AI-powered multimodal gauge assessment.

    Request body:
    {
        "gaugeId": "gauge-001",
        "imageUri": "gs://bucket/images/photo.jpg"
    }
    """
    data = request.get_json()
    if not data or 'imageUri' not in data:
        return jsonify({"error": "imageUri is required"}), 400

    gauge_id = data.get('gaugeId', 'unknown')
    image_uri = data['imageUri']

    try:
        assessment = assess_gauge(gauge_id, image_uri)
        doc_id = store_assessment(gauge_id, image_uri, assessment)

        return jsonify({
            "id": doc_id,
            "gaugeId": gauge_id,
            "assessment": assessment,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

Redeploy and test:
```bash
cd ~/riverpulse/riverpulse-api
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1

SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')
PROJECT_ID=$(gcloud config get-value project)

curl -X POST ${SERVICE_URL}/assess \
  -H "Content-Type: application/json" \
  -d "{
    \"gaugeId\": \"gauge-001\",
    \"imageUri\": \"gs://${PROJECT_ID}-riverpulse-data/images/gauge-001-high_water.png\"
  }" | python3 -m json.tool
```

---

## Step 4: Compare Vision API vs Gemini Output

Run both on the same image to see the difference:

```bash
# Vision API (Lab 13 — labels)
curl -X POST ${SERVICE_URL}/images/classify \
  -H "Content-Type: application/json" \
  -d "{
    \"gaugeId\": \"gauge-001\",
    \"imageUri\": \"gs://${PROJECT_ID}-riverpulse-data/images/gauge-001-high_water.png\"
  }" | python3 -m json.tool

echo "---"

# Gemini (Lab 14 — reasoning)
curl -X POST ${SERVICE_URL}/assess \
  -H "Content-Type: application/json" \
  -d "{
    \"gaugeId\": \"gauge-001\",
    \"imageUri\": \"gs://${PROJECT_ID}-riverpulse-data/images/gauge-001-high_water.png\"
  }" | python3 -m json.tool
```

The Vision API returns raw labels: `"water", "brown", "muddy"`. Gemini returns contextual analysis: condition assessment, hazard level, actionable recommendations. Different tools for different purposes — and you'd use both in production.

---

## Step 5: Verify in Firestore

Open Cloud Console → Firestore:
1. `ai-assessments` collection — see full assessment documents
2. `gauges` → `gauge-001` → `latestAssessment` field — most recent AI assessment
3. Compare with `latestImage` from Lab 13 — both update the gauge document

---

## Cost Analysis

Gemini pricing on Vertex AI (gemini-2.5-flash, as of 2025):
- **Input:** ~$0.075 per 1M tokens (text), images tokenized based on resolution
- **Output:** ~$0.30 per 1M tokens
- A typical image + context prompt: ~1,200 input tokens, ~200 output tokens
- **Per assessment:** fraction of a cent

For RiverPulse: 100 gauges × 4 assessments/day = 400 calls/day = ~$1–2/month. Negligible.

For a high-volume surf monitoring network: even at 1,000 assessments/day across dozens of break cameras, Gemini costs under $10/month. The Vision API first-pass filter (Lab 13) reduces Gemini calls by rejecting empty or obscured frames, which keeps costs low even at high camera volumes.

---

## Cleanup

Optional.  Recommended to keep at least through conclusion of the labs series.
```bash
# Remove local files
rm -rf ~/riverpulse/gemini-multimodal

# Delete Firestore collection
# Console → Firestore → ai-assessments → Delete collection

# Vertex AI API stays enabled — used in Lab 15
```

---

## Discussion Points for Interviews

- "We use a two-tier AI approach. Vision API handles the first pass — fast label detection at $1.50 per thousand images. Only images flagged as interesting get routed to Gemini for deeper multimodal analysis. This keeps costs under control while getting rich assessments where they matter."

- "The Gemini prompt combines the camera image with recent sensor readings. The model reasons across both modalities — it can correlate 'the water looks brown and high' with 'cfs jumped from 800 to 1800 in 6 hours' and produce a meaningful assessment that neither data source alone would support."

- "We force JSON output using `response_mime_type='application/json'` and a schema in the system instruction. This makes the AI output parseable by downstream systems. The assessment goes straight into Firestore and can trigger alerts without any human parsing."

- "Temperature is set to 0.2 for consistency. We want the same image and data to produce similar assessments across calls. For creative tasks you'd increase temperature, but for operational monitoring, determinism is better."

- "For a surf monitoring system, this same pattern processes camera frames + buoy readings + wind sensor data through a single multimodal prompt. The model fuses all the signals: 'image shows 12-foot faces with offshore winds, buoy confirms 14-second period, wind station reads 10 knots west — classify as excellent big-wave conditions, expert surfers only.' That sensor fusion in the prompt is more flexible than building a custom fusion model."

- "We're using the `google-genai` SDK, not the deprecated `vertexai.generative_models`. The new SDK is the unified interface for both Vertex AI and the Gemini Developer API — same code, swap one line to switch backends. This is what Google recommends going forward."

---

## Learning Summary

The main point of this lab is to clearly demonstrate the improvements provided by the two-tiered approach and the introduction of more advanced AI into the pipeline.  The first tier, Vision API, uses a pretrained neural network with a fixed vocabulary to provide basic, fast, and cheap classificaiton.

Anything flagged as potentially interesting is sent to a more sophisticated multimodal transformer (e.g. "an AI") which uses more sophisticated (and more expensive) reasoning across multiple modalities to provide a higher level of understanding and reasoning.

"More expensive" is mostly theoretical until running large volume.  We're receiving around 1000 tokens output on our sample, which costs us about $0.0003 (3/100 of a cent).

---

## Next Lab

Lab 15: Audio Classification — processing ambient audio from gauge microphones for environmental monitoring.
