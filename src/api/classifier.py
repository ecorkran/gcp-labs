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
