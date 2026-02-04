# Overview

**Time:** 45-60 minutes

**Why it matters:** Cloud Run is where your API lives. Serverless containers - you don't manage VMs, it scales to zero when idle, scales up automatically under load. This is the RiverPulse backend.

---
## Concepts (5 minutes)

- **Cloud Run:** Runs containers without managing infrastructure
- **Source deploy:** Cloud Run can build from source (no Dockerfile needed for simple apps)
- **Concurrency:** How many requests one container instance handles simultaneously
- **Cold start:** First request after scale-to-zero takes a bit longer

Similar but not the same as AWS Lambda. You give it a Docker container (or source code that it can containerize) and it runs your app. It's a "serverless container". Cloud Run *Functions* are like AWS Lambda.

---
## Setup

```bash
# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com

# Set default region (us-central1 is cheapest)
gcloud config set run/region us-central1
```

---
## Step 1: Create a Simple Python API
We'll use Flask for this.

```bash
# Create project directory
mkdir -p ~/riverpulse-api
cd ~/riverpulse-api
```

Click 'Open Editor' and create `main.py`:

```python
from flask import Flask, jsonify, request
import os

app = Flask(__name__)

# Simulated in-memory storage (Firestore in production)
readings = []

@app.route('/')
def health():
	return jsonify({"status": "healthy", "service": "riverpulse-api"})

@app.route('/readings', methods=['GET'])
def get_readings():
	# Query params for filtering
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

	# Add server timestamp
	from datetime import datetime
	reading['receivedAt'] = datetime.utcnow().isoformat() + 'Z'
	readings.append(reading)
	return jsonify({"status": "created", "reading": reading}), 201


@app.route('/gauges/<gauge_id>/readings', methods=['GET'])
def get_gauge_readings(gauge_id):
	gauge_readings = [r for r in readings if r.get('gaugeId') == gauge_id]
	return jsonify({"gaugeId": gauge_id, "readings": gauge_readings})

if __name__ == '__main__':
	port = int(os.environ.get('PORT', 8080))
	app.run(host='0.0.0.0', port=port, debug=True)
```

Create `requirements.txt`:
```
flask==3.0.0
gunicorn==21.2.0
```

Create `Procfile` (tells Cloud Run how to start the app):
```
web: gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
```

---
## Step 2: Test in Cloud Shell (Optional)

**This is optional.** You can skip straight to deployment (Step 3) if you prefer. But testing in Cloud Shell before deploying to Cloud Run is useful - you catch errors faster without waiting for the build.

Test in Cloud Shell:

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run locally
python main.py
```

In another gcloud terminal shell (click '+'):
```bash
# Test health endpoint
curl http://localhost:8080/

# Create a reading
curl -X POST http://localhost:8080/readings \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-001","type":"flow_reading","cfs":850,"condition":"optimal"}'

# Get all readings
curl http://localhost:8080/readings

# Filter by gauge
curl "http://localhost:8080/readings?gaugeId=gauge-001"
```

Stop the local server (Ctrl+C in main terminal) and deactivate:

```bash
deactivate
```

---
## Step 3: Deploy to Cloud Run

```bash
cd ~/riverpulse-api

# Deploy from source (Cloud Build creates container automatically)
gcloud run deploy riverpulse-api \
--source . \
--allow-unauthenticated \
--region us-central1 \
--memory 256Mi \
--max-instances 3
```

This takes 2-3 minutes. Cloud Build:

1. Uploads your source code
2. Detects Python, creates a container
3. Pushes to Artifact Registry
4. Deploys to Cloud Run

You'll get a URL like: `https://riverpulse-api-XXXXXX-uc.a.run.app`

---
## Step 4: Test Deployed Service

```bash
# Save your service URL
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

echo $SERVICE_URL

# Test health
curl $SERVICE_URL

# Create readings
curl -X POST $SERVICE_URL/readings \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-001","type":"flow_reading","cfs":850,"condition":"optimal"}'

curl -X POST $SERVICE_URL/readings \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-002","type":"flow_reading","cfs":1450,"condition":"high"}'

curl -X POST $SERVICE_URL/readings \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-001","type":"flow_reading","cfs":920,"condition":"optimal"}'

# Query readings
curl $SERVICE_URL/readings
curl "$SERVICE_URL/readings?condition=optimal"
curl $SERVICE_URL/gauges/gauge-001/readings
```

---
## Step 5: View Logs

```bash
# Stream logs in real-time
gcloud run services logs read riverpulse-api --region us-central1 --limit 50

# For realtime log streaming, you can use tail, but you will need to
# use the beta version, and install log-streaming.  Logs read is 
# sufficient for this lab.
# gcloud beta run services logs tail riverpulse-api --region us-central1
```

You can also view logs in Cloud Console: Cloud Run → riverpulse-api → Logs tab. Note that logs tail is only available in beta as of 20260131 and requires some extra package installs so skipped for this lab.

---
## Step 6: Understand Scaling

```bash
# Check current configuration
gcloud run services describe riverpulse-api --region us-central1

# Update scaling settings
gcloud run services update riverpulse-api \
--region us-central1 \
--min-instances 0 \
--max-instances 10 \
--concurrency 80
```

- `min-instances=0`: Scale to zero when no traffic (saves money)
- `max-instances=10`: Cap scaling (cost control)
- `concurrency=80`: Each instance handles up to 80 simultaneous requests

---
## Step 7: Environment Variables

For real config (API keys, database URLs), use environment variables:

```bash
gcloud run services update riverpulse-api \
--region us-central1 \
--set-env-vars "ENVIRONMENT=production,LOG_LEVEL=info"
```

For secrets, use Secret Manager (covered in a later lab).


---
## Cleanup (Optional)
It is recommended to only run cleanup here if you will restart fresh rather than proceeding through the lab series.

```bash
# Delete the service
gcloud run services delete riverpulse-api --region us-central1

# Delete the container images (optional, they cost a tiny amount)
gcloud artifacts docker images list us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cloud-run-source-deploy
```

---
## Discussion Points for Interviews

- "The API runs on Cloud Run - serverless containers. Scales automatically, no VM management, pay only for actual request time."

- "For a production monitoring system, I'd set min-instances=1 for the API to avoid cold starts on critical queries, but scale to zero in dev/staging."

- "Cloud Run handles HTTPS termination automatically. The service gets a managed SSL cert."

---
## Note on State

This example uses in-memory storage (`readings = []`), which resets when the container restarts. In production, you'd use Firestore. That's Lab 4.

---
## Learning Summary
In this lab we created a Python API using Flask and served it from a Cloud Run container. We tested the API, viewed logs, and experimented with scaling. Finally we set production environment vars to simulate "real" service.

---
## Next Lab
Lab 3: Pub/Sub Push to Cloud Run - connect the messaging system to your API.