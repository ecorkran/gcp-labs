# Overview

**Time:** 45-60 minutes  
**Why it matters:** RiverPulse stores historical data exports, gauge snapshots, and potentially webcam images from stick gauges. That data lives in Cloud Storage. The portal serves it via signed URLs (temporary, secure links). This lab covers the storage patterns you'd discuss in the interview.

---

## Concepts (5 minutes)

- **Bucket:** Container for objects (files). Globally unique name.
- **Object:** A file with metadata.
- **Signed URL:** Temporary URL that grants access to private objects without authentication.
- **Lifecycle rules:** Automatically transition or delete objects based on age.

For RiverPulse:
- Recent data exports → Standard storage (hot, fast access)
- After 30 days → Nearline (cheaper, slight retrieval cost)
- After 90 days → Coldline or Archive (much cheaper, higher retrieval cost)

---

## Step 1: Create Buckets

We create buckets with `--uniform-bucket-level-access`. This means permissions are controlled at the bucket level by IAM, not per-object ACLs. This provides a simpler and less error-prone security model. Without this, both modes are supported. If you've run into trouble trying to mix older `gsutil` commands and `gcloud` commands, you'll definitely be happier with uniform access.

```bash
# Set your project
PROJECT_ID=$(gcloud config get-value project)

# Create bucket for data (must be globally unique)
DATA_BUCKET="riverpulse-data-${PROJECT_ID}"
gcloud storage buckets create gs://${DATA_BUCKET} \
  --location=us-central1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access

# Create bucket for gauge snapshots/images (separate for access control)
SNAPSHOTS_BUCKET="riverpulse-snapshots-${PROJECT_ID}"
gcloud storage buckets create gs://${SNAPSHOTS_BUCKET} \
  --location=us-central1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access

# Verify
gcloud storage buckets list
```

---

## Step 2: Organize with Prefixes (Folders)

Cloud Storage *doesn't have real folders*, but prefixes simulate them.

```bash
# Create some test files
echo "gauge,timestamp,cfs,stage" > /tmp/sample-export.csv
echo "test snapshot content" > /tmp/sample-snapshot.jpg
echo "historical data" > /tmp/historical.json

# Upload with organized prefixes
# Pattern: /{gaugeId}/{date}/{filename}

gcloud storage cp /tmp/sample-export.csv \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/export.csv"

gcloud storage cp /tmp/historical.json \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/readings.json"

gcloud storage cp /tmp/sample-snapshot.jpg \
  "gs://${SNAPSHOTS_BUCKET}/gauge-001/2026-01-31/snapshot.jpg"

# Another gauge
gcloud storage cp /tmp/sample-export.csv \
  "gs://${DATA_BUCKET}/gauge-002/2026-01-31/export.csv"

# List contents
gcloud storage ls "gs://${DATA_BUCKET}/" --recursive
```

---

## Step 3: Set Lifecycle Rules

Note that *we* control what is nearline, coldline, and to-be-deleted. Create `lifecycle.json`.
```bash
cat > /tmp/lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
        "condition": {"age": 30}
      },
      {
        "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
        "condition": {"age": 90}
      },
      {
        "action": {"type": "Delete"},
        "condition": {"age": 365}
      }
    ]
  }
}
EOF

# Apply lifecycle rules
gcloud storage buckets update "gs://${DATA_BUCKET}" \
  --lifecycle-file=/tmp/lifecycle.json

# Verify (note: this may show as empty initially, but rules are applied)
gcloud storage buckets describe "gs://${DATA_BUCKET}" --format="json(lifecycle_config)"
```

This means:
- Day 0-30: Standard storage (hot)
- Day 30-90: Nearline (warm, $0.01/GB retrieval)
- Day 90-365: Coldline (cold, $0.02/GB retrieval)
- Day 365+: Deleted

---

## Step 4: Generate Signed URLs

Signed URLs let the portal serve private data without exposing credentials. To sign URLs, we need a service account and permission for your user account to impersonate it.

First, grant your user account permission to impersonate service accounts (required for signing):
```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="user:YOUR_EMAIL@gmail.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Now create the storage service account:
```bash
# Create a service account for signing
gcloud iam service-accounts create riverpulse-storage \
  --display-name="RiverPulse Storage Service"

# Grant it access to the bucket
gcloud storage buckets add-iam-policy-binding "gs://${DATA_BUCKET}" \
  --member="serviceAccount:riverpulse-storage@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# Grant the service account signing permission
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:riverpulse-storage@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"

# Generate a signed URL (valid for 1 hour). Don't forget the region.
gcloud storage sign-url \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/export.csv" \
  --duration=1h \
  --region=us-central1 \
  --impersonate-service-account="riverpulse-storage@${PROJECT_ID}.iam.gserviceaccount.com"
```

Copy the signed URL and open it in your browser—you can access the file without authentication, even though the bucket is private.

**Troubleshooting:** If you get a permission error on `gcloud storage sign-url`, wait at least 60 seconds for IAM propagation and try again. IAM changes don't apply instantly.


---

## Step 5: Add Signed URL Generation to API

Update your API to generate signed URLs for data files.

Add to `requirements.txt`:
```
google-cloud-storage==2.14.0
```

Add these routes to `main.py`:
```python
from google.cloud import storage
from datetime import timedelta

# Initialize Storage client
storage_client = storage.Client()

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
    bucket_name = os.environ.get('DATA_BUCKET', f"riverpulse-data-{os.environ.get('GOOGLE_CLOUD_PROJECT', 'unknown')}")
    
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
```

Note: On Cloud Run, signed URL generation works automatically with the default service account. The code above will work after redeployment. Redeploy:
```sh
gcloud run deploy riverpulse-api --source . --allow-unauthenticated --region us-central1 --memory 512Mi
```

---

## Step 6: Upload Flow (How Gauges Would Upload)

In production, gauges upload directly to Cloud Storage, not through the API.
```bash
# Simulate gauge upload with resumable upload
# This is what the gauge firmware would do

# Generate a sample data file (in reality, actual sensor data).
# Note urandom is a special linux file device for generating random bytes.
dd if=/dev/urandom bs=1024 count=100 > /tmp/gauge-data.json

# Upload with metadata
gcloud storage cp /tmp/gauge-data.json \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/readings-new.json" \
  --content-type="application/json"

# Add custom metadata
gcloud storage objects update \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/readings-new.json" \
  --custom-metadata="gaugeId=gauge-001,readingCount=1440,exportType=daily"

# View metadata
gcloud storage objects describe \
  "gs://${DATA_BUCKET}/gauge-001/2026-01-31/readings-new.json"
```

---

## Step 7: CORS Configuration (For Browser Access)

If the portal fetches signed URLs directly from browser, update CORS (Cross-Origin Resource Sharing) to allow secure request. Here we accept anything, but of course in production we would replace with our actual portal domain.
```bash
cat > /tmp/cors.json << 'EOF'
[
  {
    "origin": ["*"],
    "method": ["GET", "HEAD"],
    "responseHeader": ["Content-Type", "Content-Range"],
    "maxAgeSeconds": 3600
  }
]
EOF

gcloud storage buckets update "gs://${DATA_BUCKET}" --cors-file=/tmp/cors.json

# Verify. Note lab originally had just cors, not cors_config, resulting in the null error mentioned below.
gcloud storage buckets describe "gs://${DATA_BUCKET}" --format="json(cors_config)"

# if you receive a null, try with full filename, no quotes
gcloud storage buckets update gs://${DATA_BUCKET} --cors-file=/tmp/cors.json
```

In production, replace `"*"` with your actual portal domain.

---

## Storage Architecture Summary

```
Cloud Storage
│
├── riverpulse-data-{project}
│   ├── gauge-001/
│   │   ├── 2026-01-31/
│   │   │   ├── export.csv
│   │   │   ├── readings.json
│   │   │   └── readings-new.json
│   │   └── 2026-01-30/
│   └── gauge-002/
│
└── riverpulse-snapshots-{project}
    ├── gauge-001/
    │   └── 2026-01-31/
    │       └── snapshot.jpg  (stick gauge camera image)
    └── gauge-002/

Lifecycle:
  0-30 days:  STANDARD  (hot)
  30-90 days: NEARLINE  (warm)
  90-365 days: COLDLINE (cold)
  365+ days:  DELETED
```

---

## Discussion Points for Interviews

- "Data files go to Cloud Storage, organized by gauge and date for easy lifecycle management. The portal never serves files directly - it generates short-lived signed URLs."

- "Lifecycle rules automatically transition old data to cheaper storage classes. We keep 30 days hot for active analysis, then move to Nearline, then Coldline. Automatic deletion at 1 year unless flagged for retention."

- "For large historical exports, signed URLs support byte-range requests, so the client can stream data without downloading the entire file."

- "Remote gauge uploads go directly to Cloud Storage via resumable upload - if connectivity drops mid-transfer, it resumes from where it left off."

---

## Cleanup (Optional)

```bash
# Delete bucket contents first
gcloud storage rm "gs://${DATA_BUCKET}/**" --recursive
gcloud storage rm "gs://${SNAPSHOTS_BUCKET}/**" --recursive

# Delete buckets
gcloud storage buckets delete "gs://${DATA_BUCKET}"
gcloud storage buckets delete "gs://${SNAPSHOTS_BUCKET}"

# Delete service account
gcloud iam service-accounts delete \
  "riverpulse-storage@${PROJECT_ID}.iam.gserviceaccount.com"
```

---

## Learning Summary
We created buckets in Cloud Storage and added functionality to generate signed URLs allowing us to interact with the private bucket without authentication. The URL is valid for only the duration specified. To allow the signing, we created a service account which persists for the life of the project, and granted it the required IAM permissions.

Side note: we use uniform access to allow IAM to control bucket permissions, rather than mixing with the older ACL model. This follows current (2026) best practices and is much simpler.

This lets us simulate the much more realistic condition of data files being uploaded to the storage, with resumable upload in case of lost connection. Lifecycle rules that we define control data state. Finally, we set up CORS to allow the portal to fetch files and display in browser.


---

## Next Lab

Lab 6: Cloud Build CI/CD - automated deployments on git push.