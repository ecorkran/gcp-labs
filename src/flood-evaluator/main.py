import base64
import json
import os
from datetime import datetime, timezone

import functions_framework
from google.cloud import pubsub_v1, firestore

# Initialize clients
publisher = pubsub_v1.PublisherClient()
db = firestore.Client()

PROJECT_ID = os.environ.get('GOOGLE_CLOUD_PROJECT', '')
ALERTS_TOPIC = f"projects/{PROJECT_ID}/topics/riverpulse-alerts"

# Flood thresholds by gauge (in production, these live in Firestore or config)
# CFS values: low < runnable < optimal < high < flood
THRESHOLDS = {
    "default": {"high": 2000, "flood": 3000},
    "gauge-001": {"high": 1500, "flood": 2200},   # Arkansas at Salida
    "gauge-002": {"high": 700, "flood": 1000},     # Clear Creek - smaller river
    "gauge-003": {"high": 2500, "flood": 3500},    # Poudre at Fort Collins
}

print(f"PROJECT_ID: {PROJECT_ID}, ALERTS_TOPIC: {ALERTS_TOPIC}")

def evaluate_reading(reading):
    """
    Evaluate a reading against flood thresholds.
    Returns an alert dict if thresholds exceeded, None otherwise.
    """
    gauge_id = reading.get("gaugeId", "unknown")
    cfs = reading.get("cfs")

    if cfs is None:
        return None

    thresholds = THRESHOLDS.get(gauge_id, THRESHOLDS["default"])

    severity = None
    if cfs >= thresholds["flood"]:
        severity = "FLOOD"
    elif cfs >= thresholds["high"]:
        severity = "HIGH"

    if severity is None:
        return None

    return {
        "type": "flow_alert",
        "severity": severity,
        "gaugeId": gauge_id,
        "cfs": cfs,
        "threshold": thresholds[severity.lower()],
        "exceedance": round(cfs - thresholds[severity.lower()], 1),
        "reading_timestamp": reading.get("timestamp"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "message": f"{severity} flow alert: {gauge_id} at {cfs} cfs "
                   f"(threshold: {thresholds[severity.lower()]} cfs)"
    }


@functions_framework.cloud_event
def process_reading(cloud_event):
    """
    Triggered by Pub/Sub message on sensor-events topic.
    Evaluates flood thresholds and publishes alerts if exceeded.

    This is independent of the Cloud Run API — both consume
    from the same Pub/Sub topic via separate subscriptions.
    """
    # Decode the Pub/Sub message
    message_data = base64.b64decode(cloud_event.data["message"]["data"])

    try:
        reading = json.loads(message_data)
    except json.JSONDecodeError:
        print(f"Invalid JSON in message: {message_data}")
        return

    gauge_id = reading.get("gaugeId", "unknown")
    cfs = reading.get("cfs", "N/A")
    print(f"Evaluating: {gauge_id} at {cfs} cfs")

    # Check thresholds
    alert = evaluate_reading(reading)

    if alert is None:
        print(f"  → Normal range for {gauge_id}")
        return

    print(f"  → ALERT: {alert['severity']} for {gauge_id} ({cfs} cfs)")

    # Publish alert to dedicated alerts topic
    alert_data = json.dumps(alert).encode("utf-8")
    future = publisher.publish(
        ALERTS_TOPIC,
        alert_data,
        severity=alert["severity"],
        gaugeId=gauge_id
    )
    message_id = future.result()
    print(f"  → Alert published: {message_id}")

    # Also store alert in Firestore for portal display
    alert_ref = db.collection("alerts").document()
    alert_ref.set(alert)
    print(f"  → Alert stored: {alert_ref.id}")