# backend/main.py
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
    PayloadSchemaType,
)
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
import os
import io
import uuid
from pypdf import PdfReader
from dotenv import load_dotenv
from typing import Optional, List

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "pdf_collection"

# Qdrant client (longer timeout for large uploads)
qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY if QDRANT_API_KEY else None, timeout=120.0)

# Embeddings client
embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)

# Ensure collection exists with filename index.
def ensure_collection():
    try:
        if qdrant.collection_exists(COLLECTION_NAME):
            print(f"Deleting existing collection {COLLECTION_NAME} (to ensure proper schema/index)...")
            qdrant.delete_collection(COLLECTION_NAME)

        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            on_disk_payload=True,
        )
        # create keyword index for filename so we can filter by it
        try:
            qdrant.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="filename",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as e:
            # If index creation fails for any reason, print and continue (but we prefer success).
            print("Warning creating payload index:", e)

        print(f"Created collection {COLLECTION_NAME} with filename index.")
    except Exception as e:
        print("Error ensuring collection:", e)

ensure_collection()

app = FastAPI()

raw_origins = os.getenv("ALLOWED_ORIGINS", "")

if raw_origins:
    allow_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
else:
    allow_origins = ["http://localhost:3000"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    question: str
    filename: Optional[str] = None   # optional — frontend will pass uploaded filename

# helper settings
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
EMBED_BATCH_SIZE = 50
UPSERT_BATCH_SIZE = 64

@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            return {"status": "error", "message": "File too large. Max allowed size is 10 MB."}

        pdf_file = io.BytesIO(contents)
        reader = PdfReader(pdf_file)

        text = ""
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text += f"\n--- Page {i+1} ---\n" + page_text + "\n"

        if not text.strip():
            return {"status": "error", "message": "No text extracted from PDF. Might be image-only or password-protected."}

        # Split
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks: List[str] = splitter.split_text(text)
        if not chunks:
            return {"status": "error", "message": "No chunks were created from the extracted text."}

        print(f"Generating embeddings for {len(chunks)} chunks...")

        # Embed in batches to avoid timeouts / rate issues
        vectors = []
        for i in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[i : i + EMBED_BATCH_SIZE]
            batch_vecs = embeddings.embed_documents(batch)
            vectors.extend(batch_vecs)

        # Build points
        points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
            points.append(
                {
                    "id": str(uuid.uuid4()),
                    "vector": vector,
                    "payload": {"text": chunk, "filename": file.filename, "chunk_index": idx},
                }
            )

        # Upsert in batches
        for i in range(0, len(points), UPSERT_BATCH_SIZE):
            batch = points[i : i + UPSERT_BATCH_SIZE]
            qdrant.upsert(collection_name=COLLECTION_NAME, points=batch)

        print(f"Stored {len(points)} chunks in Qdrant (file={file.filename})")

        # Return success (frontend will save filename locally)
        return {
            "status": "success",
            "chunks_stored": len(points),
            "filename": file.filename,
            "text_length": len(text),
            "pages_processed": len(reader.pages),
        }

    except Exception as e:
        print("Error in upload_pdf:", e)
        return {"status": "error", "message": f"Error processing PDF: {str(e)}"}


@app.post("/ask")
async def ask_question(query: QueryRequest):
    try:
        print("Processing question:", query.question)
        # pick filename: prefer provided filename, otherwise error
        filename_to_search = query.filename
        if not filename_to_search:
            return {"answer": "No PDF filename provided. Upload a PDF first (frontend should pass filename).", "sources_found": 0}

        # embed the query
        qvec = embeddings.embed_query(query.question)

        # search with filename filter
        search_result = qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=qvec,
            limit=5,
            score_threshold=0.1,
            query_filter=Filter(
                must=[FieldCondition(key="filename", match=MatchValue(value=filename_to_search))]
            ),
        )

        if not search_result:
            return {"answer": "No relevant information found in that PDF.", "sources_found": 0}

        # combine contexts
        context_parts = [hit.payload.get("text", "") for hit in search_result]
        context = "\n\n".join(context_parts)

        answer = f"Based on the PDF content, here's what I found:\n\n{context[:2000]}{'...' if len(context) > 2000 else ''}"

        return {"answer": answer, "sources_found": len(search_result), "context_length": len(context)}

    except Exception as e:
        print("Error in ask_question:", e)
        return {"status": "error", "message": f"Error processing query: {str(e)}"}


@app.get("/")
async def root():
    return {"message": "PDF Reader API is running successfully!"}


@app.get("/health")
async def health_check():
    try:
        collections = qdrant.get_collections()
        return {
            "status": "healthy",
            "qdrant_connected": True,
            "collection_exists": qdrant.collection_exists(COLLECTION_NAME),
            "total_collections": len(collections.collections),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.delete("/clear-collection")
async def clear_collection():
    try:
        if qdrant.collection_exists(COLLECTION_NAME):
            qdrant.delete_collection(COLLECTION_NAME)

        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            on_disk_payload=True,
        )

        qdrant.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="filename",
            field_schema=PayloadSchemaType.KEYWORD,
        )

        return {"status": "success", "message": "Collection cleared and recreated with index."}
    except Exception as e:
        return {"status": "error", "message": f"Error clearing collection: {str(e)}"}
