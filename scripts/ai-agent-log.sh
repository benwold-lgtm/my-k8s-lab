#!/usr/bin/env bash
echo "==> ai-agent logs (Ctrl+C to stop)"
kubectl logs -f -n ai-agent -l app=ai-agent --tail=100
