from __future__ import annotations
import asyncio, inspect, io, os, re, traceback, uuid, zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from ada_response import AdaResponse, get_ada_model, is_configured
from database import get_job, create_payment, get_latest_payment, save_customer_work, get_latest_work, get_activated_work

DEBUG = os.getenv("ADA_DEBUG_ERRORS", "true").lower() in {"1", "true", "yes", "on"}
MAX_UPLOAD = int(os.getenv("ADA_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
REVIEW_CHUNK_CHARS = int(os.getenv("ADA_REVIEW_CHUNK_CHARS", "7000"))
REVIEW_MIN_CHARS = int(os.getenv("ADA_REVIEW_MIN_CHARS", "2500"))
BASE = Path(__file__).resolve().parent
DOCUMENT_ROOT = BASE / "data" / "documents"
DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}

app = FastAPI(title="Naija Pocket Business Center", version="intelligence-first-v10")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def find_file(name: str):
    for path in (BASE / name, BASE / "app" / name, BASE / "static" / name):
        if path.is_file(): return path
    return None
def clean_text(value: Any) -> str:
    if value is None: return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"```(?:markdown|md|text)?", "", text, flags=re.I).replace("```", "").strip()
def application_error(stage: str, error: Exception | str, status: int = 500, code: str = "APPLICATION_ERROR"):
    print(f"[{stage}] {error}")
    if isinstance(error, Exception): traceback.print_exc()
    return JSONResponse(status_code=status, content={"success": False, "stage": stage, "error": code, "error_message": str(error) if DEBUG else "Error"})
def safe_int(value: Any, default: int | None = None) -> int | None:
    try: return int(str(value).strip())
    except: return default
def persisted_record_to_dict(record: Any) -> dict[str, Any]:
    if record is None: return {}
    if isinstance(record, dict): return dict(record)
    try: return dict(record)
    except: return {}

def save_document_to_storage(job_id_str: str) -> dict[str, Any]:
    job_id_str = str(job_id_str).strip()
    job = _jobs.get(job_id_str)
    if not job: raise RuntimeError(f"Job {job_id_str} not available.")
    job_id = safe_int(job_id_str)
    document_text = clean_text(job.get("document_text", ""))
    version = safe_int(job.get("current_version"), 1) or 1
    job_folder = DOCUMENT_ROOT / job_id_str
    job_folder.mkdir(parents=True, exist_ok=True)
    filepath = job_folder / f"v{version}.txt"
    filepath.write_text(document_text, encoding="utf-8")
    work_id = save_customer_work(job_id=job_id, work_title=job.get("service", "Business Document"), work_type="document", storage_type="local_file", storage_reference=str(filepath), work_status="completed")
    persisted = persisted_record_to_dict(get_latest_work(job_id))
    saved_version = safe_int(persisted.get("version"), version) or version
    version_id = f"{job_id_str}:{saved_version}"
    job["current_version"] = saved_version; job["version_id"] = version_id; job["storage_reference"] = str(filepath); job["work_id"] = safe_int(persisted.get("id"), work_id)
    print(f"[STORAGE] saved job={job_id_str} version={saved_version}")
    return {"success": True, "version": saved_version, "version_id": version_id, "storage_reference": str(filepath), "work_id": job["work_id"]}

def recover_saved_job_for_approval(supplied_job_id: str, supplied_version_id: str) -> dict[str, Any] | None:
    numeric_job_id = safe_int(supplied_job_id)
    if numeric_job_id is None: return None
    persisted_job = persisted_record_to_dict(get_job(numeric_job_id))
    work = persisted_record_to_dict(get_latest_work(numeric_job_id))
    if not work: return None
    saved_version = safe_int(work.get("version"), 1) or 1
    storage_reference = str(work.get("storage_reference") or "").strip()
    filepath = Path(storage_reference)
    # CRITICAL FIX: Rebuild file from DB if Render wiped disk
    if not filepath.exists():
        document_text = clean_text(work.get("document_text", ""))
        if not document_text: document_text = clean_text(persisted_job.get("document_text", ""))
        if not document_text: return None
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(document_text, encoding="utf-8")
        print(f"[APPROVAL] Rebuilt file from DB: {filepath}")
    document_text = clean_text(filepath.read_text(encoding="utf-8"))
    pages = text_to_review_pages(document_text)
    if not pages: return None
    job = {"job_id": supplied_job_id, "customer_id": persisted_job.get("customer_id"), "service": persisted_job.get("service"), "status": "review_complete", "review_finished": True, "progress": {"completed": len(pages), "total": len(pages)}, "document_text": document_text, "document_pages": pages, "review_pages": make_review_pages(pages), "current_version": saved_version, "version_id": f"{supplied_job_id}:{saved_version}", "storage_reference": str(filepath), "work_id": safe_int(work.get("id")), "approved": False}
    _jobs[supplied_job_id] = job
    print(f"[APPROVAL] PERSISTENT RECOVERY SUCCESS job_id={supplied_job_id}")
    return job

def text_to_review_pages(text: str) -> list[dict[str, Any]]:
    text = clean_text(text)
    if not text: return []
    chunks = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [{"page_number": i, "position": i, "content": chunk} for i, chunk in enumerate(chunks, 1)]
def normalize_pages(pages: Any) -> list[dict[str, Any]]:
    if isinstance(pages, list): return [{"page_number": i, "position": i, "content": clean_text(p.get("content", p))} for i, p in enumerate(pages, 1) if isinstance(p, dict)]
    return []
def make_review_pages(pages: Any) -> list[dict[str, Any]]: return [{"page_number": i, "position": i, "status": "queued", "content": page["content"], "review": "", "error": None} for i, page in enumerate(normalize_pages(pages), 1)]
def get_session(customer_id: Any, job_id: Any, service: str | None = None) -> AdaResponse:
    key = f"{customer_id}:{job_id}"
    ada = _sessions.get(key)
    if ada is None: ada = AdaResponse(service=service); _sessions[key] = ada
    return ada

class Chat(BaseModel): message: str = ""; service: str | None = None; event: str | None = None; customer_id: str | None = None; job_id: str | None = None; activate_intelligence: bool = True; context: str | None = None; form_data: dict[str, Any] | None = None; create_work: bool = False; document_pages: list[Any] | None = None; document_text: str | None = None
class Approval(BaseModel): job_id: str; version_id: str

def build_customer_request(request: Chat) -> str: return request.message or ""
def build_context(request: Chat) -> str | None: return request.context

async def _call_method_flexibly(method: Any, kwargs: dict[str, Any]) -> Any: return await asyncio.to_thread(method, **kwargs)
async def create_document_with_intelligence(ada: AdaResponse, request: Chat, customer_request: str, context: str | None) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    result = await _call_method_flexibly(ada.create_document, {"customer_request": customer_request})
    text = clean_text(result)
    pages = text_to_review_pages(text)
    return text, pages, {}

def create_job(job_id: str, request: Chat, original_request: str, document_text: str, pages: Any) -> dict[str, Any]:
    normalized = text_to_review_pages(document_text)
    job = {"job_id": job_id, "customer_id": request.customer_id, "service": request.service, "status": "reviewing", "review_started": True, "review_finished": False, "progress": {"completed": 0, "total": len(normalized)}, "document_text": document_text, "document_pages": normalized, "review_pages": make_review_pages(normalized), "current_version": 1, "version_id": f"{job_id}:1", "approved": False}
    _jobs[job_id] = job
    return job

async def run_review(job_id: str):
    job = _jobs.get(job_id)
    if not job: return
    try:
        total = len(job["document_pages"])
        job["progress"] = {"completed": total, "total": total}
        saved = save_document_to_storage(job_id)
        job["current_version"] = saved["version"]; job["version_id"] = saved["version_id"]; job["storage_reference"] = saved["storage_reference"]; job["work_id"] = saved["work_id"]
        job["status"] = "review_complete"; job["review_finished"] = True
    except Exception as error: job["status"] = "review_error"; traceback.print_exc()

def start_review(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if not job: return False
    _review_tasks[job_id] = asyncio.create_task(run_review(job_id))
    return True

def make_job_response(job: dict[str, Any]) -> dict[str, Any]:
    pages = job["document_pages"]
    return {"success": True, "job_id": job["job_id"], "status": job.get("status"), "version_id": job.get("version_id"), "review_finished": job.get("review_finished"), "progress": job.get("progress"), "total_pages": len(pages), "document_pages": pages, "review_pages": job.get("review_pages"), "document_text": job.get("document_text"), "review_url": f"/review.html?job_id={job['job_id']}"}

def serve_html(filename: str):
    path = find_file(filename)
    if not path: return application_error("PAGE", "Not found", 404)
    return FileResponse(path, media_type="text/html")

@app.get("/") async def root(): return serve_html("index.html")
@app.get("/review.html") async def review_page(): return serve_html("review.html")
@app.get("/payment.html") async def payment_page(): return serve_html("payment.html")
@app.get("/download.html") async def download_page(): return serve_html("download.html")
@app.get("/health") async def health(): return {"success": True, "status": "ok"}

@app.post("/api/upload") async def upload(file: UploadFile = File(...)):
    data = await file.read()
    text = data.decode("utf-8", "replace")
    job_id_value = str(uuid.uuid4())
    pages = text_to_review_pages(text)
    return {"success": True, "job_id": job_id_value, "document_text": text, "document_pages": pages}

@app.post("/api/chat") async def chat(request: Chat):
    job_id = str(request.job_id or "").strip() or str(uuid.uuid4())
    if request.create_work:
        job = create_job(job_id, request, request.message, request.document_text or "", request.document_pages or [])
        start_review(job_id)
        response = make_job_response(job)
        response.update({"created_work": True, "review_started": True})
        return response
    return {"success": True, "job_id": job_id}

@app.get("/api/review") async def get_review(job_id: str):
    job = _jobs.get(job_id)
    if not job: return application_error("REVIEW", "Job not found", 404)
    return make_job_response(job)

@app.get("/api/review/pages") async def get_review_pages(job_id: str):
    job = _jobs.get(job_id)
    if not job: return application_error("REVIEW_PAGES", "Job not found", 404)
    return {"success": True, "job_id": job_id, "version_id": job["version_id"], "document_pages": job["document_pages"], "review_pages": job["review_pages"], "progress": job.get("progress")}

@app.post("/api/approve") async def approve(request: Approval):
    job = _jobs.get(request.job_id)
    if not job: job = recover_saved_job_for_approval(request.job_id, request.version_id)
    if not job: return application_error("APPROVAL", "Job not found", 404)
    job["approved"] = True; job["status"] = "approved"
    job_id_int = safe_int(request.job_id)
    payment_id = create_payment(job_id=job_id_int, amount=5000.0, payment_method="bank_transfer")
    payment_url = f"/payment.html?job_id={request.job_id}&version_id={request.version_id}&payment_id={payment_id}"
    return {"success": True, "payment_url": payment_url}

@app.get("/api/payment/status") async def payment_status(job_id: str):
    payment = get_latest_payment(safe_int(job_id))
    if not payment: return {"success": True, "status": "not_found"}
    return {"success": True, "status": payment.get("status")}

@app.get("/api/download") async def download(work_id: int, version_id: str):
    work = get_activated_work(work_id)
    if not work: return application_error("DOWNLOAD", "Not activated", 403)
    storage_reference = work.get("storage_reference")
    filepath = Path(storage_reference)
    if not filepath.exists():
        document_text = work.get("document_text", "")
        if document_text:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(document_text, encoding="utf-8")
        else: return application_error("DOWNLOAD", "File missing", 404)
    filename = f"document_v{work.get('version')}.txt"
    return FileResponse(filepath, filename=filename, media_type="text/plain")
