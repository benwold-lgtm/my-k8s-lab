#!/usr/bin/env bash
echo "==> ingestion logs (Ctrl+C to stop)"
kubectl logs -f -n ingestion -l app=ingestion --tail=100
