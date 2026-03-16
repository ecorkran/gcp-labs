"""
Verify Gemini access via Vertex AI using the google-genai SDK.
"""
from google import genai
from google.genai import types
import os

# Initialize client for Vertex AI
# Picks up GOOGLE_CLOUD_PROJECT and region from environment or gcloud config
PROJECT_ID = os.popen("gcloud config get-value project").read().strip()

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location="us-central1",
)

# Simple text prompt
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="What are three indicators of flood conditions on a river? Be concise.",
)

print(response.text)