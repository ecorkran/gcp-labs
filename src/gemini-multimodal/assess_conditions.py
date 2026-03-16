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
    # TODO: fix the bucket name
    #BUCKET = f"{PROJECT_ID}-riverpulse-data"
    BUCKET = "riverpulse-data-riverpulse-demo"
    
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
