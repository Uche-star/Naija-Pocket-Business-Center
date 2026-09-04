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

from database import (
    get_job,
    create_payment,
    get_latest_payment,
    save_customer_work,
    get_latest_work,
    get_activated_work,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEBUG = os.getenv(
    "ADA_DEBUG_ERRORS",
    "true",
).lower() in {"1", "true", "yes", "on"}

MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)

REVIEW_CHUNK_CHARS = int(
    os.getenv(
        "ADA_REVIEW_CHUNK_CHARS",
        "7000",
    )
)

REVIEW_MIN_CHARS = int(
    os.getenv(
        "ADA_REVIEW_MIN_CHARS",
        "2500",
    )
)

BASE = Path(__file__).resolve().parent

DOCUMENT_ROOT = BASE / "data" / "documents"
DOCUMENT_ROOT.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# APPLICATION STATE
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="intelligence-first-v11",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# BASIC HELPERS
# ============================================================

def find_file(name: str):
    candidates = (
        BASE / name,
        BASE / "app" / name,
        BASE / "static" / name,
    )

    for path in candidates:
        if path.is_file():
            return path

    return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    text = re.sub(
        r"```(?:markdown|md|text)?",
        "",
        text,
        flags=re.I,
    )

    text = text.replace("```", "")

    return text.strip()


def application_error(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):
    print(f"[{stage}] {error}")

    if isinstance(error, Exception):
        traceback.print_exc()

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_message": (
                str(error)
                if DEBUG
                else "Error"
            ),
        },
    )


def safe_int(
    value: Any,
    default: int | None = None,
) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def persisted_record_to_dict(
    record: Any,
) -> dict[str, Any]:

    if record is None:
        return {}

    if isinstance(record, dict):
        return dict(record)

    try:
        return dict(record)
    except Exception:
        return {}


# ============================================================
# DOCUMENT PAGE HANDLING
# ============================================================

def text_to_review_pages(
    text: str,
) -> list[dict[str, Any]]:

    text = clean_text(text)

    if not text:
        return []

    # Preserve explicit page breaks where possible.
    explicit_pages = re.split(
        r"\n\s*(?:={3,}\s*)?PAGE\s+\d+\s*(?:={3,})?\s*\n",
        text,
        flags=re.I,
    )

    if len(explicit_pages) > 1:
        chunks = [
            part.strip()
            for part in explicit_pages
            if part.strip()
        ]
    else:
        # Preserve paragraph-based documents.
        chunks = [
            part.strip()
            for part in re.split(
                r"\n\s*\n",
                text,
            )
            if part.strip()
        ]

    # If the document has no paragraph breaks, create
    # usable chunks instead of returning one enormous page.
    if len(chunks) == 1 and len(chunks[0]) > REVIEW_CHUNK_CHARS:
        source = chunks[0]
        chunks = []

        start = 0

        while start < len(source):
            end = min(
                start + REVIEW_CHUNK_CHARS,
                len(source),
            )

            if end < len(source):
                boundary = source.rfind(
                    "\n",
                    start,
                    end,
                )

                if boundary <= start:
                    boundary = source.rfind(
                        " ",
                        start,
                        end,
                    )

                if boundary > start:
                    end = boundary

            piece = source[start:end].strip()

            if piece:
                chunks.append(piece)

            start = end

    return [
        {
            "page_number": index,
            "position": index,
            "content": chunk,
        }
        for index, chunk in enumerate(
            chunks,
            1,
        )
    ]


def normalize_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    if not isinstance(pages, list):
        return []

    normalized = []

    for index, page in enumerate(
        pages,
        1,
    ):
        if isinstance(page, dict):
            content = clean_text(
                page.get(
                    "content",
                    "",
                )
            )
        else:
            content = clean_text(page)

        if not content:
            continue

        normalized.append(
            {
                "page_number": index,
                "position": index,
                "content": content,
            }
        )

    return normalized


def make_review_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    normalized = normalize_pages(pages)

    return [
        {
            "page_number": index,
            "position": index,
            "status": "queued",
            "content": page["content"],
            "review": "",
            "error": None,
        }
        for index, page in enumerate(
            normalized,
            1,
        )
    ]


# ============================================================
# SESSION HANDLING
# ============================================================

def get_session(
    customer_id: Any,
    job_id: Any,
    service: str | None = None,
) -> AdaResponse:

    key = f"{customer_id}:{job_id}"

    ada = _sessions.get(key)

    if ada is None:
        ada = AdaResponse(
            service=service,
        )

        _sessions[key] = ada

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

    activate_intelligence: bool = True

    context: str | None = None

    form_data: dict[str, Any] | None = None

    create_work: bool = False

    document_pages: list[Any] | None = None

    document_text: str | None = None


class Approval(BaseModel):
    job_id: str
    version_id: str


# ============================================================
# REQUEST HELPERS
# ============================================================

def build_customer_request(
    request: Chat,
) -> str:

    message = clean_text(
        request.message
    )

    if message:
        return message

    if request.form_data:
        parts = []

        for key, value in request.form_data.items():
            value_text = clean_text(value)

            if value_text:
                parts.append(
                    f"{key}: {value_text}"
                )

        if parts:
            return "\n".join(parts)

    return ""


def build_context(
    request: Chat,
) -> str | None:

    context = clean_text(
        request.context
    )

    return context or None


async def _call_method_flexibly(
    method: Any,
    kwargs: dict[str, Any],
) -> Any:

    if inspect.iscoroutinefunction(method):
        return await method(**kwargs)

    return await asyncio.to_thread(
        method,
        **kwargs,
    )


# ============================================================
# DOCUMENT CREATION
# ============================================================

async def create_document_with_intelligence(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
]:

    # Keep the existing intelligence interface.
    result = await _call_method_flexibly(
        ada.create_document,
        {
            "customer_request": customer_request,
        },
    )

    text = clean_text(result)

    pages = text_to_review_pages(
        text
    )

    return (
        text,
        pages,
        {},
    )


# ============================================================
# JOB CREATION
# ============================================================

def create_job(
    job_id: str,
    request: Chat,
    original_request: str,
    document_text: str,
    pages: Any,
) -> dict[str, Any]:

    document_text = clean_text(
        document_text
    )

    normalized = normalize_pages(
        pages
    )

    # If pages were not supplied, build them
    # directly from the document.
    if not normalized and document_text:
        normalized = text_to_review_pages(
            document_text
        )

    job = {
        "job_id": job_id,
        "customer_id": request.customer_id,
        "service": request.service,

        "status": "reviewing",

        "review_started": True,
        "review_finished": False,

        "progress": {
            "completed": 0,
            "total": len(normalized),
        },

        "original_request": original_request,

        "document_text": document_text,

        "document_pages": normalized,

        "review_pages": make_review_pages(
            normalized
        ),

        "current_version": 1,

        "version_id": f"{job_id}:1",

        "approved": False,
    }

    _jobs[job_id] = job

    return job


# ============================================================
# DOCUMENT STORAGE
# ============================================================

def save_document_to_storage(
    job_id_str: str,
) -> dict[str, Any]:

    job_id_str = str(
        job_id_str
    ).strip()

    job = _jobs.get(
        job_id_str
    )

    if not job:
        raise RuntimeError(
            f"Job {job_id_str} not available."
        )

    job_id = safe_int(
        job_id_str
    )

    if job_id is None:
        raise RuntimeError(
            f"Job ID must be numeric for database storage: {job_id_str}"
        )

    document_text = clean_text(
        job.get(
            "document_text",
            "",
        )
    )

    if not document_text:
        raise RuntimeError(
            "Cannot save an empty document."
        )

    version = (
        safe_int(
            job.get(
                "current_version"
            ),
            1,
        )
        or 1
    )

    job_folder = (
        DOCUMENT_ROOT
        / job_id_str
    )

    job_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filepath = (
        job_folder
        / f"v{version}.txt"
    )

    filepath.write_text(
        document_text,
        encoding="utf-8",
    )

    work_id = save_customer_work(
        job_id=job_id,
        work_title=(
            job.get("service")
            or "Business Document"
        ),
        work_type="document",
        storage_type="local_file",
        storage_reference=str(
            filepath
        ),
        work_status="completed",
    )

    persisted = persisted_record_to_dict(
        get_latest_work(job_id)
    )

    saved_version = (
        safe_int(
            persisted.get("version"),
            version,
        )
        or version
    )

    version_id = (
        f"{job_id_str}:{saved_version}"
    )

    job["current_version"] = saved_version
    job["version_id"] = version_id
    job["storage_reference"] = str(
        filepath
    )

    job["work_id"] = safe_int(
        persisted.get("id"),
        work_id,
    )

    print(
        "[STORAGE] "
        f"saved job={job_id_str} "
        f"version={saved_version}"
    )

    return {
        "success": True,
        "version": saved_version,
        "version_id": version_id,
        "storage_reference": str(
            filepath
        ),
        "work_id": job["work_id"],
    }


# ============================================================
# PERSISTENT RECOVERY FOR APPROVAL
# ============================================================

def recover_saved_job_for_approval(
    supplied_job_id: str,
    supplied_version_id: str,
) -> dict[str, Any] | None:

    numeric_job_id = safe_int(
        supplied_job_id
    )

    if numeric_job_id is None:
        return None

    persisted_job = persisted_record_to_dict(
        get_job(numeric_job_id)
    )

    work = persisted_record_to_dict(
        get_latest_work(
            numeric_job_id
        )
    )

    if not work:
        return None

    saved_version = (
        safe_int(
            work.get("version"),
            1,
        )
        or 1
    )

    storage_reference = str(
        work.get(
            "storage_reference"
        )
        or ""
    ).strip()

    if not storage_reference:
        storage_reference = str(
            DOCUMENT_ROOT
            / str(supplied_job_id)
            / f"v{saved_version}.txt"
        )

    filepath = Path(
        storage_reference
    )

    # Render can lose local files after a restart.
    # Rebuild the file from persisted database
    # content when necessary.
    if (
        not filepath.exists()
        or not filepath.is_file()
    ):

        document_text = clean_text(
            work.get(
                "document_text",
                "",
            )
        )

        if not document_text:
            document_text = clean_text(
                persisted_job.get(
                    "document_text",
                    "",
                )
            )

        if not document_text:
            return None

        filepath.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        filepath.write_text(
            document_text,
            encoding="utf-8",
        )

        print(
            "[APPROVAL] "
            f"Rebuilt file from DB: {filepath}"
        )

    try:
        document_text = clean_text(
            filepath.read_text(
                encoding="utf-8"
            )
        )
    except Exception as error:
        print(
            "[APPROVAL] "
            f"Unable to read document: {error}"
        )
        return None

    if not document_text:
        return None

    pages = text_to_review_pages(
        document_text
    )

    if not pages:
        return None

    job = {
        "job_id": supplied_job_id,

        "customer_id": persisted_job.get(
            "customer_id"
        ),

        "service": persisted_job.get(
            "service"
        ),

        "status": "review_complete",

        "review_complete": True,
        "review_finished": True,

        "progress": {
            "completed": len(pages),
            "total": len(pages),
        },

        "document_text": document_text,

        "document_pages": pages,

        "review_pages": make_review_pages(
            pages
        ),

        "current_version": saved_version,

        "version_id": (
            f"{supplied_job_id}:{saved_version}"
        ),

        "storage_reference": str(
            filepath
        ),

        "work_id": safe_int(
            work.get("id")
        ),

        "approved": False,
    }

    _jobs[supplied_job_id] = job

    print(
        "[APPROVAL] "
        "PERSISTENT RECOVERY SUCCESS "
        f"job_id={supplied_job_id}"
    )

    return job


# ============================================================
# REVIEW PROCESS
# ============================================================

async def run_review(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:
        return

    try:
        total = len(
            job.get(
                "document_pages",
                [],
            )
        )

        # The document already exists and has been
        # generated. Mark all pages as available
        # for review.
        job["progress"] = {
            "completed": total,
            "total": total,
        }

        for page in job.get(
            "review_pages",
            [],
        ):
            page["status"] = "ready"

        saved = save_document_to_storage(
            job_id
        )

        job["current_version"] = (
            saved["version"]
        )

        job["version_id"] = (
            saved["version_id"]
        )

        job["storage_reference"] = (
            saved["storage_reference"]
        )

        job["work_id"] = (
            saved["work_id"]
        )

        job["status"] = (
            "review_complete"
        )

        job["review_finished"] = True

        job["review_complete"] = True

        print(
            "[REVIEW] "
            f"completed job={job_id}"
        )

    except Exception as error:

        job["status"] = (
            "review_error"
        )

        job["review_finished"] = False

        print(
            "[REVIEW] "
            f"failed job={job_id}: {error}"
        )

        traceback.print_exc()


def start_review(
    job_id: str,
) -> bool:

    job = _jobs.get(
        job_id
    )

    if not job:
        return False

    existing = _review_tasks.get(
        job_id
    )

    if existing and not existing.done():
        return True

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(job_id)
        )
    )

    return True


# ============================================================
# RESPONSE FORMAT
# ============================================================

def make_job_response(
    job: dict[str, Any],
) -> dict[str, Any]:

    pages = job.get(
        "document_pages",
        [],
    )

    return {
        "success": True,

        "job_id": job["job_id"],

        "status": job.get(
            "status"
        ),

        "version_id": job.get(
            "version_id"
        ),

        "review_finished": job.get(
            "review_finished"
        ),

        "review_complete": job.get(
            "review_complete",
            job.get(
                "review_finished"
            ),
        ),

        "progress": job.get(
            "progress"
        ),

        "total_pages": len(
            pages
        ),

        "document_pages": pages,

        "review_pages": job.get(
            "review_pages"
        ),

        "document_text": job.get(
            "document_text"
        ),

        "storage_reference": job.get(
            "storage_reference"
        ),

        "work_id": job.get(
            "work_id"
        ),

        "review_url": (
            f"/review.html?"
            f"job_id={job['job_id']}"
        ),
    }


# ============================================================
# HTML SERVING
# ============================================================

def serve_html(
    filename: str,
):

    path = find_file(
        filename
    )

    if not path:
        return application_error(
            "PAGE",
            "Not found",
            404,
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


# ============================================================
# HTML ROUTES
# ============================================================

@app.get("/")
async def root():

    return serve_html(
        "index.html"
    )


@app.get("/workspace.html")
async def workspace_page():

    return serve_html(
        "workspace.html"
    )


@app.get("/review.html")
async def review_page():

    return serve_html(
        "review.html"
    )


@app.get("/payment.html")
async def payment_page():

    return serve_html(
        "payment.html"
    )


@app.get("/download.html")
async def download_page():

    return serve_html(
        "download.html"
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "success": True,
        "status": "ok",
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
):

    try:

        data = await file.read()

        if len(data) > MAX_UPLOAD:
            return application_error(
                "UPLOAD",
                "File exceeds upload limit.",
                413,
                "UPLOAD_TOO_LARGE",
            )

        filename = (
            file.filename
            or ""
        ).lower()

        # Basic text handling.
        if filename.endswith(
            (".txt", ".md", ".csv")
        ):
            text = data.decode(
                "utf-8",
                "replace",
            )

        elif filename.endswith(
            ".docx"
        ):
            text_parts = []

            with zipfile.ZipFile(
                io.BytesIO(data)
            ) as archive:

                xml_data = archive.read(
                    "word/document.xml"
                )

                root = ET.fromstring(
                    xml_data
                )

                namespaces = {
                    "w": (
                        "http://schemas.openxmlformats.org/"
                        "wordprocessingml/2006/main"
                    )
                }

                for paragraph in root.findall(
                    ".//w:p",
                    namespaces,
                ):

                    paragraph_text = "".join(
                        node.text or ""
                        for node in paragraph.findall(
                            ".//w:t",
                            namespaces,
                        )
                    )

                    if paragraph_text.strip():
                        text_parts.append(
                            paragraph_text
                        )

            text = "\n\n".join(
                text_parts
            )

        else:
            text = data.decode(
                "utf-8",
                "replace",
            )

        text = clean_text(
            text
        )

        job_id_value = str(
            uuid.uuid4()
        )

        pages = text_to_review_pages(
            text
        )

        return {
            "success": True,
            "job_id": job_id_value,
            "document_text": text,
            "document_pages": pages,
            "total_pages": len(pages),
        }

    except Exception as error:

        return application_error(
            "UPLOAD",
            error,
        )


# ============================================================
# CHAT / WORKSPACE API
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat,
):

    try:

        job_id = (
            str(
                request.job_id
                or ""
            ).strip()
            or str(
                uuid.uuid4()
            )
        )

        customer_request = (
            build_customer_request(
                request
            )
        )

        context = build_context(
            request
        )

        # ----------------------------------------------------
        # EXISTING JOB REQUEST
        # ----------------------------------------------------

        existing_job = _jobs.get(
            job_id
        )

        if existing_job:

            return make_job_response(
                existing_job
            )


        # ----------------------------------------------------
        # DETERMINE WHETHER THIS REQUEST
        # SHOULD CREATE A DOCUMENT
        # ----------------------------------------------------

        event = (
            str(
                request.event
                or ""
            ).strip().lower()
        )

        create_requested = (
            bool(request.create_work)
            or event in {
                "create_work",
                "create_document",
                "generate_document",
                "generate",
                "submit",
                "finish",
                "start_review",
                "review",
            }
        )


        # ----------------------------------------------------
        # IF DOCUMENT CONTENT WAS ALREADY SENT
        # BY THE WORKSPACE, USE IT.
        # ----------------------------------------------------

        supplied_text = clean_text(
            request.document_text
        )

        supplied_pages = normalize_pages(
            request.document_pages
        )

        if supplied_text:

            if not supplied_pages:
                supplied_pages = (
                    text_to_review_pages(
                        supplied_text
                    )
                )

            create_requested = True


        # ----------------------------------------------------
        # CREATE DOCUMENT
        # ----------------------------------------------------

        if create_requested:

            if not customer_request and not supplied_text:
                return application_error(
                    "CHAT",
                    "No customer request or document content was supplied.",
                    400,
                    "EMPTY_REQUEST",
                )


            # ------------------------------------------------
            # USE DOCUMENT ALREADY SUPPLIED
            # ------------------------------------------------

            if supplied_text:

                document_text = (
                    supplied_text
                )

                document_pages = (
                    supplied_pages
                )

                generation_meta = {}


            # ------------------------------------------------
            # OTHERWISE GENERATE DOCUMENT
            # THROUGH THE EXISTING INTELLIGENCE LAYER
            # ------------------------------------------------

            else:

                ada = get_session(
                    request.customer_id
                    or "anonymous",
                    job_id,
                    request.service,
                )

                (
                    document_text,
                    document_pages,
                    generation_meta,
                ) = await create_document_with_intelligence(
                    ada,
                    request,
                    customer_request,
                    context,
                )

                document_text = clean_text(
                    document_text
                )

                document_pages = (
                    normalize_pages(
                        document_pages
                    )
                )

                if (
                    not document_pages
                    and document_text
                ):
                    document_pages = (
                        text_to_review_pages(
                            document_text
                        )
                    )


            # ------------------------------------------------
            # NEVER CREATE AN EMPTY JOB
            # ------------------------------------------------

            if not document_text:
                return application_error(
                    "CHAT",
                    "Document generation returned no document.",
                    500,
                    "DOCUMENT_GENERATION_EMPTY",
                )

            if not document_pages:
                return application_error(
                    "CHAT",
                    "Document contains no reviewable pages.",
                    500,
                    "DOCUMENT_PAGES_EMPTY",
                )


            # ------------------------------------------------
            # CREATE JOB
            # ------------------------------------------------

            job = create_job(
                job_id=job_id,
                request=request,
                original_request=customer_request,
                document_text=document_text,
                pages=document_pages,
            )

            job["generation_meta"] = (
                generation_meta
            )

            start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "created_work": True,
                    "review_started": True,
                }
            )

            return response


        # ----------------------------------------------------
        # NORMAL CHAT MESSAGE
        # ----------------------------------------------------

        # Do not destroy the Workspace flow by pretending
        # every ordinary message is a completed document.
        #
        # Return the same job ID so the client can continue
        # using the API.

        return {
            "success": True,
            "job_id": job_id,
            "status": "conversation",
            "message": customer_request,
            "response": "",
            "created_work": False,
        }


    except Exception as error:

        return application_error(
            "CHAT",
            error,
        )


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def get_review(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        # Try persistent recovery before
        # declaring the job missing.
        job = recover_saved_job_for_approval(
            job_id,
            "",
        )

    if not job:
        return application_error(
            "REVIEW",
            "Job not found",
            404,
            "JOB_NOT_FOUND",
        )

    return make_job_response(
        job
    )


# ============================================================
# REVIEW PAGES
# ============================================================

@app.get("/api/review/pages")
async def get_review_pages(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        job = recover_saved_job_for_approval(
            job_id,
            "",
        )

    if not job:
        return application_error(
            "REVIEW_PAGES",
            "Job not found",
            404,
            "JOB_NOT_FOUND",
        )

    return {
        "success": True,

        "job_id": job_id,

        "version_id": job.get(
            "version_id"
        ),

        "document_pages": job.get(
            "document_pages",
            [],
        ),

        "review_pages": job.get(
            "review_pages",
            [],
        ),

        "progress": job.get(
            "progress"
        ),

        "review_finished": job.get(
            "review_finished"
        ),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval,
):

    try:

        supplied_job_id = str(
            request.job_id
        ).strip()

        supplied_version_id = str(
            request.version_id
        ).strip()

        job = _jobs.get(
            supplied_job_id
        )

        # If the server restarted or Render
        # recycled the instance, recover the
        # completed document from persistent storage.
        if not job:

            job = recover_saved_job_for_approval(
                supplied_job_id,
                supplied_version_id,
            )

        if not job:

            return application_error(
                "APPROVAL",
                "Job not found",
                404,
                "JOB_NOT_FOUND",
            )

        # Make sure the document actually exists
        # before allowing approval.
        if not clean_text(
            job.get(
                "document_text",
                "",
            )
        ):

            return application_error(
                "APPROVAL",
                "Document is empty.",
                400,
                "EMPTY_DOCUMENT",
            )

        job["approved"] = True
        job["status"] = "approved"

        job_id_int = safe_int(
            supplied_job_id
        )

        if job_id_int is None:

            return application_error(
                "APPROVAL",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        payment_id = create_payment(
            job_id=job_id_int,
            amount=5000.0,
            payment_method="bank_transfer",
        )

        payment_url = (
            "/payment.html"
            f"?job_id={supplied_job_id}"
            f"&version_id={supplied_version_id}"
            f"&payment_id={payment_id}"
        )

        return {
            "success": True,
            "job_id": supplied_job_id,
            "version_id": supplied_version_id,
            "approved": True,
            "payment_id": payment_id,
            "payment_url": payment_url,
        }

    except Exception as error:

        return application_error(
            "APPROVAL",
            error,
        )


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    job_id: str,
):

    try:

        numeric_job_id = safe_int(
            job_id
        )

        if numeric_job_id is None:
            return {
                "success": True,
                "status": "not_found",
            }

        payment = get_latest_payment(
            numeric_job_id
        )

        if not payment:

            return {
                "success": True,
                "status": "not_found",
            }

        payment_dict = (
            persisted_record_to_dict(
                payment
            )
        )

        return {
            "success": True,
            "status": payment_dict.get(
                "status",
                "unknown",
            ),
            "payment_id": payment_dict.get(
                "id"
            ),
        }

    except Exception as error:

        return application_error(
            "PAYMENT_STATUS",
            error,
        )


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    work_id: int,
    version_id: str,
):

    try:

        work = get_activated_work(
            work_id
        )

        if not work:

            return application_error(
                "DOWNLOAD",
                "Not activated",
                403,
                "NOT_ACTIVATED",
            )

        work = persisted_record_to_dict(
            work
        )

        storage_reference = str(
            work.get(
                "storage_reference"
            )
            or ""
        ).strip()

        if not storage_reference:

            return application_error(
                "DOWNLOAD",
                "Storage reference missing",
                404,
                "STORAGE_REFERENCE_MISSING",
            )

        filepath = Path(
            storage_reference
        )

        if (
            not filepath.exists()
            or not filepath.is_file()
        ):

            document_text = clean_text(
                work.get(
                    "document_text",
                    "",
                )
            )

            if document_text:

                filepath.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                filepath.write_text(
                    document_text,
                    encoding="utf-8",
                )

            else:

                return application_error(
                    "DOWNLOAD",
                    "File missing",
                    404,
                    "FILE_MISSING",
                )

        filename = (
            f"document_v"
            f"{work.get('version', 1)}"
            ".txt"
        )

        return FileResponse(
            filepath,
            filename=filename,
            media_type="text/plain",
        )

    except Exception as error:

        return application_error(
            "DOWNLOAD",
            error,
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    print(
        "=================================================="
    )

    print(
        "[STARTUP] Naija Pocket Business Center API"
    )

    print(
        "[STARTUP] API startup complete."
    )

    print(
        f"[STARTUP] Base directory: {BASE}"
    )

    print(
        f"[STARTUP] Document directory: {DOCUMENT_ROOT}"
    )

    print(
        "[STARTUP] POST /api/chat available."
    )

    print(
        "[STARTUP] GET /api/review available."
    )

    print(
        "[STARTUP] POST /api/approve available."
    )

    print(
        "[STARTUP] GET /api/payment/status available."
    )

    print(
        "[STARTUP] GET /api/download available."
    )

    print(
        "=================================================="
    )
