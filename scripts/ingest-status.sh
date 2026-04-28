#!/usr/bin/env bash
# Show recent ingestion jobs and their status.
# Usage:
#   ./ingest-status.sh              (show last 20 documents)
#   ./ingest-status.sh <doc_id>     (show a specific document)

INGESTION_URL="http://192.168.1.112:30083"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

status_color() {
    case "$1" in
        completed)  echo -e "${GREEN}$1${NC}" ;;
        failed)     echo -e "${RED}$1${NC}" ;;
        processing) echo -e "${YELLOW}$1${NC}" ;;
        *)          echo "$1" ;;
    esac
}

if [[ -n "$1" ]]; then
    # Single document detail
    RESPONSE=$(curl -s "${INGESTION_URL}/documents/$1")
    echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"  doc_id   : {d['id']}\")
print(f\"  title    : {d.get('title','')}\")
print(f\"  url      : {d.get('url','')}\")
print(f\"  vendor   : {d.get('vendor','')}\")
print(f\"  status   : {d.get('status','')}\")
print(f\"  chunks   : {d.get('chunk_count',0)}\")
print(f\"  type     : {d.get('source_type','')}\")
print(f\"  updated  : {d.get('updated_at','')}\")
if d.get('error'):
    print(f\"  error    : {d['error']}\")
"
else
    # List recent documents
    RESPONSE=$(curl -s "${INGESTION_URL}/documents")
    echo ""
    echo -e "${CYAN}Recent ingestion jobs${NC}"
    echo "────────────────────────────────────────────────────────────────────"
    printf "%-16s %-12s %-8s %-10s %-6s %s\n" "DOC_ID" "STATUS" "CHUNKS" "VENDOR" "TYPE" "TITLE"
    echo "────────────────────────────────────────────────────────────────────"
    echo "$RESPONSE" | python3 -c "
import json, sys
docs = json.load(sys.stdin).get('documents', [])[:20]
for d in docs:
    title = (d.get('title') or d.get('url') or '')[:50]
    print(f\"{d['id']:<16} {d.get('status',''):<12} {d.get('chunk_count',0):<8} {d.get('vendor',''):<10} {d.get('source_type',''):<8} {title}\")
"
    echo ""
fi
