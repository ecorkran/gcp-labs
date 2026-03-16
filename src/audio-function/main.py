"""
Cloud Function: Auto-classify audio uploads.

Triggered by OBJECT_FINALIZE on Cloud Storage.
Classifies the audio using Gemini via audio_classifier module,
stores result in Firestore.
"""
import functions_framework
from audio_classifier import classify_audio, store_audio_event

AUDIO_EXTENSIONS = {"wav", "mp3", "flac", "ogg", "m4a"}


@functions_framework.cloud_event
def process_audio(cloud_event):
    """Triggered by Cloud Storage OBJECT_FINALIZE."""
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    if not file_name.startswith("audio/"):
        print(f"Skipping non-audio path: {file_name}")
        return

    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in AUDIO_EXTENSIONS:
        print(f"Skipping non-audio file: {file_name}")
        return

    audio_uri = f"gs://{bucket_name}/{file_name}"
    base_name = file_name.split("/")[-1]
    gauge_id = "-".join(base_name.split("-")[:2]) if "-" in base_name else "unknown"

    print(f"Processing audio: {audio_uri}")

    try:
        classification = classify_audio(audio_uri, gauge_id=gauge_id)
        doc_id = store_audio_event(gauge_id, audio_uri, classification)
        print(f"Stored classification: {doc_id}")

        if classification.get("alert_recommended") or classification.get("threat_detected"):
            print(f"ALERT for {gauge_id}: {classification.get('alert_reason') or classification.get('summary', 'threat detected')}")
            # In production: publish to Pub/Sub alerts topic
            # publisher.publish(alerts_topic, json.dumps({...}).encode())

    except Exception as e:
        print(f"Classification error: {e}")
