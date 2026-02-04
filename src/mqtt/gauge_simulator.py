# Create a gauge simulator script
# gauge_simulator.py
#!/usr/bin/env python3
"""
Simulates a RiverPulse remote monitoring gauge publishing telemetry and events.
In production, this runs on the device itself (ARM/embedded Linux).
"""
import paho.mqtt.client as mqtt
import json
import time
import random
from datetime import datetime, timezone

DEVICE_ID = "gauge-001"
BROKER_HOST = "localhost"
BROKER_PORT = 1883

# RiverPulse MQTT topic hierarchy
# riverpulse/{message_type}/{device_id}
TOPICS = {
    "telemetry": f"riverpulse/telemetry/{DEVICE_ID}",
    "event":     f"riverpulse/events/{DEVICE_ID}",
    "heartbeat": f"riverpulse/heartbeat/{DEVICE_ID}",
    "status":    f"riverpulse/status/{DEVICE_ID}",
}

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[{DEVICE_ID}] Connected to broker (rc={rc})")
    # Subscribe to commands FROM the cloud
    client.subscribe(f"riverpulse/commands/{DEVICE_ID}/#")
    print(f"[{DEVICE_ID}] Subscribed to command topics")

def on_message(client, userdata, msg):
    """Handle commands from the cloud (config updates, firmware, etc)."""
    print(f"[{DEVICE_ID}] Command received on {msg.topic}: {msg.payload.decode()}")

def publish_heartbeat(client):
    """Device health check - sent every 60 seconds in production."""
    payload = {
        "deviceId": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "heartbeat",
        "battery": round(random.uniform(60, 100), 1),
        "storageUsedPct": round(random.uniform(10, 80), 1),
        "cpuTemp": round(random.uniform(30, 55), 1),
        "firmware": "2.1.0",
        "uptime": random.randint(3600, 604800),
        "signalStrength": random.randint(-90, -30),
        "connectivity": random.choice(["halow", "satellite", "wifi"]),
    }
    client.publish(TOPICS["heartbeat"], json.dumps(payload), qos=1)
    print(f"  [heartbeat] battery={payload['battery']}% signal={payload['signalStrength']}dBm")

def publish_telemetry(client):
    """Environmental sensor readings - sent every 5 minutes in production."""
    payload = {
        "deviceId": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "telemetry",
        "temperature": round(random.uniform(-10, 35), 1),
        "humidity": round(random.uniform(20, 90), 1),
        "pressure": round(random.uniform(800, 1013), 1),
        "airQuality": random.randint(0, 300),
        "lightLevel": random.randint(0, 1000),
        "gasLevel": round(random.uniform(0, 5), 2),
    }
    client.publish(TOPICS["telemetry"], json.dumps(payload), qos=0)
    print(f"  [telemetry] temp={payload['temperature']}C humidity={payload['humidity']}%")

def publish_event(client):
    """Water condition change detected - sent when sensor triggers alert thresholds."""
    event_types = [
        {"type": "flow_reading", "condition": "optimal", "confidence": 0.94},
        {"type": "flow_reading", "condition": "high", "confidence": 0.88},
        {"type": "flow_reading", "condition": "flood_risk", "confidence": 0.97},
        {"type": "gauge_malfunction", "confidence": 0.82},
        {"type": "water_temperature_alert", "confidence": 0.91},
    ]
    event = random.choice(event_types)
    payload = {
        "gaugeId": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventId": f"evt-{int(time.time())}",
        "location": {"lat": 38.7816 + random.uniform(-0.01, 0.01),
                      "lon": -106.1978 + random.uniform(-0.01, 0.01)},
        "snapshotUrl": f"gs://riverpulse-data/gauge-001/{datetime.now().strftime('%Y-%m-%d')}/evt-{int(time.time())}/snapshot.jpg",
        **event,
    }
    client.publish(TOPICS["event"], json.dumps(payload), qos=1)
    print(f"  [EVENT] {payload.get('type')} condition={payload.get('condition')}")

# Connect and run
client = mqtt.Client(client_id=DEVICE_ID, protocol=mqtt.MQTTv5)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
client.loop_start()

print(f"\n=== RiverPulse Remote Gauge Simulator ({DEVICE_ID}) ===")
print("Publishing: heartbeat every 10s, telemetry every 5s, random events\n")

try:
    cycle = 0
    while True:
        cycle += 1
        publish_telemetry(client)
        if cycle % 2 == 0:
            publish_heartbeat(client)
        if random.random() < 0.3:  # 30% chance of event each cycle
            publish_event(client)
        time.sleep(5)
except KeyboardInterrupt:
    print("\nDevice simulator stopped.")
    client.disconnect()