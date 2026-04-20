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
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://192.168.1.112:30000/v1")
VLLM_MODEL    = os.getenv("VLLM_MODEL", "mistralai/Mistral-Nemo-Instruct-FP8-2407")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
BRAVE_URL     = "https://api.search.brave.com/res/v1/web/search"

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
async def run_agent(messages: list, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    """
    Manual agent loop:
    1. Send messages to LLM
    2. If LLM calls a tool, execute it
    3. Append tool results and call LLM again
    4. Return final response
    """
    llm = ChatOpenAI(
        base_url=VLLM_BASE_URL,
        api_key="not-needed",
        model=VLLM_MODEL,
        temperature=temperature,
        max_tokens=max_tokens
    )

    # Build system prompt that instructs tool use
    system_prompt = """You are a helpful assistant with access to a web search tool.

When you need current information (weather, news, recent events, prices, etc.),
use the following format to search:
[TOOL_CALLS][{"name": "brave_search", "arguments": {"query": "your search query"}}]

After receiving search results, provide a clear, helpful answer based on those results.
If you don't need to search, answer directly."""

    # Prepend system message if not already present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": system_prompt}] + messages

    max_iterations = 3  # prevent infinite loops
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        # Call the LLM
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

        # Check if LLM wants to call a tool
        tool_calls = extract_tool_calls(content)

        if not tool_calls:
            # No tool calls — this is the final answer
            return content

        # Execute each tool call and collect results
        tool_results = []
        for call in tool_calls:
            tool_name = call.get("name")
            tool_args = call.get("arguments", {})

            if tool_name == "brave_search":
                query = tool_args.get("query", "")
                result = await run_brave_search(query)
                tool_results.append(f"Search results for '{query}':\n{result}")
            else:
                tool_results.append(f"Unknown tool: {tool_name}")

        # Append assistant tool call and tool results to messages
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": f"Here are the search results:\n\n" + "\n\n".join(tool_results) + "\n\nPlease provide a helpful answer based on these results."
        })

    return "I was unable to complete the request after multiple attempts."

# ── Request/Response Models ───────────────────────────────────────────────────
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False
    max_tokens: Optional[int] = 1024

# ── OpenAI-Compatible Endpoint ────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    try:
        final_response = await run_agent(
            messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )

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
            }
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