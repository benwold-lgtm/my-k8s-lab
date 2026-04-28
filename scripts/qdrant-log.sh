#!/usr/bin/env bash
echo "==> qdrant logs (Ctrl+C to stop)"
kubectl logs -f -n qdrant -l app=qdrant --tail=100
