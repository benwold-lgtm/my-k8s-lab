import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from sentence_transformers import SentenceTransformer

app = FastAPI(title="Embedding Service")

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")

# ── Load model at startup ─────────────────────────────────────────────────────
print(f"Loading embedding model: {MODEL_NAME}")
model = SentenceTransformer(MODEL_NAME, trust_remote_code=True)
print("Embedding model loaded successfully")

# ── Request/Response Models ───────────────────────────────────────────────────
class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: Optional[str] = MODEL_NAME

class EmbeddingData(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]

class EmbeddingResponse(BaseModel):
    object: str = "list"
    model: str
    data: list[EmbeddingData]

# ── OpenAI-compatible embeddings endpoint ─────────────────────────────────────
@app.post("/v1/embeddings")
async def create_embeddings(request: EmbeddingRequest):
    try:
        # Normalize input to list
        texts = request.input if isinstance(request.input, list) else [request.input]

        # Add nomic prefix required by nomic-embed-text
        prefixed = [f"search_document: {t}" for t in texts]

        # Generate embeddings in thread pool (CPU bound)
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: model.encode(prefixed, normalize_embeddings=True).tolist()
        )

        return EmbeddingResponse(
            model=MODEL_NAME,
            data=[
                EmbeddingData(index=i, embedding=emb)
                for i, emb in enumerate(embeddings)
            ]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Query embedding endpoint ──────────────────────────────────────────────────
# Separate endpoint for query embeddings vs document embeddings
# nomic-embed-text uses different prefixes for queries vs documents
@app.post("/v1/embeddings/query")
async def create_query_embedding(request: EmbeddingRequest):
    try:
        texts = request.input if isinstance(request.input, list) else [request.input]

        # Query prefix for nomic-embed-text
        prefixed = [f"search_query: {t}" for t in texts]

        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: model.encode(prefixed, normalize_embeddings=True).tolist()
        )

        return EmbeddingResponse(
            model=MODEL_NAME,
            data=[
                EmbeddingData(index=i, embedding=emb)
                for i, emb in enumerate(embeddings)
            ]
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "model": MODEL_NAME,
        "dimensions": model.get_sentence_embedding_dimension()
    }

# ── Model info ────────────────────────────────────────────────────────────────
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": MODEL_NAME,
            "object": "model",
            "dimensions": model.get_sentence_embedding_dimension()
        }]
    }