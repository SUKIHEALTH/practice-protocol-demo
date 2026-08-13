import os
import sqlite3
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

import chromadb
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader
import docx
import anthropic

# ---------- Config ----------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATA_DIR = os.environ.get("DATA_DIR", "/data")  # mount a Railway volume here
FREE_QUESTIONS = int(os.environ.get("FREE_QUESTIONS", 5))
OWN_DOCS_QUESTION_LIMIT = int(os.environ.get("OWN_DOCS_QUESTION_LIMIT", 30))
OWN_DOCS_EXPIRY_HOURS = int(os.environ.get("OWN_DOCS_EXPIRY_HOURS", 24))
MAX_FILES = 10
MAX_FILE_SIZE_MB = 5
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")  # set to your V0 domain once live
PROTOCOLS_DIR = Path(__file__).parent

Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "leads.db")
DEMO_CHROMA_PATH = os.path.join(DATA_DIR, "chroma_demo")
USER_CHROMA_PATH = os.path.join(DATA_DIR, "chroma_user")

DEMO_SYSTEM_PROMPT = """World: You are a demo protocol assistant showing how an AI-powered internal knowledge base works for a GP practice. The example protocols provided are illustrative demo content, not real patient-facing guidance from any actual practice.

Task: Answer questions using ONLY the protocol excerpts provided in each message. If the answer isn't in the excerpts, say so plainly and note this is a demo with a limited example document set.

Constraint:
- This is a demonstration only — never present an answer as real clinical guidance a patient or clinician should act on.
- Always cite the source document name for every claim you make.
- Keep answers short and direct."""

USER_DOCS_SYSTEM_PROMPT = """World: You are a protocol assistant answering questions using documents the visitor has uploaded themselves for this trial session.

Task: Answer questions using ONLY the excerpts provided in each message, drawn from the visitor's own uploaded documents. If the answer isn't in the excerpts, say so plainly and suggest they check the original document or their practice lead.

Constraint:
- Never answer clinical judgement questions about a specific patient — process and protocol questions only.
- Always cite the source document name for every claim you make.
- Keep answers short and direct."""

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- DB ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT, practice_name TEXT,
        consent INTEGER, created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        question_count INTEGER DEFAULT 0,
        has_lead INTEGER DEFAULT 0,
        has_own_docs INTEGER DEFAULT 0,
        docs_expiry TEXT,
        last_seen TEXT
    )""")
    conn.commit()
    conn.close()


init_db()

# ---------- RAG setup ----------
embedder = SentenceTransformer("all-MiniLM-L6-v2")
demo_chroma_client = chromadb.PersistentClient(path=DEMO_CHROMA_PATH)
user_chroma_client = chromadb.PersistentClient(path=USER_CHROMA_PATH)


def build_demo_index():
    try:
        collection = demo_chroma_client.get_collection("demo_protocols")
        if collection.count() > 0:
            return collection
    except Exception:
        pass

    collection = demo_chroma_client.get_or_create_collection("demo_protocols")
    chunks, metadatas, ids = [], [], []
    cid = 0
    for fpath in sorted(PROTOCOLS_DIR.glob("Protocol*.txt")):
        text = fpath.read_text(encoding="utf-8")
        words = text.split()
        for i in range(0, len(words), 700):
            chunk = " ".join(words[i:i + 800])
            chunks.append(chunk)
            metadatas.append({"source": fpath.name})
            ids.append(f"chunk_{cid}")
            cid += 1
    if chunks:
        embeddings = embedder.encode(chunks).tolist()
        collection.add(embeddings=embeddings, documents=chunks, metadatas=metadatas, ids=ids)
    return collection


demo_collection = build_demo_index()
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------- Models ----------
class ChatRequest(BaseModel):
    session_id: str
    message: str


class LeadRequest(BaseModel):
    session_id: str
    name: str
    email: EmailStr
    practice_name: Optional[str] = None
    consent: bool


# ---------- Session helpers ----------
def get_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT question_count, has_lead, has_own_docs, docs_expiry FROM sessions WHERE session_id=?",
        (session_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "question_count": row[0],
            "has_lead": bool(row[1]),
            "has_own_docs": bool(row[2]),
            "docs_expiry": row[3],
        }
    return {"question_count": 0, "has_lead": False, "has_own_docs": False, "docs_expiry": None}


def upsert_session(session_id: str, **fields):
    session = get_session(session_id)
    session.update(fields)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO sessions (session_id, question_count, has_lead, has_own_docs, docs_expiry, last_seen)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
              question_count=excluded.question_count,
              has_lead=excluded.has_lead,
              has_own_docs=excluded.has_own_docs,
              docs_expiry=excluded.docs_expiry,
              last_seen=excluded.last_seen""",
        (
            session_id,
            session["question_count"],
            int(session["has_lead"]),
            int(session["has_own_docs"]),
            session["docs_expiry"],
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def cleanup_expired_sessions():
    """Lazy cleanup: runs on every request. Deletes any user-uploaded
    document collections past their expiry window."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT session_id FROM sessions WHERE has_own_docs=1 AND docs_expiry IS NOT NULL"
    )
    rows = cur.fetchall()
    now = datetime.utcnow()
    for (session_id,) in rows:
        session = get_session(session_id)
        expiry = datetime.fromisoformat(session["docs_expiry"])
        if now > expiry:
            try:
                user_chroma_client.delete_collection(f"session_{session_id}")
            except Exception:
                pass
            conn.execute(
                "UPDATE sessions SET has_own_docs=0, docs_expiry=NULL WHERE session_id=?",
                (session_id,),
            )
    conn.commit()
    conn.close()


def retrieve(collection, query, n_results=4):
    query_embedding = embedder.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=n_results)
    return list(zip(results["documents"][0], results["metadatas"][0]))


def extract_text(filename: str, content: bytes) -> str:
    ext = filename.lower().split(".")[-1]
    tmp_path = f"/tmp/{filename}"
    with open(tmp_path, "wb") as f:
        f.write(content)
    try:
        if ext == "pdf":
            reader = PdfReader(tmp_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext == "docx":
            d = docx.Document(tmp_path)
            return "\n".join(p.text for p in d.paragraphs)
        elif ext == "txt":
            return content.decode("utf-8", errors="ignore")
    finally:
        os.remove(tmp_path)
    return ""


# ---------- Routes ----------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(req: ChatRequest):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "Server misconfigured: missing API key")

    cleanup_expired_sessions()
    session = get_session(req.session_id)

    if session["has_own_docs"]:
        limit = OWN_DOCS_QUESTION_LIMIT
        collection = user_chroma_client.get_collection(f"session_{req.session_id}")
        system_prompt = USER_DOCS_SYSTEM_PROMPT
    else:
        limit = FREE_QUESTIONS
        collection = demo_collection
        system_prompt = DEMO_SYSTEM_PROMPT
        if session["question_count"] >= limit and not session["has_lead"]:
            return {
                "limit_reached": True,
                "message": "Free question limit reached — leave your details to try it with your own documents.",
            }

    retrieved = retrieve(collection, req.message)
    context = "\n\n---\n\n".join(f"[Source: {m['source']}]\n{t}" for t, m in retrieved)
    user_message = f"Protocol excerpts:\n\n{context}\n\nQuestion: {req.message}"

    response = anthropic_client.messages.create(
        model="claude-sonnet-5",
        max_tokens=400,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    answer = response.content[0].text

    new_count = session["question_count"] + 1
    upsert_session(req.session_id, question_count=new_count)

    return {
        "limit_reached": False,
        "answer": answer,
        "questions_remaining": max(0, limit - new_count),
        "mode": "own_docs" if session["has_own_docs"] else "demo",
    }


@app.post("/leads")
def capture_lead(req: LeadRequest):
    if not req.consent:
        raise HTTPException(400, "Consent is required to store your details.")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO leads (name, email, practice_name, consent, created_at) VALUES (?, ?, ?, ?, ?)",
        (req.name, req.email, req.practice_name, 1, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    session = get_session(req.session_id)
    upsert_session(req.session_id, has_lead=True)

    return {"status": "ok"}


@app.post("/upload")
async def upload_documents(session_id: str = Form(...), files: List[UploadFile] = File(...)):
    cleanup_expired_sessions()
    session = get_session(session_id)

    if not session["has_lead"]:
        raise HTTPException(403, "Leave your details first before uploading your own documents.")

    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Maximum {MAX_FILES} files allowed.")

    chunks, metadatas, ids = [], [], []
    cid = 0
    for file in files:
        ext = file.filename.lower().split(".")[-1]
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {file.filename}")

        content = await file.read()
        if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(400, f"{file.filename} exceeds {MAX_FILE_SIZE_MB}MB limit.")

        text = extract_text(file.filename, content)
        words = text.split()
        for i in range(0, len(words), 700):
            chunk = " ".join(words[i:i + 800])
            if chunk.strip():
                chunks.append(chunk)
                metadatas.append({"source": file.filename})
                ids.append(f"chunk_{cid}")
                cid += 1

    if not chunks:
        raise HTTPException(400, "No readable text found in uploaded files.")

    collection_name = f"session_{session_id}"
    try:
        user_chroma_client.delete_collection(collection_name)
    except Exception:
        pass
    collection = user_chroma_client.create_collection(collection_name)

    embeddings = embedder.encode(chunks).tolist()
    collection.add(embeddings=embeddings, documents=chunks, metadatas=metadatas, ids=ids)

    expiry = (datetime.utcnow() + timedelta(hours=OWN_DOCS_EXPIRY_HOURS)).isoformat()
    upsert_session(session_id, has_own_docs=True, docs_expiry=expiry, question_count=0)

    return {
        "status": "ok",
        "files_processed": len(files),
        "chunks_indexed": len(chunks),
        "expires_at": expiry,
    }
