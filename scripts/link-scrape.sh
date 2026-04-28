#!/usr/bin/env bash
# Ingest a URL into the RAG knowledge base.
# Usage:
#   ./link-scrape.sh                        (interactive prompts)
#   ./link-scrape.sh <url>                  (prompts for remaining fields)
#   ./link-scrape.sh <url> --deep           (deep crawl mode)

INGESTION_URL="http://192.168.1.112:30083"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

# ── URL ───────────────────────────────────────────────────────────────────────
URL="${1}"
if [[ -z "$URL" ]]; then
    read -rp "URL to ingest: " URL
fi
if [[ -z "$URL" ]]; then
    echo -e "${RED}Error: URL is required.${NC}" >&2; exit 1
fi

# ── Mode ──────────────────────────────────────────────────────────────────────
DEEP=false
for arg in "$@"; do [[ "$arg" == "--deep" ]] && DEEP=true; done

if [[ "$DEEP" == "false" ]]; then
    read -rp "Deep crawl? Follows links on the page [y/N]: " deep_ans
    [[ "$deep_ans" =~ ^[Yy]$ ]] && DEEP=true
fi

# ── Collection & vendor ───────────────────────────────────────────────────────
read -rp "Collection name (e.g. juniper, cisco): " COLLECTION
COLLECTION="${COLLECTION:-general}"

read -rp "Vendor tag [${COLLECTION}]: " VENDOR
VENDOR="${VENDOR:-$COLLECTION}"

# ── Deep crawl options ────────────────────────────────────────────────────────
if [[ "$DEEP" == "true" ]]; then
    read -rp "Max depth [2]: " MAX_DEPTH;  MAX_DEPTH="${MAX_DEPTH:-2}"
    read -rp "Max pages [30]: " MAX_PAGES; MAX_PAGES="${MAX_PAGES:-30}"
    read -rp "Include pattern (restrict crawl to URL subtree, leave blank for none): " PATTERN

    if [[ -n "$PATTERN" ]]; then
        PAYLOAD=$(printf '{"url":"%s","collection":"%s","vendor":"%s","max_depth":%s,"max_pages":%s,"include_pattern":"%s"}' \
            "$URL" "$COLLECTION" "$VENDOR" "$MAX_DEPTH" "$MAX_PAGES" "$PATTERN")
    else
        PAYLOAD=$(printf '{"url":"%s","collection":"%s","vendor":"%s","max_depth":%s,"max_pages":%s}' \
            "$URL" "$COLLECTION" "$VENDOR" "$MAX_DEPTH" "$MAX_PAGES")
    fi
    ENDPOINT="/ingest/deep"
else
    PAYLOAD=$(printf '{"url":"%s","collection":"%s","vendor":"%s"}' "$URL" "$COLLECTION" "$VENDOR")
    ENDPOINT="/ingest/url"
fi

# ── Submit ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}Submitting to ${INGESTION_URL}${ENDPOINT}...${NC}"
RESPONSE=$(curl -s -X POST "${INGESTION_URL}${ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

DOC_ID=$(echo "$RESPONSE" | grep -o '"doc_id":"[^"]*"' | cut -d'"' -f4)
STATUS=$(echo "$RESPONSE"  | grep -o '"status":"[^"]*"'  | cut -d'"' -f4)

if [[ -n "$DOC_ID" ]]; then
    echo -e "${GREEN}Submitted.${NC} doc_id=${DOC_ID}  status=${STATUS}"
    echo ""
    echo "  Track progress:  ./ingest-status.sh ${DOC_ID}"
    echo "  Watch logs:      ./ingestion-log.sh"
else
    echo -e "${RED}Unexpected response:${NC}"
    echo "$RESPONSE"
fi
