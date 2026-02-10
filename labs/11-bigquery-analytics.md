# Overview

**Time:** 60-90 minutes  
**Prerequisites:** Labs 1-4 completed (Pub/Sub, Cloud Run, Firestore with readings data)  

###### New Skills
* BigQuery dataset and table creation
* Loading data from Firestore exports and Cloud Storage
* Analytical SQL queries (aggregations, window functions)
* Partitioned and clustered tables
* Streaming inserts

---

## Concepts (5 minutes)

- **BigQuery:** Serverless, petabyte-scale data warehouse. You write SQL, Google handles the infrastructure.
- **Dataset:** A container for tables (like a schema in PostgreSQL).
- **Partitioned Table:** Table split by a column (usually timestamp). Queries that filter on the partition column scan less data = cheaper and faster.
- **Clustered Table:** Within each partition, rows are sorted by specified columns. Further reduces scan cost for filtered queries.
- **Streaming Insert:** Push rows in real-time (arrives in seconds). Versus batch load (from files, cheaper but takes minutes).

This is the OLTP→OLAP boundary from DDIA. Firestore handles transactional reads and writes — the portal querying "show me the latest reading for gauge-001." BigQuery handles analytical queries — "what was the average daily flow for every gauge in Colorado over the last 6 months, and which days exceeded the 95th percentile?"

You don't replace one with the other. You use both, with data flowing from Firestore (operational) to BigQuery (analytical).

AWS equivalents: Redshift (closest), or Athena (serverless query over S3). BigQuery is closer to Athena in that it's serverless and you pay per query, but it has its own storage rather than querying files in place.

---

## Setup

```bash
# Enable BigQuery API.  The BigQuery API is likely to be enabled already, but you need the resource
# manager as well.  If it is missing, bq commands will hang with no output and no error message.
gcloud services enable bigquery.googleapis.com
gcloud services enable cloudresourcemanager.googleapis.com

# Verify
bq version
```

The `bq` command-line tool is pre-installed in Cloud Shell. It's the primary CLI for BigQuery.

---

## Step 1: Create a Dataset

```bash
PROJECT_ID=$(gcloud config get-value project)

# Create dataset for RiverPulse analytics
bq mk \
  --dataset \
  --location=US \
  --description="RiverPulse river monitoring analytics" \
  ${PROJECT_ID}:riverpulse

# Verify
bq ls
```

You should see `riverpulse` listed. Datasets are regional — we use `US` (multi-region) which is the default and cheapest for analysis. For production with data residency requirements, you'd pick a specific region.

---

## Step 2: Create Tables with Schema

We'll create two tables: one for flow readings (the bulk of data) and one for gauge metadata.

```bash
# Create readings table — partitioned by day, clustered by gauge
bq mk \
  --table \
  --time_partitioning_field=timestamp \
  --time_partitioning_type=DAY \
  --clustering_fields=gauge_id,condition \
  --description="Flow readings from all gauges" \
  ${PROJECT_ID}:riverpulse.readings \
  gauge_id:STRING,timestamp:TIMESTAMP,cfs:FLOAT,stage_height:FLOAT,water_temp:FLOAT,condition:STRING,source:STRING,received_at:TIMESTAMP

# Create gauges reference table
bq mk \
  --table \
  --description="Gauge metadata and locations" \
  ${PROJECT_ID}:riverpulse.gauges \
  gauge_id:STRING,name:STRING,river:STRING,lat:FLOAT,lon:FLOAT,river_mile:FLOAT,installed_at:TIMESTAMP,status:STRING

# Verify tables
bq ls riverpulse
bq show riverpulse.readings
```

**Why partition and cluster?** Imagine 1000 gauges reporting every 5 minutes for a year. That's ~105 million rows. A query for "gauge-001 readings last week" without partitioning scans the entire table. With partitioning on timestamp, it scans only 7 days. With clustering on gauge_id, it scans only gauge-001's rows within those 7 days. Cost difference: potentially 1000x less data scanned.

##### Troubleshooting
If `bq` does not work for anything beyond `--version` and `ls` (e.g. you cannot create tables), you are probably missing the resource manager API: `cloudresourcemanager.googleapis.com`

---

## Step 3: Load Sample Data

Let's create realistic sample data and batch-load it. In production this would flow from Firestore exports or streaming inserts — we'll do both patterns.

**Batch load from a file:**

```bash
# Generate sample readings CSV
cat > /tmp/readings.csv << 'EOF'
gauge_id,timestamp,cfs,stage_height,water_temp,condition,source,received_at
gauge-001,2026-01-28T06:00:00Z,680,3.6,38,optimal,mqtt,2026-01-28T06:00:12Z
gauge-001,2026-01-28T12:00:00Z,720,3.8,42,optimal,mqtt,2026-01-28T12:00:08Z
gauge-001,2026-01-28T18:00:00Z,850,4.1,45,optimal,mqtt,2026-01-28T18:00:15Z
gauge-001,2026-01-29T06:00:00Z,1200,5.0,40,high,mqtt,2026-01-29T06:00:11Z
gauge-001,2026-01-29T12:00:00Z,1850,5.9,43,high,mqtt,2026-01-29T12:00:09Z
gauge-001,2026-01-29T18:00:00Z,2400,6.8,44,flood,mqtt,2026-01-29T18:00:14Z
gauge-001,2026-01-30T06:00:00Z,1600,5.4,41,high,mqtt,2026-01-30T06:00:10Z
gauge-001,2026-01-30T12:00:00Z,1100,4.8,43,runnable,mqtt,2026-01-30T12:00:07Z
gauge-001,2026-01-30T18:00:00Z,900,4.3,42,optimal,mqtt,2026-01-30T18:00:13Z
gauge-002,2026-01-28T06:00:00Z,240,1.8,36,low,mqtt,2026-01-28T06:00:20Z
gauge-002,2026-01-28T12:00:00Z,310,2.0,41,low,mqtt,2026-01-28T12:00:18Z
gauge-002,2026-01-28T18:00:00Z,380,2.2,44,runnable,mqtt,2026-01-28T18:00:22Z
gauge-002,2026-01-29T06:00:00Z,520,2.8,39,runnable,mqtt,2026-01-29T06:00:19Z
gauge-002,2026-01-29T12:00:00Z,780,3.6,42,optimal,mqtt,2026-01-29T12:00:16Z
gauge-002,2026-01-29T18:00:00Z,950,4.2,43,optimal,mqtt,2026-01-29T18:00:21Z
gauge-002,2026-01-30T06:00:00Z,620,3.1,40,runnable,mqtt,2026-01-30T06:00:17Z
gauge-002,2026-01-30T12:00:00Z,440,2.5,42,runnable,mqtt,2026-01-30T12:00:15Z
gauge-002,2026-01-30T18:00:00Z,340,2.1,41,low,mqtt,2026-01-30T18:00:23Z
gauge-003,2026-01-28T06:00:00Z,1800,6.2,35,high,mqtt,2026-01-28T06:00:30Z
gauge-003,2026-01-28T12:00:00Z,2100,6.8,38,high,mqtt,2026-01-28T12:00:28Z
gauge-003,2026-01-28T18:00:00Z,2600,7.4,40,flood,mqtt,2026-01-28T18:00:32Z
gauge-003,2026-01-29T06:00:00Z,3200,8.1,37,flood,mqtt,2026-01-29T06:00:29Z
gauge-003,2026-01-29T12:00:00Z,4100,9.0,39,flood,mqtt,2026-01-29T12:00:27Z
gauge-003,2026-01-29T18:00:00Z,3600,8.5,40,flood,mqtt,2026-01-29T18:00:31Z
gauge-003,2026-01-30T06:00:00Z,2800,7.6,38,flood,mqtt,2026-01-30T06:00:28Z
gauge-003,2026-01-30T12:00:00Z,2200,7.0,40,high,mqtt,2026-01-30T12:00:26Z
gauge-003,2026-01-30T18:00:00Z,1900,6.4,39,high,mqtt,2026-01-30T18:00:33Z
EOF

# Load into BigQuery
bq load \
  --source_format=CSV \
  --skip_leading_rows=1 \
  riverpulse.readings \
  /tmp/readings.csv

# Load gauge metadata
cat > /tmp/gauges.csv << 'EOF'
gauge_id,name,river,lat,lon,river_mile,installed_at,status
gauge-001,Arkansas at Salida,Arkansas River,38.5347,-106.0017,125.4,2025-06-15T00:00:00Z,active
gauge-002,Clear Creek at Golden,Clear Creek,39.7555,-105.2211,15.2,2025-08-01T00:00:00Z,active
gauge-003,Poudre at Fort Collins,Cache la Poudre,40.5853,-105.0844,42.8,2025-07-10T00:00:00Z,active
EOF

bq load \
  --source_format=CSV \
  --skip_leading_rows=1 \
  riverpulse.gauges \
  /tmp/gauges.csv

# Verify row counts
bq query --use_legacy_sql=false 'SELECT COUNT(*) as total_readings FROM riverpulse.readings'
bq query --use_legacy_sql=false 'SELECT COUNT(*) as total_gauges FROM riverpulse.gauges'
```

---

## Step 4: Analytical Queries

This is where BigQuery earns its keep. These queries would be painful or impossible against Firestore.

**Daily averages per gauge:**
```bash
bq query --use_legacy_sql=false '
SELECT
  gauge_id,
  DATE(timestamp) AS reading_date,
  ROUND(AVG(cfs), 1) AS avg_cfs,
  ROUND(MIN(cfs), 1) AS min_cfs,
  ROUND(MAX(cfs), 1) AS max_cfs,
  COUNT(*) AS reading_count
FROM riverpulse.readings
GROUP BY gauge_id, DATE(timestamp)
ORDER BY gauge_id, reading_date
'
```

**Condition distribution — how often is each gauge in each state?**
```bash
bq query --use_legacy_sql=false '
SELECT
  gauge_id,
  condition,
  COUNT(*) AS count,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY gauge_id), 1) AS pct
FROM riverpulse.readings
WHERE condition IS NOT NULL
GROUP BY gauge_id, condition
ORDER BY gauge_id, count DESC
'
```

**Peak flow identification with window functions:**
```bash
bq query --use_legacy_sql=false '
SELECT
  gauge_id,
  timestamp,
  cfs,
  condition,
  RANK() OVER (PARTITION BY gauge_id ORDER BY cfs DESC) AS flow_rank
FROM riverpulse.readings
QUALIFY flow_rank <= 3
ORDER BY gauge_id, flow_rank
'
```

The `QUALIFY` clause is BigQuery-specific — it filters on window function results without needing a subquery. Handy.

**Join with gauge metadata — flow readings enriched with location:**
```bash
bq query --use_legacy_sql=false '
SELECT
  g.name AS gauge_name,
  g.river,
  r.timestamp,
  r.cfs,
  r.condition,
  r.water_temp
FROM riverpulse.readings r
JOIN riverpulse.gauges g ON r.gauge_id = g.gauge_id
WHERE r.condition IN ("flood", "high")
ORDER BY r.cfs DESC
LIMIT 10
'
```

**Rate of change — which gauges are rising fastest?**
```bash
bq query --use_legacy_sql=false '
SELECT
  gauge_id,
  timestamp,
  cfs,
  LAG(cfs) OVER (PARTITION BY gauge_id ORDER BY timestamp) AS prev_cfs,
  cfs - LAG(cfs) OVER (PARTITION BY gauge_id ORDER BY timestamp) AS cfs_delta,
  ROUND(
    (cfs - LAG(cfs) OVER (PARTITION BY gauge_id ORDER BY timestamp))
    / NULLIF(LAG(cfs) OVER (PARTITION BY gauge_id ORDER BY timestamp), 0) * 100,
    1
  ) AS pct_change
FROM riverpulse.readings
ORDER BY ABS(cfs - LAG(cfs) OVER (PARTITION BY gauge_id ORDER BY timestamp)) DESC NULLS LAST
LIMIT 10
'
```

Rate of change is the kind of signal that matters for early flood detection — a gauge going from 1200 to 2400 in 6 hours is more alarming than one that's been steady at 2400 for a week.

---

## Step 5: Streaming Inserts from Cloud Run

Batch loading works for backfills and exports. For real-time analytics, you want streaming inserts — data appears in BigQuery within seconds of ingestion.

Add to `requirements.txt`:
```
google-cloud-bigquery==3.17.0
```

Add to `main.py`:
```python
from google.cloud import bigquery

# Initialize BigQuery client
bq_client = bigquery.Client()
BQ_TABLE = f"{os.environ.get('GOOGLE_CLOUD_PROJECT')}.riverpulse.readings"


def stream_to_bigquery(reading):
    """
    Stream a reading to BigQuery for analytics.
    This runs alongside the Firestore write — both get the data.
    Firestore for real-time queries, BigQuery for analytics.
    """
    try:
        row = {
            "gauge_id": reading.get("gaugeId"),
            "timestamp": reading.get("timestamp", reading.get("receivedAt")),
            "cfs": reading.get("cfs"),
            "stage_height": reading.get("stageHeight"),
            "water_temp": reading.get("waterTemp"),
            "condition": reading.get("condition"),
            "source": reading.get("source", "api"),
            "received_at": reading.get("receivedAt"),
        }
        errors = bq_client.insert_rows_json(BQ_TABLE, [row])
        if errors:
            print(f"BigQuery streaming insert errors: {errors}")
    except Exception as e:
        # Don't fail the request if BQ insert fails — Firestore is primary
        print(f"BigQuery insert failed (non-fatal): {e}")
```

Update the `create_reading` endpoint to also stream to BigQuery:
```python
@app.route('/readings', methods=['POST'])
def create_reading():
    start_time = time.time()
    reading = request.get_json()

    if not reading:
        log_structured("WARNING", "Empty reading received",
                       endpoint="/readings", method="POST")
        return jsonify({"error": "No reading data provided"}), 400

    # Add server timestamp
    from datetime import datetime, timezone
    reading['receivedAt'] = datetime.now(timezone.utc).isoformat()
    
    gauge_id = reading.get('gaugeId', 'unknown')
    condition = reading.get('condition', 'unknown')
    cfs = reading.get('cfs', 0)

    # Store in Firestore (from Lab 4)
    doc_ref = db.collection('readings').document()
    doc_ref.set(reading)

    # Secondary: stream to BigQuery for analytics
    stream_to_bigquery(reading)

    elapsed_ms = (time.time() - start_time) * 1000

    log_structured("INFO", "Reading ingested",
                   gaugeId=gauge_id,
                   condition=condition,
                   cfs=cfs,
                   processingTimeMs=round(elapsed_ms, 2),
                   firestoreDocId=doc_ref.id)

    # Log a warning for extreme readings
    if cfs and cfs > 5000:
        log_structured("WARNING", "Extreme flow reading detected",
                       gaugeId=gauge_id,
                       cfs=cfs,
                       threshold=5000)

    return jsonify({"status": "created", "reading": reading}), 201
```

Redeploy and test:
```bash
gcloud run deploy riverpulse-api \
  --source . \
  --allow-unauthenticated \
  --region us-central1

SERVICE_URL=$(gcloud run services describe riverpulse-api \
  --region us-central1 --format='value(status.url)')

# Send a reading that goes to both Firestore AND BigQuery
curl -X POST ${SERVICE_URL}/readings \
  -H "Content-Type: application/json" \
  -d '{"gaugeId":"gauge-001","cfs":975,"stageHeight":4.5,"waterTemp":44,"condition":"optimal","timestamp":"2026-02-01T10:00:00Z"}'

# Wait a few seconds for streaming insert to land
sleep 5

# Verify it's in BigQuery
bq query --use_legacy_sql=false '
SELECT gauge_id, timestamp, cfs, condition, source
FROM riverpulse.readings
WHERE gauge_id = "gauge-001"
ORDER BY timestamp DESC
LIMIT 5
'
```

---

## Step 6: Cost Awareness

BigQuery charges for two things: storage and queries.

```bash
# Check how much data your table uses
bq show --format=prettyjson riverpulse.readings | grep -E "numRows|numBytes"

# See query cost before running (dry run)
bq query --use_legacy_sql=false --dry_run '
SELECT gauge_id, AVG(cfs) FROM riverpulse.readings GROUP BY gauge_id
'
```

The dry run output shows bytes that *would* be processed. At $5/TB queried, our sample data costs fractions of a cent. At 105 million rows (1000 gauges for a year), a full scan might be ~10GB = $0.05. Partitioning and clustering can reduce that by 90%+.

**Free tier:** First 1TB of queries per month is free. First 10GB of storage is free. For lab and small-scale work, BigQuery is effectively free.

**Streaming inserts:** $0.01 per 200MB. At ~500 bytes per reading, that's ~400,000 readings per penny. Not a concern until very high scale.

---

## BigQuery Architecture Summary

```
[Cloud Run: riverpulse-api]
      |
      |── primary write ──────► [Firestore: readings collection]
      |                            (real-time queries, portal, device status)
      |
      |── streaming insert ───► [BigQuery: riverpulse.readings]
                                   (analytics, aggregations, historical trends)

[Cloud Storage: data exports]
      |
      |── batch load ─────────► [BigQuery: riverpulse.readings]
                                   (backfill, bulk import, Firestore exports)

Query patterns:
  Firestore: "Latest 10 readings for gauge-001" (OLTP)
  BigQuery:  "Average daily flow per gauge for last 6 months" (OLAP)
```

---

## Discussion Points for Interviews

- "Firestore handles the operational workload — portal queries, real-time device status, low-latency reads. BigQuery handles analytics — daily averages, trend detection, fleet-wide reporting. Every reading goes to both: Firestore via the primary write path, BigQuery via streaming insert."

- "The readings table is partitioned by timestamp and clustered by gauge_id and condition. A query for one gauge's last week of data scans a tiny fraction of the table. At 10,000 gauges reporting every 5 minutes, that partitioning is the difference between a $5 query and a $0.005 query."

- "Rate of change is more valuable than absolute values for alerting. A gauge at 2400 cfs is normal on some rivers. A gauge that jumped from 800 to 2400 in 6 hours is a flood warning regardless of the absolute number. Window functions in BigQuery make that analysis straightforward."

- "For backfills or data migration, we batch-load from CSV or Firestore exports. For real-time dashboards, streaming inserts land data in seconds. The streaming path is fire-and-forget from the API's perspective — if it fails, Firestore still has the data and we can backfill later."

---

## Cleanup
Again optional.  Recommended to keep the structure and data for the duration of the labs series.

```bash
# Delete the dataset and all tables (careful — this is permanent)
bq rm -r -f riverpulse

# Or delete individual tables
bq rm -f riverpulse.readings
bq rm -f riverpulse.gauges
```

---

## Learning Summary

This lab covered BiqQuery analytics database.  This is Google's serverless, column-oriented data warehouse.  It's designed for running OLAP SQL queries across massive datasets, without requiring us to manage the infrastructure.  Billing is per query (bytes scanned) rather than instances that we manage.  It's ideal for historical analysis, aggregations, and dashboards over data that has been collected from streaming or batch sources.

We added partitioning and clustering.  It wouldn't be needed at the small scale of this lab, but it becomes important as the datasets grow to enterprise scale.

Additionally, we learned that error messages could be better.  If you try `bq` commands and they "just hang", you are probably missing the resource manager API.  You will receive no obvious errors or warnings in this case, but it's easily fixed once you realize the error.

---

## Next Lab

Lab 12: Cloud Functions — event-driven processing with Pub/Sub triggers.
