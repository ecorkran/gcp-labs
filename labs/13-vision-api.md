# Lab 13: Vision API — Image Classification from Gauge Cameras

# Overview

**Time:** 60–90 minutes  
**Prerequisites:** Labs 1–5 completed (Cloud Run API, Firestore, Cloud Storage with `riverpulse-data` bucket)  

###### New Skills
* Cloud Vision API (pre-trained label detection, safe search)
* Processing images from Cloud Storage via API
* Storing classification results in Firestore alongside sensor readings
* Cloud Run endpoint for on-demand image analysis

---

## Concepts (5 minutes)

- **Cloud Vision API:** Google's pre-trained computer vision models exposed as a REST/gRPC API. No training required — you send an image, it returns labels, objects, text, faces, landmarks, safe-search scores.
- **Label Detection:** Identifies general objects, scenes, activities. Returns labels with confidence scores. "Water", "River", "Snow", "Flood", "Debris" — exactly what a gauge camera would capture.
- **Object Detection (Localization):** Like label detection but also returns bounding boxes. Useful for counting objects or understanding spatial relationships.
- **Safe Search Detection:** Classifies content for safety categories. For RiverPulse, this filters out accidental or malicious uploads before they reach downstream processing.
- **Feature Request:** You specify which detection features you want per image. Each feature is a billable unit. First 1,000 units/month are free.

RiverPulse gauges in the field may include cameras — periodic snapshots of river conditions that supplement numeric flow data. A photo showing ice formation, debris accumulation, flooding, or clear water provides context that raw cfs numbers can't capture. The Vision API classifies these images automatically without any model training.

Again this is relevant to numerous use cases outside of flow monitoring -- power grids, construction sites, security, and more.  We're using the Vision API to handle a quick "first pass" for fast, cheap classification.  We'll use more advanced multimodal reasoning with Gemini starting in Lab 14.

```
[Gauge Camera]
      |
      | photo upload
      v
[Cloud Storage: riverpulse-data/images/]
      |
      | Cloud Run endpoint or Cloud Function
      v
[Cloud Vision API]
      |
      | labels, objects, safe-search
      v
[Firestore: image-classifications]
      |
      | linked to gauge readings
      v
[Portal / alerting pipeline]
```

AWS equivalent: Amazon Rekognition (very close). Same pattern — send image, get labels and confidence scores. Pricing model is similar (per-image).

---

## Setup

```bash
# Enable Vision API
gcloud services enable vision.googleapis.com

# Verify
gcloud services list --enabled --filter="name:vision"
```

Vision API uses the default Compute Engine service account for authentication on Cloud Run. No additional IAM bindings needed — the service account already has project-level access.

---

## Step 1: Install and Test Vision API "Locally" in Cloud Shell
We'll test the Vision API as a standalone script in Cloud Shell before integrating it into the API. Cloud Shell already has Python and GCP authentication configured — no additional setup needed.

```bash
mkdir -p ~/riverpulse/gauge-vision && cd $_

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install google-cloud-vision google-cloud-storage google-cloud-firestore
```

Create `test_vision.py` — a standalone script to verify Vision API works:
```python
"""
Quick test: classify a public image using Cloud Vision API.
"""
from google.cloud import vision

def classify_image_uri(image_uri: str):
    """Classify an image from a GCS URI or public URL."""
    client = vision.ImageAnnotatorClient()

    image = vision.Image()
    image.source.image_uri = image_uri

    # Request multiple feature types in one call
    features = [
        vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=10),
        vision.Feature(type_=vision.Feature.Type.OBJECT_LOCALIZATION, max_results=5),
        vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
    ]

    request = vision.AnnotateImageRequest(image=image, features=features)
    response = client.annotate_image(request=request)

    if response.error.message:
        raise Exception(f"Vision API error: {response.error.message}")

    return response


def print_results(response):
    """Print classification results in a readable format."""
    print("=" * 60)
    print("LABEL DETECTION")
    print("=" * 60)
    for label in response.label_annotations:
        print(f"  {label.score:5.1%}  {label.description}")

    print()
    print("=" * 60)
    print("OBJECT LOCALIZATION")
    print("=" * 60)
    for obj in response.localized_object_annotations:
        print(f"  {obj.score:5.1%}  {obj.name}")
        vertices = obj.bounding_poly.normalized_vertices
        print(f"         box: ({vertices[0].x:.2f},{vertices[0].y:.2f}) -> ({vertices[2].x:.2f},{vertices[2].y:.2f})")

    print()
    print("=" * 60)
    print("SAFE SEARCH")
    print("=" * 60)
    safe = response.safe_search_annotation
    likelihood_names = {0: "UNKNOWN", 1: "VERY_UNLIKELY", 2: "UNLIKELY",
                        3: "POSSIBLE", 4: "LIKELY", 5: "VERY_LIKELY"}
    print(f"  Adult:    {likelihood_names.get(safe.adult, 'UNKNOWN')}")
    print(f"  Violence: {likelihood_names.get(safe.violence, 'UNKNOWN')}")
    print(f"  Racy:     {likelihood_names.get(safe.racy, 'UNKNOWN')}")
    print(f"  Spoof:    {likelihood_names.get(safe.spoof, 'UNKNOWN')}")


if __name__ == "__main__":
    # Use a public sample image — river scene
    test_uri = "gs://cloud-samples-data/vision/label/wakeupcat.jpg"
    print(f"Classifying: {test_uri}\n")

    response = classify_image_uri(test_uri)
    print_results(response)
```

Run it:
```bash
python test_vision.py
```

You should see labels with confidence scores, detected objects with bounding boxes, and safe search ratings. The output tells you the Vision API is working and authenticated.

---

## Step 2: Upload Sample Gauge Images to Cloud Storage

We'll create sample "gauge camera" images by uploading a few public-domain river photos. In production these would come from actual field cameras.

```bash
# Create an images prefix in the existing bucket
PROJECT_ID=$(gcloud config get-value project)
BUCKET="gs://${PROJECT_ID}-riverpulse-data"

# Download some sample images (public domain river/nature photos)
# These simulate what a gauge camera would capture
mkdir -p ~/riverpulse/gauge-vision/sample-images

# Use Google's sample images or create placeholder descriptions
# Option 1: Use curl to grab CC0 images (if available)
# Option 2: Create a simple Python script to generate test images

cat > ~/riverpulse/gauge-vision/create_test_images.py << 'EOF'
"""
Create simple test images that simulate gauge camera output.
In production, these would be actual field photos.
For the lab, we use solid colored images with text overlays
to represent different river conditions.
"""
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Pillow not installed. Creating minimal PNG files instead.")

import struct
import zlib
import os

OUTPUT_DIR = os.path.expanduser("~/riverpulse/gauge-vision/sample-images")

# Conditions we want to classify
CONDITIONS = {
    "clear_flow": (34, 139, 230),      # Blue — clear water
    "high_water": (139, 90, 43),        # Brown — muddy flood
    "ice_formation": (200, 220, 235),   # Light blue/white — ice
    "debris_field": (80, 80, 60),       # Dark — debris
}


def create_minimal_png(filepath, r, g, b, width=640, height=480):
    """Create a minimal valid PNG without PIL."""
    # PNG header
    header = b'\x89PNG\r\n\x1a\n'
    
    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc & 0xffffffff)
    
    # IDAT chunk — create raw image data
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00'  # filter byte
        for x in range(width):
            raw_data += bytes([r, g, b])
    
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc & 0xffffffff)
    
    # IEND chunk
    iend_crc = zlib.crc32(b'IEND')
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc & 0xffffffff)
    
    with open(filepath, 'wb') as f:
        f.write(header + ihdr + idat + iend)


def create_test_images():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    for name, (r, g, b) in CONDITIONS.items():
        filepath = os.path.join(OUTPUT_DIR, f"gauge-001-{name}.png")
        
        if HAS_PIL:
            img = Image.new('RGB', (640, 480), (r, g, b))
            draw = ImageDraw.Draw(img)
            # Add text overlay
            draw.text((20, 20), f"Gauge: gauge-001", fill=(255, 255, 255))
            draw.text((20, 50), f"Condition: {name}", fill=(255, 255, 255))
            draw.text((20, 80), f"Simulated gauge camera image", fill=(200, 200, 200))
            img.save(filepath)
        else:
            create_minimal_png(filepath, r, g, b)
        
        print(f"Created: {filepath}")


if __name__ == "__main__":
    create_test_images()
EOF

pip install Pillow 2>/dev/null || echo "Pillow not available, using minimal PNG fallback"
python create_test_images.py

# Upload to Cloud Storage
gcloud storage cp ~/riverpulse/gauge-vision/sample-images/*.png ${BUCKET}/images/
gcloud storage ls ${BUCKET}/images/
```

---

## Step 3: Build the Image Classification Module

This is the core module that classifies images and stores results. It'll be used by both the CLI and the Cloud Run endpoint.

Create `classifier.py`:
```python
"""
RiverPulse Image Classifier

Classifies gauge camera images using Cloud Vision API,
maps labels to river conditions, and stores results in Firestore.
"""
import os
from datetime import datetime, timezone

from google.cloud import vision
from google.cloud import firestore


# River condition mapping — Vision API labels to RiverPulse conditions
# These thresholds and mappings would be tuned with real field data
CONDITION_KEYWORDS = {
    "flood": {
        "labels": ["flood", "flooding", "muddy", "turbid", "overflow", "brown water"],
        "min_confidence": 0.6,
    },
    "ice": {
        "labels": ["ice", "frost", "frozen", "snow", "winter", "icicle"],
        "min_confidence": 0.6,
    },
    "debris": {
        "labels": ["debris", "log", "wood", "trash", "obstruction", "branch", "tree"],
        "min_confidence": 0.5,
    },
    "clear": {
        "labels": ["water", "river", "stream", "creek", "clear", "blue water", "nature"],
        "min_confidence": 0.5,
    },
}

# Safe search thresholds — reject images above these
# Likelihood enum: UNKNOWN=0, VERY_UNLIKELY=1, UNLIKELY=2, POSSIBLE=3, LIKELY=4, VERY_LIKELY=5
SAFE_SEARCH_MAX = {
    "adult": 3,     # Block POSSIBLE and above
    "violence": 4,  # Block LIKELY and above (some river scenes may look dramatic)
}


def classify_image(image_uri: str) -> dict:
    """
    Classify a gauge camera image using Vision API.
    
    Args:
        image_uri: GCS URI (gs://bucket/path) or public URL
        
    Returns:
        dict with labels, objects, safe_search, and derived condition
    """
    client = vision.ImageAnnotatorClient()

    image = vision.Image()
    image.source.image_uri = image_uri

    features = [
        vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=15),
        vision.Feature(type_=vision.Feature.Type.OBJECT_LOCALIZATION, max_results=10),
        vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
    ]

    request = vision.AnnotateImageRequest(image=image, features=features)
    response = client.annotate_image(request=request)

    if response.error.message:
        raise Exception(f"Vision API error: {response.error.message}")

    # Extract results
    labels = [
        {"description": l.description.lower(), "score": round(l.score, 4)}
        for l in response.label_annotations
    ]

    objects = [
        {
            "name": o.name.lower(),
            "score": round(o.score, 4),
            "bounds": {
                "x_min": round(o.bounding_poly.normalized_vertices[0].x, 4),
                "y_min": round(o.bounding_poly.normalized_vertices[0].y, 4),
                "x_max": round(o.bounding_poly.normalized_vertices[2].x, 4),
                "y_max": round(o.bounding_poly.normalized_vertices[2].y, 4),
            }
        }
        for o in response.localized_object_annotations
    ]

    safe = response.safe_search_annotation
    safe_search = {
        "adult": safe.adult,
        "violence": safe.violence,
        "racy": safe.racy,
        "spoof": safe.spoof,
    }

    # Check safe search — flag if above thresholds
    flagged = False
    flag_reasons = []
    for category, max_level in SAFE_SEARCH_MAX.items():
        level = safe_search.get(category, 0)
        if level >= max_level:
            flagged = True
            flag_reasons.append(f"{category}={level}")

    # Derive river condition from labels
    condition = derive_condition(labels)

    return {
        "labels": labels,
        "objects": objects,
        "safe_search": safe_search,
        "flagged": flagged,
        "flag_reasons": flag_reasons,
        "derived_condition": condition,
    }


def derive_condition(labels: list) -> dict:
    """
    Map Vision API labels to a RiverPulse river condition.
    Returns the highest-confidence matching condition.
    """
    label_descriptions = {l["description"]: l["score"] for l in labels}
    
    best_condition = None
    best_score = 0.0

    for condition, config in CONDITION_KEYWORDS.items():
        for keyword in config["labels"]:
            if keyword in label_descriptions:
                score = label_descriptions[keyword]
                if score >= config["min_confidence"] and score > best_score:
                    best_condition = condition
                    best_score = score

    if best_condition is None:
        return {"condition": "unknown", "confidence": 0.0, "matched_keyword": None}

    return {
        "condition": best_condition,
        "confidence": round(best_score, 4),
        "matched_keyword": None,  # Could track which keyword matched
    }


def store_classification(gauge_id: str, image_uri: str, classification: dict) -> str:
    """
    Store image classification results in Firestore.
    
    Returns the Firestore document ID.
    """
    db = firestore.Client()

    doc_data = {
        "gaugeId": gauge_id,
        "imageUri": image_uri,
        "timestamp": datetime.now(timezone.utc),
        "labels": classification["labels"],
        "objects": classification["objects"],
        "safeSearch": classification["safe_search"],
        "flagged": classification["flagged"],
        "flagReasons": classification["flag_reasons"],
        "derivedCondition": classification["derived_condition"],
    }

    # Store in image-classifications collection
    doc_ref = db.collection("image-classifications").add(doc_data)
    doc_id = doc_ref[1].id

    # Also update the gauge document with latest image classification
    gauge_ref = db.collection("gauges").document(gauge_id)
    gauge_ref.set({
        "latestImage": {
            "uri": image_uri,
            "condition": classification["derived_condition"]["condition"],
            "confidence": classification["derived_condition"]["confidence"],
            "classifiedAt": datetime.now(timezone.utc),
        }
    }, merge=True)

    return doc_id
```

---

## Step 4: CLI Tool for Batch Classification

Create `classify_batch.py` to classify all images in a GCS prefix:
```python
"""
Batch classify all gauge images in a Cloud Storage prefix.
Useful for initial testing and backfill processing.
"""
import sys
from google.cloud import storage
from classifier import classify_image, store_classification


def classify_bucket_images(bucket_name: str, prefix: str = "images/"):
    """Classify all images under a GCS prefix."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    blobs = bucket.list_blobs(prefix=prefix)
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    
    results = []
    for blob in blobs:
        ext = "." + blob.name.rsplit(".", 1)[-1].lower() if "." in blob.name else ""
        if ext not in image_extensions:
            continue
        
        image_uri = f"gs://{bucket_name}/{blob.name}"
        print(f"\nClassifying: {image_uri}")
        
        try:
            classification = classify_image(image_uri)
            
            # Extract gauge ID from filename convention: gauge-XXX-condition.ext
            filename = blob.name.split("/")[-1]
            gauge_id = "-".join(filename.split("-")[:2]) if "-" in filename else "unknown"
            
            doc_id = store_classification(gauge_id, image_uri, classification)
            
            condition = classification["derived_condition"]
            print(f"  Condition: {condition['condition']} ({condition['confidence']:.0%})")
            print(f"  Labels: {', '.join(l['description'] for l in classification['labels'][:5])}")
            print(f"  Flagged: {classification['flagged']}")
            print(f"  Stored: {doc_id}")
            
            results.append({
                "image": image_uri,
                "condition": condition["condition"],
                "doc_id": doc_id,
            })
            
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"image": image_uri, "error": str(e)})
    
    print(f"\n{'=' * 60}")
    print(f"Classified {len(results)} images")
    return results


if __name__ == "__main__":
    import os
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.popen("gcloud config get-value project").read().strip()
    bucket = f"{project_id}-riverpulse-data"
    
    if len(sys.argv) > 1:
        bucket = sys.argv[1]
    
    classify_bucket_images(bucket, prefix="images/")
```

Run the batch classification:
```bash
python classify_batch.py
```

Check results in Firestore Console → `image-classifications` collection. You should see documents with labels, confidence scores, and derived conditions.

---

## Step 5: Add Image Classification Endpoint to Cloud Run API

We already built a solid classifier module in `gauge-vision/classifier.py`. Rather than rewriting a weaker version inline, we'll copy the module into the API project and import it.

```bash
# Copy the classifier module into the API project
cp ~/riverpulse/gauge-vision/classifier.py ~/riverpulse/riverpulse-api/classifier.py
```

Add `google-cloud-vision` to `requirements.txt`:
```
google-cloud-vision==3.7.0
```

Now add these routes to the existing `riverpulse-api` `main.py`:

```python
# Add to imports at top of main.py
from classifier import classify_image, store_classification

# ============ IMAGE CLASSIFICATION ============
@app.route('/images/classify', methods=['POST'])
def classify_image_endpoint():
    """
    Classify a gauge camera image.

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
        classification = classify_image(image_uri)

        # Check safe search flags before storing
        if classification["flagged"]:
            return jsonify({
                "error": "Image flagged by safe search",
                "reasons": classification["flag_reasons"],
            }), 422

        doc_id = store_classification(gauge_id, image_uri, classification)

        return jsonify({
            "id": doc_id,
            "gaugeId": gauge_id,
            "imageUri": image_uri,
            "labels": classification["labels"],
            "objects": classification["objects"],
            "derivedCondition": classification["derived_condition"],
            "flagged": classification["flagged"],
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/gauges/<gauge_id>/images', methods=['GET'])
def get_gauge_images(gauge_id):
    """Get image classifications for a specific gauge."""
    query = db.collection("image-classifications") \
        .where("gaugeId", "==", gauge_id) \
        .order_by("timestamp", direction=firestore.Query.DESCENDING) \
        .limit(20)

    docs = query.stream()
    classifications = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        classifications.append(d)

    return jsonify({"gaugeId": gauge_id, "images": classifications})
```

Redeploy:
```bash
cd ~/riverpulse/riverpulse-api
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1
```

Test the endpoint:
```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

PROJECT_ID=$(gcloud config get-value project)

# Classify a gauge image
curl -X POST ${SERVICE_URL}/images/classify \
  -H "Content-Type: application/json" \
  -d "{
    \"gaugeId\": \"gauge-001\",
    \"imageUri\": \"gs://${PROJECT_ID}-riverpulse-data/images/gauge-001-clear_flow.png\"
  }" | python3 -m json.tool

# Get classifications for a gauge
curl ${SERVICE_URL}/gauges/gauge-001/images | python3 -m json.tool
```

You are likely to receive an error related to needing an index for the composite query.  If results are not as expected, checl logs:
```sh
gcloud logging read 'resource.type="cloud_run_revision" AND textPayload:"index"' \
  --limit=5 --freshness=10m
```

If you see anything about requiring an index, it should provide you with a URL to click and create one.  Follow it, create the index, wait about 1 minute, and run again.

The endpoint now uses the same full classifier from Steps 3–4: all three Vision API features, condition mapping, safe search thresholds, and structured Firestore storage. One module, one behavior — whether called from CLI or API.

---

## Step 6: Verify in Firestore Console

Open Cloud Console → Firestore:
1. Click on `image-classifications` collection — see classification documents
2. Each document has labels, confidence scores, safe search ratings
3. Click on `gauges` → `gauge-001` — see `latestImage` field with most recent classification

This data structure supports the portal use case: show the latest camera image for each gauge, color-coded by derived condition.

---

## Cost Analysis

Vision API pricing (as of 2025):
- **Label Detection:** $1.50 per 1,000 images (first 1,000/month free)
- **Object Localization:** $1.50 per 1,000 images
- **Safe Search:** $1.50 per 1,000 images

Each feature request is a separate billable unit. Our classify call uses 3 features = 3 units per image.

For RiverPulse: 100 gauges × 4 photos/day × 3 features = 1,200 units/day = ~36,000/month = ~$54/month. For a monitoring system, that's negligible.

For higher scale: Motion-triggered cameras might produce 50–500 images/day per device. At 100 devices, that's 5,000–50,000 images/day. Vision API for first-pass filtering (is there an noteworthy event happening in this image?) is still cost-effective at this scale. Complex classifications get routed to Gemini (Lab 14) only when Vision API flags something interesting — this tiered approach cuts costs significantly.

---

## Cleanup
Optional.  Again I recommend keeping at least for the duration of the labs course.

```bash
# Remove sample images from Cloud Storage
PROJECT_ID=$(gcloud config get-value project)
gcloud storage rm "gs://${PROJECT_ID}-riverpulse-data/images/**" 2>/dev/null

# Delete Firestore collection (use console — no single CLI command for this)
# Console → Firestore → image-classifications → Delete collection

# Remove local files
rm -rf ~/riverpulse/gauge-vision

# Disable Vision API if you're done with it
# gcloud services disable vision.googleapis.com
```

---

## Discussion Points for Interviews

- "Gauge cameras upload photos to Cloud Storage. The Vision API classifies them without any model training — label detection identifies river conditions like ice, debris, flooding, or clear water. Results go to Firestore alongside the numeric readings so the portal can show both data and visual context."

- "Safe search runs on every image automatically. For a field-deployed camera, you need to filter out corrupted frames, accidental occlusions, or in a security context, content that shouldn't be in the system. It's a one-line addition to the feature request."

- "The condition mapping is a simple keyword-to-condition lookup with confidence thresholds. In production, you'd tune this with real field photos. The Vision API returns generic labels — 'water', 'nature', 'blue' — and the mapping layer translates those into domain-specific conditions."

- "For a security camera system, the Vision API is the first tier of classification. It's fast and cheap — good for answering 'is there anything noteworthy in this image?' When the answer is yes, you route to Gemini for deeper analysis: what's happening, who's involved, does this require action. The two-tier approach avoids burning expensive model inference on empty frames."

- "Each feature type in the Vision API is a separate billable unit. You request only what you need. For a gauge camera checking ice conditions, label detection alone might be sufficient. For a security camera, you'd add object localization to get bounding boxes."

---

## Architecture Summary — Labs 1–13

```
[Gauge / Camera]
      |
      |── MQTT ──────────────────────► [Compute Engine: MQTT Broker]
      |                                      |── bridge ──► [Pub/Sub: sensor-events]
      |
      |── photo upload ──────────────► [Cloud Storage: images/]
                                             |
                                             |── Cloud Run endpoint
                                             v
                                       [Cloud Vision API]
                                             |
                                             v
                                       [Firestore: image-classifications]

[Pub/Sub: sensor-events]
      |
      |── push ──► [Cloud Run: riverpulse-api] ──► [Firestore + BigQuery]
      |
      |── Eventarc ──► [Cloud Function: flood-evaluator] ──► [Pub/Sub: alerts]
```

---

## Learning Summary

*Write your own here after completing the lab.*

---

## Next Lab

Lab 14: Gemini Multimodal — combining photos with sensor data for AI-powered condition assessment.
