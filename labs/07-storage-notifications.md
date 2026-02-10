# Overview

**Time:** 45-60 minutes  
**Prerequisites:** Labs 1-5 completed (Pub/Sub, Cloud Run, Firestore, Cloud Storage all working)  
**Why it matters:** This closes the loop. When a gauge uploads data, the system automatically processes it - no polling, no manual triggers. True event-driven architecture.

---

## Concepts (5 minutes)

- **Object Finalize:** Cloud Storage event fired when an object is created or overwritten
- **Pub/Sub Notification:** Cloud Storage can publish events to a Pub/Sub topic
- **Event-driven processing:** Upload triggers processing automatically

Complete flow after this lab:
```
[Gauge uploads data to Cloud Storage]
        |
        v
[Cloud Storage fires OBJECT_FINALIZE event]
        |
        v
[Pub/Sub: data-uploads topic]
        |
        v
[Cloud Run: processes data, extracts metadata]
        |
        v
[Firestore: stores data record with metadata]
```

---

## Step 1: Create Pub/Sub Topic for Storage Notifications

```bash
# Create dedicated topic for data upload notifications
gcloud pubsub topics create data-uploads

# Create subscription for monitoring (useful for debugging)
gcloud pubsub subscriptions create data-uploads-debug \
  --topic=data-uploads \
  --ack-deadline=60
```

---

## Step 2: Configure Cloud Storage Notification

```bash
PROJECT_ID=$(gcloud config get-value project)
DATA_BUCKET="${PROJECT_ID}-riverpulse-data"

# Create notification - fires on object finalize (create/overwrite)
gcloud storage buckets notifications create gs://${DATA_BUCKET} \
  --topic=data-uploads \
  --event-types=OBJECT_FINALIZE

# Verify notification exists
gcloud storage buckets notifications list gs://${DATA_BUCKET}
```

You should see output showing the notification config with `event_types: OBJECT_FINALIZE`.

**Troubleshooting:** If you get an error about the Cloud Storage service account not existing, wait 10 seconds and retry the `notifications create` command. GCP provisions service accounts asynchronously, and occasionally the Pub/Sub topic is created before the Cloud Storage service account is ready. A retry always succeeds.

Note that OBJECT_FINALIZE is a system-provided Cloud Storage event. There are several, including:
```sh
OBJECT_FINALIZE         # object created or overwritten
OBJECT_DELETE           # object deleted
OBJECT_ARCHIVE          # object archived (versioning)
OBJECT_METADATA_UPDATE  # metadata changed
```

---

## Step 3: Test the Notification

```bash
# Upload a test file
echo "test data content" > /tmp/test-upload.json
gcloud storage cp /tmp/test-upload.json \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/export-test/readings.json"

# Check if notification was published
gcloud pubsub subscriptions pull data-uploads-debug --limit=5 --auto-ack
```

You should see a message with metadata about the uploaded object:
- `bucketId`: your bucket name
- `objectId`: the object path
- `eventType`: OBJECT_FINALIZE
- `eventTime`: when it happened

---

## Step 4: Add Data Processing Endpoint to API

Update `main.py` to handle data upload notifications:

```python
# Add this route to main.py

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
```

Make sure you have the timezone import at the top of `main.py`.

---

## Step 5: Redeploy the API

If you have CI/CD from Lab 6:
```sh
git add .
git commit -m "Lab 7 - data upload processing"
git push
```

You can also manually redeploy, but will receive error when Cloud Run sees the Dockerfile and isn't sure what to do if you don't add the `--clear-base-image` here.
```bash
cd ~/riverpulse-api
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1 \
  --memory 512Mi \
  --clear-base-image
```

---

## Step 6: Create Push Subscription for Data Uploads

```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

# Create push subscription to trigger our API
gcloud pubsub subscriptions create data-uploads-push \
  --topic=data-uploads \
  --push-endpoint="${SERVICE_URL}/pubsub/data-upload" \
  --ack-deadline=60

# Verify
gcloud pubsub subscriptions describe data-uploads-push
```

---

## Step 7: Test the Complete Flow

```bash
# Upload a "readings" file
dd if=/dev/urandom bs=1024 count=50 > /tmp/daily-readings.json
gcloud storage cp /tmp/daily-readings.json \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/export-daily-001/readings.json"

# Upload a "csv export" file
dd if=/dev/urandom bs=1024 count=10 > /tmp/export.csv
gcloud storage cp /tmp/export.csv \
  "gs://${DATA_BUCKET}/gauge-002/2026-01-31/export-hourly-001/readings.csv"

# Upload a "snapshot" from stick gauge camera
dd if=/dev/urandom bs=1024 count=5 > /tmp/snapshot.jpg
gcloud storage cp /tmp/snapshot.jpg \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/export-daily-001/snapshot.jpg"

# Wait for processing
sleep 5

# Check export records in Firestore
curl $SERVICE_URL/exports | python3 -m json.tool
```

You should see export records with gaugeId, dataType, size, and processedAt timestamps.

---

## Step 8: Get Export with Signed URL

```bash
# Get the export ID from the previous response
# Then fetch with signed URL:
curl "$SERVICE_URL/exports" | python3 -m json.tool

# Pick an ID from the response (the "id" field, without braces):
export EXPORT_ID=your_actual_id_here
curl "$SERVICE_URL/exports/${EXPORT_ID}" | python3 -m json.tool
```

The response should include a `signedUrl` you can open in a browser to download the file. If you run into permission errors, and you might, it's probably a permissions issue, but you can check. First, check the raw output:
```sh
curl $SERVICE_URL/exports
```

If you see something like a 500 error here (or no URL), check the logs
```
gcloud run services logs read riverpulse-api --region us-central1 --limit 10
```

That will confirm the error, which from experience is probably permissions related. If that is the case, grant the Cloud Run service account the required IAM role on itself
```sh
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')

gcloud iam service-accounts add-iam-policy-binding \
  ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Now redeploy.  Use this method as git will show no changes. You'll need to use the `--clear-base-image` as mentioned above:
```sh
cd ~/riverpulse-api
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1 \
  --memory 512Mi \
  --clear-base-image
```

Then retry with the signed URL.
```sh
curl "$SERVICE_URL/exports/${EXPORT_ID}"
```

Note: Signed URL with default credentials has some quirks. If you still receive 500 error, consider this exploration path. Change the Python code in the `get_export` endpoint from:
```python
signed_url = blob.generate_signed_url(
    version="v4",
    expiration=timedelta(hours=1),
    method="GET"
)
```

To:
```python
import google.auth
from google.auth.transport import requests as auth_requests

# Get default credentials with signing capability
credentials, project = google.auth.default()
if hasattr(credentials, 'refresh'):
    credentials.refresh(auth_requests.Request())

signed_url = blob.generate_signed_url(
    version="v4",
    expiration=timedelta(hours=1),
    method="GET",
    credentials=credentials
)
```

---

## Step 9: Check Logs

```bash
gcloud run services logs read riverpulse-api --region us-central1 --limit 20
```

You should see:
- "Data upload notification: OBJECT_FINALIZE - gs://..."
- "Data record created: {id} - json from gauge-001"

---

## Architecture Summary

```
[Gauge uploads to Cloud Storage]
        |
        v
[Cloud Storage: gs://riverpulse-data-xxx/gauge-001/2026-01-31/export-001/readings.json]
        |
        | OBJECT_FINALIZE notification
        v
[Pub/Sub: data-uploads topic]
        |
        | push subscription
        v
[Cloud Run: /pubsub/data-upload endpoint]
        |
        +---> [Cloud Storage: get object metadata (size, hash, etc)]
        |
        +---> [Firestore: create export record]
        |
        v
[Export record stored with full metadata]

[Portal requests export]
        |
        v
[Cloud Run: /exports/{id} endpoint]
        |
        +---> [Firestore: get export record]
        +---> [Cloud Storage: generate signed URL]
        |
        v
[Response with metadata + signed URL]
        |
        v
[Browser fetches data directly from Cloud Storage]
```

---

## Discussion Points for Interviews

- "Data uploads are fully event-driven. Gauge uploads to Cloud Storage, which fires a notification to Pub/Sub, which triggers our processing service. No polling, no cron jobs."

- "The processing extracts metadata from the object path and Cloud Storage APIs - file size, content type, MD5 hash for integrity. All stored in Firestore for fast querying."

- "When the portal needs to display data, it gets a record from Firestore and we generate a signed URL on demand. The URL expires in an hour, so even if leaked, access is time-limited."

- "This pattern scales automatically. 10 uploads or 10,000 uploads - Cloud Storage handles the writes, Pub/Sub handles the fanout, Cloud Run scales to process notifications."

---

## Cleanup (Optional)

```bash
# Delete subscriptions
gcloud pubsub subscriptions delete data-uploads-push
gcloud pubsub subscriptions delete data-uploads-debug

# Delete notification
# First get the notification ID:
gcloud storage buckets notifications list gs://${DATA_BUCKET}
# Then delete it:
gcloud storage buckets notifications delete gs://${DATA_BUCKET} --notification-id=NOTIFICATION_ID

# Delete topic
gcloud pubsub topics delete data-uploads
```

---

## Learning Summary

In this lab we configured Cloud Storage to automatically notify our system when new data files are uploaded. The notification fires a Pub/Sub message, which triggers our Cloud Run API to process the upload - extracting metadata, storing records in Firestore, and making the data available via signed URLs. This event-driven pattern eliminates polling and scales automatically with upload volume.

---

##### Note: Public Access
The associated Cloud Run service has public access (allows allUsers). You can revoke this with the button (in the warning box) on the Cloud Run page. You can also use gcloud.
```sh
# Remove public access
gcloud run services update riverpulse-api --region us-central1 --no-allow-unauthenticated

# Restore public access
gcloud run services update riverpulse-api --region us-central1 --allow-unauthenticated
```


***
## Next Lab

Lab 8: IoT + MQTT - device connectivity and fleet management.