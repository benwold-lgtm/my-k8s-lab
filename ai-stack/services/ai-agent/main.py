import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio

from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langgraph.prebuilt import create_react_agent

app = FastAPI(title="AI Agent Service")

# ── Configuration ────────────────────────────────────────────────────────────
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://192.168.1.112:30000/v1")
VLLM_MODEL    = os.getenv("VLLM_MODEL", "mistralai/Mistral-Nemo-Instruct-FP8-2407")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
BRAVE_URL     = "https://api.search.brave.com/res/v1/web/search"

# ── Brave Search Tool ─────────────────────────────────────────────────────────
@tool
async def brave_search(query: str) -> str:
    """Search the internet for current information using Brave Search.
    Use this tool for current events, weather, news, or any information
    that may have changed recently."""
    
    if not BRAVE_API_KEY:
        return "Error: Brave Search API key not configured."
    
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY
    }
    params = {
        "q": query,
        "count": 5,          # number of results to fetch
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
            
            # Extract and format results for the LLM
            results = []
            web_results = data.get("web", {}).get("results", [])
            
            for r in web_results:
                results.append(
                    f"Title: {r.get('title', '')}\n"
                    f"URL: {r.get('url', '')}\n"
                    f"Summary: {r.get('description', '')}\n"
                )
            
            if not results:
                return "No results found for this query."
                
            return "\n---\n".join(results)
            
        except httpx.HTTPError as e:
            return f"Search error: {str(e)}"

# ── LangGraph Agent ───────────────────────────────────────────────────────────
def get_agent():
    llm = ChatOpenAI(
        base_url=VLLM_BASE_URL,
        api_key="not-needed",
        model=VLLM_MODEL,
        temperature=0.7,
        streaming=True
    )
    tools = [brave_search]
    agent = create_react_agent(llm, tools)
    return agent

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
    agent = get_agent()
    
    # Convert messages to LangGraph format
    messages = [
        {"role": msg.role, "content": msg.content}
        for msg in request.messages
    ]
    
    try:
        if request.stream:
            return StreamingResponse(
                stream_response(agent, messages),
                media_type="text/event-stream"
            )
        else:
            result = await agent.ainvoke({"messages": messages})
            final_message = result["messages"][-1].content
            
            return {
                "id": "chatcmpl-agent",
                "object": "chat.completion",
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": final_message
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

async def stream_response(agent, messages):
    """Stream response in OpenAI SSE format."""
    try:
        async for chunk in agent.astream({"messages": messages}):
            if "agent" in chunk:
                for msg in chunk["agent"].get("messages", []):
                    if hasattr(msg, "content") and msg.content:
                        data = {
                            "id": "chatcmpl-agent",
                            "object": "chat.completion.chunk",
                            "model": VLLM_MODEL,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": msg.content},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(data)}\n\n"
                        await asyncio.sleep(0)
        
        # Send final done message
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        error_data = {"error": str(e)}
        yield f"data: {json.dumps(error_data)}\n\n"

# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "healthy", "vllm_url": VLLM_BASE_URL}

# ── Models endpoint (OpenAI-compatible) ───────────────────────────────────────
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