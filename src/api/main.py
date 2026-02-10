from flask import Flask, jsonify, request
from google.cloud import firestore, storage, secretmanager, bigquery
import os
import base64
import json
import time
from datetime import datetime, timezone, timedelta

# Note: Using datetime.now(timezone.utc).isoformat() produces '+00:00' suffix
# e.g., '2026-01-31T12:34:56.789012+00:00'
# The older datetime.utcnow().isoformat() + 'Z' produced 'Z' suffix
# e.g., '2026-01-31T12:34:56.789012Z'
# Both are valid ISO 8601 UTC formats and should parse equivalently

app = Flask(__name__)

# Initialize Firestore client
# On Cloud Run, credentials are automatic via service account
db = firestore.Client()

# Initialize Storage client
storage_client = storage.Client()

# Initialize BigQuery client
bq_client = bigquery.Client()
BQ_TABLE = f"{os.environ.get('GOOGLE_CLOUD_PROJECT')}.riverpulse.readings"

@app.route('/')
def health():
    return jsonify({"status": "healthy", "service": "riverpulse-api", "database": "firestore"})

# ============ READINGS ============
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

# BigQuery helper
def stream_to_bigquery(reading):
    """
    Stream a reading to BigQuery for analytics.
    This runs alongside the Firestore write — both get the data.
    Firestore for real-time queries, BigQuery for analytics.
    """
    try:
        row = {
            "gauge_id": reading.get("gaugeId"),
            "timestamp": reading.get("timestamp", reading.get("receivedAt")),
            "cfs": reading.get("cfs"),
            "stage_height": reading.get("stageHeight"),
            "water_temp": reading.get("waterTemp"),
            "condition": reading.get("condition"),
            "source": reading.get("source", "api"),
            "received_at": reading.get("receivedAt"),
        }
        errors = bq_client.insert_rows_json(BQ_TABLE, [row])
        if errors:
            print(f"BigQuery streaming insert errors: {errors}")
    except Exception as e:
        # Don't fail the request if BQ insert fails — Firestore is primary
        print(f"BigQuery insert failed (non-fatal): {e}")


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

    # Secondary: stream to BigQuery for analytics
    stream_to_bigquery(reading)

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

# is this causing duplicate?
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

@app.route('/data/signed-url', methods=['GET'])
def get_signed_url():
    """Generate a signed URL for a data file."""
    bucket_name = request.args.get('bucket')
    object_path = request.args.get('path')
    
    if not bucket_name or not object_path:
        return jsonify({"error": "bucket and path required"}), 400
    
    # Get the bucket and blob
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_path)
    
    # Check if object exists
    if not blob.exists():
        return jsonify({"error": "Object not found"}), 404
    
    # Generate signed URL (valid for 1 hour)
    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(hours=1),
        method="GET"
    )
    
    return jsonify({
        "signedUrl": url,
        "expiresIn": "1 hour",
        "path": object_path
    })

@app.route('/gauges/<gauge_id>/exports', methods=['GET'])
def get_gauge_exports(gauge_id):
    """Get signed URLs for data exports associated with a gauge."""
    
    # Construct base path
    bucket_name = os.environ.get('DATA_BUCKET', f"{os.environ.get('GOOGLE_CLOUD_PROJECT', 'unknown')}-riverpulse-data")    
    bucket = storage_client.bucket(bucket_name)
    
    # List objects for this gauge
    blobs = bucket.list_blobs(prefix=f"{gauge_id}/")
    
    exports = []
    for blob in blobs:
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET"
        )
        exports.append({
            "name": blob.name.split('/')[-1],
            "path": blob.name,
            "signedUrl": url,
            "size": blob.size,
            "contentType": blob.content_type
        })
    
    return jsonify({
        "gaugeId": gauge_id,
        "exports": exports
    })

@app.route('/pubsub/data-upload', methods=['POST'])
def handle_data_upload():
    """
    Handle Cloud Storage upload notifications via Pub/Sub.
    
    Storage notification format:
    {
        "message": {
            "attributes": {
                "bucketId": "bucket-name",
                "objectId": "path/to/object",
                "eventType": "OBJECT_FINALIZE",
                "eventTime": "2026-01-31T...",
                ...
            },
            "data": "<base64-encoded-object-metadata>",
            "messageId": "...",
            "publishTime": "..."
        }
    }
    """
    envelope = request.get_json()
    
    if not envelope or 'message' not in envelope:
        return jsonify({"error": "Invalid Pub/Sub message"}), 400
    
    pubsub_message = envelope['message']
    attributes = pubsub_message.get('attributes', {})
    
    bucket_id = attributes.get('bucketId')
    object_id = attributes.get('objectId')
    event_type = attributes.get('eventType')
    event_time = attributes.get('eventTime')
    
    if not bucket_id or not object_id:
        print(f"Missing bucket or object ID in notification")
        return jsonify({"error": "Missing required attributes"}), 400
    
    print(f"Data upload notification: {event_type} - gs://{bucket_id}/{object_id}")
    
    # Parse the object path to extract metadata
    # Expected format: {gaugeId}/{date}/{exportId}/{filename}
    path_parts = object_id.split('/')
    
    if len(path_parts) >= 4:
        gauge_id = path_parts[0]
        date_str = path_parts[1]
        export_id = path_parts[2]
        filename = path_parts[3]
    else:
        # Fallback for unexpected path format
        gauge_id = 'unknown'
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        export_id = 'unknown'
        filename = path_parts[-1] if path_parts else 'unknown'
    
    # Determine data type from filename
    if filename.endswith('.json'):
        data_type = 'json'
    elif filename.endswith('.csv'):
        data_type = 'csv'
    elif filename.endswith('.jpg') or filename.endswith('.png'):
        data_type = 'snapshot'
    else:
        data_type = 'unknown'
    
    # Get object metadata from Cloud Storage
    bucket = storage_client.bucket(bucket_id)
    blob = bucket.blob(object_id)
    blob.reload()  # Fetch metadata from server
    
    # Create data record in Firestore
    data_record = {
        'bucketId': bucket_id,
        'objectId': object_id,
        'gaugeId': gauge_id,
        'exportId': export_id,
        'filename': filename,
        'dataType': data_type,
        'size': blob.size,
        'contentType': blob.content_type,
        'created': event_time,
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'storageClass': blob.storage_class,
        'md5Hash': blob.md5_hash,
    }
    
    # Store in Firestore
    doc_ref = db.collection('exports').document()
    doc_ref.set(data_record)
    
    print(f"Data record created: {doc_ref.id} - {data_type} from {gauge_id}")
    
    # Optionally, update the related reading if it exists
    # This links exports to the readings that triggered them
    if export_id and export_id != 'unknown':
        readings_query = db.collection('readings').where('gaugeId', '==', gauge_id).limit(1)
        # In production, you'd have a better way to link readings to exports
        # (e.g., exportId in the path matches a document ID)
    
    return jsonify({
        "status": "processed",
        "exportId": doc_ref.id,
        "dataType": data_type,
        "size": blob.size
    }), 200


# Add endpoint to list data records
@app.route('/exports', methods=['GET'])
def list_exports():
    """List export records with optional filters."""
    gauge_id = request.args.get('gaugeId')
    data_type = request.args.get('type')
    limit = int(request.args.get('limit', 50))
    
    query = db.collection('exports')
    
    if gauge_id:
        query = query.where('gaugeId', '==', gauge_id)
    if data_type:
        query = query.where('dataType', '==', data_type)
    
    query = query.order_by('processedAt', direction=firestore.Query.DESCENDING).limit(limit)
    
    docs = query.stream()
    exports = []
    for doc in docs:
        record = doc.to_dict()
        record['id'] = doc.id
        exports.append(record)
    
    return jsonify({"exports": exports, "count": len(exports)})


@app.route('/exports/<export_id>', methods=['GET'])
def get_export(export_id):
    """Get export record with signed URL for access."""
    doc = db.collection('exports').document(export_id).get()
    
    if not doc.exists:
        return jsonify({"error": "Export not found"}), 404
    
    record = doc.to_dict()
    record['id'] = doc.id
    
    # Generate signed URL for access
    bucket = storage_client.bucket(record['bucketId'])
    blob = bucket.blob(record['objectId'])
    
    signed_url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(hours=1),
        method="GET"
    )
    
    record['signedUrl'] = signed_url
    record['urlExpiresIn'] = '1 hour'
    
    return jsonify(record)

# ============ GAUGE REGISTRY ============
@app.route('/gauges/register', methods=['POST'])
def register_gauge():
    """
    Register a new gauge in the fleet.
    In production: called during gauge provisioning, generates credentials.
    """
    data = request.get_json()
    gauge_id = data.get('gaugeId')

    if not gauge_id:
        return jsonify({"error": "gaugeId required"}), 400

    gauge_ref = db.collection('gauges').document(gauge_id)

    # Check if already registered
    if gauge_ref.get().exists:
        return jsonify({"error": f"Gauge {gauge_id} already registered"}), 409

    gauge_doc = {
        'gaugeId': gauge_id,
        'name': data.get('name', ''),
        'location': data.get('location', {}),
        'riverName': data.get('riverName', ''),
        'firmware': data.get('firmware', 'unknown'),
        'status': 'registered',  # registered → online → offline
        'lastHeartbeat': None,
        'batteryLevel': None,
        'connectivity': data.get('connectivity', 'unknown'),
        'config': data.get('config', {}),
        'registeredAt': datetime.now(timezone.utc).isoformat(),
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    }

    gauge_ref.set(gauge_doc)
    return jsonify({"status": "registered", "gauge": gauge_doc}), 201


@app.route('/gauges/<gauge_id>/heartbeat', methods=['POST'])
def process_heartbeat(gauge_id):
    """
    Process a heartbeat from a gauge.
    Updates the gauge registry with latest health data.
    Called by the Pub/Sub → Cloud Run pipeline when message_type=heartbeat.
    """
    data = request.get_json()

    gauge_ref = db.collection('gauges').document(gauge_id)
    doc = gauge_ref.get()

    if not doc.exists:
        # Auto-register unknown gauges (or reject - your policy choice)
        gauge_ref.set({
            'gaugeId': gauge_id,
            'status': 'online',
            'registeredAt': datetime.now(timezone.utc).isoformat(),
        })

    # Update gauge with heartbeat data
    update_data = {
        'status': 'online',
        'lastHeartbeat': datetime.now(timezone.utc).isoformat(),
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    }

    # Copy relevant fields from heartbeat payload
    for field in ['battery', 'firmware', 'cpuTemp', 'storageUsedPct',
                  'signalStrength', 'connectivity', 'uptime']:
        if field in data:
            update_data[field] = data[field]

    gauge_ref.update(update_data)

    return jsonify({"status": "ok", "gaugeId": gauge_id}), 200


@app.route('/gauges/<gauge_id>/command', methods=['POST'])
def send_command(gauge_id):
    """
    Send a command to a gauge via Pub/Sub → Bridge → MQTT.
    Commands: config_update, calibrate, reboot, capture_snapshot
    """
    data = request.get_json()
    command_type = data.get('command')

    if not command_type:
        return jsonify({"error": "command field required"}), 400

    # Publish command to a dedicated Pub/Sub topic
    # The bridge script (or a separate subscriber) picks this up
    # and publishes to the MQTT topic riverpulse/commands/{gauge_id}
    command_payload = {
        'gaugeId': gauge_id,
        'command': command_type,
        'payload': data.get('payload', {}),
        'issuedAt': datetime.now(timezone.utc).isoformat(),
        'issuedBy': 'portal',  # or the authenticated user
    }

    # For now, store command in Firestore (the bridge polls or gets pushed)
    cmd_ref = db.collection('commands').document()
    cmd_ref.set(command_payload)

    return jsonify({
        "status": "queued",
        "commandId": cmd_ref.id,
        "command": command_payload
    }), 202


@app.route('/gauges/fleet', methods=['GET'])
def fleet_status():
    """
    Fleet overview - all gauges with status summary.
    This powers the fleet management dashboard.
    """
    gauges = db.collection('gauges').stream()

    fleet = []
    summary = {"total": 0, "online": 0, "offline": 0, "registered": 0}

    for doc in gauges:
        gauge = doc.to_dict()
        gauge['id'] = doc.id
        fleet.append(gauge)
        summary["total"] += 1
        status = gauge.get('status', 'unknown')
        if status in summary:
            summary[status] += 1

    return jsonify({"fleet": fleet, "summary": summary})

# ============ SECRET MANAGER ============
def get_secret(secret_id, version="latest"):
    """
    Retrieve a secret from Secret Manager.
    In production, cache this — don't call on every request.
    The project ID is automatically detected on Cloud Run.
    """
    client = secretmanager.SecretManagerServiceClient()
    project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
    
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
    
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Warning: Could not access secret {secret_id}: {e}")
        return None

# Remove or at least protect this route in production
@app.route('/admin/config-check', methods=['GET'])
def config_check():
    """
    Verify secret access is working.
    In production, remove this endpoint or protect with authentication.
    """
    weather_key = get_secret("weather-api-key")
    
    return jsonify({
        "secrets": {
            "weather-api-key": "accessible" if weather_key else "NOT FOUND",
            "key-preview": f"{weather_key[:8]}..." if weather_key else None,
        },
        "note": "Remove this endpoint before production"
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
