# Cloud Run Quick Reference

## Toggle Public Access

When working through labs, you may want to temporarily enable public access for testing, then lock it down when you're done to avoid unexpected traffic and billing.

### Remove public access

```bash
gcloud run services remove-iam-policy-binding SERVICE_NAME \
  --member="allUsers" \
  --role="roles/run.invoker" \
  --region us-central1
```

### Restore public access

```bash
gcloud run services add-iam-policy-binding SERVICE_NAME \
  --member="allUsers" \
  --role="roles/run.invoker" \
  --region us-central1
```

Replace `SERVICE_NAME` with your deployed service (e.g. `riverpulse-api`). Adjust `--region` if you deployed elsewhere.