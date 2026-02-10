# Overview

**Time:** 45-60 minutes  
**Prerequisites:** Labs 1-8 completed (full RiverPulse pipeline working — Pub/Sub, Cloud Run, Firestore, Cloud Storage, MQTT)  

###### New Skills
* Cloud Monitoring (dashboards, custom metrics)
* Cloud Logging (structured queries, log-based metrics)
* Alerting policies and notification channels

---

## Concepts (5 minutes)

- **Cloud Monitoring:** Metrics, dashboards, uptime checks. Collects data from all GCP services automatically.
- **Cloud Logging:** Centralized logs from Cloud Run, Cloud Functions, Compute Engine, etc. Structured and queryable.
- **Log-based Metric:** A custom metric derived from log entries matching a filter. Turns log patterns into numbers you can chart and alert on.
- **Alerting Policy:** A condition + notification. "If X exceeds Y for Z minutes, notify the team."
- **Notification Channel:** Where alerts go — email, Slack, PagerDuty, SMS.

Until now we've been building the system. This lab is about *watching* it. In production, monitoring is how you know the difference between "everything is fine" and "gauges stopped reporting 20 minutes ago and nobody noticed."

AWS equivalents: CloudWatch (metrics + logs + alarms combined). GCP splits this into Monitoring and Logging as separate but integrated services.

---

## Setup

```bash
# Enable Monitoring and Logging APIs (may already be enabled)
gcloud services enable monitoring.googleapis.com
gcloud services enable logging.googleapis.com

# Verify
gcloud services list --enabled --filter="name:monitoring OR name:logging"
```

Both of these are likely already enabled — Cloud Run and other services feed into them by default. But explicit is better than implicit.

---

## Step 1: Explore Existing Logs

Cloud Run has been writing logs since Lab 2. Let's look at them.

```bash
# View recent Cloud Run logs
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="riverpulse-api"' \
  --limit=20 \
  --format="table(timestamp, severity, textPayload)"

# Filter for errors only
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="riverpulse-api" AND severity>=ERROR' \
  --limit=10 \
  --format="table(timestamp, severity, textPayload)"

# Search for specific text in logs
gcloud logging read 'resource.type="cloud_run_revision" AND textPayload:"reading"' \
  --limit=10 \
  --format="table(timestamp, textPayload)"
```

You can also view these in the Cloud Console: Logging → Logs Explorer. The console is actually better for exploratory work — the query builder is interactive and you can see log structure visually.

**Console tip:** In Logs Explorer, use the Resource filter dropdown to select Cloud Run → riverpulse-api. This pre-fills the query for you.

---

## Step 2: Add Structured Logging to the API

Plain text logs are searchable but structured JSON logs are *queryable*. The difference matters when you need to find "all requests for gauge-003 that took longer than 500ms."

Update `main.py` — add a logging helper:
```python
import json
import time

def log_structured(severity, message, **kwargs):
    """
    Write structured log entry. Cloud Logging parses JSON automatically.
    Severity: DEBUG, INFO, NOTICE, WARNING, ERROR, CRITICAL
    """
    entry = {
        "severity": severity,
        "message": message,
        **kwargs
    }
    print(json.dumps(entry))

# Update the reading ingestion endpoint to use structured logging:
@app.route('/readings', methods=['POST'])
def create_reading():
    start_time = time.time()
    reading = request.get_json()

    if not reading:
        log_structured("WARNING", "Empty reading received",
                       endpoint="/readings", method="POST")
        return jsonify({"error": "No reading data provided"}), 400

    # Add server timestamp
    from datetime import datetime, timezone
    reading['receivedAt'] = datetime.now(timezone.utc).isoformat()
    
    gauge_id = reading.get('gaugeId', 'unknown')
    condition = reading.get('condition', 'unknown')
    cfs = reading.get('cfs', 0)

    # Store in Firestore (from Lab 4)
    doc_ref = db.collection('readings').document()
    doc_ref.set(reading)

    elapsed_ms = (time.time() - start_time) * 1000

    log_structured("INFO", "Reading ingested",
                   gaugeId=gauge_id,
                   condition=condition,
                   cfs=cfs,
                   processingTimeMs=round(elapsed_ms, 2),
                   firestoreDocId=doc_ref.id)

    # Log a warning for extreme readings
    if cfs and cfs > 5000:
        log_structured("WARNING", "Extreme flow reading detected",
                       gaugeId=gauge_id,
                       cfs=cfs,
                       threshold=5000)

    return jsonify({"status": "created", "reading": reading}), 201
```

Redeploy (don't git push here, you need the allow-unauthenticated):
```bash
cd ~/riverpulse/riverpulse-api
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1
```

Generate some test readings:
```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

# Normal reading
curl -X POST ${SERVICE_URL}/readings \
  -H "Content-Type: application/json" \
  -d '{"gaugeId":"gauge-001","cfs":850,"condition":"optimal"}'

# High flow reading (triggers warning)
curl -X POST ${SERVICE_URL}/readings \
  -H "Content-Type: application/json" \
  -d '{"gaugeId":"gauge-002","cfs":6200,"condition":"flood"}'

# Another normal reading
curl -X POST ${SERVICE_URL}/readings \
  -H "Content-Type: application/json" \
  -d '{"gaugeId":"gauge-003","cfs":420,"condition":"low"}'

# Empty reading (triggers warning)
curl -X POST ${SERVICE_URL}/readings \
  -H "Content-Type: application/json" \
  -d '{}'
```

Now query the structured logs:
```bash
# Find all readings from gauge-002
gcloud logging read 'resource.type="cloud_run_revision" AND jsonPayload.gaugeId="gauge-002"' \
  --limit=5 \
  --format="json(timestamp, jsonPayload)"

# Find all extreme flow warnings
gcloud logging read 'resource.type="cloud_run_revision" AND jsonPayload.severity="WARNING" AND jsonPayload.message="Extreme flow reading detected"' \
  --limit=5 \
  --format="json(timestamp, jsonPayload)"
```

This is dramatically more useful than `grep`-ing through plain text logs. You can filter by gauge, by condition, by processing time — any field you log.

---

## Step 3: Create Log-Based Metrics

Log-based metrics turn log patterns into time-series data you can chart and alert on. We'll create two that matter for RiverPulse.

```bash
# Metric 1: Count of readings ingested (per gauge)
gcloud logging metrics create readings-ingested \
  --description="Count of readings successfully ingested" \
  --log-filter='resource.type="cloud_run_revision" AND jsonPayload.message="Reading ingested"'

# Metric 2: Count of extreme flow warnings
gcloud logging metrics create extreme-flow-warnings \
  --description="Extreme flow readings exceeding threshold" \
  --log-filter='resource.type="cloud_run_revision" AND jsonPayload.message="Extreme flow reading detected"'

# Verify
gcloud logging metrics list
```

These metrics now accumulate over time. Every matching log entry increments the counter. You can also create *distribution* metrics (e.g., processing time percentiles), but counters cover the most common use case.

**Console exploration:** Go to Monitoring → Metrics Explorer. Search for `logging/user/readings-ingested`. You won't see data until some log entries match — generate a few more test readings if needed.

---

## Step 4: Create a Notification Channel

Before creating alerts, we need somewhere to send them.

```bash
# Create an email notification channel
# Replace with your actual email
gcloud beta monitoring channels create \
  --display-name="RiverPulse Alerts" \
  --type=email \
  --channel-labels=email_address=YOUR_EMAIL@gmail.com

# List channels to get the channel ID (you'll need this for alerts)
gcloud beta monitoring channels list --format="table(name, displayName, type)"
```

Copy the channel `name` value — it looks like `projects/PROJECT_ID/notificationChannels/CHANNEL_ID`. You'll use this in the next step.

**Note:** For Slack, PagerDuty, or SMS channels, it's easier to set these up in the Console: Monitoring → Alerting → Edit notification channels. The CLI works for email; the console is better for webhook-based integrations.

---

## Step 5: Create Alerting Policies

Two alerts that would matter in production:

**Alert 1: Cloud Run Error Rate Spike**

This uses the built-in Cloud Run metrics — no custom setup needed.

```bash
# Get your notification channel name from Step 4
CHANNEL_NAME=$(gcloud beta monitoring channels list \
  --filter='displayName="RiverPulse Alerts"' \
  --format='value(name)')

echo "Channel: ${CHANNEL_NAME}"
```

Create the alert policy via a JSON config:
```bash
cat > /tmp/error-rate-alert.json << EOF
{
  "displayName": "RiverPulse API Error Rate > 5%",
  "documentation": {
    "content": "The RiverPulse API error rate has exceeded 5%. Check Cloud Run logs for details.",
    "mimeType": "text/markdown"
  },
  "conditions": [
    {
      "displayName": "Cloud Run 5xx error rate",
      "conditionThreshold": {
        "filter": "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"riverpulse-api\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 5,
        "duration": "300s",
        "aggregations": [
          {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_RATE"
          }
        ]
      }
    }
  ],
  "combiner": "OR",
  "enabled": true,
  "notificationChannels": ["${CHANNEL_NAME}"]
}
EOF

gcloud alpha monitoring policies create --policy-from-file=/tmp/error-rate-alert.json
```

If the CLI gives you trouble with the JSON policy (the monitoring CLI can be finicky), use the Console instead: Monitoring → Alerting → Create Policy. The visual builder is actually faster for complex conditions.

**Alert 2: No Readings Received (Console Method)**

This one is easier to create in the Console. Go to Monitoring → Alerting → Create Policy:

1. **Metric:** Select `logging/user/readings-ingested` (your custom log-based metric)
2. **Condition:** "Metric absence" — triggers when no readings are logged for a period
3. **Duration:** 15 minutes (no readings for 15 min = something is wrong)
4. **Notification:** Select the email channel from Step 4
5. **Name:** "No RiverPulse Readings for 15 Minutes"

This is the "silence alarm" — arguably more important than error alerts. Errors are visible. Silence means something broke and nobody noticed.

```bash
# Verify alert policies exist
gcloud alpha monitoring policies list \
  --format="table(displayName, enabled, conditions.displayName)"
```

---

## Step 6: Create a Monitoring Dashboard

Dashboards are significantly easier in the Console than via CLI. Go to Monitoring → Dashboards → Create Dashboard.

**Recommended widgets for RiverPulse:**

**Widget 1: Cloud Run Request Count**
- Metric: `run.googleapis.com/request_count`
- Group by: `response_code_class`
- Chart type: Stacked bar
- This shows traffic volume and error proportion at a glance

**Widget 2: Cloud Run Request Latency**
- Metric: `run.googleapis.com/request_latencies`
- Aggregation: 95th percentile
- Chart type: Line
- Answers "how fast is the API responding?"

**Widget 3: Readings Ingested (Custom)**
- Metric: `logging/user/readings-ingested`
- Chart type: Line
- This is your custom metric from Step 3

**Widget 4: Pub/Sub Undelivered Messages**
- Metric: `pubsub.googleapis.com/subscription/num_undelivered_messages`
- Filter: subscription = `sensor-events-push` (or your subscription name)
- Chart type: Line
- If this climbs, messages are backing up — processing is behind

**Widget 5: Firestore Document Writes**
- Metric: `firestore.googleapis.com/document/write_count`
- Chart type: Line
- Correlates with readings ingested — they should track together

Alternatively, create the dashboard from CLI with a JSON definition. Here's a minimal version with two key widgets:

```bash
cat > /tmp/dashboard.json << 'EOF'
{
  "displayName": "RiverPulse Overview",
  "mosaicLayout": {
    "tiles": [
      {
        "width": 6,
        "height": 4,
        "widget": {
          "title": "API Request Count by Response Code",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_count\"",
                    "aggregation": {
                      "alignmentPeriod": "60s",
                      "perSeriesAligner": "ALIGN_RATE",
                      "crossSeriesReducer": "REDUCE_SUM",
                      "groupByFields": ["metric.labels.response_code_class"]
                    }
                  }
                }
              }
            ]
          }
        }
      },
      {
        "xPos": 6,
        "width": 6,
        "height": 4,
        "widget": {
          "title": "Pub/Sub Undelivered Messages",
          "xyChart": {
            "dataSets": [
              {
                "timeSeriesQuery": {
                  "timeSeriesFilter": {
                    "filter": "resource.type = \"pubsub_subscription\" AND metric.type = \"pubsub.googleapis.com/subscription/num_undelivered_messages\"",
                    "aggregation": {
                      "alignmentPeriod": "60s",
                      "perSeriesAligner": "ALIGN_MEAN"
                    }
                  }
                }
              }
            ]
          }
        }
      }
    ]
  }
}
EOF

gcloud monitoring dashboards create --config-from-file=/tmp/dashboard.json
```

Verify:
```bash
gcloud monitoring dashboards list --format="table(displayName, name)"
```

View the dashboard in Console: Monitoring → Dashboards → RiverPulse Overview. Add more widgets visually from there — the console's drag-and-drop builder is the practical way to iterate on dashboards.

---

## Step 7: Uptime Check (Optional but Useful)

An uptime check pings your endpoint from multiple global locations. If it fails from 2+ locations, it fires an alert.

```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

# Extract just the hostname (no https://)
SERVICE_HOST=$(echo ${SERVICE_URL} | sed 's|https://||')

echo "Host: ${SERVICE_HOST}"
```

Create the uptime check in Console (the CLI for this is verbose):

1. Go to Monitoring → Uptime checks → Create Uptime Check
2. **Protocol:** HTTPS
3. **Hostname:** paste the Cloud Run hostname
4. **Path:** `/` (the health endpoint)
5. **Check frequency:** 5 minutes
6. **Regions:** Select 3+ (US, Europe, Asia-Pacific)
7. **Response validation:** Check for HTTP 200
8. **Alert:** Attach the notification channel from Step 4

This gives you external confirmation that the API is reachable, independent of internal GCP metrics. A complement to internal monitoring, not a replacement.

---

## Monitoring Architecture Summary

```
[Cloud Run: riverpulse-api]
      |
      |── writes structured JSON logs ──► [Cloud Logging]
      |                                         |
      |                                         |── log-based metrics ──► [Cloud Monitoring]
      |                                         |       (readings-ingested, extreme-flow-warnings)
      |                                         |
      |── built-in metrics ──────────────► [Cloud Monitoring]
      |   (request_count, request_latencies)         |
      |                                              |── dashboard ──► [RiverPulse Overview]
[Pub/Sub]                                            |
      |── built-in metrics ──────────────► [Cloud Monitoring]
      |   (num_undelivered_messages)                 |── alerting ──► [Email / Slack]
      |                                              |     (error rate, no readings, uptime)
[Firestore]                                          |
      |── built-in metrics ──────────────► [Cloud Monitoring]
          (write_count, read_count)

[Uptime Check] ──► pings /health from 3+ regions ──► alert if down
```

---

## Discussion Points for Interviews

- "We use structured JSON logging from Cloud Run so we can query by any field — gauge ID, flow value, processing time. Log-based metrics turn those log patterns into time series for dashboards and alerts."

- "The most important alert isn't the error rate — it's the silence alarm. If no readings arrive for 15 minutes, something is wrong upstream. The gauge network, the MQTT broker, or the Pub/Sub pipeline could be down. We alert on absence, not just presence of errors."

- "The dashboard correlates across services: Pub/Sub undelivered messages climbing while Firestore writes flatline tells me Cloud Run processing is stuck. Request latency spiking while Firestore write count stays normal tells me the database is healthy but something else in the API is slow."

- "For a fleet of remote gauges, monitoring is the difference between finding out a gauge died today versus finding out it died three weeks ago when the field team visits. Heartbeat monitoring with absence-based alerts is critical."

---

## Cleanup

```bash
# Delete log-based metrics
gcloud logging metrics delete readings-ingested --quiet
gcloud logging metrics delete extreme-flow-warnings --quiet

# Delete alert policies (list first to get names)
gcloud alpha monitoring policies list --format="value(name)"
# Then delete each:
# gcloud alpha monitoring policies delete POLICY_NAME --quiet

# Delete dashboards
gcloud monitoring dashboards list --format="value(name)"
# gcloud monitoring dashboards delete DASHBOARD_NAME

# Delete notification channels
gcloud beta monitoring channels list --format="value(name)"
# gcloud beta monitoring channels delete CHANNEL_NAME --quiet
```

---

## Learning Summary

We explored the existing Cloud Run logs, then added structured JSON logging so entries are queryable by any field rather than just searchable by text. We created log-based metrics that turn log patterns into time-series numbers (readings ingested, extreme flow warnings). We set up a notification channel and created alerting policies for both error conditions and silence conditions — the silence alarm being the more critical of the two. We built a monitoring dashboard combining built-in GCP metrics (Cloud Run requests, Pub/Sub backlog, Firestore writes) with our custom metrics. The key insight is that monitoring ties together all the individual services from Labs 1-8 into a single view of system health.

---

## Next Lab

Lab 11: BigQuery Analytics — long-term analysis of RiverPulse reading history.
