# Overview

**Time:** 45-60 minutes  
**Prerequisites:** Labs 1-4 completed (Pub/Sub with sensor-events topic, Cloud Run API, Firestore)  

###### New Skills
* Cloud Functions (2nd gen) deployment
* Pub/Sub-triggered functions
* Cloud Functions vs Cloud Run — when to use which

---

## Concepts (5 minutes)

- **Cloud Functions:** Single-purpose functions triggered by events. You write a function, Google runs it.
- **2nd gen vs 1st gen:** 2nd gen runs on Cloud Run under the hood — longer timeouts (up to 60 min), larger instances, concurrency support, Eventarc triggers. Always use 2nd gen for new work.
- **Eventarc:** The trigger system for 2nd gen functions. Connects events (Pub/Sub message, Cloud Storage upload, Firestore write) to function invocations.
- **Cold Start:** First invocation after idle spins up a new instance. Typically 1-3 seconds for Python. Subsequent invocations reuse warm instances.

Until now, all RiverPulse processing goes through the Cloud Run API — readings come in via Pub/Sub push, the API stores them in Firestore, done. But what about logic that should run *independently* of the API? Flood threshold evaluation doesn't belong in the API request path. If the check is slow or fails, it shouldn't delay the acknowledgment to Pub/Sub.

Cloud Functions let you attach independent processing to the same event stream. The reading arrives on Pub/Sub, the API stores it (existing path), and *separately* a Cloud Function evaluates flood thresholds and publishes alerts. Each consumer is independent — one failing doesn't affect the other.

```
[Pub/Sub: sensor-events]
      |
      |── push subscription ──► [Cloud Run: riverpulse-api]
      |                              (store reading in Firestore + BigQuery)
      |
      |── Eventarc trigger ───► [Cloud Function: flood-evaluator]
                                     (evaluate thresholds, publish alerts)
```

**Cloud Functions vs Cloud Run — when to use which:**

| | Cloud Functions | Cloud Run |
|---|---|---|
| **Use for** | Single-purpose event handlers | Full APIs, multi-route services |
| **Trigger** | Events (Pub/Sub, Storage, Firestore) | HTTP requests, Pub/Sub push |
| **Concurrency** | 1-1000 per instance (2nd gen) | 1-1000 per instance |
| **Max timeout** | 60 min (2nd gen) | 60 min |
| **When to pick** | "When X happens, do Y" | "Serve this API / run this service" |

AWS equivalent: Lambda (very close). The 2nd gen/Eventarc model is similar to Lambda + EventBridge.

---

## Setup

```bash
# Enable required APIs
gcloud services enable cloudfunctions.googleapis.com
gcloud services enable eventarc.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com

# 2nd gen Cloud Functions need Eventarc permissions for the default compute SA
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

# Grant Eventarc event receiver role
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/eventarc.eventReceiver"

# Grant the Pub/Sub service agent permission to create tokens
# (needed for Pub/Sub to authenticate to the function)
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

The IAM setup is the most common stumbling point with 2nd gen functions. If deployment fails with permission errors, these bindings are usually what's missing.

---

## Step 1: Create the Alerts Topic

The flood evaluator will publish to a dedicated alerts topic, separate from sensor-events. This keeps the alert stream clean and allows different consumers (email notifications, SMS, dashboard push) to subscribe independently.

```bash
# Create alerts topic
gcloud pubsub topics create riverpulse-alerts

# Create a pull subscription for monitoring/debugging
gcloud pubsub subscriptions create alerts-debug \
  --topic=riverpulse-alerts \
  --ack-deadline=60

# Verify
gcloud pubsub topics list
```

---

## Step 2: Write the Cloud Function
```bash
# Create function directory
mkdir -p ~/riverpulse/flood-evaluator && cd $_
```

Create `main.py`:
```python
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
```

Create `requirements.txt`:
```
functions-framework==3.*
google-cloud-pubsub==2.19.0
google-cloud-firestore==2.14.0
```

---

## Step 3: Deploy the Function

```bash
cd ~/riverpulse/flood-evaluator

# Deploy 2nd gen Cloud Function triggered by Pub/Sub
gcloud functions deploy flood-evaluator \
  --gen2 \
  --runtime=python312 \
  --region=us-central1 \
  --source=. \
  --entry-point=process_reading \
  --trigger-topic=sensor-events \
  --memory=256Mi \
  --timeout=60s \
  --min-instances=0 \
  --max-instances=10
```

This may take 1-2 minutes. The `--trigger-topic=sensor-events` creates an Eventarc trigger that subscribes to the existing sensor-events topic. This is a *separate* subscription from the one feeding Cloud Run — both get every message independently.

Verify deployment:
```bash
# Check function status
gcloud functions describe flood-evaluator --region=us-central1 --gen2

# Check the Eventarc trigger
gcloud eventarc triggers list --location=us-central1
```

---

## Step 4: Test with Readings

Send readings at different flow levels and watch the function respond.

```bash
# Normal reading — should NOT trigger alert
gcloud pubsub topics publish sensor-events \
  --message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":720,"condition":"optimal","timestamp":"2026-02-01T14:00:00Z"}'

# High reading — SHOULD trigger HIGH alert
gcloud pubsub topics publish sensor-events \
  --message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":1800,"condition":"high","timestamp":"2026-02-01T14:05:00Z"}'

# Flood reading — SHOULD trigger FLOOD alert
gcloud pubsub topics publish sensor-events \
  --message='{"gaugeId":"gauge-003","type":"flow_reading","cfs":4100,"condition":"flood","timestamp":"2026-02-01T14:10:00Z"}'

# Clear Creek high — lower threshold
gcloud pubsub topics publish sensor-events \
  --message='{"gaugeId":"gauge-002","type":"flow_reading","cfs":850,"condition":"high","timestamp":"2026-02-01T14:15:00Z"}'
```

Check the function logs:
```bash
# View function logs (give it 15-30 seconds for cold start on first invocation)
gcloud functions logs read flood-evaluator \
  --region=us-central1 \
  --gen2 \
  --limit=20
```

You should see output like:
```
Evaluating: gauge-001 at 720 cfs
  → Normal range for gauge-001
Evaluating: gauge-001 at 1800 cfs
  → ALERT: HIGH for gauge-001 (1800 cfs)
  → Alert published: 12345678
  → Alert stored: abc123
```

Check the alerts arrived on the alerts topic:
```bash
gcloud pubsub subscriptions pull alerts-debug --limit=10 --auto-ack
```

Check alerts in Firestore (Console → Firestore → alerts collection), or via the API if you have the route:
```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

# If you have an /alerts endpoint (add one, or just check the console)
# curl ${SERVICE_URL}/alerts | python3 -m json.tool
```

---

## Step 5: Add Alerts Endpoint to Cloud Run API (Optional)

If you want the portal to display alerts, add this to your Cloud Run `main.py`:

```python
@app.route('/alerts', methods=['GET'])
def get_alerts():
    """Get recent alerts with optional filters."""
    gauge_id = request.args.get('gaugeId')
    severity = request.args.get('severity')
    limit = int(request.args.get('limit', 50))

    query = db.collection('alerts')

    if gauge_id:
        query = query.where('gaugeId', '==', gauge_id)
    if severity:
        query = query.where('severity', '==', severity)

    query = query.order_by('evaluated_at', direction=firestore.Query.DESCENDING).limit(limit)

    docs = query.stream()
    alerts = []
    for doc in docs:
        alert = doc.to_dict()
        alert['id'] = doc.id
        alerts.append(alert)

    return jsonify({"alerts": alerts, "count": len(alerts)})
```

Redeploy if you add this. The key point is that the Cloud Function writes alerts to Firestore, and the Cloud Run API reads them for the portal. They share the database but don't depend on each other operationally.

---

## Step 6: Observe the Decoupled Architecture

Send a reading and trace it through both paths:

```bash
# Publish one reading
gcloud pubsub topics publish sensor-events \
  --message='{"gaugeId":"gauge-002","type":"flow_reading","cfs":1100,"condition":"flood","timestamp":"2026-02-01T15:00:00Z"}'
```

Now verify it landed in both places:

```bash
# Path 1: Cloud Run stored the reading in Firestore
curl "${SERVICE_URL}/readings?gaugeId=gauge-002&limit=1" | python3 -m json.tool

# Path 2: Cloud Function evaluated it and created an alert
gcloud functions logs read flood-evaluator --region=us-central1 --gen2 --limit=5

# The alert is in Firestore
# (check Console or the /alerts endpoint if you added it)

# The alert is also on the alerts topic
gcloud pubsub subscriptions pull alerts-debug --limit=5 --auto-ack
```

One Pub/Sub message → two independent consumers → two independent outcomes. If the Cloud Function crashes, readings still get stored. If the Cloud Run API is down for a redeploy, alerts still get evaluated. This is the fan-out pattern from Lab 1, now doing real work.

---

## Step 7: View in Console

Go to Cloud Console → Cloud Functions. You'll see the flood-evaluator with:

- **Invocations/sec:** How often it fires
- **Execution time:** How long each invocation takes (should be <1s for this logic)
- **Memory usage:** Should be well under 256Mi
- **Error rate:** Should be 0% for well-formed messages
- **Active instances:** Likely 0-1 for this testing volume

Click into the function → Logs tab for the same view as the CLI but more browseable.

Also check Eventarc → Triggers to see the Pub/Sub trigger binding. This is where you'd add additional triggers if you wanted the function to also respond to Cloud Storage uploads or Firestore writes.

---

## Event Processing Architecture Summary

```
[MQTT Gauge / Direct API]
      |
      v
[Pub/Sub: sensor-events]
      |
      |── Subscription: sensor-events-push ──► [Cloud Run: riverpulse-api]
      |                                            |── Firestore: readings
      |                                            |── BigQuery: streaming insert (Lab 11)
      |
      |── Eventarc trigger ──────────────────► [Cloud Function: flood-evaluator]
                                                   |── evaluate thresholds
                                                   |── Pub/Sub: riverpulse-alerts
                                                   |── Firestore: alerts

[Pub/Sub: riverpulse-alerts]
      |
      |── (future) push to email/SMS notification service
      |── (future) push to portal WebSocket for real-time alert banner
      |── alerts-debug subscription (for monitoring)
```

---

## Discussion Points for Interviews

- "Readings and alerts are decoupled. The Cloud Run API stores every reading regardless of threshold status. The Cloud Function independently evaluates each reading against per-gauge thresholds and publishes alerts. One failing doesn't affect the other."

- "Per-gauge thresholds matter because rivers are different. 2000 cfs is normal on the Arkansas but flooding on Clear Creek. In production these thresholds live in Firestore so field teams can tune them without code changes."

- "I used Cloud Functions for the evaluator because it's a single-purpose event handler — one trigger, one job. The API stays on Cloud Run because it's a multi-route service handling HTTP requests. The 2nd gen Cloud Function runs on Cloud Run infrastructure anyway, so performance characteristics are similar."

- "The alerts topic enables fan-out for notifications. Right now we have a debug subscription. In production, you'd add push subscriptions to an email notification service, an SMS gateway, and a WebSocket relay for the portal. Each gets every alert independently."

- "Cold start is ~1-3 seconds on first invocation. For flood alerting that's acceptable — a 3-second delay on a threshold check doesn't change the response. For sub-second latency requirements, you'd set min-instances=1 to keep one warm instance, at the cost of ~$15/month."

---

## Cleanup
Optional - you probably want to keep this around.

```bash
# Delete the Cloud Function
gcloud functions delete flood-evaluator --region=us-central1 --gen2 --quiet

# Delete the Eventarc trigger (may be auto-deleted with the function)
# gcloud eventarc triggers delete TRIGGER_NAME --location=us-central1 --quiet

# Delete the alerts topic and subscription
gcloud pubsub subscriptions delete alerts-debug --quiet
gcloud pubsub topics delete riverpulse-alerts --quiet

# Remove the function directory
rm -rf ~/flood-evaluator
```

---

## Learning Summary

This lab covered Cloud Functions (Cloud Run Functions) which are designed for short-lived worker processes.  If you are familiar with AWS, these are most similar to AWS Lambda.  Total runtime is limited to 60 minutes (vs 15 for AWS Lambda).  While they run on the same structure as Cloud Run services, they have different purposes.

Cloud Run (services) are containerized and designed for long-running processes.  In those, a single call has the configurable time limit (up to 60 minutes as of 2026-02).  Use these for services like APIs that need to exist indefinitely, while benefitting from containerization and auto-scaling.  Use functions that we covered here for well-defined single jobs.

Cloud Functions as used here are connected to a Pubsub topic and triggered by EventArc.  Optionally, they can be figured by HTTP. This is handled automatically by the GCP infrastructure.

---

## Next Lab

Lab 13: Vertex AI Vision API — classifying images from gauge cameras.
