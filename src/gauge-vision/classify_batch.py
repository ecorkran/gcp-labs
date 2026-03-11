"""
Batch classify all gauge images in a Cloud Storage prefix.
Useful for initial testing and backfill processing.
"""
import sys
from google.cloud import storage
from classifier import classify_image, store_classification


def classify_bucket_images(bucket_name: str, prefix: str = "images/"):
    """Classify all images under a GCS prefix."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    blobs = bucket.list_blobs(prefix=prefix)
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    
    results = []
    for blob in blobs:
        ext = "." + blob.name.rsplit(".", 1)[-1].lower() if "." in blob.name else ""
        if ext not in image_extensions:
            continue
        
        image_uri = f"gs://{bucket_name}/{blob.name}"
        print(f"\nClassifying: {image_uri}")
        
        try:
            classification = classify_image(image_uri)
            
            # Extract gauge ID from filename convention: gauge-XXX-condition.ext
            filename = blob.name.split("/")[-1]
            gauge_id = "-".join(filename.split("-")[:2]) if "-" in filename else "unknown"
            
            doc_id = store_classification(gauge_id, image_uri, classification)
            
            condition = classification["derived_condition"]
            print(f"  Condition: {condition['condition']} ({condition['confidence']:.0%})")
            print(f"  Labels: {', '.join(l['description'] for l in classification['labels'][:5])}")
            print(f"  Flagged: {classification['flagged']}")
            print(f"  Stored: {doc_id}")
            
            results.append({
                "image": image_uri,
                "condition": condition["condition"],
                "doc_id": doc_id,
            })
            
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"image": image_uri, "error": str(e)})
    
    print(f"\n{'=' * 60}")
    print(f"Classified {len(results)} images")
    return results


if __name__ == "__main__":
    import os
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.popen("gcloud config get-value project").read().strip()
    bucket = f"{project_id}-riverpulse-data"
    
    if len(sys.argv) > 1:
        bucket = sys.argv[1]
    
    classify_bucket_images(bucket, prefix="images/")