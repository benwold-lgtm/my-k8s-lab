#!/usr/bin/env bash
echo "==> vllm-server logs (Ctrl+C to stop)"
kubectl logs -f -n ai-stack -l app=vllm --tail=100
