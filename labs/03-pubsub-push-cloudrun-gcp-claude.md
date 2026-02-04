# Overview
  
**Time:** 45-60 minutes
**Prerequisites:** Labs 1 and 2 completed (Pub/Sub topic exists, Cloud Run service deployed)
**Why it matters:** This is the RiverPulse data pipeline. Gauge publishes to Pub/Sub → Pub/Sub pushes to Cloud Run → API processes and stores. Fully decoupled, scales automatically.

---
## Concepts (5 minutes)

- **Push subscription:** Pub/Sub POSTs messages to an HTTPS endpoint
- **Pull subscription:** Your code polls Pub/Sub for messages (Lab 1)
- **Push is better for Cloud Run** because it wakes up the service automatically. Pull would require always-running code.

Message flow:
```
Gauge → Pub/Sub Topic → Push Subscription → Cloud Run → Firestore
```

This is where things actually start to connect. We have the Pub/Sub topics and the Cloud Run (container) service deployed. Now we'll push to Pub/Sub which will push to our Cloud Run service where our API will process and store the messages. Handles queuing, scaling, and prevents infinite retries.

---
## Step 1: Add Push Endpoint to Your API

Update your Cloud Run service to handle Pub/Sub push messages.
```bash
cd ~/riverpulse-api
```

Replace `main.py` with this updated version:

```python
from flask import Flask, jsonify, request
import os
import base64
import json

app = Flask(__name__)
readings = []

@app.route('/')
def health():
	return jsonify({"status": "healthy", "service": "riverpulse-api"})

@app.route('/readings', methods=['GET'])
def get_readings():
	gauge_id = request.args.get('gaugeId')
	condition = request.args.get('condition')
	filtered = readings

	if gauge_id:
		filtered = [r for r in filtered if r.get('gaugeId') == gauge_id]
	
	if condition:
		filtered = [r for r in filtered if r.get('condition') == condition]
	
	return jsonify({"readings": filtered, "count": len(filtered)})


@app.route('/readings', methods=['POST'])
def create_reading():
	reading = request.get_json()
	
	if not reading:
		return jsonify({"error": "No reading data provided"}), 400
	
	from datetime import datetime
	reading['receivedAt'] = datetime.utcnow().isoformat() + 'Z'
	reading['source'] = 'direct'
	readings.append(reading)
	
	print(f"Direct reading received: {reading}")
	return jsonify({"status": "created", "reading": reading}), 201


# NEW: Pub/Sub push endpoint. We're just pushing everything to this endpoint
# for simplicity. In a real app, we'd determine what to do with these and route
# accordingly. Or we'd create separate pub/sub topics, which we'd almost
# definitely do as the application scaled up.
@app.route('/pubsub/push', methods=['POST'])
def pubsub_push():
	"""
	Handle Pub/Sub push messages.
	Pub/Sub sends messages in this format:
	
	{
		"message": {
			"data": "<base64-encoded-data>",
			"messageId": "123",
			"publishTime": "2026-01-31T...",
			"attributes": {"key": "value"}
		},
		
		"subscription": "projects/.../subscriptions/..."
	}
	"""

	envelope = request.get_json()
	if not envelope:
		return jsonify({"error": "No Pub/Sub message received"}), 400
	
	if 'message' not in envelope:
		return jsonify({"error": "Invalid Pub/Sub message format"}), 400
	
	pubsub_message = envelope['message']
	
	# Decode the base64 data
	if 'data' in pubsub_message:
		data = base64.b64decode(pubsub_message['data']).decode('utf-8')
	
	try:
		reading = json.loads(data)

	except json.JSONDecodeError:	
		# If not JSON, treat as plain text
		reading = {"rawData": data}
	else:
		reading = {}
	
	# Add metadata from Pub/Sub
	from datetime import datetime
	reading['receivedAt'] = datetime.utcnow().isoformat() + 'Z'
	reading['source'] = 'pubsub'
	reading['messageId'] = pubsub_message.get('messageId')
	reading['publishTime'] = pubsub_message.get('publishTime')
	
	# Include any attributes
	if 'attributes' in pubsub_message:
		reading['attributes'] = pubsub_message['attributes']
		readings.append(reading)
	
	print(f"Pub/Sub reading received: {reading}")
	
	# Return 200/204 to acknowledge the message
	# Any other status code causes Pub/Sub to retry
	return jsonify({"status": "processed", "messageId": reading.get('messageId')}), 200


@app.route('/gauges/<gauge_id>/readings', methods=['GET'])
def get_gauge_readings(gauge_id):
	gauge_readings = [r for r in readings if r.get('gaugeId') == gauge_id]
	return jsonify({"gaugeId": gauge_id, "readings": gauge_readings})


if __name__ == '__main__':
	port = int(os.environ.get('PORT', 8080))
	app.run(host='0.0.0.0', port=port, debug=True)
```

Optional: it could be useful to update the local test section and re-test locally, but that is not yet done here.

Recommended: push this to a git repo and use a Personal Access Token (PAT).

---
## Step 2: Redeploy

```bash
gcloud run deploy riverpulse-api \
--source . \
--allow-unauthenticated \
--region us-central1 \
--memory 256Mi
```

---
## Step 3: Create Push Subscription

```bash
# Get your Cloud Run service URL
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

echo "Service URL: $SERVICE_URL"

# Create push subscription pointing to your /pubsub/push endpoint. This 
# endpoint is created in main.py
gcloud pubsub subscriptions create event-push-sub \
--topic=sensor-events \
--push-endpoint="$SERVICE_URL/pubsub/push" \
--ack-deadline=60

# You can verify the endpoint is created with:
# gcloud pubsub subscriptions list 
```

---
## Step 4: Test the Pipeline

Note: you *may* not see the reading here because the worker could be dying too fast. This will be fixed in Lab 4 when we add persistence with Firestore. For now, even if your `CURL` doesn't work, verifying that the message is received in the logs should be sufficient.

```bash
# Publish a message to the topic
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-003","type":"flow_reading","cfs":1850,"stageHeight":6.1,"condition":"high","timestamp":"2026-01-31T08:15:00Z"}'

# Wait 2-3 seconds for Pub/Sub to push
# Check if the reading was received
curl $SERVICE_URL/readings
```

You should see the reading with `"source": "pubsub"` and additional metadata like `messageId` and `publishTime`. If you do not see the reading, verify that the subscription was created and points to the right place.
```sh
gcloud pubsub subscriptions describe event-push-sub
```

You should see something like:
```
pushConfig.pushEndpoint: https://{your service}/pubsub/push
```

Additionally, you can check the logs:
```sh
gcloud run services logs read riverpulse-api --region us-central1 --limit 20
```

If you discover errors and need to update, redeploy
```sh
gcloud run deploy riverpulse-api --source . --allow-unauthenticated --region us-central1 --memory 512Mi
```

---
## Step 5: Publish Multiple Readings

```bash
# Optimal flow reading
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":720,"stageHeight":3.8,"condition":"optimal"}'

# High water alert
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-002","type":"flow_reading","cfs":2100,"stageHeight":7.2,"condition":"high"}' \
--attribute=priority=medium,region=arkansas-river

# Temperature reading
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-003","type":"temp_reading","waterTemp":54,"airTemp":72}'

# Check all readings
sleep 3
curl $SERVICE_URL/readings | python3 -m json.tool
```

See note for Step 4 on `CURL`. If you can see any of the messages, consider it good. Again, view in logs if in doubt
```sh
gcloud run services logs read riverpulse-api --region us-central1 --limit 20
```

---
## Step 6: View Logs

```bash
# See the readings being processed
gcloud run services logs read riverpulse-api --region us-central1 --limit 20
```

You should see the `print()` statements showing readings received.

---
## Step 7: Understand Retry Behavior

Pub/Sub retries if your endpoint returns anything other than 2xx:
```bash
# Check subscription config
gcloud pubsub subscriptions describe event-push-sub
```

Key settings:
- `ackDeadlineSeconds`: Time to respond before Pub/Sub retries
- `retryPolicy`: Exponential backoff settings (can be configured)

If your Cloud Run service is overloaded or crashes, Pub/Sub automatically retries with backoff. Messages don't get lost.

---
## Step 8: Add Dead Letter Queue (Production Pattern)

```bash
# If not created in Lab 1, create DLQ topic
gcloud pubsub topics create sensor-events-dlq 2>/dev/null || true
gcloud pubsub subscriptions create dlq-monitor-sub --topic=sensor-events-dlq 2>/dev/null || true

# Update push subscription with DLQ
gcloud pubsub subscriptions update event-push-sub \
--dead-letter-topic=sensor-events-dlq \
--max-delivery-attempts=5
```

Now if a message fails 5 times, it goes to DLQ instead of infinite retries.

---
## Architecture So Far

```
[gcloud pubsub publish]
	|
	v
[Cloud Pub/Sub: sensor-events topic]
	|
	+---> [Push Sub: event-push-sub]
	|     |
	|     | HTTPS POST to /pubsub/push
	|     v
	| [Cloud Run: riverpulse-api]
	|     |
	|     v
	| [In-memory storage (Firestore in Lab 4)]
	|
	+---> [Pull Sub: event-processor-sub] (from Lab 1)
	+---> [Pull Sub: event-archive-sub] (from Lab 1)
```

---
## Cleanup (Optional)

```bash
# Delete push subscription only (keep topic and service for Lab 4)
gcloud pubsub subscriptions delete event-push-sub
```

---
## Discussion Points for Interviews

- "Pub/Sub push subscriptions integrate seamlessly with Cloud Run. Message arrives, Cloud Run wakes up, processes, acknowledges. No polling loops needed."

- "The acknowledgment model is powerful - if my service crashes mid-processing, Pub/Sub automatically retries. No message loss."

- "Dead letter queues catch poison messages. If a reading has malformed data that crashes my parser every time, after 5 attempts it goes to DLQ for manual inspection instead of blocking the queue."

---
## Learning Summary

This lab connects our Pub/Sub topics and subscriptions to the API running in the Cloud Run container. We diagnosed some issues related to worker lifetime and verified messages using logging. Lab 4 will solve this problem by adding persistence.

---
## Next Lab
Lab 4: Firestore - persistent storage for readings and gauges.