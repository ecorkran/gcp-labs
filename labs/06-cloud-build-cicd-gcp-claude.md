# Overview

**Time:** 45-60 minutes  
**Why it matters:** CI/CD is a core engineering practice. This lab sets up automated deployment: push to main → tests run → deploy to Cloud Run. No manual deploys.

---

## Concepts (5 minutes)

- **Cloud Build:** GCP's CI/CD service. Runs steps in containers.
- **Trigger:** Watches a repo, runs build on push/PR.
- **cloudbuild.yaml:** Defines the build steps.
- **Artifact Registry:** Stores container images (replaced Container Registry).

---

## Step 1: Push Your API to GitHub

If you haven't already, create a GitHub repo for your API:

```bash
cd ~/riverpulse/

# Initialize git if needed
git init

# Create .gitignore
cat > .gitignore << 'EOF'
venv/
__pycache__/
*.pyc
.env
EOF

# Commit
git add .
git commit -m "RiverPulse API with Firestore"

# Create repo on GitHub and push
# Option A: Using gh CLI (easiest)
gh repo create riverpulse --private --source=. --push

# Option B: Manually via github.com
# 1. Go to github.com and create a new repo named "riverpulse" (private)
# 2. git remote add origin https://github.com/YOUR_USERNAME/riverpulse.git
# 3. git branch -M main  # Rename branch to main if needed
# 4. git push -u origin main
```

---

## Step 2: Create cloudbuild.yaml

This file defines your build pipeline.

```bash
cd ~/riverpulse/riverpulse-api
cat > cloudbuild.yaml << 'EOF'
# Cloud Build configuration for RiverPulse API
# Triggered on push to main branch

steps:
  # Step 1: Run tests (if you have them)
  - name: 'python:3.11-slim'
    id: 'test'
    dir: 'riverpulse-api'
    entrypoint: 'bash'
    args:
      - '-c'
      - |
        pip install -r requirements.txt
        # python -m pytest tests/ -v  # Uncomment when you have tests

  # Step 2: Build container image
  - name: 'gcr.io/cloud-builders/docker'
    id: 'build'
    dir: 'riverpulse-api'
    args:
      - 'build'
      - '-t'
      - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${SHORT_SHA}'
      - '-t'
      - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:latest'
      - '.'

  # Step 3: Push to Artifact Registry
  - name: 'gcr.io/cloud-builders/docker'
    id: 'push'
    args:
      - 'push'
      - '--all-tags'
      - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}'

  # Step 4: Deploy to Cloud Run
  - name: 'gcr.io/cloud-builders/gcloud'
    id: 'deploy'
    args:
      - 'run'
      - 'deploy'
      - '${_SERVICE}'
      - '--image'
      - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${SHORT_SHA}'
      - '--region'
      - '${_REGION}'
      - '--platform'
      - 'managed'
      - '--allow-unauthenticated'
      - '--memory'
      - '512Mi'
      - '--set-env-vars'
      - 'DATA_BUCKET=${PROJECT-ID}-riverpulse-data'

# Substitution variables (can be overridden in trigger config)
substitutions:
  _REGION: us-central1
  _SERVICE: riverpulse-api
  _REPO: riverpulse-repo

# Build options
options:
  logging: CLOUD_LOGGING_ONLY

# Images to store in Artifact Registry
images:
  - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${SHORT_SHA}'
  - '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:latest'
EOF
```

---

## Step 3: Create Dockerfile

Cloud Build needs a Dockerfile for this configuration:

```bash
cat > Dockerfile << 'EOF'
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run with gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
EOF
```

---

## Step 4: Create Artifact Registry Repository

This creates a specific artifact registry repository under our control. Earlier `gcloud run --deploy` commands created and updated a default repository (`cloud-run-source-deploy`) which is why you may see an "extra" artifact repository when you run the list command below.
```bash
PROJECT_ID=$(gcloud config get-value project)

# Enable Artifact Registry API
gcloud services enable artifactregistry.googleapis.com

# Create repository for Docker images
gcloud artifacts repositories create riverpulse-repo \
  --repository-format=docker \
  --location=us-central1 \
  --description="RiverPulse container images"

# Verify
gcloud artifacts repositories list --location=us-central1
```

---

## Step 5: Grant Cloud Build Permissions

```bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

# Cloud Build service account needs permission to deploy to Cloud Run
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/run.admin"

# And to act as the runtime service account
gcloud iam service-accounts add-iam-policy-binding \
  ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

---

## Step 6: Connect GitHub and Create Trigger

**Option A: Via Console (Easier for first time)**
You may need to select a service account. There should be an @developer.gserviceaccount.com account. Pick that one. For production environments you would want to heed the warning and select an account with narrow permissions. Not important for this lab in a temporary project.

1. Go to Cloud Console → Cloud Build → Triggers
2. Click "Connect Repository"
3. Select "GitHub (Cloud Build GitHub App)"
4. Authorize and select your repository
5. Click "Create Trigger"
6. Configure:
   - Name: `deploy-on-push`
   - Event: Push to branch
   - Branch: `^main$`
   - Configuration: Cloud Build configuration file
   - Location: `cloudbuild.yaml`
7. Save

**Option B: Via gcloud (After GitHub connection exists)**
```bash
# First connect GitHub via console (required once)
# Then create trigger via CLI:

gcloud builds triggers create github \
  --name="deploy-on-push" \
  --repo-name="riverpulse-api" \
  --repo-owner="YOUR_GITHUB_USERNAME" \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml"
```

---

## Step 7: Test the Pipeline

```bash
# If needed, make a small change
echo "# CI/CD enabled" >> README.md

```bash
cd ~/riverpulse/riverpulse-api
git add .
git commit -m "RiverPulse API with Firestore and CI/CD"
git push -u origin main
```


Watch the build:
1. Cloud Console → Cloud Build → History
2. Click on the running build to see logs
3. Or via CLI: `gcloud builds list --limit=5`

After ~2-3 minutes, your new version should be deployed.

---

## Step 8: Verify Deployment

```bash
# Get the service URL
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

# Test
curl $SERVICE_URL
curl $SERVICE_URL/gauges
```

---

## Step 9: Add a Simple Test (Optional but Recommended)

Create `tests/test_api.py`:

```bash
mkdir -p tests

cat > tests/test_api.py << 'EOF'
import pytest
from main import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_health_endpoint(client):
    response = client.get('/')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'healthy'

def test_readings_endpoint(client):
    response = client.get('/readings')
    assert response.status_code == 200
    data = response.get_json()
    assert 'readings' in data
    assert 'count' in data
EOF
```

Add pytest to `requirements.txt`:
```
pytest==7.4.0
pytest-cov==3.0
```

Update `cloudbuild.yaml` step 1 to uncomment the test line:
```yaml
- python -m pytest tests/ -v
```

Now pushes will run tests before deploying.  Verify by committing and pushing these latest changes.

---

## Build Pipeline Summary

```
[Developer pushes to main]
         |
         v
[Cloud Build Trigger fires]
         |
         v
[Step 1: Run tests]
    - Install dependencies
    - Run pytest
    - Fail build if tests fail
         |
         v
[Step 2: Build Docker image]
    - Tag with commit SHA
    - Tag with 'latest'
         |
         v
[Step 3: Push to Artifact Registry]
    - Store versioned images
         |
         v
[Step 4: Deploy to Cloud Run]
    - Zero-downtime deployment
    - New revision becomes active
         |
         v
[Service live with new code]
```

---

## Discussion Points for Interviews

- "CI/CD is fully automated. Push to main runs tests, builds a container, and deploys to Cloud Run. No manual steps."

- "Every deployment is tagged with the git commit SHA, so I can trace any running version back to exact source code. Rollback is just deploying an older image."

- "Cloud Build runs in isolated containers, so the build environment is reproducible. No 'works on my machine' issues."

- "For a production system, I'd extend this to multiple environments: push to main deploys to staging, manual approval promotes to production. Could also add integration tests that hit the staging API before production promotion."

---

## Cleanup (Optional)

```bash
# Delete trigger
gcloud builds triggers delete deploy-on-push

# Delete Artifact Registry images (keeps repo)
gcloud artifacts docker images delete \
  us-central1-docker.pkg.dev/${PROJECT_ID}/riverpulse-repo/riverpulse-api \
  --delete-tags

# Delete repository
gcloud artifacts repositories delete riverpulse-repo --location=us-central1
```

---

## What You've Built

After these 6 labs, you have:

1. **Pub/Sub** - Message queue for sensor events
2. **Cloud Run** - Serverless API backend
3. **Pub/Sub → Cloud Run** - Event-driven processing
4. **Firestore** - NoSQL database for gauges and readings
5. **Cloud Storage** - Data files with lifecycle management
6. **Cloud Build** - Automated CI/CD pipeline

This is a functional skeleton of the RiverPulse backend architecture. In the interview, you can speak from hands-on experience with these services.

***

## Learning Summary

In this lab we:
* Established github repo if not already done (ours was)
* Created YAML build configuration file
* Specified a minimal Dockerfile
* Created explicit Artifact Registry Repository (previously we used default)
* Created a trigger in Cloud Console and connected it to push in our github repository
* Granted the required IAM permissions to allow the build to run
* Added minimal testing with pytest that runs as part of the build spec
* Tested the configuration and confirmed builds run as expected