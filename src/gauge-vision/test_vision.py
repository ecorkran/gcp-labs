from google.cloud import vision

def classify_image_uri(image_uri: str):
    """Classify an image from a GCS URI or public URL."""
    client = vision.ImageAnnotatorClient()

    image = vision.Image()
    image.source.image_uri = image_uri

    # Request multiple feature types in one call
    features = [
        vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=10),
        vision.Feature(type_=vision.Feature.Type.OBJECT_LOCALIZATION, max_results=5),
        vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
    ]

    request = vision.AnnotateImageRequest(image=image, features=features)
    response = client.annotate_image(request=request)

    if response.error.message:
        raise Exception(f"Vision API error: {response.error.message}")

    return response


def print_results(response):
    """Print classification results in a readable format."""
    print("=" * 60)
    print("LABEL DETECTION")
    print("=" * 60)
    for label in response.label_annotations:
        print(f"  {label.score:5.1%}  {label.description}")

    print()
    print("=" * 60)
    print("OBJECT LOCALIZATION")
    print("=" * 60)
    for obj in response.localized_object_annotations:
        print(f"  {obj.score:5.1%}  {obj.name}")
        vertices = obj.bounding_poly.normalized_vertices
        print(f"         box: ({vertices[0].x:.2f},{vertices[0].y:.2f}) -> ({vertices[2].x:.2f},{vertices[2].y:.2f})")

    print()
    print("=" * 60)
    print("SAFE SEARCH")
    print("=" * 60)
    safe = response.safe_search_annotation
    likelihood_names = {0: "UNKNOWN", 1: "VERY_UNLIKELY", 2: "UNLIKELY",
                        3: "POSSIBLE", 4: "LIKELY", 5: "VERY_LIKELY"}
    print(f"  Adult:    {likelihood_names.get(safe.adult, 'UNKNOWN')}")
    print(f"  Violence: {likelihood_names.get(safe.violence, 'UNKNOWN')}")
    print(f"  Racy:     {likelihood_names.get(safe.racy, 'UNKNOWN')}")
    print(f"  Spoof:    {likelihood_names.get(safe.spoof, 'UNKNOWN')}")


if __name__ == "__main__":
    # Use a public sample image — river scene
    test_uri = "gs://cloud-samples-data/vision/label/wakeupcat.jpg"
    print(f"Classifying: {test_uri}\n")

    response = classify_image_uri(test_uri)
    print_results(response)
