# Overview
**Time:** 30-45 minutes

**Why it matters:** Pub/Sub is the central nervous system of RiverPulse. Gauges publish readings, multiple services consume them. This decoupling is what lets the system scale.

---
## Concepts (5 minutes)

- **Topic:** A named channel where publishers send messages
- **Subscription:** A named attachment to a topic that receives copies of messages
- **Pull:** Your code asks for messages when ready
- **Push:** Pub/Sub POSTs messages to your endpoint (we'll do this in Lab 3)

One topic can have multiple subscriptions. Each subscription gets its own copy of every message. This is how one reading can trigger both "save to database" and "check for alerts" independently.

---
## Setup

```bash
# Set your project (replace with your project ID)
gcloud config set project YOUR_PROJECT_ID

# Verify you're in the right project
gcloud config get-value project

# Enable Pub/Sub API (may already be enabled)
gcloud services enable pubsub.googleapis.com
```

---
## Step 1: Create a Topic

```bash
# Create topic for sensor events
gcloud pubsub topics create sensor-events

# Verify it exists
gcloud pubsub topics list
```

You should see `projects/YOUR_PROJECT_ID/topics/sensor-events` in the output.

---
## Step 2: Create Subscriptions

We'll create two subscriptions to demonstrate fan-out (one message → multiple consumers).
```bash
# Subscription for event processing
gcloud pubsub subscriptions create event-processor-sub \
--topic=sensor-events \
--ack-deadline=60

# Subscription for logging/archival
gcloud pubsub subscriptions create event-archive-sub \
--topic=sensor-events \
--ack-deadline=60

# Verify
gcloud pubsub subscriptions list
```

The `--ack-deadline=60` gives your consumer 60 seconds to acknowledge a message before Pub/Sub redelivers it.

---
## Step 3: Publish Messages

```bash
# Publish a simple flow reading
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":850,"timestamp":"2026-01-31T08:00:00Z"}'

# Publish another with more data
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-002","type":"flow_reading","cfs":1240,"stageHeight":5.2,"condition":"high","timestamp":"2026-01-31T08:01:00Z"}'

# Publish with attributes (metadata outside the message body)
gcloud pubsub topics publish sensor-events \
--message='{"gaugeId":"gauge-001","type":"flow_reading","cfs":720,"condition":"optimal"}' \
--attribute=priority=low,region=colorado
```

---
## Step 4: Pull Messages

```bash
# Pull from the processor subscription (get up to 10 messages)
gcloud pubsub subscriptions pull event-processor-sub --limit=10 --auto-ack

# Pull from archive subscription - same messages, independent consumption
gcloud pubsub subscriptions pull event-archive-sub --limit=10 --auto-ack
```

The `--auto-ack` flag automatically acknowledges messages (marks them as processed). Without it, messages would be redelivered.

**Notice:** Both subscriptions received the same messages. This is the fan-out pattern.

---
## Step 5: Understand Message Retention

```bash
# Check subscription details
gcloud pubsub subscriptions describe event-processor-sub
```

Default retention is 7 days for unacknowledged messages. For RiverPulse, this means if your processing service goes down for maintenance, messages queue up and wait.

---
## Step 6: Dead Letter Topics (Production Pattern)

When message processing repeatedly fails, you don't want infinite retries. Dead letter topics catch poison messages.

```bash
# Create a dead letter topic
gcloud pubsub topics create sensor-events-dlq

# Create subscription for monitoring dead letters
gcloud pubsub subscriptions create dlq-monitor-sub --topic=sensor-events-dlq

# Update processor subscription to use dead letter topic
gcloud pubsub subscriptions update event-processor-sub \
--dead-letter-topic=sensor-events-dlq \
--max-delivery-attempts=5
```

Now if a message fails processing 5 times, it moves to the DLQ instead of blocking the queue.

---
## Cleanup (Optional)

Only run this if you want to start fresh:

```bash
gcloud pubsub subscriptions delete event-processor-sub
gcloud pubsub subscriptions delete event-archive-sub
gcloud pubsub subscriptions delete dlq-monitor-sub
gcloud pubsub topics delete sensor-events
gcloud pubsub topics delete sensor-events-dlq
```

---
## Discussion Points for Interviews

- "Readings flow through Pub/Sub, which decouples ingestion from processing. If the processing function is slow or down, messages queue automatically."

- "Multiple subscriptions let us fan out - same reading triggers both storage and alerting pipelines independently."

- "Dead letter queues catch poison messages so one malformed event doesn't block the whole system."

---
## Learning Summary

```sh
# Structured system:
# topic:
#   subscriptions are created on topics
#   events are published to topic
#   subscriptions receive the events (pull) and they are queued as needed
#   can set maxDeliveryAttempts (dead-letter-topic) to prevent infinite retries
#
#   default time to ack is 10 seconds but you can modify
#   you can see and modify most things, use --help to view
```


---
## Next Lab

Lab 2: Cloud Run - deploy an API service that could consume these events.