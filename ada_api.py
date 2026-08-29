from __future__ import annotations

import asyncio
import inspect
import io
import os
import re
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ada_response import AdaResponse, get_ada_model, is_configured


# ============================================================
# CONFIGURATION
# ============================================================

DEBUG = os.getenv("ADA_DEBUG_ERRORS", "true").lower() in {
    "1", "true", "yes", "on"
}

MAX_UPLOAD = int(os.getenv("ADA_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

# This is REVIEW DISPLAY chunking only. It is deliberately not
# used as a customer page-count requirement.
REVIEW_CHUNK_CHARS = int(os.getenv("ADA_REVIEW_CHUNK_CHARS", "7000"))
REVIEW_MIN_CHARS = int(os.getenv("ADA_REVIEW_MIN_CHARS", "2500"))

BASE = Path(__file__).resolve().parent


# ============================================================
# RUNTIME
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="Naija Pocket Business Center", version="intelligence-first-v9")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================

def find_file(name: str):
    for path in (
        BASE / name,
        BASE / "app" / name,
        BASE / "static" / name,
        BASE / "public" / name,
        BASE / "assets" / name,
    ):
        if path.is_file():
            return path
    return None


def event_value(value: Any) -> str:
    return str(value or "").strip().lower()


def job_key(customer_id: Any, job_id: Any) -> str:
    customer = str(customer_id or "anonymous").strip() or "anonymous"
    job = str(job_id or "default").strip() or "default"
    return f"{customer}:{job}"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```(?:markdown|md|text)?", "", text, flags=re.I)
    return text.replace("```", "").strip()


def application_error(stage: str, error: Exception | str, status: int = 500, code: str = "APPLICATION_ERROR"):
    print(f"[{stage}] {type(error).__name__}: {error}")
    if isinstance(error, Exception):
        traceback.print_exc()
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_type": type(error).__name__ if isinstance(error, Exception) else "ApplicationError",
            "error_message": str(error) if DEBUG else "An internal application error occurred.",
        },
    )


# ============================================================
# PAGE NORMALIZATION
# ============================================================

def normalize_document_pages(pages: Any) -> list[dict[str, Any]]:
    if pages is None:
        return []
    if isinstance(pages, str):
        text = clean_text(pages)
        return ([{"page_number": 1, "position": 1, "content": text}] if text else [])
    if not isinstance(pages, list):
        return []

    output: list[dict[str, Any]] = []
    for index, item in enumerate(pages, 1):
        if isinstance(item, dict):
            content = clean_text(
                item.get("content", item.get("text", item.get("document_text", "")))
            )
            if not content:
                continue
            page = dict(item)
            page["page_number"] = index
            page["position"] = index
            page["content"] = content
            output.append(page)
        elif isinstance(item, str):
            content = clean_text(item)
            if content:
                output.append({"page_number": index, "position": index, "content": content})
    return output


def text_to_review_pages(text: str) -> list[dict[str, Any]]:
    """Turn the COMPLETE returned document into review chunks.

    This function never truncates the supplied text and never limits
    the result to one page. If more text exists, more review pages
    are produced.
    """
    text = clean_text(text)
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_length = 0

    for paragraph in paragraphs:
        if len(paragraph) <= REVIEW_CHUNK_CHARS:
            if current and current_length + len(paragraph) + 2 > REVIEW_CHUNK_CHARS:
                flush()
            current.append(paragraph)
            current_length += len(paragraph) + 2
            continue

        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if current and current_length + len(sentence) + 1 > REVIEW_CHUNK_CHARS:
                flush()
            current.append(sentence)
            current_length += len(sentence) + 1

    flush()

    if len(chunks) >= 2 and len(chunks[-1]) < REVIEW_MIN_CHARS:
        if len(chunks[-2]) + len(chunks[-1]) + 2 <= REVIEW_CHUNK_CHARS:
            chunks[-2] += "\n\n" + chunks[-1]
            chunks.pop()

    return [
        {"page_number": i, "position": i, "content": chunk}
        for i, chunk in enumerate(chunks, 1)
        if chunk.strip()
    ]


def normalize_pages_for_review(text: str, supplied_pages: Any = None) -> list[dict[str, Any]]:
    """Use all available document text and never collapse it to page 1.

    If a full document string is available, it is the authoritative
    source for review pagination. Returned page objects are only used
    when no usable full text can be assembled.
    """
    text = clean_text(text)
    if text:
        return text_to_review_pages(text)
    return normalize_document_pages(supplied_pages)


def normalize_pages(pages: Any) -> list[dict[str, Any]]:
    return normalize_document_pages(pages)


# ============================================================
# SESSION
# ============================================================

def get_session(customer_id: Any, job_id: Any, service: str | None = None) -> AdaResponse:
    key = job_key(customer_id, job_id)
    ada = _sessions.get(key)
    if ada is None:
        ada = AdaResponse(service=service)
        _sessions[key] = ada
    elif service:
        setter = getattr(ada, "set_service", None)
        if callable(setter):
            setter(service)
    return ada


# ============================================================
# REQUEST MODELS
# ============================================================

class Chat(BaseModel):
    message: str = ""
    service: str | None = None
    event: str | None = None
    customer_id: str | None = None
    job_id: str | None = None
    client_request_id: str | None = None
    activate_intelligence: bool = True
    context: str | None = None
    form_data: dict[str, Any] | None = None
    guidance_only: bool = False
    create_work: bool = False
    document_pages: list[Any] | None = None
    document_text: str | None = None


class Correction(BaseModel):
    job_id: str
    instruction: str


class Approval(BaseModel):
    job_id: str
    version_id: str


# ============================================================
# CUSTOMER REQUEST
# ============================================================

def build_customer_request(request: Chat) -> str:
    parts: list[str] = []
    if request.service:
        parts.append("SELECTED SERVICE:\n" + request.service.strip())
    if request.form_data:
        information: list[str] = []
        for key, value in request.form_data.items():
            value_text = str(value or "").strip()
            if not value_text:
                continue
            label = str(key).replace("_", " ").strip().title()
            information.append(f"{label}: {value_text}")
        if information:
            parts.append("CUSTOMER PROVIDED SERVICE INFORMATION:\n" + "\n".join(information))
    if request.context and request.context.strip():
        parts.append("ADDITIONAL CUSTOMER CONTEXT:\n" + request.context.strip())
    if request.message and request.message.strip():
        parts.append("CUSTOMER REQUEST:\n" + request.message.strip())
    return "\n\n".join(parts).strip()


def build_context(request: Chat) -> str | None:
    parts: list[str] = []
    if request.context and request.context.strip():
        parts.append(request.context.strip())
    if request.customer_id:
        parts.append("CUSTOMER ID:\n" + request.customer_id)
    if request.client_request_id:
        parts.append("CLIENT REQUEST ID:\n" + request.client_request_id)
    result = "\n\n".join(parts).strip()
    return result or None


# ============================================================
# INTELLIGENCE RESULT EXTRACTION
# ============================================================

_TEXT_KEYS = (
    "document_text", "prepared_work", "generated_document", "generated", "output",
    "document", "content", "text", "reply", "response", "message", "result", "answer",
)
_PAGE_KEYS = ("pages", "document_pages", "prepared_pages", "content_pages")


def _extract_from_value(value: Any, depth: int = 0) -> tuple[str, list[dict[str, Any]]]:
    if depth > 4 or value is None:
        return "", []

    if isinstance(value, str):
        text = clean_text(value)
        return text, text_to_review_pages(text) if text else []

    if isinstance(value, list):
        pages = normalize_pages(value)
        if pages:
            text = "\n\n".join(p["content"] for p in pages)
            return text, text_to_review_pages(text)
        return "", []

    if isinstance(value, dict):
        # Prefer explicit full-document fields before generic reply fields.
        for key in _TEXT_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and clean_text(candidate):
                text = clean_text(candidate)
                return text, text_to_review_pages(text)
        for key in _PAGE_KEYS:
            candidate = value.get(key)
            text, pages = _extract_from_value(candidate, depth + 1)
            if text or pages:
                return text, pages
        for key, candidate in value.items():
            if key in _TEXT_KEYS or key in _PAGE_KEYS:
                continue
            text, pages = _extract_from_value(candidate, depth + 1)
            if text or pages:
                return text, pages
        return "", []

    # Handle Pydantic/dataclass/custom result objects without requiring
    # one rigid response schema.
    try:
        data = vars(value)
    except Exception:
        data = None
    if isinstance(data, dict):
        return _extract_from_value(data, depth + 1)

    for key in _TEXT_KEYS + _PAGE_KEYS:
        try:
            candidate = getattr(value, key, None)
        except Exception:
            candidate = None
        text, pages = _extract_from_value(candidate, depth + 1)
        if text or pages:
            return text, pages

    return "", []


def extract_complete_document(result: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    text, pages = _extract_from_value(result)
    if not text:
        raise ValueError("The intelligence completed the operation but returned no usable document content.")

    # IMPORTANT: the text is authoritative. Rebuild review pages from
    # the complete text instead of trusting a single page object.
    pages = text_to_review_pages(text)
    if not pages:
        raise ValueError("Usable document text was returned but no review pages could be constructed.")

    metadata: dict[str, Any] = {}
    if isinstance(result, dict):
        metadata = {k: v for k, v in result.items() if k not in _TEXT_KEYS + _PAGE_KEYS}
    return text, pages, metadata


# ============================================================
# DOCUMENT CREATION CALLER
# ============================================================

async def _call_method_flexibly(method: Any, kwargs: dict[str, Any]) -> Any:
    """Call an intelligence method without throwing away a valid result
    because its signature uses fewer parameters.
    """
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return await asyncio.to_thread(method, **kwargs)

    parameters = signature.parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    if accepts_kwargs:
        call_kwargs = kwargs
    else:
        call_kwargs = {k: v for k, v in kwargs.items() if k in parameters}
    return await asyncio.to_thread(method, **call_kwargs)


async def create_document_with_intelligence(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    kwargs = {
        "customer_request": customer_request,
        "service": request.service,
        "form_data": request.form_data,
        "context": context,
        "event": request.event,
        "message": customer_request,
        "original_request": customer_request,
        "create_work": True,
    }

    attempted: list[str] = []
    for method_name in ("create_document", "generate_document", "create_work", "generate_work"):
        method = getattr(ada, method_name, None)
        if not callable(method):
            continue
        attempted.append(method_name)
        result = await _call_method_flexibly(method, kwargs)
        try:
            return extract_complete_document(result)
        except ValueError:
            # A method exists but did not return a document. Continue to
            # another explicit document-generation method before falling back
            # to ordinary chat.
            continue

    respond = getattr(ada, "respond", None)
    if callable(respond):
        result = await _call_method_flexibly(
            respond,
            {
                "message": customer_request,
                "service": request.service,
                "event": request.event,
                "context": context,
            },
        )
        return extract_complete_document(result)

    raise AttributeError(
        "AdaResponse has no usable document creation method. "
        f"Methods checked: {', '.join(attempted) or 'none'}."
    )


# ============================================================
# JOBS / REVIEW
# ============================================================

def make_review_pages(pages: Any) -> list[dict[str, Any]]:
    normalized = normalize_pages(pages)
    return [
        {
            "page_number": i,
            "position": i,
            "status": "queued",
            "content": page["content"],
            "review": "",
            "error": None,
        }
        for i, page in enumerate(normalized, 1)
    ]


def synchronize_job_document(job: dict[str, Any]) -> None:
    pages = normalize_pages(job.get("document_pages", []))
    job["document_pages"] = pages
    if len(job.get("review_pages", [])) != len(pages):
        job["review_pages"] = make_review_pages(pages)
    job["total_pages"] = len(pages)


def create_job(job_id: str, request: Chat, original_request: str, document_text: str, pages: Any) -> dict[str, Any]:
    # Rebuild pages from the complete text. This is the critical API-side
    # protection against a valid multi-page document being represented as
    # one page merely because the intelligence returned one page object.
    document_text = clean_text(document_text)
    normalized = text_to_review_pages(document_text)
    if not normalized:
        normalized = normalize_pages(pages)
    if not normalized:
        raise ValueError("No complete document content was returned by intelligence.")

    job = {
        "job_id": job_id,
        "customer_id": request.customer_id,
        "service": request.service,
        "original_request": original_request,
        "context": build_context(request),
        "status": "reviewing",
        "review_started": True,
        "review_finished": False,
        "review_error": None,
        "progress": {"completed": 0, "total": len(normalized)},
        "document_text": document_text,
        "document_pages": normalized,
        "review_pages": make_review_pages(normalized),
        "assembled_review": "",
        "current_version": 1,
        "version_id": f"{job_id}:1",
        "approved": False,
        "paid": False,
    }
    _jobs[job_id] = job
    print(f"[JOB] created job={job_id} document_chars={len(document_text)} total_pages={len(normalized)}")
    return job


def review_callback(job_id: str):
    def callback(update: dict[str, Any]):
        job = _jobs.get(job_id)
        if not job:
            return
        update_type = event_value(update.get("type"))
        page_number = str(update.get("page_number", ""))
        if update_type == "page_started":
            for page in job["review_pages"]:
                if str(page["page_number"]) == page_number:
                    page["status"] = "reviewing"
        elif update_type == "page_completed":
            for page in job["review_pages"]:
                if str(page["page_number"]) != page_number:
                    continue
                page["status"] = "reviewed"
                page["review"] = clean_text(update.get("review", ""))
                if update.get("content") is not None:
                    page["content"] = clean_text(update.get("content"))
                page["error"] = None
            try:
                completed = int(update.get("position", 0))
            except Exception:
                completed = 0
            if completed:
                job["progress"]["completed"] = min(completed, len(job["document_pages"]))
        elif update_type == "page_error":
            for page in job["review_pages"]:
                if str(page["page_number"]) == page_number:
                    page["status"] = "error"
                    page["error"] = str(update.get("error", "Page review failed."))
        elif update_type == "review_completed":
            total = len(job["document_pages"])
            job["status"] = "review_complete"
            job["review_finished"] = True
            job["progress"] = {"completed": total, "total": total}
            job["assembled_review"] = clean_text(update.get("assembled_review", ""))
    return callback


async def run_review(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return
    try:
        ada = get_session(job.get("customer_id"), job_id, job.get("service"))
        pages = normalize_pages(job["document_pages"])
        method = getattr(ada, "review_document_pages", None)
        if not callable(method):
            raise AttributeError("AdaResponse has no review_document_pages() method.")

        result = await _call_method_flexibly(
            method,
            {
                "pages": pages,
                "service": job.get("service"),
                "context": job.get("context"),
                "customer_request": job.get("original_request"),
                "event": "send_for_review",
                "progress_callback": review_callback(job_id),
            },
        )

        if isinstance(result, dict):
            returned_pages = normalize_pages(result.get("pages", []))
            if returned_pages:
                # Review may update content, but do not permit it to collapse
                # a complete multi-page document to a single page.
                returned_text = "\n\n".join(p["content"] for p in returned_pages)
                if returned_text:
                    job["document_text"] = returned_text
                    job["document_pages"] = text_to_review_pages(returned_text)
                    job["review_pages"] = make_review_pages(job["document_pages"])
            job["assembled_review"] = clean_text(
                result.get("assembled_review", job.get("assembled_review", ""))
            )

        total = len(job["document_pages"])
        job["progress"] = {"completed": total, "total": total}
        job["status"] = "review_complete"
        job["review_started"] = True
        job["review_finished"] = True
        job["review_error"] = None
        print(f"[REVIEW] job={job_id} total_pages={total}")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        job["status"] = "review_error"
        job["review_finished"] = True
        job["review_error"] = {"type": type(error).__name__, "message": str(error)}
        traceback.print_exc()


def start_review(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if not job or not job.get("document_pages") or job.get("status") != "reviewing":
        return False
    existing = _review_tasks.get(job_id)
    if existing and not existing.done():
        return False
    _review_tasks[job_id] = asyncio.create_task(run_review(job_id))
    return True


def make_job_response(job: dict[str, Any]) -> dict[str, Any]:
    synchronize_job_document(job)
    pages = job["document_pages"]
    progress = job.get("progress", {})
    return {
        "success": True,
        "job_id": job["job_id"],
        "customer_id": job.get("customer_id"),
        "service": job.get("service"),
        "status": job.get("status"),
        "current_version": job.get("current_version", 1),
        "version_id": job.get("version_id"),
        "review_started": job.get("review_started", False),
        "review_finished": job.get("review_finished", False),
        "approved": job.get("approved", False),
        "paid": job.get("paid", False),
        "progress": {"completed": int(progress.get("completed", 0)), "total": len(pages)},
        "total_pages": len(pages),
        "document_pages": pages,
        "pages": pages,
        "review_pages": job.get("review_pages", []),
        "document_text": job.get("document_text", ""),
        "assembled_review": job.get("assembled_review", ""),
        "error": job.get("review_error"),
        "review_url": f"/review.html?job_id={job['job_id']}",
    }


# ============================================================
# DOCUMENT EXTRACTION / UPLOAD
# ============================================================

def extract_document(data: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".csv"}:
        return data.decode("utf-8", "replace")
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix in {".docx", ".xlsx", ".pptx"}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if suffix == ".docx":
                names = ["word/document.xml"] if "word/document.xml" in names else []
            elif suffix == ".pptx":
                names = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml", n)]
            else:
                names = [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml", n)]
            texts: list[str] = []
            for name in sorted(names):
                root = ET.fromstring(archive.read(name))
                values = [
                    element.text or ""
                    for element in root.iter()
                    if isinstance(element.tag, str) and element.tag.rsplit("}", 1)[-1] == "t"
                ]
                if values:
                    texts.append(" ".join(values))
            return "\n\n".join(texts)
    raise RuntimeError(f"Unsupported document type: {suffix or 'unknown'}")


def uploaded_document_pages(filename: str, data: bytes) -> list[dict[str, Any]]:
    text = clean_text(extract_document(data, filename))
    if not text:
        raise ValueError("The uploaded document contains no extractable text.")
    return text_to_review_pages(text)


# ============================================================
# HTML / HEALTH
# ============================================================


def serve_html(filename: str):
    path = find_file(filename)
    if not path:
        return application_error("PAGE", f"{filename} was not found.", 404, "HTML_NOT_FOUND")
    return FileResponse(path, media_type="text/html")


@app.get("/")
async def root(): return serve_html("index.html")

@app.get("/index.html")
async def index(): return serve_html("index.html")

@app.get("/conversation.html")
async def conversation(): return serve_html("conversation.html")

@app.get("/workspace.html")
async def workspace(): return serve_html("workspace.html")

@app.get("/review.html")
async def review_page(): return serve_html("review.html")

@app.get("/payment.html")
async def payment_page(): return serve_html("payment.html")

@app.get("/download.html")
async def download_page(): return serve_html("download.html")


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "ok",
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
        "architecture": "intelligence-first",
    }


@app.get("/api/status")
async def api_status():
    return {
        "success": True,
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
        "active_sessions": len(_sessions),
        "active_jobs": len(_jobs),
        "architecture": "intelligence-first",
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):
    try:
        data = await file.read()
        if not data:
            return application_error("UPLOAD", "The uploaded file is empty.", 400, "EMPTY_FILE")
        if len(data) > MAX_UPLOAD:
            return application_error("UPLOAD", "The uploaded document is too large.", 413, "FILE_TOO_LARGE")
        filename = file.filename or "document"
        pages = await asyncio.to_thread(uploaded_document_pages, filename, data)
        job_id_value = str(job_id or "").strip() or str(uuid.uuid4())
        text = "\n\n".join(p["content"] for p in pages)
        return {
            "success": True,
            "filename": filename,
            "job_id": job_id_value,
            "customer_id": customer_id,
            "client_request_id": client_request_id,
            "service": service,
            "document_text": text,
            "total_pages": len(pages),
            "document_pages": pages,
            "pages": pages,
        }
    except Exception as error:
        return application_error("UPLOAD", error, 400, "DOCUMENT_UPLOAD_ERROR")


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(request: Chat):
    if not request.activate_intelligence:
        return application_error("INTELLIGENCE", "Intelligence activation is disabled.", 400, "INTELLIGENCE_NOT_ACTIVATED")
    if not is_configured():
        return application_error("INTELLIGENCE", "Intelligence is not configured.", 503, "INTELLIGENCE_NOT_CONFIGURED")

    job_id = str(request.job_id or "").strip() or str(uuid.uuid4())
    context = build_context(request)
    customer_request = build_customer_request(request)
    pages = normalize_pages(request.document_pages)
    document_text = clean_text(request.document_text)
    if not document_text and pages:
        document_text = "\n\n".join(p["content"] for p in pages)

    try:
        ada = get_session(request.customer_id, job_id, request.service)

        if request.guidance_only:
            if not request.message.strip():
                return application_error("GUIDANCE", "The guidance message is empty.", 400, "EMPTY_GUIDANCE_MESSAGE")
            reply = await _call_method_flexibly(
                ada.respond,
                {"message": request.message.strip(), "service": request.service, "event": request.event, "context": context},
            )
            return {"success": True, "reply": clean_text(reply), "job_id": job_id, "created_work": False}

        create_requested = request.create_work or event_value(request.event) in {
            "form_submitted_create_work", "create_work", "create_document", "submit_service", "service_submitted"
        }

        if create_requested:
            if document_text or pages:
                complete_text = document_text or "\n\n".join(p["content"] for p in pages)
                job = create_job(job_id, request, customer_request, complete_text, pages)
            else:
                if not customer_request:
                    return application_error("WORK_CREATION", "The customer request contains no usable information.", 400, "EMPTY_WORK_REQUEST")
                complete_text, created_pages, metadata = await create_document_with_intelligence(
                    ada, request, customer_request, context
                )
                print(f"[PAG-INPUT] document_text_chars={len(complete_text)}")
                print(f"[PAG-INPUT] generated_pages={len(created_pages)}")
                job = create_job(job_id, request, customer_request, complete_text, created_pages)
                job["intelligence_metadata"] = metadata

            started = start_review(job_id)
            response = make_job_response(job)
            response.update({
                "reply": "Your work has been prepared and sent for review.",
                "created_work": True,
                "work_created": True,
                "review_started": started,
            })
            return response

        if pages or document_text:
            complete_text = document_text or "\n\n".join(p["content"] for p in pages)
            existing_job = _jobs.get(job_id)
            if existing_job:
                existing_job["document_text"] = complete_text
                existing_job["document_pages"] = text_to_review_pages(complete_text)
                existing_job["review_pages"] = make_review_pages(existing_job["document_pages"])
                existing_job["progress"] = {"completed": 0, "total": len(existing_job["document_pages"])}
                existing_job["status"] = "reviewing"
                existing_job["review_started"] = True
                existing_job["review_finished"] = False
                existing_job["review_error"] = None
                existing_job["approved"] = False
                existing_job["paid"] = False
                job = existing_job
            else:
                job = create_job(job_id, request, customer_request, complete_text, pages)
            started = start_review(job_id)
            response = make_job_response(job)
            response.update({
                "reply": "Your document has been received and is being reviewed.",
                "created_work": True,
                "review_started": started,
            })
            return response

        if not request.message.strip():
            return application_error("CHAT", "The chat message is empty.", 400, "EMPTY_MESSAGE")
        reply = await _call_method_flexibly(
            ada.respond,
            {"message": request.message.strip(), "service": request.service, "event": request.event, "context": context},
        )
        return {
            "success": True,
            "reply": clean_text(reply),
            "job_id": job_id,
            "service": request.service or getattr(ada, "service", None),
            "created_work": False,
        }
    except Exception as error:
        return application_error("CHAT", error, 500, "CHAT_ERROR")


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def get_review(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return application_error("REVIEW", "The requested review job does not exist.", 404, "JOB_NOT_FOUND")
    start_review(job_id)
    return make_job_response(job)


@app.get("/api/review/pages")
async def get_review_pages(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return application_error("REVIEW_PAGES", "The requested review job does not exist.", 404, "JOB_NOT_FOUND")
    start_review(job_id)
    synchronize_job_document(job)
    return {
        "success": True,
        "job_id": job_id,
        "current_version": job["current_version"],
        "version_id": job["version_id"],
        "status": job["status"],
        "total_pages": len(job["document_pages"]),
        "document_text": job.get("document_text", ""),
        "pages": job["document_pages"],
        "document_pages": job["document_pages"],
        "review_pages": job["review_pages"],
        "progress": job["progress"],
        "approved": job["approved"],
        "paid": job["paid"],
    }


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(request: Correction):
    job = _jobs.get(request.job_id)
    if not job:
        return application_error("CORRECTION", "Job not found.", 404, "JOB_NOT_FOUND")
    instruction = request.instruction.strip()
    if not instruction:
        return application_error("CORRECTION", "Correction instruction is empty.", 400, "EMPTY_CORRECTION")
    if job.get("status") in {"reviewing", "correcting"}:
        return application_error("CORRECTION", "The document is still being processed.", 409, "DOCUMENT_STILL_PROCESSING")
    if not job.get("document_pages"):
        return application_error("CORRECTION", "There is no document available for correction.", 409, "NO_DOCUMENT")

    job["current_version"] += 1
    job["version_id"] = f"{request.job_id}:{job['current_version']}"
    job.update({
        "status": "correcting", "approved": False, "paid": False,
        "review_started": False, "review_finished": False, "review_error": None,
        "correction_instruction": instruction,
        "progress": {"completed": 0, "total": len(job["document_pages"])},
    })

    async def correction_worker():
        try:
            ada = get_session(job.get("customer_id"), request.job_id, job.get("service"))
            method = getattr(ada, "correct_document", None)
            if not callable(method):
                raise AttributeError("AdaResponse has no correct_document() method.")
            result = await _call_method_flexibly(
                method,
                {
                    "document_pages": normalize_pages(job["document_pages"]),
                    "correction": instruction,
                    "service": job.get("service"),
                    "context": job.get("context"),
                    "progress_callback": None,
                },
            )
            corrected_text, corrected_pages, metadata = extract_complete_document(result)
            job["document_text"] = corrected_text
            job["document_pages"] = corrected_pages
            job["review_pages"] = make_review_pages(corrected_pages)
            job["intelligence_metadata"] = metadata
            job["status"] = "reviewing"
            job["review_started"] = True
            job["review_finished"] = False
            job["review_error"] = None
            job["progress"] = {"completed": 0, "total": len(corrected_pages)}
            start_review(request.job_id)
        except Exception as error:
            job["status"] = "correction_error"
            job["review_error"] = {"type": type(error).__name__, "message": str(error)}
            traceback.print_exc()

    old_task = _correction_tasks.get(request.job_id)
    if old_task and not old_task.done():
        old_task.cancel()
    _correction_tasks[request.job_id] = asyncio.create_task(correction_worker())

    return {
        "success": True,
        "job_id": request.job_id,
        "status": "correcting",
        "version_id": job["version_id"],
        "current_version": job["current_version"],
        "message": "Correction has started. The corrected document will be reviewed again.",
    }


# ============================================================
# APPROVAL / PAYMENT / DOWNLOAD
# ============================================================

@app.post("/api/approve")
async def approve(request: Approval):
    job = _jobs.get(request.job_id)
    if not job:
        return application_error("APPROVAL", "Job not found.", 404, "JOB_NOT_FOUND")
    if request.version_id != job["version_id"]:
        return application_error("APPROVAL", "The supplied document version does not match.", 409, "VERSION_MISMATCH")
    if job["status"] != "review_complete":
        return application_error("APPROVAL", "The document review is not complete.", 409, "REVIEW_NOT_COMPLETE")
    job["approved"] = True
    job["status"] = "approved"
    return {
        "success": True, "job_id": request.job_id, "version_id": request.version_id,
        "current_version": job["current_version"], "approved": True, "status": "approved",
        "total_pages": len(job["document_pages"]), "pages": job["document_pages"],
        "payment_url": f"/payment.html?job_id={request.job_id}&version_id={request.version_id}",
    }


@app.post("/api/payment/complete")
async def payment_complete(job_id: str, version_id: str):
    job = _jobs.get(job_id)
    if not job:
        return application_error("PAYMENT", "Job not found.", 404, "JOB_NOT_FOUND")
    if version_id != job["version_id"]:
        return application_error("PAYMENT", "Version mismatch.", 409, "VERSION_MISMATCH")
    if not job["approved"]:
        return application_error("PAYMENT", "The document must be approved before payment.", 409, "DOCUMENT_NOT_APPROVED")
    job["paid"] = True
    job["status"] = "paid"
    return {
        "success": True, "job_id": job_id, "version_id": version_id, "paid": True, "status": "paid",
        "total_pages": len(job["document_pages"]),
        "download_url": f"/download.html?job_id={job_id}&version_id={version_id}",
        "api_download_url": f"/api/download?job_id={job_id}&version_id={version_id}",
    }


@app.get("/api/payment")
async def payment_state(job_id: str, version_id: str):
    job = _jobs.get(job_id)
    if not job:
        return application_error("PAYMENT_STATE", "Job not found.", 404, "JOB_NOT_FOUND")
    if version_id != job["version_id"]:
        return application_error("PAYMENT_STATE", "Version mismatch.", 409, "VERSION_MISMATCH")
    return {
        "success": True, "job_id": job_id, "version_id": version_id,
        "status": job["status"], "approved": job["approved"], "paid": job["paid"],
        "total_pages": len(job["document_pages"]), "payment_complete": job["paid"],
    }


@app.get("/api/download")
async def download(job_id: str, version_id: str):
    job = _jobs.get(job_id)
    if not job:
        return application_error("DOWNLOAD", "Job not found.", 404, "JOB_NOT_FOUND")
    if version_id != job["version_id"]:
        return application_error("DOWNLOAD", "Version mismatch.", 409, "VERSION_MISMATCH")
    if not job["approved"]:
        return application_error("DOWNLOAD", "The current document version has not been approved.", 409, "DOCUMENT_NOT_APPROVED")
    if not job["paid"]:
        return application_error("DOWNLOAD", "Payment for the current document version has not been completed.", 409, "PAYMENT_NOT_COMPLETED")
    return {
        "success": True, "job_id": job_id, "version_id": version_id, "status": "paid",
        "total_pages": len(job["document_pages"]), "pages": job["document_pages"],
        "document_pages": job["document_pages"], "document_text": job.get("document_text", ""),
        "message": "The approved and paid document is ready for final document generation.",
    }


# ============================================================
# CLEAR CHAT / STARTUP
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(customer_id: str | None = None, job_id: str | None = None):
    ada = _sessions.get(job_key(customer_id, job_id))
    if ada:
        clear_method = getattr(ada, "clear_history", None)
        if callable(clear_method):
            clear_method()
    return {"success": True, "message": "Conversation cleared."}


@app.on_event("startup")
async def startup():
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER — FASTAPI")
    print("Architecture: INTELLIGENCE-FIRST")
    print("Intelligence:", get_ada_model())
    print("Configured:", is_configured())
    print("Customer page-count requirement: DISABLED")
    print("Global page assumption: DISABLED")
    print("Complete document preservation: ENABLED")
    print("Review workflow: ENABLED")
    print("Review pagination source: COMPLETE DOCUMENT TEXT")
    print("=" * 70)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ada_api:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
