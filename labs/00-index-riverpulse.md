# GCP Labs Series - Overview
These labs build a functional RiverPulse backend skeleton - a real-time river flow monitoring system. Work through them in order - each builds on the previous.

---
## Lab Overview
| Lab | Topic | Time | Discussion Points for Interviews |
|-----|-------|------|----------------------------------|
| 01 | Pub/Sub Basics | 30-45 min | Message decoupling, fan-out, dead letter queues |
| 02 | Cloud Run API | 45-60 min | Serverless containers, auto-scaling, no VM management |
| 03 | Pub/Sub → Cloud Run | 45-60 min | Push subscriptions, acknowledgment model, retry behavior |
| 04 | Firestore | 60-90 min | NoSQL document model, real-time listeners, composite indexes |
| 05 | Cloud Storage | 45-60 min | Data storage, signed URLs, lifecycle rules |
| 06 | Cloud Build CI/CD | 45-60 min | Automated deployment, git → build → test → deploy |
| 07 | Storage Notifications | 45-60 min | Event-driven processing, OBJECT_FINALIZE triggers |
| 08 | IoT + MQTT | 90-120 min | Self-managed MQTT broker, device registry, fleet management |

**Total time: ~8-10 hours** (working through carefully with exploration)

---
## Recommended Approach

**Session 1 (2-3 hours):** Labs 1-3
- Get the message pipeline working
- Gauge readings flow through Pub/Sub to your API

**Session 2 (2-3 hours):** Labs 4-6
- Add persistence (Firestore, Cloud Storage)
- Add CI/CD - deploy on every push

**Session 3 (2-3 hours):** Labs 7-8
- Event-driven storage processing
- IoT/MQTT device connectivity

**Optional Session 4:**
- Explore the console, run queries
- Practice explaining the architecture out loud
- Break something and fix it

---
## What You'll Have Built

```
[Gauge sensor publishes reading]
	|
	v
[MQTT Broker (Compute Engine)]
	|
	| bridge to Pub/Sub
	v
[Cloud Pub/Sub: sensor-events]
	|
	| push subscription
	v
[Cloud Run: riverpulse-api]
	|
	+---> [Firestore: readings, gauges]
	+---> [Cloud Storage: data files]
	            |
	            | OBJECT_FINALIZE
	            v
	      [Pub/Sub → processing]

[GitHub push to main]
	|
	v
[Cloud Build trigger]
	|
	v
[Build → Test → Deploy to Cloud Run]
```

---
## Files

- `01-pubsub-basics.md` - Topics, subscriptions, CLI commands
- `02-cloud-run-api.md` - Deploy Python Flask API
- `03-pubsub-push-cloudrun.md` - Wire Pub/Sub to trigger API
- `04-firestore.md` - NoSQL database for readings/gauges
- `05-cloud-storage.md` - Data files, signed URLs, lifecycle
- `06-cloud-build-cicd.md` - Automated deployments
- `07-storage-notifications.md` - Event-driven data processing
- `08-iot-mqtt.md` - MQTT broker, device registry, fleet management

---
## CLI Cheat Sheet

```bash
# Project setup
gcloud config set project YOUR_PROJECT_ID
gcloud config get-value project

# Pub/Sub
gcloud pubsub topics create TOPIC_NAME
gcloud pubsub topics publish TOPIC_NAME --message='{"key":"value"}'
gcloud pubsub subscriptions create SUB_NAME --topic=TOPIC_NAME
gcloud pubsub subscriptions pull SUB_NAME --limit=10 --auto-ack

# Cloud Run
gcloud run deploy SERVICE --source . --allow-unauthenticated --region us-central1
gcloud run services describe SERVICE --region us-central1
gcloud run services logs read SERVICE --region us-central1 --limit 50

# Cloud Storage
gcloud storage buckets create gs://BUCKET_NAME --location=us-central1
gcloud storage cp FILE gs://BUCKET/path/
gcloud storage ls gs://BUCKET/ --recursive
gcloud storage buckets update gs://BUCKET --lifecycle-file=lifecycle.json

# Firestore (limited CLI - use console or SDK)
gcloud firestore databases create --location=us-central1

# Cloud Build
gcloud builds submit --config=cloudbuild.yaml .
gcloud builds list --limit=5

# Compute Engine (for MQTT broker)
gcloud compute instances list
gcloud compute ssh INSTANCE_NAME --zone=ZONE
```

---
## Cost Notes

All of this runs within GCP free tier or costs pennies:
- Pub/Sub: First 10GB free
- Cloud Run: First 2M requests free, generous CPU-seconds
- Firestore: 1GB storage, 50k reads, 20k writes per day free
- Cloud Storage: 5GB free, egress limited
- Cloud Build: 120 build-minutes/day free
- Compute Engine: e2-micro is free-tier eligible

Delete resources when done if you're concerned, but leaving them idle costs nearly nothing.

---
## After the Labs

You can now confidently:
1. Sketch the RiverPulse architecture on a whiteboard
2. Explain why Pub/Sub decouples ingestion from processing
3. Discuss Firestore's document model and indexing tradeoffs
4. Describe data storage strategy with lifecycle management
5. Talk about CI/CD and deployment automation
6. Explain event-driven processing with storage notifications
7. Discuss IoT patterns: MQTT brokers, device registry, fleet management

This is the goal: hands-on experience backing up your architectural discussions.

---
## The RiverPulse Domain

This curriculum uses river flow monitoring as its domain. The patterns apply to any IoT/sensor system:

**Gauges** = remote sensors reporting flow data (cfs, stage height, temperature)  
**Readings** = timestamped measurements from gauges  
**Conditions** = derived states (low / runnable / optimal / high / flood)

Sample event:
```json
{
  "gaugeId": "gauge-001",
  "type": "flow_reading",
  "cfs": 850,
  "stageHeight": 4.2,
  "waterTemp": 52,
  "condition": "optimal",
  "timestamp": "2026-01-31T08:15:00Z"
}
```

The architecture handles any sensor network - swap "gauges" for cameras, weather stations, or industrial monitors and the patterns remain identical.
