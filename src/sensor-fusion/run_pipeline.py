"""
End-to-end sensor fusion pipeline.
Simulates events → correlates → assesses → reports.
"""
from simulate_events import simulate_normal_event, simulate_threat_event
from correlator import correlate_pending_events
from fusion_assessor import assess_correlated_event

from google.cloud import firestore
from event_model import EventState

db = firestore.Client()


def run_pipeline():
    print("=" * 70)
    print("STAGE 1: Event Ingestion")
    print("=" * 70)
    
    print("\n--- Normal Surf Event (gauge-001) ---")
    _, normal_ids = simulate_normal_event("gauge-001")

    print("\n--- Threat Event (gauge-002) ---")
    _, threat_ids = simulate_threat_event("gauge-002")

    print(f"\nIngested {len(normal_ids) + len(threat_ids)} raw events")

    print("\n" + "=" * 70)
    print("STAGE 2: Temporal Correlation")
    print("=" * 70)
    
    correlated = correlate_pending_events()
    print(f"\nCreated {len(correlated)} correlated events")

    print("\n" + "=" * 70)
    print("STAGE 3: Multi-Modal Fusion Assessment")
    print("=" * 70)
    
    results = []
    query = db.collection("correlated-events") \
        .where("state", "==", EventState.CORRELATED)
    
    for doc in query.stream():
        assessment = assess_correlated_event(doc.id)
        results.append({
            "event_id": doc.id,
            "type": assessment.get("event_type"),
            "severity": assessment.get("severity"),
            "confidence": assessment.get("confidence"),
            "summary": assessment.get("summary"),
            "alert": assessment.get("alert_priority"),
        })

    print("\n" + "=" * 70)
    print("PIPELINE RESULTS")
    print("=" * 70)
    
    for r in results:
        alert_marker = " ⚠ ALERT" if r["alert"] in ("immediate", "emergency") else ""
        print(f"\n  [{r['severity'].upper():8s}] {r['type']}{alert_marker}")
        print(f"  Confidence: {r['confidence']}")
        print(f"  {r['summary']}")

    print(f"\nTotal events processed: {len(normal_ids) + len(threat_ids)} raw → "
          f"{len(correlated)} correlated → {len(results)} assessed")


if __name__ == "__main__":
    run_pipeline()
