# gcp-labs

**Learn GCP by building a real-time river monitoring system.**

Not toy examples - a system that can solve a real problem for a real community.

---

## The Problem

Whitewater enthusiasts - kayakers, riverboarders, rafters - need to know river conditions before driving to the put-in. USGS provides gauge data, but it's raw numbers: cubic feet per second, stage height, maybe temperature.

What people actually want to know is more nuanced. *Is it runnable? Is it optimal for me?*

"Optimal" is personal. One paddler wants low and technical. Another wants it full-on raging. It depends on the discipline, the skill level, the specific run, the acceptable risk. A flow that's perfect for an experienced kayaker might be dangerous for a beginner. A riverboarder reads the same water differently than a rafter.  Everyone makes their own call. The data should help them make it well.

## What We're Building

A cloud-native system that ingests gauge readings, processes events, stores historical data, and classifies conditions. These labs build the pipeline piece by piece using simulated gauges.

This is a demo - a foundation for something that could become real. The existing options for checking river conditions work, but there's room for better integration and visualization. I started sketching a mobile app last summer, but the AI tooling wasn't where I needed it to be to build what I envisioned. That gap is part of why I started building my own development tools.

For now, this is a hands-on way to learn GCP by building something that matters. The architecture is real. The domain is real. The gauges are simulated - for now.

This is a demo - a foundation for something that could become real. The existing options for checking river conditions are limited, and can be difficult to navigate.  There is ample
opportunity for improvement.  Maybe this becomes that. For now, it's a hands-on way to learn GCP by building something that matters.
```
[Simulated gauge publishes reading]
    │
    ▼
[MQTT Broker (Compute Engine)]
    │
    │ bridge to Pub/Sub
    ▼
[Cloud Pub/Sub: sensor-events]
    │
    │ push subscription
    ▼
[Cloud Run: riverpulse-api]
    │
    ├──▶ [Firestore: readings, gauges]
    ├──▶ [Cloud Storage: data files]
    │           │
    │           │ OBJECT_FINALIZE
    │           ▼
    │     [Pub/Sub → processing]
    │
[GitHub push to main]
    │
    ▼
[Cloud Build trigger]
    │
    ▼
[Build → Test → Deploy to Cloud Run]
```

---

## Labs

| Lab | Topic | Time | What You'll Discuss in Interviews |
|-----|-------|------|-----------------------------------|
| 01 | [Pub/Sub Basics](labs/01-pubsub-basics.md) | 30-45 min | Message decoupling, fan-out, dead letter queues |
| 02 | [Cloud Run API](labs/02-cloud-run-api.md) | 45-60 min | Serverless containers, auto-scaling, cold starts |
| 03 | [Pub/Sub → Cloud Run](labs/03-pubsub-push-cloudrun.md) | 45-60 min | Push subscriptions, acknowledgment, retry behavior |
| 04 | [Firestore](labs/04-firestore.md) | 60-90 min | NoSQL document model, real-time listeners, composite indexes |
| 05 | [Cloud Storage](labs/05-cloud-storage.md) | 45-60 min | Object storage, signed URLs, lifecycle policies |
| 06 | [Cloud Build CI/CD](labs/06-cloud-build-cicd.md) | 45-60 min | Automated deployment, build triggers, test integration |
| 07 | [Storage Notifications](labs/07-storage-notifications.md) | 60-75 min | Event-driven processing, OBJECT_FINALIZE triggers |
| 08 | [IoT + MQTT](labs/08-gcp-iot.md) | 90-120 min | Self-managed MQTT broker, device patterns, fleet management |

**Total: ~8-10 hours** working through carefully with exploration.

---

## Recommended Approach

**Session 1 (2-3 hours):** Labs 1-3  
Get the message pipeline working. Gauge readings flow through Pub/Sub to your API.

**Session 2 (2-3 hours):** Labs 4-6  
Add persistence (Firestore, Cloud Storage) and CI/CD.

**Session 3 (2-3 hours):** Labs 7-8  
Event-driven storage processing and IoT/MQTT device connectivity.

**Session 4 (optional):**  
Explore the console, run queries, practice explaining the architecture out loud, break something and fix it.

---

## How to Use This Repo

**Build it yourself, then compare.**

The labs guide you through creating everything from scratch - you'll make your own project directory, write the code, run the commands. This is intentional. Typing beats copying; debugging your own mistakes teaches more than running working code.

The `src/` directory contains reference implementations - what your code should look like when each lab is complete. Use it when you're stuck or want to compare your approach.
```
src/
├── api/                    # Cloud Run Flask API (Labs 2-7)
│   ├── main.py
│   └── ProcFile
├── mqtt/                   # MQTT broker & bridge (Lab 8)
│   ├── gauge_simulator.py
│   ├── mqtt_pubsub_bridge.py
│   └── mosquitto-riverpulse.conf
├── Dockerfile              # Container image config
├── requirements.txt        # Python dependencies
└── cloudbuild.yaml         # CI/CD pipeline config (Lab 6)
```

**Suggested workflow:**
1. Follow the lab instructions, building in your own directory
2. Get stuck? Check the reference implementation
3. Finished? Compare your code to `src/` - differences aren't necessarily wrong, just different approaches
4. Move on when you can explain *why* the code works, not just *that* it works

---

## The Domain

River flow monitoring is the example, but the patterns apply to any IoT/sensor system:

- **Gauges** = remote sensors reporting flow data (cfs, stage height, temperature)
- **Readings** = timestamped measurements from gauges
- **Conditions** = derived states (low / runnable / optimal / high / flood)

Sample event:
```json
{
  "gaugeId": "gauge-nantahala-001",
  "type": "flow_reading",
  "cfs": 850,
  "stageHeight": 4.2,
  "waterTemp": 52,
  "condition": "optimal",
  "timestamp": "2026-02-01T08:15:00Z"
}
```

Swap "gauges" for wildlife cameras, weather stations, or industrial monitors and the architecture is identical.

---

## Why This Exists

I needed to learn GCP quickly. Generic tutorials with disconnected toy examples don't stick. So I built something I actually care about.

I've been in whitewater for years - riverboarding, mostly, which means you're in the water, not on it. I check these gauges. I understand why the data matters and why "optimal" is personal. Building around a domain I know made the learning stick and makes the architecture easier to explain.

![Riverboarding the Horns of God, Nantahala Cascades](assets/nantahala-cascades.jpg)
*Riverboarding "Horns of God" on the Nantahala Cascades. Photo by Paul Parsons.*

---

## After the Labs

You'll be able to:

1. Sketch this architecture on a whiteboard and explain every component
2. Discuss why Pub/Sub decouples ingestion from processing
3. Explain Firestore's document model and when to use composite indexes
4. Describe object storage patterns with lifecycle management
5. Talk through CI/CD pipelines and deployment automation
6. Explain event-driven processing with storage notifications
7. Discuss IoT patterns: MQTT brokers, device simulation, fleet management

This is the goal: hands-on experience backing up your architectural discussions.

---

## Cost

All of this runs within GCP free tier or costs pennies:

- Pub/Sub: First 10GB free
- Cloud Run: 2M requests free, generous CPU-seconds
- Firestore: 1GB storage, 50k reads, 20k writes/day free
- Cloud Storage: 5GB free
- Cloud Build: 120 build-minutes/day free
- Compute Engine: e2-micro is free-tier eligible

Delete resources when done if concerned, but idle resources cost nearly nothing.

---

## Structure
```
gcp-labs/
├── labs/           # The 8 lab guides
├── src/
│   ├── api/        # Cloud Run Flask API
│   ├── mqtt/       # MQTT broker config and bridge
│   └── cloudbuild.yaml
└── README.md
```

---

## License

MIT. Use it, learn from it, adapt it.

---

## About

Built by [Erik Corkran](https://www.linkedin.com/in/erikcorkran/) - software engineer, riverboarder, someone who learns by building.