# GCP Labs Series — Complete Index (Labs 01–17)

# Overview

These labs build a functional RiverPulse system — a multi-modal river monitoring platform on GCP. The patterns apply directly to any multi-sensor IoT product: coastal monitoring, wildlife surveillance, industrial telemetry, smart infrastructure. Work through them in order; each builds on the previous.

---

## Lab Overview

| Lab | Topic | Time | Key Skills |
|-----|-------|------|------------|
| 01 | Pub/Sub Basics | 30–45 min | Message decoupling, fan-out, dead letter queues |
| 02 | Cloud Run API | 45–60 min | Serverless containers, auto-scaling |
| 03 | Pub/Sub → Cloud Run | 45–60 min | Push subscriptions, acknowledgment, retry |
| 04 | Firestore | 60–90 min | NoSQL document model, composite indexes |
| 05 | Cloud Storage | 45–60 min | Data files, signed URLs, lifecycle rules |
| 06 | Cloud Build CI/CD | 45–60 min | git → build → test → deploy |
| 07 | Storage Notifications | 45–60 min | Event-driven processing, OBJECT_FINALIZE |
| 08 | IoT + MQTT | 90–120 min | Self-managed broker, device registry, fleet mgmt |
| 09 | Secret Manager | 30–45 min | Secret versioning, IAM-scoped access |
| 10 | Monitoring & Alerting | 45–60 min | Dashboards, structured logging, alert policies |
| 11 | BigQuery Analytics | 60–90 min | OLAP warehouse, streaming inserts, window functions |
| 12 | Cloud Functions | 45–60 min | Pub/Sub-triggered functions, Eventarc |
| 13 | Vision API | 60–90 min | Image classification, label detection, safe search |
| 14 | Gemini Multimodal | 60–90 min | google-genai SDK, image+text reasoning, JSON output |
| 15 | Audio Classification | 60–90 min | Gemini native audio, Cloud Function auto-trigger |
| 16 | Sensor Fusion | 75–105 min | Multi-modal correlation, fusion prompting, event state machine |

**Total time: ~15–20 hours** (working through carefully with exploration)

---

## Recommended Sessions

**Session 1 (2–3 hrs):** Labs 1–3 — Message pipeline  
**Session 2 (2–3 hrs):** Labs 4–6 — Persistence + CI/CD  
**Session 3 (2–3 hrs):** Labs 7–8 — Events + IoT  
**Session 4 (2–3 hrs):** Labs 9–12 — Operations + analytics + functions  
**Session 5 (3–4 hrs):** Labs 13–15 — AI: vision, multimodal, audio  
**Session 6 (2–3 hrs):** Lab 16 — Sensor fusion  

---

## What You'll Have Built

```
DATA PIPELINE (Labs 1–8)
[Gauge / Camera / Microphone]
  ├── MQTT → broker → Pub/Sub: sensor-events
  ├── Photos → Cloud Storage: images/
  └── Audio → Cloud Storage: audio/

[Pub/Sub: sensor-events]
  ├── push → Cloud Run API → Firestore + BigQuery
  └── Eventarc → Cloud Function: flood-evaluator → alerts

AI PIPELINE (Labs 13–16)
[images/]  → Vision API → image-classifications (fast first pass)
[images/]  → Gemini multimodal → ai-assessments (deep reasoning)
[audio/]   → Gemini audio → audio-events (acoustic classification)
[all modalities] → Correlation engine → Gemini fusion → assessed-events

OPERATIONS (Labs 6, 9–12)
Cloud Build → CI/CD → Cloud Run deploy
Cloud Monitoring → dashboards + alerts
Secret Manager → credential management
BigQuery → analytics warehouse
```

---

## The Pattern Generalizes

RiverPulse uses river gauges as its domain, but every component maps to any multi-sensor IoT product:

| RiverPulse Component | Surf Monitoring Equivalent | General Pattern |
|---|---|---|
| River gauge | Buoy / beach station | Remote sensor node |
| Flow readings (cfs, stage) | Wave height, period, tide | Numeric telemetry |
| Gauge camera | Break camera | Visual sensor |
| Ambient microphone | Hydrophone / surface mic | Acoustic sensor |
| Environmental sensors | Wind, air temp, humidity | Environmental telemetry |
| Vision API classification | Surfer/vessel/crowd detection | Fast first-pass CNN |
| Gemini multimodal assessment | Conditions + hazard reasoning | Deep multimodal LLM |
| Audio classification | Wave impact / engine / crowd | Acoustic event detection |
| Sensor fusion pipeline | Cross-modal event correlation | Multi-signal aggregation |
| Firestore event storage | Event index + real-time queries | Operational document store |
| BigQuery analytics | Historical trends + reporting | Analytics warehouse |

---

## After the Labs

You can:
1. Sketch the complete architecture on a whiteboard and explain every arrow
2. Discuss tradeoffs at each layer (Firestore vs BigQuery, Vision API vs Gemini, Cloud Run vs Cloud Functions)
3. Explain the two-tier AI approach (fast/cheap first pass → deep multimodal reasoning)
4. Describe sensor fusion and why it reduces false positives
5. Walk through the event lifecycle from sensor to alert
6. Discuss cost analysis at scale (100s–1000s of devices)
