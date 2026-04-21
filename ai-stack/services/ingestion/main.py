import os
import re
import json
import hashlib
import asyncio
import aiosqlite
import httpx
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from bs4 import BeautifulSoup
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue
)
from tenacity import retry, stop_after_attempt, wait_exponential

app = FastAPI(title="Ingestion Service")

# ── Configuration ─────────────────────────────────────────────────────────────
QDRANT_URL        = os.getenv("QDRANT_URL", "http://qdrant.qdrant.svc.cluster.local:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY")
EMBEDDING_URL     = os.getenv("EMBEDDING_URL", "http://embedding.embedding.svc.cluster.local:8001")
DB_PATH           = os.getenv("DB_PATH", "/app/data/ingestion.db")
CHUNK_SIZE        = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP     = int(os.getenv("CHUNK_OVERLAP", "50"))
EMBEDDING_DIM     = int(os.getenv("EMBEDDING_DIM", "768"))

# ── Database setup ────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                collection  TEXT NOT NULL,
                vendor      TEXT,
                title       TEXT,
                content_hash TEXT,
                status      TEXT DEFAULT 'pending',
                chunk_count INTEGER DEFAULT 0,
                error       TEXT,
                created_at  TEXT,
                updated_at  TEXT,
                last_checked TEXT
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    await init_db()

# ── Qdrant client ─────────────────────────────────────────────────────────────
def get_qdrant():
    return QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY
    )

# ── Ensure collection exists ──────────────────────────────────────────────────
async def ensure_collection(collection: str):
    client = get_qdrant()
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE
            )
        )

# ── Text chunking ─────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

# ── Web fetcher ───────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def fetch_url(url: str) -> tuple[str, str]:
    """Fetch URL and return (title, clean_text)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RAG-Ingestion/1.0)"
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Extract title
    title = ""
    if soup.title:
        title = soup.title.string or ""

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                      "aside", "form", "iframe", "noscript",
                      "cookie-banner", "advertisement"]):
        tag.decompose()

    # Extract main content — prefer article/main tags
    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main:
        text = main.get_text(separator=" ", strip=True)
    else:
        text = soup.get_text(separator=" ", strip=True)

    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return title.strip(), text

# ── Embedding caller ──────────────────────────────────────────────────────────
async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Call embedding service to get vectors."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{EMBEDDING_URL}/v1/embeddings",
            json={"input": texts}
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

# ── Core ingestion logic ──────────────────────────────────────────────────────
async def ingest_url(
    doc_id: str,
    url: str,
    collection: str,
    vendor: str,
    access_roles: list[str],
    classification: str
):
    """Full ingestion pipeline for a single URL."""
    now = datetime.utcnow().isoformat()

    # Update status to processing
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE documents SET status=?, updated_at=? WHERE id=?",
            ("processing", now, doc_id)
        )
        await db.commit()

    try:
        # 1. Fetch and parse
        title, text = await fetch_url(url)

        if len(text) < 100:
            raise ValueError(f"Insufficient content extracted: {len(text)} chars")

        # 2. Check if content changed
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT content_hash FROM documents WHERE id=?", (doc_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] == content_hash:
                    # Content unchanged — skip re-embedding
                    await db.execute(
                        "UPDATE documents SET status=?, last_checked=?, updated_at=? WHERE id=?",
                        ("unchanged", now, now, doc_id)
                    )
                    await db.commit()
                    return

        # 3. Chunk text
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("No chunks generated from content")

        # 4. Ensure Qdrant collection exists
        await ensure_collection(collection)

        # 5. Delete old chunks for this document if re-ingesting
        client = get_qdrant()
        client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[FieldCondition(
                    key="doc_id",
                    match=MatchValue(value=doc_id)
                )]
            )
        )

        # 6. Embed chunks in batches of 32
        all_embeddings = []
        batch_size = 32
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            embeddings = await embed_texts(batch)
            all_embeddings.extend(embeddings)
            await asyncio.sleep(0.1)  # be kind to the embedding service

        # 7. Store in Qdrant with metadata
        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, all_embeddings)):
            point_id = abs(hash(f"{doc_id}-{i}")) % (2**63)
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "doc_id":         doc_id,
                    "url":            url,
                    "title":          title,
                    "collection":     collection,
                    "vendor":         vendor,
                    "chunk_index":    i,
                    "total_chunks":   len(chunks),
                    "content":        chunk,
                    "access_roles":   access_roles,
                    "classification": classification,
                    "ingested_at":    now,
                    "content_hash":   content_hash
                }
            ))

        # Upload in batches of 100
        for i in range(0, len(points), 100):
            client.upsert(
                collection_name=collection,
                points=points[i:i + 100]
            )

        # 8. Update tracking DB
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE documents
                SET status=?, title=?, content_hash=?,
                    chunk_count=?, updated_at=?, last_checked=?
                WHERE id=?
            """, ("completed", title, content_hash,
                  len(chunks), now, now, doc_id))
            await db.commit()

    except Exception as e:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE documents SET status=?, error=?, updated_at=? WHERE id=?",
                ("failed", str(e), now, doc_id)
            )
            await db.commit()
        raise

# ── Request/Response Models ───────────────────────────────────────────────────
class IngestRequest(BaseModel):
    url: str
    collection: str
    vendor: str
    access_roles: Optional[list[str]] = ["all"]
    classification: Optional[str] = "public"

class BatchIngestRequest(BaseModel):
    documents: list[IngestRequest]

class CollectionCreateRequest(BaseModel):
    name: str

# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.post("/ingest/url")
async def ingest_single(request: IngestRequest, background_tasks: BackgroundTasks):
    """Submit a single URL for ingestion."""
    doc_id = hashlib.sha256(request.url.encode()).hexdigest()[:16]
    now = datetime.utcnow().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO documents
            (id, url, collection, vendor, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (doc_id, request.url, request.collection, request.vendor, now, now))
        await db.commit()

    background_tasks.add_task(
        ingest_url,
        doc_id,
        request.url,
        request.collection,
        request.vendor,
        request.access_roles,
        request.classification
    )

    return {
        "doc_id": doc_id,
        "status": "pending",
        "message": f"Ingestion started for {request.url}"
    }

@app.post("/ingest/batch")
async def ingest_batch(request: BatchIngestRequest, background_tasks: BackgroundTasks):
    """Submit multiple URLs for ingestion."""
    results = []
    for doc in request.documents:
        doc_id = hashlib.sha256(doc.url.encode()).hexdigest()[:16]
        now = datetime.utcnow().isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO documents
                (id, url, collection, vendor, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """, (doc_id, doc.url, doc.collection, doc.vendor, now, now))
            await db.commit()

        background_tasks.add_task(
            ingest_url,
            doc_id,
            doc.url,
            doc.collection,
            doc.vendor,
            doc.access_roles,
            doc.classification
        )

        results.append({
            "doc_id": doc_id,
            "url": doc.url,
            "status": "pending"
        })

    return {"submitted": len(results), "documents": results}

@app.get("/documents")
async def list_documents(collection: Optional[str] = None, status: Optional[str] = None):
    """List all ingested documents with optional filters."""
    query = "SELECT * FROM documents WHERE 1=1"
    params = []

    if collection:
        query += " AND collection=?"
        params.append(collection)
    if status:
        query += " AND status=?"
        params.append(status)

    query += " ORDER BY updated_at DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return {"documents": [dict(row) for row in rows]}

@app.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    """Get status of a specific document."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            return dict(row)

@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Remove a document from Qdrant and tracking DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
            doc = dict(row)

    # Delete from Qdrant
    client = get_qdrant()
    client.delete(
        collection_name=doc["collection"],
        points_selector=Filter(
            must=[FieldCondition(
                key="doc_id",
                match=MatchValue(value=doc_id)
            )]
        )
    )

    # Delete from tracking DB
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        await db.commit()

    return {"message": f"Document {doc_id} deleted successfully"}

@app.get("/collections")
async def list_collections():
    """List all Qdrant collections."""
    client = get_qdrant()
    collections = client.get_collections().collections
    return {
        "collections": [
            {"name": c.name} for c in collections
        ]
    }

@app.post("/collections")
async def create_collection(request: CollectionCreateRequest):
    """Create a new Qdrant collection."""
    await ensure_collection(request.name)
    return {"message": f"Collection '{request.name}' ready"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "qdrant_url": QDRANT_URL,
        "embedding_url": EMBEDDING_URL
    }