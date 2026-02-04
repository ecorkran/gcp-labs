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
        # Format: riverpulse/{type}/{device_id}
        topic_parts = msg.topic.split("/")
        message_type = topic_parts[1] if len(topic_parts) > 1 else "unknown"
        device_id = topic_parts[2] if len(topic_parts) > 2 else "unknown"

        # The MQTT payload becomes Pub/Sub message data
        data = msg.payload

        # Add MQTT metadata as Pub/Sub attributes
        # Attributes enable filtering on the Pub/Sub side without parsing the payload
        attributes = {
            "mqtt_topic": msg.topic,
            "message_type": message_type,
            "device_id": device_id,
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
        print(f"  [→ Pub/Sub] {message_type} from {device_id} → msgId={message_id}")

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