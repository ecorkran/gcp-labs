# Overview
This lab covers IoT development on GCP using a self-managed MQTT broker on Compute Engine bridging to Pub/Sub. The earlier platform was retired in August 2023.

**Time:** 90-120 minutes  
**Prerequisites:** 
* Labs 1-5 (recommended 1-7) completed (Pub/Sub, Cloud Run, Firestore, Cloud Storage all working)  

###### New Skills
* MQTT
* mosquitto self-managed MQTT broker

---

## Concepts (5 minutes)

**The Old Way (IoT Core - Deprecated):**
- Google-managed MQTT bridge → Pub/Sub
- Device registry, authentication, config management baked in
- Convenient but proprietary, limited MQTT features, retired Aug 2023

**The New Way (What You're Building):**
- Self-managed MQTT broker (Mosquitto, HiveMQ, or EMQX) on Compute Engine or GKE
- You own authentication (mTLS, JWT), device registry (Firestore), and config management
- More work, more control, no vendor lock-in, full MQTT 5.0 support

**Why this matters for RiverPulse:**
River gauges run in remote locations. They need MQTT for low-bandwidth telemetry, reliable delivery with QoS, and bidirectional communication (readings up, config/calibration commands down). This pattern applies to any IoT system with remote sensors.

Key concepts:
- **MQTT Broker:** Server that routes messages between publishers and subscribers (port 1883, or 8883 with TLS)
- **MQTT Topic:** Hierarchical path like `riverpulse/telemetry/gauge-001` (different from Pub/Sub topics)
- **QoS Levels:** 0 = fire-and-forget, 1 = at-least-once, 2 = exactly-once
- **Bridge:** Pattern where MQTT broker forwards messages to another system (Pub/Sub in our case)
- **Device Registry:** Metadata store tracking what devices exist, their status, firmware version, last heartbeat

Data flow after this lab:
```
[RiverPulse gauge]
      |
      | MQTT publish (telemetry, readings, heartbeat)
      v
[Mosquitto MQTT Broker on Compute Engine]
      |
      | Python bridge script subscribes to MQTT, publishes to Pub/Sub
      v
[Cloud Pub/Sub: sensor-events topic]
      |
      | (existing pipeline from Labs 1-7)
      v
[Cloud Run → Firestore → Cloud Storage]

[Cloud-to-Device commands]
      |
      | Pub/Sub → bridge script → MQTT publish to device topic
      v
[Mosquitto → Gauge receives config/calibration command]
```

---

## Step 1: Create a Compute Engine VM for MQTT Broker

Note the ease of specifying startup commands right here where we create the instance.
```bash
# Create a small VM for Mosquitto
# e2-micro is free-tier eligible and sufficient for dev/testing
# Note: if using Python 3.12+, you may need  --break-system-packages
# on the pip installs because the system packages are protected. This will
# not apply in Ubuntu 22.04.
gcloud compute instances create mqtt-broker \
  --zone=us-central1-a \
  --machine-type=e2-small \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --tags=mqtt-server \
  --metadata=startup-script='#!/bin/bash
    apt-get update
    apt-get install -y mosquitto mosquitto-clients python3-pip
    pip3 install paho-mqtt google-cloud-pubsub
    systemctl enable mosquitto
    systemctl start mosquitto'

# Create firewall rule to allow MQTT traffic (port 1883)
# In production you'd use 8883 (TLS) and restrict source IPs
gcloud compute firewall-rules create allow-mqtt \
  --direction=INGRESS \
  --priority=1000 \
  --network=default \
  --action=ALLOW \
  --rules=tcp:1883 \
  --target-tags=mqtt-server \
  --source-ranges=0.0.0.0/0
```

Wait ~60 seconds for the VM to boot and the startup script to finish.
```bash
# Verify the VM is running
gcloud compute instances describe mqtt-broker \
  --zone=us-central1-a \
  --format='value(status)'

# Get the external IP (you'll need this)
MQTT_IP=$(gcloud compute instances describe mqtt-broker \
  --zone=us-central1-a \
  --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
  
# Verify if you like:
echo "MQTT Broker IP: $MQTT_IP"
```

---

## Step 2: SSH In and Verify Mosquitto

```bash
# SSH into the VM
gcloud compute ssh mqtt-broker --zone=us-central1-a
```

Once inside the VM:
If in Cloud Console (gcloud), it's easiest to do this with multiple tabs. Use one for project level, one for SSH and the subscription, and a third (also with SSH) for publish.
```bash
# Check Mosquitto is running
systemctl status mosquitto

# Check it's listening on port 1883
# Notice: ss, the replacement for netstat, which is now deprecated.
ss -tlnp | grep 1883

# Test with a quick pub/sub (two terminals, or use & for background)
# Terminal approach - subscribe in background, then publish:
mosquitto_sub -t "test/hello" -C 1 &
sleep 1
mosquitto_pub -t "test/hello" -m "Mosquitto is alive"

# You should see "Mosquitto is alive" printed
# The -C 1 flag means "receive 1 message then exit"
```

If you see the message, Mosquitto is working. If not, check `journalctl -u mosquitto` for errors.

---

## Step 3: Configure Mosquitto for RiverPulse Topics

Still on the VM, create a configuration that defines the topic structure:
```bash
# Back up default config (on the mqtt_broker instance, from SSH)
sudo cp /etc/mosquitto/mosquitto.conf /etc/mosquitto/mosquitto.conf.bak

# Create RiverPulse-specific configuration
sudo tee /etc/mosquitto/conf.d/riverpulse.conf << 'EOF'
# RiverPulse MQTT Configuration
# Listener on default port
listener 1883

# Allow anonymous for development (NEVER in production)
allow_anonymous true

# Logging
log_type all
# Uncomment only if default mosquitto.conf does not specify (it usually does)
# log_dest file /var/log/mosquitto/mosquitto.log

# Message size limit (10MB for data metadata, not actual data files)
message_size_limit 10485760

# Persistence (survive broker restarts)
# Again uncomment only if not already in default mosquitto.conf
# persistence true
# persistence_location /var/lib/mosquitto/

# Max queued messages per client (for offline devices)
max_queued_messages 1000

# Keep alive interval
max_keepalive 120
EOF

# Restart Mosquitto with new config
sudo systemctl restart mosquitto

# Verify it restarted cleanly
sudo systemctl status mosquitto
```

---

## Step 4: Simulate RiverPulse Gauge Telemetry

Still on the VM, let's simulate what a RiverPulse gauge would publish:
```python
# Create a gauge simulator script
# gauge_simulator.py
#!/usr/bin/env python3
"""
Simulates a RiverPulse gauge publishing telemetry and readings.
In production, this runs on the gauge itself (ARM/embedded Linux).
"""
import paho.mqtt.client as mqtt
import json
import time
import random
from datetime import datetime, timezone

GAUGE_ID = "gauge-001"
BROKER_HOST = "localhost"
BROKER_PORT = 1883

# RiverPulse MQTT topic hierarchy
# riverpulse/{message_type}/{gauge_id}
TOPICS = {
    "telemetry": f"riverpulse/telemetry/{GAUGE_ID}",
    "reading":   f"riverpulse/readings/{GAUGE_ID}",
    "heartbeat": f"riverpulse/heartbeat/{GAUGE_ID}",
    "status":    f"riverpulse/status/{GAUGE_ID}",
}

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[{GAUGE_ID}] Connected to broker (rc={rc})")
    # Subscribe to commands FROM the cloud
    client.subscribe(f"riverpulse/commands/{GAUGE_ID}/#")
    print(f"[{GAUGE_ID}] Subscribed to command topics")

def on_message(client, userdata, msg):
    """Handle commands from the cloud (config updates, calibration, etc)."""
    print(f"[{GAUGE_ID}] Command received on {msg.topic}: {msg.payload.decode()}")

def publish_heartbeat(client):
    """Gauge health check - sent every 60 seconds in production."""
    payload = {
        "gaugeId": GAUGE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "heartbeat",
        "battery": round(random.uniform(60, 100), 1),
        "storageUsedPct": round(random.uniform(10, 80), 1),
        "cpuTemp": round(random.uniform(30, 55), 1),
        "firmware": "2.1.0",
        "uptime": random.randint(3600, 604800),
        "signalStrength": random.randint(-90, -30),
        "connectivity": random.choice(["cellular", "satellite", "wifi"]),
    }
    client.publish(TOPICS["heartbeat"], json.dumps(payload), qos=1)
    print(f"  [heartbeat] battery={payload['battery']}% signal={payload['signalStrength']}dBm")

def publish_telemetry(client):
    """Environmental sensor readings - sent every 5 minutes in production."""
    payload = {
        "gaugeId": GAUGE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "telemetry",
        "airTemp": round(random.uniform(-10, 35), 1),
        "humidity": round(random.uniform(20, 90), 1),
        "barometer": round(random.uniform(800, 1013), 1),
        "solarVoltage": round(random.uniform(0, 18), 1),
    }
    client.publish(TOPICS["telemetry"], json.dumps(payload), qos=0)
    print(f"  [telemetry] airTemp={payload['airTemp']}C humidity={payload['humidity']}%")

def publish_reading(client):
    """Flow reading - the core measurement."""
    conditions = ["low", "optimal", "high", "flood"]
    weights = [0.2, 0.5, 0.25, 0.05]
    condition = random.choices(conditions, weights=weights)[0]
    
    cfs_ranges = {"low": (100, 400), "optimal": (400, 1200), "high": (1200, 2500), "flood": (2500, 5000)}
    cfs_min, cfs_max = cfs_ranges[condition]
    
    payload = {
        "gaugeId": GAUGE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "flow_reading",
        "cfs": random.randint(cfs_min, cfs_max),
        "stageHeight": round(random.uniform(2.0, 8.0), 1),
        "waterTemp": round(random.uniform(40, 65), 1),
        "condition": condition,
        "location": {"lat": 38.5347 + random.uniform(-0.01, 0.01),
                      "lon": -106.0017 + random.uniform(-0.01, 0.01)},
    }
    client.publish(TOPICS["reading"], json.dumps(payload), qos=1)
    print(f"  [READING] {payload['cfs']} cfs, condition={condition}")

# Connect and run
client = mqtt.Client(client_id=GAUGE_ID, protocol=mqtt.MQTTv5)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
client.loop_start()

print(f"\n=== RiverPulse Gauge Simulator ({GAUGE_ID}) ===")
print("Publishing: heartbeat every 10s, telemetry every 5s, random readings\n")

try:
    cycle = 0
    while True:
        cycle += 1
        publish_telemetry(client)
        if cycle % 2 == 0:
            publish_heartbeat(client)
        if random.random() < 0.3:  # 30% chance of reading each cycle
            publish_reading(client)
        time.sleep(5)
except KeyboardInterrupt:
    print("\nGauge simulator stopped.")
    client.disconnect()
```

*ProTip: if in straight nano (i.e. cloud shell), use Ctrl+V / Ctrl+Y for pgdn/pgup.*

```bash
chmod +x ~/gauge_simulator.py
```

Run the simulator:
```bash
# or uv run the same file if you have that set up.
python3 ~/gauge_simulator.py

# Note: you may need additional packages:
pip3 install paho-mqtt
pip3 install google-cloud-pubsub
```

You should see telemetry, heartbeats, and occasional readings printing. Let it run for 30 seconds to generate some data, then `Ctrl+C` to stop.

---

## Step 5: Build the MQTT → Pub/Sub Bridge

This is the critical piece. In the old IoT Core world, Google handled this. Now you own it.

Still on the VM:

```python
# ~/mqtt_pubsub_bridge.py
#!/usr/bin/env python3
"""
MQTT → Pub/Sub Bridge

Subscribes to all riverpulse/# MQTT topics and forwards messages
to Google Cloud Pub/Sub. This replaces what IoT Core used to do.

In production, this runs as a systemd service on the same VM
as the MQTT broker (or a sidecar container in GKE).
"""
import paho.mqtt.client as mqtt
from google.cloud import pubsub_v1
import json
import os
import time
from datetime import datetime, timezone

# Configuration
MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_SUBSCRIBE_TOPIC = "riverpulse/#"  # Subscribe to ALL riverpulse messages

# Pub/Sub configuration
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "YOUR_PROJECT_ID")
PUBSUB_TOPIC = "sensor-events"  # Reuse the topic from Lab 1

# Initialize Pub/Sub publisher
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, PUBSUB_TOPIC)

# Stats tracking
stats = {"messages_forwarded": 0, "errors": 0, "started": datetime.now(timezone.utc).isoformat()}

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[Bridge] Connected to MQTT broker (rc={rc})")
    client.subscribe(MQTT_SUBSCRIBE_TOPIC, qos=1)
    print(f"[Bridge] Subscribed to '{MQTT_SUBSCRIBE_TOPIC}'")

def on_message(client, userdata, msg):
    """Forward every MQTT message to Pub/Sub."""
    try:
        # Parse the MQTT topic to extract metadata
        # Format: riverpulse/{type}/{gauge_id}
        topic_parts = msg.topic.split("/")
        message_type = topic_parts[1] if len(topic_parts) > 1 else "unknown"
        gauge_id = topic_parts[2] if len(topic_parts) > 2 else "unknown"

        # The MQTT payload becomes Pub/Sub message data
        data = msg.payload

        # Add MQTT metadata as Pub/Sub attributes
        # Attributes enable filtering on the Pub/Sub side without parsing the payload
        attributes = {
            "mqtt_topic": msg.topic,
            "message_type": message_type,
            "gauge_id": gauge_id,
            "bridge_timestamp": datetime.now(timezone.utc).isoformat(),
            "mqtt_qos": str(msg.qos),
        }

        # Publish to Pub/Sub
        future = publisher.publish(
            topic_path,
            data=data,
            **attributes,
        )
        message_id = future.result(timeout=5)

        stats["messages_forwarded"] += 1
        print(f"  [→ Pub/Sub] {message_type} from {gauge_id} → msgId={message_id}")

    except Exception as e:
        stats["errors"] += 1
        print(f"  [ERROR] Failed to forward message: {e}")

def on_disconnect(client, userdata, rc, properties=None, reasonCode=None):
    print(f"[Bridge] Disconnected from MQTT broker (rc={rc})")
    if rc != 0:
        print("[Bridge] Unexpected disconnect. Attempting reconnect...")

# Set up MQTT client
client = mqtt.Client(client_id="pubsub-bridge", protocol=mqtt.MQTTv5)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

# Reconnect settings
client.reconnect_delay_set(min_delay=1, max_delay=30)

print(f"\n=== MQTT → Pub/Sub Bridge ===")
print(f"MQTT: {MQTT_HOST}:{MQTT_PORT}")
print(f"Pub/Sub: projects/{PROJECT_ID}/topics/{PUBSUB_TOPIC}")
print(f"Subscribing to: {MQTT_SUBSCRIBE_TOPIC}\n")

client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

try:
    client.loop_forever()
except KeyboardInterrupt:
    print(f"\n[Bridge] Shutting down. Stats: {json.dumps(stats, indent=2)}")
    client.disconnect()
```

```sh
chmod +x ~/mqtt_pubsub_bridge.py
```

Before running, set your project ID:
```bash
# Set the project ID (the bridge needs it for Pub/Sub)
export GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project)

# Run the bridge in the background. BRIDGE_PID will hold the ID of the
# last backgrounded process (the one we are running here).
python3 ~/mqtt_pubsub_bridge.py &
BRIDGE_PID=$!
echo "Bridge running as PID $BRIDGE_PID"
```

---

## Step 6: Test the Full Pipeline

Note on setup: this is again easiest to do with three terminal tabs: 
1. VM/System tab: list subscriptions, pull pub/sub events, etc
2. Event bridge (SSH)
3. Gauge simulator (SSH)

Now run the gauge simulator and watch messages flow through:
```bash
# In the same SSH session (bridge is in background)
python3 ~/gauge_simulator.py
```

You should see two interleaved outputs:
1. The simulator printing what it publishes
2. The bridge printing `[→ Pub/Sub]` as it forwards each message

Let it run for ~20 seconds, then `Ctrl+C` the simulator.

**Back in Cloud Shell** (exit the SSH session or open a new terminal):
```bash
# Check messages arrived in Pub/Sub
# Use the pull subscription from Lab 1 (or create a debug one)
gcloud pubsub subscriptions pull event-processor-sub --limit=10 --auto-ack
```

You should see your gauge telemetry, heartbeats, and readings with the `mqtt_topic`, `gauge_id`, and `message_type` attributes attached.

If you still have the Cloud Run pipeline from Labs 3-7, these messages will also flow through to Firestore automatically. If you don't see them, proceed through the steps below.

First, check subscription. If you can see this, you're probably good.
```sh
# Check whether push subscription is still pointing at Cloud Run service:
gcloud pubsub subscriptions list
```

If you don't see subscription, you need to do some checking to see if this is still running, and update it if not. If you disabled public access you'll get a `403`. You can re-enable here.
```sh
# Verify whether Cloud Run is still deployed:
gcloud run services list --region us-central1

# If it's there, hit the health endpoint make sure it responds:
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')
curl $SERVICE_URL

# Re-enable public access
gcloud run services add-iam-policy-binding riverpulse-api \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"

# Re-test:
curl $SERVICE_URL
```

---

## Step 7: Gauge Registry in Firestore

The gauge registry is how you track what gauges exist, whether they're online, and their configuration. IoT Core had this built in. Now we build it ourselves.

**Back in Cloud Shell:**
```bash
cd ~/riverpulse-api
```

Add this to your `main.py` (or create a new file if you prefer). Remember, at this point, we're just pushing everything to the same pub/sub endpoint, no special routing for heartbeats or other, no separate endpoints for different topics and subscriptions. A real app would certainly have this processing. Here we're just adding REST endpoints.
```python
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
```

Redeploy, or just push if you have the CI/CD pipeline from previous labs still set up.
```bash
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1 \
  --memory 512Mi
```

---

## Step 8: Register Gauges and Test Fleet Management

```bash
SERVICE_URL=$(gcloud run services describe riverpulse-api --region us-central1 --format 'value(status.url)')

# Register three RiverPulse gauges
curl -X POST "$SERVICE_URL/gauges/register" \
  -H "Content-Type: application/json" \
  -d '{
    "gaugeId": "gauge-004",
    "name": "Arkansas at Salida",
    "riverName": "Arkansas River",
    "location": {"lat": 38.5347, "lon": -106.0017},
    "firmware": "2.1.0",
    "connectivity": "cellular"
  }'

curl -X POST "$SERVICE_URL/gauges/register" \
  -H "Content-Type: application/json" \
  -d '{
    "gaugeId": "gauge-005",
    "name": "Clear Creek at Golden",
    "riverName": "Clear Creek",
    "location": {"lat": 39.7555, "lon": -105.2211},
    "firmware": "2.1.0",
    "connectivity": "wifi"
  }'

curl -X POST "$SERVICE_URL/gauges/register" \
  -H "Content-Type: application/json" \
  -d '{
    "gaugeId": "gauge-006",
    "name": "Poudre at Filter Plant",
    "riverName": "Cache la Poudre",
    "location": {"lat": 40.6652, "lon": -105.2211},
    "firmware": "2.0.8",
    "connectivity": "satellite"
  }'

echo ""
echo "=== Fleet Status ==="
curl "$SERVICE_URL/gauges/fleet" | python3 -m json.tool
```

---

## Step 9: Simulate Heartbeats and Watch Gauge Status Change

```bash
# Simulate heartbeat from gauge-004
curl -X POST "$SERVICE_URL/gauges/gauge-004/heartbeat" \
  -H "Content-Type: application/json" \
  -d '{
    "battery": 87.3,
    "firmware": "2.1.0",
    "cpuTemp": 42.1,
    "storageUsedPct": 34.2,
    "signalStrength": -67,
    "connectivity": "cellular",
    "uptime": 259200
  }'

# Simulate heartbeat from gauge-005 (low battery!)
curl -X POST "$SERVICE_URL/gauges/gauge-005/heartbeat" \
  -H "Content-Type: application/json" \
  -d '{
    "battery": 12.1,
    "firmware": "2.1.0",
    "cpuTemp": 51.8,
    "storageUsedPct": 78.9,
    "signalStrength": -85,
    "connectivity": "satellite",
    "uptime": 86400
  }'

echo ""
echo "=== Fleet Status After Heartbeats ==="
curl "$SERVICE_URL/gauges/fleet" | python3 -m json.tool
```

Notice gauge-004 and gauge-005 are now `"online"` with battery/signal data, while gauge-006 remains `"registered"` (no heartbeat received).

---

## Step 10: Send a Command to a Gauge

```bash
# Send calibration command to gauge-006
curl -X POST "$SERVICE_URL/gauges/gauge-006/command" \
  -H "Content-Type: application/json" \
  -d '{
    "command": "calibrate",
    "payload": {
      "zeroPoint": 0.0,
      "calibrationFactor": 1.02
    }
  }'

# Send "capture snapshot" command to gauge-004
curl -X POST "$SERVICE_URL/gauges/gauge-004/command" \
  -H "Content-Type: application/json" \
  -d '{
    "command": "capture_snapshot",
    "payload": {
      "resolution": "high",
      "includeTimestamp": true
    }
  }'
  
echo ""
echo "=== Fleet Status After Commands ==="
curl "$SERVICE_URL/gauges/fleet" | python3 -m json.tool
```

---

## Step 11: Check Everything in the Console

Open Cloud Console and explore:

1. **Firestore** → `gauges` collection: See your three registered gauges with heartbeat data
2. **Firestore** → `commands` collection: See queued commands
3. **Pub/Sub** → `sensor-events` topic: See messages flowing from the MQTT bridge
4. **Compute Engine** → `mqtt-broker` VM: See it running

---

## MQTT Topic Design (Interview Discussion Point)

```
riverpulse/
├── telemetry/{gauge_id}     # Environmental sensor readings (QoS 0)
├── readings/{gauge_id}      # Flow measurements (QoS 1)
├── heartbeat/{gauge_id}     # Gauge health (QoS 1)
├── status/{gauge_id}        # Online/offline/error (QoS 1, retained)
├── commands/{gauge_id}/     # Cloud → gauge commands
│   ├── config               #   Configuration updates
│   ├── calibrate            #   Calibration commands
│   └── snapshot             #   On-demand snapshot triggers
└── alerts/{gauge_id}        # High water alerts (QoS 2)
```

**Design decisions to discuss:**
- **QoS 0 for telemetry:** High-frequency, losing one reading is fine
- **QoS 1 for readings:** Must be delivered at least once (flow data can't be lost)
- **Retained messages on status:** New subscribers immediately get latest gauge state
- **Hierarchical topics:** Enable wildcard subscriptions (`riverpulse/readings/#` gets all readings from all gauges)

---

## Production Considerations (What to Discuss in Interview)

**Authentication (not implemented in this lab, but know the patterns):**
- Mutual TLS (mTLS): Gauge has X.509 certificate, broker validates it
- JWT tokens: Gauge presents a signed token, broker validates against public key
- Store credentials in Secret Manager, rotate every 90 days
- Each gauge gets unique credentials (never share across gauges)

**High Availability:**
- Mosquitto cluster: 3 instances behind TCP Network Load Balancer
- If primary fails, standby takes over (health check on port 1883)
- In production, consider HiveMQ or EMQX for built-in clustering
- Mosquitto is great for < 10k devices; clustered brokers for 10k+

**Low-Connectivity Handling (remote gauge challenge):**
- MQTT QoS 1/2 with persistent sessions: broker queues messages while gauge is offline
- Gauge-side buffer: store readings on local storage when connectivity is lost
- Priority-based sync: HIGH readings (flood alerts) transmit immediately, routine telemetry batches for next window
- Delta sync: send only changed sensor values, not full snapshots
- Compression: protobuf or CBOR instead of JSON to reduce bandwidth

**Scaling Path:**
- 50 gauges: Single Mosquitto on e2-small (this lab)
- 500 gauges: Single Mosquitto on e2-standard-2, dedicated Pub/Sub bridge
- 5,000 gauges: HiveMQ or EMQX cluster on GKE, multiple bridge instances
- 50,000+ gauges: Managed MQTT service (HiveMQ Cloud, EMQX Cloud on GCP)

---

## Architecture Summary

```
                    ┌─────────────────────────────────────────┐
                    │          GCP Backend                    │
                    │                                         │
[Gauge gauge-001]───┐ │  ┌──────────────┐  ┌─────────────┐   │
                  │ │  │  Mosquitto   │  │  Pub/Sub    │   │
[Gauge gauge-002]──┼─┼─→│  MQTT Broker │─→│  sensor-    │─┼──→ Cloud Run API
                  │ │  │  (Compute    │  │  events     │   │     │
[Gauge gauge-003]──┘ │  │   Engine)    │  │             │   │     ├→ Firestore
                    │  └──────┬───────┘  └─────────────┘   │     ├→ Cloud Storage
                    │         │                             │     └→ BigQuery
                    │         │ ◄── Commands ──┐            │
                    │         │                │            │
                    │  ┌──────┴───────┐  ┌─────┴───────┐   │
                    │  │ Bridge       │  │ Cloud Run   │   │
                    │  │ (Python)     │  │ (commands   │   │
                    │  │              │  │  endpoint)  │   │
                    │  └──────────────┘  └─────────────┘   │
                    └─────────────────────────────────────────┘
```

---

## Discussion Points for Interviews

- "Google retired IoT Core in August 2023, so the modern pattern is a self-managed MQTT broker on Compute Engine bridging to Pub/Sub. I've built this exact pattern - Mosquitto handles device connections, a Python bridge forwards to Pub/Sub, and our existing event pipeline picks it up from there."

- "For RiverPulse's scale (50-1000 gauges), Mosquitto on a single e2-standard-2 instance is more than sufficient. If the network grows beyond 10,000, we'd migrate to HiveMQ or EMQX with built-in clustering on GKE."

- "The topic hierarchy matters. I use `riverpulse/{type}/{gauge_id}` which lets us subscribe to `riverpulse/readings/#` for all readings across the network, or `riverpulse/+/gauge-001` for everything from one gauge. QoS 0 for telemetry, QoS 1 for readings that can't be lost."

- "Gauge registry in Firestore gives us real-time fleet visibility. Heartbeats update gauge status, and the portal can query network health instantly."

- "For low-connectivity environments, the MQTT persistent session is key. The broker queues messages while the gauge is offline. On the gauge side, we buffer to local storage and priority-sync: flood alerts go immediately, routine telemetry batches for the next connectivity window."

---

## Cleanup

```bash
# Delete the VM (stops billing)
gcloud compute instances delete mqtt-broker --zone=us-central1-a --quiet

# Delete the firewall rule
gcloud compute firewall-rules delete allow-mqtt --quiet

# Keep the Pub/Sub topics and Firestore data - they're used by other labs
```

If you re-enabled public access, disable it while you aren't working on this:
```sh
gcloud run services remove-iam-policy-binding riverpulse-api \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"
```

---

## Connecting the Dots: Labs 1-8

| Lab | What it covers | RiverPulse component |
|-----|---------------|----------------------|
| 01 | Pub/Sub | Message backbone |
| 02 | Cloud Run API | Backend service |
| 03 | Pub/Sub → Cloud Run | Event-driven processing |
| 04 | Firestore | Gauge registry, readings, exports |
| 05 | Cloud Storage | Data files, signed URLs |
| 06 | Cloud Build | CI/CD pipeline |
| 07 | Storage Notifications | Automatic data processing |
| **08** | **IoT + MQTT** | **Gauge connectivity, fleet management** |

With this lab, you've touched every layer of the RiverPulse architecture from gauge to dashboard. The remaining pieces (Vertex AI for flow prediction, BigQuery for analytics) build on top of this foundation but don't change the core patterns.