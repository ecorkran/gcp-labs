from flask import Flask, jsonify, request
from google.cloud import firestore, storage
import os
import base64
import json
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

# Initialize storage client
storage_client = storage.Client()

@app.route('/')
def health():
    return jsonify({"status": "healthy", "service": "riverpulse-api", "database": "firestore"})

# ============ EVENTS ============
@app.route('/events', methods=['GET'])
def get_events():
    """Query events with optional filters."""
    device_id = request.args.get('deviceId')
    event_type = request.args.get('type')
    limit = int(request.args.get('limit', 50))

    # Start with events collection
    query = db.collection('events')

    # Apply filters
    if device_id:
        query = query.where('deviceId', '==', device_id)

    if event_type:
        query = query.where('type', '==', event_type)

    # Order by timestamp descending, limit results
    query = query.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)

    # Execute query
    docs = query.stream()
    events = []

    for doc in docs:
        event = doc.to_dict()
        event['id'] = doc.id
        events.append(event)

    return jsonify({"events": events, "count": len(events)})


@app.route('/events', methods=['POST'])
def create_event():
    """Create event directly via API."""
    event = request.get_json()
    if not event:
        return jsonify({"error": "No event data provided"}), 400

    event['receivedAt'] = datetime.now(timezone.utc).isoformat()
    event['source'] = 'direct'

    # Use timestamp as part of document ID for ordering
    # Or let Firestore auto-generate: db.collection('events').add(event)
    doc_ref = db.collection('events').document()
    doc_ref.set(event)

    return jsonify({"status": "created", "id": doc_ref.id, "event": event}), 201


@app.route('/events/<event_id>', methods=['GET'])
def get_event(event_id):
    """Get single event by ID."""
    doc = db.collection('events').document(event_id).get()
    if not doc.exists:
        return jsonify({"error": "Event not found"}), 404
    event = doc.to_dict()
    event['id'] = doc.id
    return jsonify(event)

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
            event = json.loads(data)
        except json.JSONDecodeError:
            event = {"rawData": data}
    else:
        event = {}

    # Add metadata
    event['receivedAt'] = datetime.now(timezone.utc).isoformat()
    event['source'] = 'pubsub'
    event['messageId'] = pubsub_message.get('messageId')
    event['publishTime'] = pubsub_message.get('publishTime')

    if 'attributes' in pubsub_message:
        event['attributes'] = pubsub_message['attributes']

    # Ensure timestamp field exists for ordering
    if 'timestamp' not in event:
        event['timestamp'] = event['receivedAt']

    # Store in Firestore
    doc_ref = db.collection('events').document()
    doc_ref.set(event)
    print(f"Event stored: {doc_ref.id} - {event.get('type', 'unknown')}")
    return jsonify({"status": "processed", "id": doc_ref.id}), 200

# ============ DEVICES ============
@app.route('/devices', methods=['GET'])
def get_devices():
    """List all devices."""
    docs = db.collection('devices').stream()
    devices = []

    for doc in docs:
        device = doc.to_dict()
        device['id'] = doc.id
        devices.append(device)
    return jsonify({"devices": devices, "count": len(devices)})

@app.route('/devices', methods=['POST'])
def create_device():
    """Register a new device."""
    device = request.get_json()
    if not device or 'deviceId' not in device:
        return jsonify({"error": "deviceId required"}), 400

    device_id = device['deviceId']
    device['createdAt'] = datetime.now(timezone.utc).isoformat()
    device['status'] = device.get('status', 'active')

    # Use deviceId as document ID for easy lookup
    db.collection('devices').document(device_id).set(device)
    return jsonify({"status": "created", "device": device}), 201

@app.route('/devices/<device_id>', methods=['GET'])
def get_device(device_id):
    """Get device details."""
    doc = db.collection('devices').document(device_id).get()

    if not doc.exists:
        return jsonify({"error": "Device not found"}), 404

    device = doc.to_dict()
    device['id'] = doc.id
    return jsonify(device)


@app.route('/devices/<device_id>', methods=['PATCH'])
def update_device(device_id):
    """Update device fields (partial update)."""
    updates = request.get_json()
    if not updates:
        return jsonify({"error": "No update data provided"}), 400

    updates['updatedAt'] = datetime.now(timezone.utc).isoformat()
    doc_ref = db.collection('devices').document(device_id)
    doc_ref.update(updates)
    return jsonify({"status": "updated", "deviceId": device_id})


@app.route('/devices/<device_id>/events', methods=['GET'])
def get_device_events(device_id):
    """Get events for a specific device."""
    limit = int(request.args.get('limit', 50))

    query = db.collection('events') \
        .where('deviceId', '==', device_id) \
        .order_by('timestamp', direction=firestore.Query.DESCENDING) \
        .limit(limit)

    docs = query.stream()
    events = [{"id": doc.id, **doc.to_dict()} for doc in docs]
    return jsonify({"deviceId": device_id, "events": events, "count": len(events)})


# ============ STATS ============
@app.route('/stats', methods=['GET'])
def get_stats():
    """Get basic statistics."""
    # Count devices
    devices = list(db.collection('devices').stream())

    # Count events (expensive at scale - would use counters in production)
    events = list(db.collection('events').limit(1000).stream())

    # Count by type
    type_counts = {}
    for doc in events:
        event = doc.to_dict()
        t = event.get('type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1

    return jsonify({
        "deviceCount": len(devices),
        "eventCount": len(events),
        "eventsByType": type_counts
    })

# ============ SIGNED URL GENERATION ============
@app.route('/media/signed-url', methods=['GET'])
def get_signed_url():
    """Generate a signed URL for a media file."""
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

@app.route('/events/<event_id>/media', methods=['GET'])
def get_event_media(event_id):
    """Get signed URLs for all media associated with an event."""

    # In production, you'd look up the event in Firestore to get media paths
    # For this lab, we'll construct the path from event metadata
    doc = db.collection('events').document(event_id).get()
    if not doc.exists:
        return jsonify({"error": "Event not found"}), 404
    
    event = doc.to_dict()
    device_id = event.get('deviceId', 'unknown')
    timestamp = event.get('timestamp', '')[:10]  # Get date portion
    
    # Construct base path
    base_path = f"{device_id}/{timestamp}/{event_id}"
    bucket_name = os.environ.get('MEDIA_BUCKET', f"riverpulse-data-{os.environ.get('GOOGLE_CLOUD_PROJECT', 'unknown')}")
    bucket = storage_client.bucket(bucket_name)
    
    # List objects in the event folder
    blobs = bucket.list_blobs(prefix=base_path)
    
    media = []
    for blob in blobs:
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET"
        )
        media.append({
            "name": blob.name.split('/')[-1],
            "path": blob.name,
            "signedUrl": url,
            "size": blob.size,
            "contentType": blob.content_type
        })
    
    return jsonify({
        "eventId": event_id,
        "media": media
    })

@app.route('/pubsub/media-upload', methods=['POST'])
def handle_media_upload():
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
    
    print(f"Media upload notification: {event_type} - gs://{bucket_id}/{object_id}")
    
    # Parse the object path to extract metadata
    # Expected format: {deviceId}/{date}/{eventId}/{filename}
    path_parts = object_id.split('/')
    
    if len(path_parts) >= 4:
        device_id = path_parts[0]
        date_str = path_parts[1]
        event_id = path_parts[2]
        filename = path_parts[3]
    else:
        # Fallback for unexpected path format
        device_id = 'unknown'
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        event_id = 'unknown'
        filename = path_parts[-1] if path_parts else 'unknown'
    
    # Determine media type from filename
    if filename.endswith('.mp4') or filename.endswith('.mov'):
        media_type = 'video'
    elif filename.endswith('.wav') or filename.endswith('.mp3'):
        media_type = 'audio'
    elif filename.endswith('.jpg') or filename.endswith('.png'):
        media_type = 'image'
    else:
        media_type = 'unknown'
    
    # Get object metadata from Cloud Storage
    bucket = storage_client.bucket(bucket_id)
    blob = bucket.blob(object_id)
    blob.reload()  # Fetch metadata from server
    
    # Create media record in Firestore
    media_record = {
        'bucketId': bucket_id,
        'objectId': object_id,
        'deviceId': device_id,
        'eventId': event_id,
        'filename': filename,
        'mediaType': media_type,
        'size': blob.size,
        'contentType': blob.content_type,
        'created': event_time,
        'processedAt': datetime.now(timezone.utc).isoformat(),
        'storageClass': blob.storage_class,
        'md5Hash': blob.md5_hash,
    }
    
    # Store in Firestore
    doc_ref = db.collection('media').document()
    doc_ref.set(media_record)
    
    print(f"Media record created: {doc_ref.id} - {media_type} from {device_id}")
    
    # Optionally, update the related event if it exists
    # This links media to the event that triggered the capture
    if event_id and event_id != 'unknown':
        events_query = db.collection('events').where('deviceId', '==', device_id).limit(1)
        # In production, you'd have a better way to link events to media
        # (e.g., eventId in the path matches a document ID)
    
    return jsonify({
        "status": "processed",
        "mediaId": doc_ref.id,
        "mediaType": media_type,
        "size": blob.size
    }), 200

# Add endpoint to list media records
@app.route('/media', methods=['GET'])
def list_media():
    """List media records with optional filters."""
    device_id = request.args.get('deviceId')
    media_type = request.args.get('type')
    limit = int(request.args.get('limit', 50))
    
    query = db.collection('media')
    
    if device_id:
        query = query.where('deviceId', '==', device_id)
    if media_type:
        query = query.where('mediaType', '==', media_type)
    
    query = query.order_by('processedAt', direction=firestore.Query.DESCENDING).limit(limit)
    
    docs = query.stream()
    media = []
    for doc in docs:
        record = doc.to_dict()
        record['id'] = doc.id
        media.append(record)
    
    return jsonify({"media": media, "count": len(media)})

@app.route('/media/<media_id>', methods=['GET'])
def get_media(media_id):
    """Get media record with signed URL for access."""
    doc = db.collection('media').document(media_id).get()
    
    if not doc.exists:
        return jsonify({"error": "Media not found"}), 404
    
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

# ============ DEVICE REGISTRY ============
# In the earlier IoT implementations, this was handled for us with the managed
# MQTT service.  No we do it ourselves, which is more work initially, but results
# in more control and no vendor lock-in.
@app.route('/devices/register', methods=['POST'])
def register_device():
    """
    Register a new device in the fleet.
    In production: called during device provisioning, generates credentials.
    """
    data = request.get_json()
    device_id = data.get('deviceId')

    if not device_id:
        return jsonify({"error": "deviceId required"}), 400

    device_ref = db.collection('devices').document(device_id)

    # Check if already registered
    if device_ref.get().exists:
        return jsonify({"error": f"Device {device_id} already registered"}), 409

    device_doc = {
        'deviceId': device_id,
        'name': data.get('name', ''),
        'location': data.get('location', {}),
        'firmware': data.get('firmware', 'unknown'),
        'status': 'registered',  # registered → online → offline
        'lastHeartbeat': None,
        'batteryLevel': None,
        'connectivity': data.get('connectivity', 'unknown'),
        'config': data.get('config', {}),
        'registeredAt': datetime.now(timezone.utc).isoformat(),
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    }

    device_ref.set(device_doc)
    return jsonify({"status": "registered", "device": device_doc}), 201


@app.route('/devices/<device_id>/heartbeat', methods=['POST'])
def process_heartbeat(device_id):
    """
    Process a heartbeat from a device.
    Updates the device registry with latest health data.
    Called by the Pub/Sub → Cloud Run pipeline when message_type=heartbeat.
    """
    data = request.get_json()

    device_ref = db.collection('devices').document(device_id)
    doc = device_ref.get()

    if not doc.exists:
        # Auto-register unknown devices (or reject - your policy choice)
        device_ref.set({
            'deviceId': device_id,
            'status': 'online',
            'registeredAt': datetime.now(timezone.utc).isoformat(),
        })

    # Update device with heartbeat data
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

    device_ref.update(update_data)

    return jsonify({"status": "ok", "deviceId": device_id}), 200


@app.route('/devices/<device_id>/command', methods=['POST'])
def send_command(device_id):
    """
    Send a command to a device via Pub/Sub → Bridge → MQTT.
    Commands: config_update, firmware_update, reboot, capture_now
    """
    data = request.get_json()
    command_type = data.get('command')

    if not command_type:
        return jsonify({"error": "command field required"}), 400

    # Publish command to a dedicated Pub/Sub topic
    # The bridge script (or a separate subscriber) picks this up
    # and publishes to the MQTT topic riverpulse/commands/{device_id}
    command_payload = {
        'deviceId': device_id,
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


@app.route('/devices/fleet', methods=['GET'])
def fleet_status():
    """
    Fleet overview - all devices with status summary.
    This powers the fleet management dashboard.
    """
    devices = db.collection('devices').stream()

    fleet = []
    summary = {"total": 0, "online": 0, "offline": 0, "registered": 0}

    for doc in devices:
        device = doc.to_dict()
        device['id'] = doc.id
        fleet.append(device)
        summary["total"] += 1
        status = device.get('status', 'unknown')
        if status in summary:
            summary[status] += 1

    return jsonify({"fleet": fleet, "summary": summary})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
