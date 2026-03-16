"""
Event Correlation Engine

Scans pending raw events, groups them by gauge + time window,
and creates correlated event documents containing all modalities.
"""
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from google.cloud import firestore

from event_model import EventState, CorrelatedEvent


db = firestore.Client()

# Correlation window: events within this many seconds are considered
# part of the same real-world event
CORRELATION_WINDOW_SEC = 30.0


def correlate_pending_events() -> list:
    """
    Find all pending raw events, group by gauge + time window,
    create correlated events.
    """
    # Fetch all pending events
    query = db.collection("raw-events") \
        .where("state", "==", EventState.PENDING) \
        .order_by("timestamp")
    
    pending = []
    for doc in query.stream():
        d = doc.to_dict()
        d["_doc_id"] = doc.id
        pending.append(d)
    
    if not pending:
        print("No pending events to correlate")
        return []

    print(f"Found {len(pending)} pending events")

    # Group by gauge_id
    by_gauge = defaultdict(list)
    for event in pending:
        by_gauge[event["gauge_id"]].append(event)

    correlated_events = []

    for gauge_id, events in by_gauge.items():
        # Sort by timestamp
        events.sort(key=lambda e: e["timestamp"])
        
        # Sliding window grouping
        groups = []
        current_group = [events[0]]

        for event in events[1:]:
            t_current = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            t_first = datetime.fromisoformat(current_group[0]["timestamp"].replace("Z", "+00:00"))
            
            if (t_current - t_first).total_seconds() <= CORRELATION_WINDOW_SEC:
                current_group.append(event)
            else:
                groups.append(current_group)
                current_group = [event]
        
        groups.append(current_group)  # Don't forget the last group

        # Create correlated events from groups with multiple modalities
        for group in groups:
            modalities = list(set(e["modality"] for e in group))
            raw_ids = [e["_doc_id"] for e in group]
            timestamps = [e["timestamp"] for e in group]

            correlated = CorrelatedEvent(
                gauge_id=gauge_id,
                correlation_window_sec=CORRELATION_WINDOW_SEC,
                raw_event_ids=raw_ids,
                modalities_present=modalities,
                earliest_timestamp=min(timestamps),
                latest_timestamp=max(timestamps),
            )

            # Store correlated event
            doc_ref = db.collection("correlated-events").add(correlated.to_dict())
            correlated.event_id = doc_ref[1].id

            # Update raw events to CORRELATED state
            batch = db.batch()
            for raw_id in raw_ids:
                ref = db.collection("raw-events").document(raw_id)
                batch.update(ref, {
                    "state": EventState.CORRELATED,
                    "correlated_event_id": correlated.event_id,
                })
            batch.commit()

            print(f"  Correlated: {correlated.event_id} "
                  f"({len(raw_ids)} events, modalities: {modalities})")
            
            correlated_events.append(correlated)

    return correlated_events


if __name__ == "__main__":
    print("=== Running Correlation Engine ===")
    results = correlate_pending_events()
    print(f"\nCreated {len(results)} correlated events")