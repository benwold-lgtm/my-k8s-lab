#!/usr/bin/env bash
# Send a question to the AI agent and display the response.
# Usage:
#   ./rag-query.sh
#   ./rag-query.sh "What is the BGP DPF reference design?"

AI_AGENT_URL="http://192.168.1.112:30081"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; NC='\033[0m'

QUERY="$*"
if [[ -z "$QUERY" ]]; then
    read -rp "Question: " QUERY
fi
if [[ -z "$QUERY" ]]; then
    echo "Error: question is required." >&2; exit 1
fi

PAYLOAD=$(printf '{"model":"rag","messages":[{"role":"user","content":"%s"}]}' \
    "$(echo "$QUERY" | sed 's/"/\\"/g')")

echo ""
echo -e "${CYAN}Query:${NC} $QUERY"
echo -e "${GREEN}────────────────────────────────────────────────────────────────${NC}"

RESPONSE=$(curl -s -X POST "${AI_AGENT_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

echo "$RESPONSE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
choices = d.get('choices', [])
if choices:
    print(choices[0]['message']['content'])
else:
    print(json.dumps(d, indent=2))
"
echo ""
