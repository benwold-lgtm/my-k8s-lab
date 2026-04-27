import os
import httpx
import json
import asyncio
import re
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain.tools import tool

app = FastAPI(title="AI Agent Service")

# ── Configuration ─────────────────────────────────────────────────────────────
VLLM_BASE_URL  = os.getenv("VLLM_BASE_URL",  "http://192.168.1.112:30000/v1")
VLLM_MODEL     = os.getenv("VLLM_MODEL",     "mistralai/Mistral-Nemo-Instruct-FP8-2407")
BRAVE_API_KEY  = os.getenv("BRAVE_API_KEY")
BRAVE_URL      = "https://api.search.brave.com/res/v1/web/search"
QDRANT_URL     = os.getenv("QDRANT_URL",     "http://qdrant.qdrant.svc.cluster.local:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
EMBEDDING_URL  = os.getenv("EMBEDDING_URL",  "http://embedding.embedding.svc.cluster.local:8001")

# ── Brave Search ──────────────────────────────────────────────────────────────
async def run_brave_search(query: str) -> str:
    """Execute a Brave Search and return formatted results."""
    if not BRAVE_API_KEY:
        return "Error: Brave Search API key not configured."

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY
    }
    params = {
        "q": query,
        "count": 5,
        "text_decorations": False,
        "search_lang": "en"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                BRAVE_URL,
                headers=headers,
                params=params,
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("web", {}).get("results", []):
                results.append(
                    f"Title: {r.get('title', '')}\n"
                    f"URL: {r.get('url', '')}\n"
                    f"Summary: {r.get('description', '')}\n"
                )

            return "\n---\n".join(results) if results else "No results found."

        except httpx.HTTPError as e:
            return f"Search error: {str(e)}"

# ── RAG Search ───────────────────────────────────────────────────────────────
async def run_rag_search(query: str, top_k: int = 5) -> tuple[str, list[dict]]:
    """Embed query, search all Qdrant collections, return (formatted_text, sources)."""
    qdrant_headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        embed_resp = await client.post(
            f"{EMBEDDING_URL}/v1/embeddings/query",
            json={"input": query}
        )
        embed_resp.raise_for_status()
        query_vector = embed_resp.json()["data"][0]["embedding"]

        coll_resp = await client.get(f"{QDRANT_URL}/collections", headers=qdrant_headers)
        coll_resp.raise_for_status()
        collections = [c["name"] for c in coll_resp.json()["result"]["collections"]]

        if not collections:
            return "No ingested documents found.", []

        all_hits: list[tuple[float, dict]] = []
        for collection in collections:
            try:
                resp = await client.post(
                    f"{QDRANT_URL}/collections/{collection}/points/search",
                    headers=qdrant_headers,
                    json={"vector": query_vector, "limit": top_k, "with_payload": True},
                )
                if resp.status_code == 200:
                    for hit in resp.json().get("result", []):
                        all_hits.append((hit["score"], hit["payload"]))
            except Exception:
                continue

    if not all_hits:
        return "No relevant documents found.", []

    all_hits.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate by URL, keeping the highest-scoring chunk per source
    seen_urls: set[str] = set()
    top_hits: list[tuple[float, dict]] = []
    for score, payload in all_hits:
        url = payload.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            top_hits.append((score, payload))
        if len(top_hits) >= top_k:
            break

    parts = []
    sources = []
    for _, payload in top_hits:
        parts.append(
            f"[{payload.get('title', '')} | {payload.get('vendor', '')} | {payload.get('url', '')}]\n"
            f"{payload.get('content', '')}"
        )
        sources.append({
            "url":         payload.get("url", ""),
            "title":       payload.get("title", ""),
            "vendor":      payload.get("vendor", ""),
            "chunk_index": payload.get("chunk_index", 0),
        })

    return "\n\n---\n\n".join(parts), sources


# ── Tool call parser ──────────────────────────────────────────────────────────
def extract_tool_calls(content: str) -> list:
    """Parse tool calls from vLLM/Mistral response content."""
    tool_calls = []

    # Pattern 1: [TOOL_CALLS][{"name": ..., "arguments": {...}}]
    match = re.search(r'\[TOOL_CALLS\]\s*(\[.*?\])', content, re.DOTALL)
    if match:
        try:
            calls = json.loads(match.group(1))
            if isinstance(calls, list):
                tool_calls.extend(calls)
        except json.JSONDecodeError:
            pass

    # Pattern 2: {"name": ..., "arguments": {...}}
    if not tool_calls:
        match = re.search(r'\{"name":\s*"(\w+)",\s*"arguments":\s*(\{.*?\})\}', content, re.DOTALL)
        if match:
            try:
                tool_calls.append({
                    "name": match.group(1),
                    "arguments": json.loads(match.group(2))
                })
            except json.JSONDecodeError:
                pass

    return tool_calls

# ── Core agent loop ───────────────────────────────────────────────────────────
async def run_agent(
    messages: list,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    top_p: float = 0.9,
) -> tuple[str, list[dict]]:
    """
    Manual agent loop:
    1. Send messages to LLM
    2. If LLM calls a tool, execute it (rag_search or brave_search)
    3. Append tool results and call LLM again
    4. Return (final_response, sources) where sources are RAG chunks used
    """
    llm = ChatOpenAI(
        base_url=VLLM_BASE_URL,
        api_key="not-needed",
        model=VLLM_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )

    system_prompt = """You are a helpful assistant with access to two tools:

1. rag_search — Search the ingested vendor documentation and knowledge base.
   Use this first for any questions about products, vendors, or technical topics.
   Format: [TOOL_CALLS][{"name": "rag_search", "arguments": {"query": "your search query"}}]

2. brave_search — Search the live internet for current information.
   Use this for recent news, current pricing, or when rag_search returns no useful results.
   Format: [TOOL_CALLS][{"name": "brave_search", "arguments": {"query": "your search query"}}]

Always try rag_search first. Use brave_search only when the knowledge base lacks the answer.

When formulating your answer after receiving tool results:
- Answer ONLY using the information returned by the tools.
- If the retrieved context does not contain enough information to answer, say so explicitly.
- Do NOT use your training knowledge to supplement the retrieved context.
- Do NOT fabricate quotes or specific details not present in the retrieved context."""

    # Prepend system message if not already present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": system_prompt}] + messages

    max_iterations = 3  # prevent infinite loops
    iteration = 0
    all_sources: list[dict] = []

    while iteration < max_iterations:
        iteration += 1

        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

        lc_messages = []
        for m in messages:
            if m["role"] == "system":
                lc_messages.append(SystemMessage(content=m["content"]))
            elif m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))

        response = await llm.ainvoke(lc_messages)
        content = response.content

        tool_calls = extract_tool_calls(content)

        if not tool_calls:
            return content, all_sources

        tool_results = []
        for call in tool_calls:
            tool_name = call.get("name")
            tool_args = call.get("arguments", {})
            query = tool_args.get("query", "")

            if tool_name == "rag_search":
                result_text, sources = await run_rag_search(query)
                all_sources.extend(sources)
                tool_results.append(f"Knowledge base results for '{query}':\n{result_text}")
            elif tool_name == "brave_search":
                result_text = await run_brave_search(query)
                tool_results.append(f"Web search results for '{query}':\n{result_text}")
            else:
                tool_results.append(f"Unknown tool: {tool_name}")

        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "Here are the results:\n\n" + "\n\n".join(tool_results) + "\n\nPlease provide a helpful answer based on these results."
        })

    return "I was unable to complete the request after multiple attempts.", all_sources

# ── Request/Response Models ───────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: Optional[float] = 0.2
    stream: Optional[bool] = False
    max_tokens: Optional[int] = 1024
    top_p: Optional[float] = 0.9

# ── OpenAI-Compatible Endpoint ────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    try:
        final_response, sources = await run_agent(
            messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            top_p=request.top_p,
        )

        if sources:
            sources_md = "\n\n---\n**Sources:**\n" + "\n".join(
                f"- [{s['vendor']}] {s['title']} — {s['url']}"
                for s in sources
            )
            final_response += sources_md

        if request.stream:
            return StreamingResponse(
                stream_text(final_response),
                media_type="text/event-stream"
            )

        return {
            "id": "chatcmpl-agent",
            "object": "chat.completion",
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": final_response
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "sources": sources
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def stream_text(text: str):
    """Stream a completed response word by word."""
    words = text.split(" ")
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        data = {
            "id": "chatcmpl-agent",
            "object": "chat.completion.chunk",
            "model": VLLM_MODEL,
            "choices": [{
                "index": 0,
                "delta": {"content": chunk},
                "finish_reason": None
            }]
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(0.01)
    yield "data: [DONE]\n\n"

# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy", "vllm_url": VLLM_BASE_URL}

# ── Models Endpoint ───────────────────────────────────────────────────────────
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": VLLM_MODEL,
            "object": "model",
            "owned_by": "ai-agent"
        }]
    }