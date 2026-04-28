#!/usr/bin/env bash
echo "==> embedding logs (Ctrl+C to stop)"
kubectl logs -f -n embedding -l app=embedding --tail=100
