# Overview

**Time:** 60-90 minutes

**Why it matters:** Firestore is the primary datastore for RiverPulse. Gauge registry, reading metadata, user data. NoSQL document model fits IoT's hierarchical data well. Real-time listeners enable live dashboards.

---

## Concepts (5 minutes)

- **Document:** JSON-like object with fields (like a row, but flexible schema)
- **Collection:** Group of documents (like a table)
- **Subcollection:** Collection nested under a document
- **Real-time listeners:** Subscribe to changes, get notified instantly (WebSocket under the hood)

Firestore vs SQL mental model:
```
SQL: Table → Row → Column
Firestore: Collection → Document → Field
```

Key difference: Each document can have different fields. No schema migration needed.

---

## Setup

```bash
# Enable Firestore API
gcloud services enable firestore.googleapis.com

# Create Firestore database in Native mode (not Datastore mode)
gcloud firestore databases create --location=us-central1
```

If you get an error that database already exists, that's fine. Once again you can always check to see it's really there:
```sh
gcloud firestore databases list
```

---

## Step 1: Explore Firestore Console

Open Cloud Console → Firestore. This is where you'll see your data visually.
The console is actually faster than CLI for Firestore exploration. We'll use both.

---

## Step 2: Update API to Use Firestore

**Pro Tip: Firebase CLI (optional)**

If you want Firebase CLI tools for local development, you can install them on your machine (not in Cloud Shell - it doesn't have npm):
```bash
npm install -g firebase-tools
firebase login
```
Firebase CLI is useful for local testing and more advanced operations, but for this lab we'll use the Python SDK and Cloud Console, which are simpler.

---

```bash
cd ~/riverpulse/riverpulse-api
```

Update `requirements.txt`:
```
flask==3.0.0
gunicorn==21.2.0
google-cloud-firestore==2.14.0
```

Replace `main.py`:
```python
from flask import Flask, jsonify, request
from google.cloud import firestore
import os
import base64
import json
from datetime import datetime, timezone

# Note: Using datetime.now(timezone.utc).isoformat() produces '+00:00' suffix
# e.g., '2026-01-31T12:34:56.789012+00:00'
# The older datetime.utcnow().isoformat() + 'Z' produced 'Z' suffix
# e.g., '2026-01-31T12:34:56.789012Z'
# Both are valid ISO 8601 UTC formats and should parse equivalently

app = Flask(__name__)

# Initialize Firestore client
# On Cloud Run, credentials are automatic via service account
db = firestore.Client()

@app.route('/')
def health():
    return jsonify({"status": "healthy", "service": "riverpulse-api", "database": "firestore"})

# ============ READINGS ============
@app.route('/readings', methods=['GET'])
def get_readings():
    """Query readings with optional filters."""
    gauge_id = request.args.get('gaugeId')
    condition = request.args.get('condition')
    limit = int(request.args.get('limit', 50))

    # Start with readings collection
    query = db.collection('readings')

    # Apply filters
    if gauge_id:
        query = query.where('gaugeId', '==', gauge_id)

    if condition:
        query = query.where('condition', '==', condition)

    # Order by timestamp descending, limit results
    query = query.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)

    # Execute query
    docs = query.stream()
    readings = []

    for doc in docs:
        reading = doc.to_dict()
        reading['id'] = doc.id
        readings.append(reading)

    return jsonify({"readings": readings, "count": len(readings)})


@app.route('/readings', methods=['POST'])
def create_reading():
    """Create reading directly via API."""
    reading = request.get_json()
    if not reading:
        return jsonify({"error": "No reading data provided"}), 400

    reading['receivedAt'] = datetime.now(timezone.utc).isoformat()
    reading['source'] = 'direct'

    # Use timestamp as part of document ID for ordering
    # Or let Firestore auto-generate: db.collection('readings').add(reading)
    doc_ref = db.collection('readings').document()
    doc_ref.set(reading)

    return jsonify({"status": "created", "id": doc_ref.id, "reading": reading}), 201


@app.route('/readings/<reading_id>', methods=['GET'])
def get_reading(reading_id):
    """Get single reading by ID."""
    doc = db.collection('readings').document(reading_id).get()
    if not doc.exists:
        return jsonify({"error": "Reading not found"}), 404
    reading = doc.to_dict()
    reading['id'] = doc.id
    return jsonify(reading)

# ============ PUB/SUB PUSH ============
@app.route('/pubsub/push', methods=['POST'])
def pubsub_push():
    """Handle Pub/Sub push messages and store in Firestore."""
    envelope = request.get_json()

    if not envelope or 'message' not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400

    pubsub_message = envelope['message']

    # Decode message data
    if 'data' in pubsub_message:
        data = base64.b64decode(pubsub_message['data']).decode('utf-8')

        try:
            reading = json.loads(data)
        except json.JSONDecodeError:
            reading = {"rawData": data}
    else:
        reading = {}

    # Add metadata
    reading['receivedAt'] = datetime.now(timezone.utc).isoformat()
    reading['source'] = 'pubsub'
    reading['messageId'] = pubsub_message.get('messageId')
    reading['publishTime'] = pubsub_message.get('publishTime')

    if 'attributes' in pubsub_message:
        reading['attributes'] = pubsub_message['attributes']

    # Ensure timestamp field exists for ordering
    if 'timestamp' not in reading:
        reading['timestamp'] = reading['receivedAt']

    # Store in Firestore
    doc_ref = db.collection('readings').document()
    doc_ref.set(reading)
    print(f"Reading stored: {doc_ref.id} - {reading.get('type', 'unknown')}")
    return jsonify({"status": "processed", "id": doc_ref.id}), 200

# ============ GAUGES ============
@app.route('/gauges', methods=['GET'])
def get_gauges():
    """List all gauges."""
    docs = db.collection('gauges').stream()
    gauges = []

    for doc in docs:
        gauge = doc.to_dict()
        gauge['id'] = doc.id
        gauges.append(gauge)
    return jsonify({"gauges": gauges, "count": len(gauges)})

@app.route('/gauges', methods=['POST'])
def create_gauge():
    """Register a new gauge."""
    gauge = request.get_json()
    if not gauge or 'gaugeId' not in gauge:
        return jsonify({"error": "gaugeId required"}), 400

    gauge_id = gauge['gaugeId']
    gauge['createdAt'] = datetime.now(timezone.utc).isoformat()
    gauge['status'] = gauge.get('status', 'active')

    # Use gaugeId as document ID for easy lookup
    db.collection('gauges').document(gauge_id).set(gauge)
    return jsonify({"status": "created", "gauge": gauge}), 201

@app.route('/gauges/<gauge_id>', methods=['GET'])
def get_gauge(gauge_id):
    """Get gauge details."""
    doc = db.collection('gauges').document(gauge_id).get()

    if not doc.exists:
        return jsonify({"error": "Gauge not found"}), 404

    gauge = doc.to_dict()
    gauge['id'] = doc.id
    return jsonify(gauge)


@app.route('/gauges/<gauge_id>', methods=['PATCH'])
def update_gauge(gauge_id):
    """Update gauge fields (partial update)."""
    updates = request.get_json()
    if not updates:
        return jsonify({"error": "No update data provided"}), 400

    updates['updatedAt'] = datetime.now(timezone.utc).isoformat()
    doc_ref = db.collection('gauges').document(gauge_id)
    doc_ref.update(updates)
    return jsonify({"status": "updated", "gaugeId": gauge_id})


@app.route('/gauges/<gauge_id>/readings', methods=['GET'])
def get_gauge_readings(gauge_id):
    """Get readings for a specific gauge."""
    limit = int(request.args.get('limit', 50))

    query = db.collection('readings') \
        .where('gaugeId', '==', gauge_id) \
        .order_by('timestamp', direction=firestore.Query.DESCENDING) \
        .limit(limit)

    docs = query.stream()
    readings = [{"id": doc.id, **doc.to_dict()} for doc in docs]
    return jsonify({"gaugeId": gauge_id, "readings": readings, "count": len(readings)})


# ============ STATS ============
@app.route('/stats', methods=['GET'])
def get_stats():
    """Get basic statistics."""
    # Count gauges
    gauges = list(db.collection('gauges').stream())

    # Count readings (expensive at scale - would use counters in production)
    readings = list(db.collection('readings').limit(1000).stream())

    # Count by condition
    condition_counts = {}
    for doc in readings:
        reading = doc.to_dict()
        c = reading.get('condition', 'unknown')
        condition_counts[c] = condition_counts.get(c, 0) + 1

    return jsonify({
        "gaugeCount": len(gauges),
        "readingCount": len(readings),
        "readingsByCondition": condition_counts
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
```

##### Code Notes
Default database. Every project has one default database which will be created first. We are just using that, so we don't need to worry about the name.

Calling `db.collection({name}).document()` with no arguments returns a random unique ID and gives a reference to the (not-yet-existing) document. Then `.set({object})` replaces it with our data. If you call `document(id)` you get a reference to the matching document, if it exists.

Streaming. `query.stream()` is a generator -- it yields documents one at a time. For results expected to be large, you can paginate with `.limit(n)` and `start_after(last_doc)`.

Firestore SDK `.document(id)` will never return `None` even if the document doesn't exist. Check the `exists` property to confirm existence.

---
## Step 3: Deploy Updated API

```bash
gcloud run deploy riverpulse-api \
--source . \
--allow-unauthenticated \
--region us-central1 \
--memory 512Mi
```

The Cloud Run service account automatically has Firestore access.

---

## Step 4: Register Gauges

```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

# Register gauges
curl -X POST $SERVICE_URL/gauges \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-001","name":"Arkansas at Salida","lat":38.5347,"lon":-106.0017,"riverMile":125.4}'

curl -X POST $SERVICE_URL/gauges \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-002","name":"Clear Creek at Golden","lat":39.7555,"lon":-105.2211,"riverMile":15.2}'

curl -X POST $SERVICE_URL/gauges \
-H "Content-Type: application/json" \
-d '{"gaugeId":"gauge-003","name":"Poudre at Fort Collins","lat":40.5853,"lon":-105.0844,"riverMile":42.8}'

# List gauges
curl $SERVICE_URL/gauges | python3 -m json.tool
```

Check Firestore console - you should see the `gauges` collection with documents.

---

## Step 5: Create Readings via Pub/Sub

```bash
# Publish readings (will be stored in Firestore via push subscription)
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":720,"stageHeight":3.8,"condition":"optimal","timestamp":"2026-01-31T08:15:00Z"}'

gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":1850,"stageHeight":5.9,"condition":"high","timestamp":"2026-01-31T08:22:00Z"}'

gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-002","type":"flow_reading","cfs":340,"stageHeight":2.1,"condition":"low","timestamp":"2026-01-31T09:00:00Z"}'

gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-002","type":"temp_reading","waterTemp":52,"airTemp":68,"timestamp":"2026-01-31T09:15:00Z"}'

gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-003","type":"flow_reading","cfs":2400,"stageHeight":7.2,"condition":"flood","timestamp":"2026-01-31T10:00:00Z"}'

# Wait for processing (console is synchronous, this shouldn't be needed)
# sleep 5

# Query readings
curl $SERVICE_URL/readings | python3 -m json.tool
```

---

## Step 6: Create Composite Index (Required for Complex Queries)

Firestore requires indexes for queries with multiple filters or filter + orderBy. Our API queries by `gaugeId` and `timestamp`, so we need an index.

```bash
# Create the index via gcloud
gcloud firestore indexes composite create \
--collection-group=readings \
--field-config field-path=gaugeId,order=ASCENDING \
--field-config field-path=timestamp,order=DESCENDING
```

**Note:** Index creation takes a few minutes. You can proceed to the queries below while it builds.

---

## Step 7: Query Readings

```bash
# All readings for a gauge
curl "$SERVICE_URL/gauges/gauge-001/readings" | python3 -m json.tool

# Filter by condition
curl "$SERVICE_URL/readings?condition=optimal" | python3 -m json.tool

# Filter by gauge and condition
curl "$SERVICE_URL/readings?gaugeId=gauge-001&condition=high" | python3 -m json.tool

# Get stats
curl $SERVICE_URL/stats | python3 -m json.tool
```

If queries fail with an index error, check that your index has finished building:
```sh
gcloud firestore indexes list
```

You can also check the service logs:
```sh
gcloud run services logs read riverpulse-api --region us-central1 --limit 10
```

---

## Step 8: Explore in Firestore Console

Open Cloud Console → Firestore:
1. Click on `gauges` collection - see your three gauges
2. Click on `readings` collection - see readings with all metadata
3. Try the query builder: Filter where `condition == "high"`

This visual exploration is valuable for understanding the data model.

---

## Data Model Summary

```
Firestore Database
│
├── gauges (collection)
│   ├── gauge-001 (document)
│   │   ├── gaugeId: "gauge-001"
│   │   ├── name: "Arkansas at Salida"
│   │   ├── lat: 38.5347
│   │   ├── lon: -106.0017
│   │   ├── riverMile: 125.4
│   │   ├── status: "active"
│   │   └── createdAt: "2026-01-31T..."
│   │
│   ├── gauge-002 (document)
│   └── gauge-003 (document)
│
└── readings (collection)
    ├── auto-generated-id-1 (document)
    │   ├── gaugeId: "gauge-001"
    │   ├── type: "flow_reading"
    │   ├── cfs: 1850
    │   ├── stageHeight: 5.9
    │   ├── condition: "high"
    │   ├── timestamp: "2026-01-31T..."
    │   ├── source: "pubsub"
    │   └── messageId: "..."
    │
    ├── auto-generated-id-2 (document)
    └── ... more readings
```

---

## Discussion Points for Interviews

- "Firestore's document model maps naturally to IoT data. Each gauge is a document, readings reference gauges by ID. Flexible schema means we can add new sensor types without migrations."

- "For RiverPulse, I'd use document IDs strategically: gaugeId as the document ID in the gauges collection for O(1) lookups, auto-generated IDs for readings."

- "Firestore requires composite indexes for complex queries. The tradeoff is write-time indexing cost for fast read queries. For a read-heavy dashboard with write-occasional readings, this works well."

- "Real-time listeners would power the live dashboard - when a high water reading arrives, subscribed clients get notified instantly without polling."

---

## Learning Summary
In this lab we created a Firestore NoSQL database for persistence, and updated our API to use this database, with ability to store and retrieve readings. We registered simulated gauges and pushed messages through Pub/Sub, handled by our API, storing them as readings in our database.

The WebSocket "under the hood" is provided by Firestore's realtime listeners. When client subscribes to query (ex: watch for high water conditions), Firestore maintains a persistent connection to push changes instantly. From portal code you could call `collection.onSnapshot(callback)` and Firestore will handle the connection. The portal part doesn't apply here as we are doing REST queries.


---

## Next Lab

Lab 5: Cloud Storage - data file storage with signed URLs.