# Overview

**Time:** 30-45 minutes  
**Prerequisites:** Labs 1-6 completed (Cloud Run API deployed, Firestore working)  

###### New Skills
* Secret Manager
* Accessing secrets from Cloud Run
* Secret versioning and IAM-scoped access

---

## Concepts (5 minutes)

- **Secret:** A named resource containing one or more secret versions (the actual sensitive data)
- **Secret Version:** An immutable, timestamped snapshot of the secret's value
- **Accessor Role:** IAM role that allows reading secret values — separate from admin permissions
- **Automatic Rotation:** Scheduled secret updates (not covered hands-on here, but worth knowing)

Right now the RiverPulse system has credentials scattered in places they shouldn't be: the MQTT bridge script has Pub/Sub topic names and potentially API keys inline, Cloud Run environment variables hold configuration that should be locked down. Secret Manager centralizes all of this.

The pattern:
```
[Cloud Run / Cloud Function / Compute Engine]
      |
      | IAM-authenticated request
      v
[Secret Manager]
      |
      | returns secret value
      v
[Application uses credential]
```

AWS equivalent: Secrets Manager. Nearly identical concept, slightly different API surface.

---

## Setup

```bash
# Enable Secret Manager API
gcloud services enable secretmanager.googleapis.com

# Verify
gcloud services list --enabled --filter="name:secretmanager"
```

---

## Step 1: Create Secrets

We'll create secrets that a production RiverPulse system would actually need.

```bash
# Create a secret for an external API key (e.g., weather service for correlation)
echo -n "rp-weather-api-key-2026-abc123" | \
  gcloud secrets create weather-api-key \
    --data-file=- \
    --replication-policy="automatic"

# Create a secret for the MQTT broker password
echo -n "mqtt-br0ker-s3cure-passw0rd" | \
  gcloud secrets create mqtt-broker-password \
    --data-file=- \
    --replication-policy="automatic"

# Create a secret for a database connection string (if we ever add Cloud SQL)
echo -n "postgresql://riverpulse:dbpass@10.0.0.5:5432/riverpulse" | \
  gcloud secrets create db-connection-string \
    --data-file=- \
    --replication-policy="automatic"

# List secrets (values are NOT shown — only metadata)
gcloud secrets list
```

Note `--data-file=-` reads from stdin. You can also use `--data-file=path/to/file` for larger secrets like certificates. The `--replication-policy="automatic"` lets Google manage replication across regions. For specific compliance requirements you can use `user-managed` and pick regions.

---

## Step 2: Read and Verify Secrets

```bash
# Access the latest version of a secret
gcloud secrets versions access latest --secret=weather-api-key

# Access the latest version of MQTT password
gcloud secrets versions access latest --secret=mqtt-broker-password

# View secret metadata (not the value)
gcloud secrets describe weather-api-key

# List all versions of a secret
gcloud secrets versions list weather-api-key
```

Notice the distinction: `describe` shows metadata (creation time, replication, labels). `versions access` retrieves the actual value. IAM controls who can do which operation — an operator might have `describe` access for auditing without being able to read the actual secret.

---

## Step 3: Add a New Secret Version

Secrets change. API keys rotate, passwords update. Secret Manager handles this with versioning — old versions remain accessible until explicitly disabled or destroyed.

```bash
# Rotate the weather API key — add a new version
echo -n "rp-weather-api-key-2026-xyz789-rotated" | \
  gcloud secrets versions add weather-api-key --data-file=-

# Now "latest" points to version 2
gcloud secrets versions access latest --secret=weather-api-key

# But version 1 still exists
gcloud secrets versions access 1 --secret=weather-api-key

# List versions — see both
gcloud secrets versions list weather-api-key
```

In production, your application always references `latest` (or a pinned version for stability). When you rotate, you add a new version, verify the app works, then disable the old one:

```bash
# Disable old version (can't be accessed, but not destroyed)
gcloud secrets versions disable 1 --secret=weather-api-key

# Verify — this will fail now
gcloud secrets versions access 1 --secret=weather-api-key
# ERROR: Secret version is in DISABLED state

# If needed, re-enable
gcloud secrets versions enable 1 --secret=weather-api-key
```

---

## Step 4: IAM-Scoped Access

The key security principle: only the services that need a secret should be able to read it. Cloud Run needs the weather API key. The MQTT broker VM needs the broker password. Neither needs the other's secrets.

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

# Grant Cloud Run's default service account access to the weather API key ONLY
gcloud secrets add-iam-policy-binding weather-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Grant it access to the DB connection string too
gcloud secrets add-iam-policy-binding db-connection-string \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# View the IAM policy on a secret
gcloud secrets get-iam-policy weather-api-key
```

The `secretAccessor` role grants read-only access to secret values. The service account cannot create, delete, or modify secrets — only read them. This is the principle of least privilege.

If you had a dedicated MQTT service account (from Lab 8's broker), you'd grant it access to `mqtt-broker-password` only, not to the weather key or DB connection string.

---

## Step 5: Access Secrets from Cloud Run

Update the RiverPulse API to read secrets at startup instead of using hardcoded values or environment variables.

Add to `requirements.txt`:
```
google-cloud-secret-manager==2.18.0
```

Add a helper to `main.py`:
```python
from google.cloud import secretmanager

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
```

Redeploy (or git commit and push):
```bash
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1
```

Test after deployment:
```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

curl ${SERVICE_URL}/admin/config-check
```

You should see the secret is accessible. The Cloud Run service account has the IAM binding from Step 4.

---

## Step 6: Alternative — Mount Secrets as Environment Variables

Cloud Run also supports mounting secrets directly as environment variables, without any SDK code. This is simpler for configuration values that don't change per-request.

```bash
# Redeploy with secret mounted as env var
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1 \
  --set-secrets="WEATHER_API_KEY=weather-api-key:latest"
```

Now in your code, `os.environ.get('WEATHER_API_KEY')` returns the secret value. Cloud Run fetches it at container startup. No SDK needed.

The tradeoff: env var mounting is simpler but only fetches the secret at deploy/startup time. The SDK approach (Step 5) can fetch fresh values at runtime, which matters for rotation without redeployment.

---

## Discussion Points for Interviews

- "Credentials are in Secret Manager, not environment variables or config files. The service account has accessor permission scoped to only the secrets it needs — the API can read the weather key but not the MQTT broker password."

- "Secret versioning handles rotation without downtime. We add a new version, the app picks it up on next access, and we disable the old version once confirmed. If something breaks, we re-enable the old version — instant rollback."

- "For Cloud Run, we have two patterns: mount as environment variable for simple config (fetched at startup), or use the SDK for secrets that might rotate between deployments. We use env var mounting for stable config and the SDK for anything that rotates."

- "On the MQTT broker VM, the bridge script reads its credentials from Secret Manager at startup. If the VM restarts, it gets fresh credentials automatically — no stale keys baked into the image."

---

## Cleanup

```bash
# Delete secrets (this is permanent)
gcloud secrets delete weather-api-key --quiet
gcloud secrets delete mqtt-broker-password --quiet
gcloud secrets delete db-connection-string --quiet
```

If you deployed with `--set-secrets`, redeploy without to remove the binding:
```bash
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1 \
  --remove-secrets="WEATHER_API_KEY"
```

---

## Learning Summary

We created secrets in Secret Manager, added new versions to simulate rotation, and disabled old versions. We scoped IAM access so only the services that need each secret can read it. We integrated secret access into the Cloud Run API two ways: via the Python SDK (runtime access, supports rotation without redeploy) and via environment variable mounting (simpler, fetched at startup). The core principle is that credentials live in one secure, auditable, version-controlled place rather than scattered across config files, environment variables, and source code.

---

## Next Lab

Lab 10: Cloud Monitoring & Alerting — observe the system you've built.
