"""
Naija Pocket Business Center
ADA API
============================================================

FASTAPI APPLICATION
DOCUMENT CREATION + DOCUMENT REVIEW INTELLIGENCE

Customer
    ↓
Workspace
    ↓
Service form
    ↓
FastAPI
    ↓
AdaResponse
    ↓
Prepared customer work
    ↓
Document pages
    ↓
Review job
    ↓
Page-by-page review
    ↓
review.html
    ↓
Correction
    ↓
AdaResponse
    ↓
Re-review
    ↓
Approval
    ↓
Payment / Download

IMPORTANT
---------
FastAPI owns workflow state and document transport.

AdaResponse owns intelligence and reasoning.

There is NO keyword intelligence in this file.
"""

from __future__ import annotations

import asyncio
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

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
    normalize_document_pages,
    document_text_to_pages,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEBUG_ERRORS = (
    os.getenv("ADA_DEBUG_ERRORS", "true")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

MAX_UPLOAD_BYTES = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent


def find_file(filename: str) -> Path | None:
    candidates = (
        BASE_DIR / filename,
        BASE_DIR / "app" / filename,
        BASE_DIR / "static" / filename,
        BASE_DIR / "public" / filename,
        BASE_DIR / "assets" / filename,
    )

    for path in candidates:
        if path.is_file():
            return path

    return None


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="review-intelligence-v5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# SESSION STORAGE
# ============================================================

_sessions: dict[str, AdaResponse] = {}


def session_key(
    customer_id: str | None,
    job_id: str | None,
) -> str:

    customer = (
        str(customer_id or "anonymous").strip()
        or "anonymous"
    )

    job = (
        str(job_id or "default").strip()
        or "default"
    )

    return f"{customer}:{job}"


def get_session(
    customer_id: str | None,
    job_id: str | None,
    service: str | None = None,
) -> AdaResponse:

    key = session_key(
        customer_id,
        job_id,
    )

    session = _sessions.get(key)

    if session is None:
        session = AdaResponse(
            service=service
        )
        _sessions[key] = session

    elif service:
        session.set_service(service)

    return session


# ============================================================
# JOB STORAGE
# ============================================================

_jobs: dict[str, dict[str, Any]] = {}

_review_tasks: dict[str, asyncio.Task] = {}

_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

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


class CorrectionRequest(BaseModel):

    job_id: str

    instruction: str


class ApprovalRequest(BaseModel):

    job_id: str

    version_id: str


# ============================================================
# EVENTS
# ============================================================

REVIEW_EVENTS = {
    "review",
    "review_requested",
    "send_for_review",
    "review_document",
    "review_called",
    "form_submitted_create_work",
    "document_uploaded",
}


# ============================================================
# EVENT HELPERS
# ============================================================

def normalized_event(
    event: str | None,
) -> str:

    return str(
        event or ""
    ).strip().lower()


def is_work_creation_event(
    request: ChatRequest,
) -> bool:

    event = normalized_event(
        request.event
    )

    return bool(
        request.create_work
        or event == "form_submitted_create_work"
    )


# ============================================================
# ERROR HANDLING
# ============================================================

def error_response(
    *,
    stage: str,
    error: Exception | str,
    status_code: int = 500,
    error_code: str = "APPLICATION_ERROR",
):

    error_type = (
        type(error).__name__
        if isinstance(error, Exception)
        else "Error"
    )

    error_message = str(error)

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER ERROR")
    print("=" * 78)
    print("Stage:", stage)
    print("Type:", error_type)
    print("Message:", error_message)
    print("=" * 78)

    if isinstance(error, Exception):
        traceback.print_exc()

    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "stage": stage,
            "error": error_code,
            "error_type": error_type,
            "error_message": (
                error_message
                if DEBUG_ERRORS
                else "An internal application error occurred."
            ),
        },
    )


# ============================================================
# CUSTOMER REQUEST
# ============================================================

def build_customer_request(
    *,
    message: str,
    service: str | None,
    form_data: dict[str, Any] | None,
    context: str | None,
) -> str:

    parts: list[str] = []

    if service:
        parts.append(
            "SELECTED SERVICE:\n"
            + str(service).strip()
        )

    if form_data:

        lines: list[str] = []

        for key, value in form_data.items():

            value_text = str(
                value or ""
            ).strip()

            if not value_text:
                continue

            label = (
                str(key)
                .replace("_", " ")
                .strip()
                .title()
            )

            lines.append(
                f"{label}: {value_text}"
            )

        if lines:
            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(lines)
            )

    if context:

        context_text = str(
            context
        ).strip()

        if context_text:
            parts.append(
                "ADDITIONAL CONTEXT:\n"
                + context_text
            )

    message_text = str(
        message or ""
    ).strip()

    if message_text:
        parts.append(
            "CUSTOMER REQUEST:\n"
            + message_text
        )

    return "\n\n".join(parts).strip()


def build_application_context(
    request: ChatRequest,
) -> str | None:

    parts: list[str] = []

    if request.context:

        value = str(
            request.context
        ).strip()

        if value:
            parts.append(value)

    if request.customer_id:

        parts.append(
            "CUSTOMER ID:\n"
            + str(request.customer_id)
        )

    if request.client_request_id:

        parts.append(
            "CLIENT REQUEST ID:\n"
            + str(request.client_request_id)
        )

    return "\n\n".join(parts) or None


# ============================================================
# DOCUMENT TEXT EXTRACTION
# ============================================================

def extract_docx_text(
    data: bytes,
) -> str:

    with zipfile.ZipFile(
        io.BytesIO(data)
    ) as archive:

        if "word/document.xml" not in archive.namelist():
            raise ValueError(
                "The DOCX file does not contain a valid document."
            )

        root = ET.fromstring(
            archive.read(
                "word/document.xml"
            )
        )

        paragraphs: list[str] = []

        for paragraph in root.iter():

            tag = paragraph.tag

            if isinstance(tag, str):
                tag = tag.rsplit("}", 1)[-1]

            if tag != "p":
                continue

            text_parts: list[str] = []

            for child in paragraph.iter():

                child_tag = child.tag

                if isinstance(child_tag, str):
                    child_tag = child_tag.rsplit(
                        "}",
                        1,
                    )[-1]

                if child_tag == "t":
                    text_parts.append(
                        child.text or ""
                    )

                elif child_tag == "tab":
                    text_parts.append("\t")

            text = "".join(
                text_parts
            ).strip()

            if text:
                paragraphs.append(text)

        return "\n\n".join(paragraphs)


def extract_xlsx_text(
    data: bytes,
) -> str:

    with zipfile.ZipFile(
        io.BytesIO(data)
    ) as archive:

        shared_strings: list[str] = []

        if "xl/sharedStrings.xml" in archive.namelist():

            root = ET.fromstring(
                archive.read(
                    "xl/sharedStrings.xml"
                )
            )

            for item in root:

                parts: list[str] = []

                for child in item.iter():

                    tag = child.tag

                    if isinstance(tag, str):
                        tag = tag.rsplit("}", 1)[-1]

                    if tag == "t":
                        parts.append(
                            child.text or ""
                        )

                shared_strings.append(
                    "".join(parts)
                )

        rows: list[str] = []

        sheet_names = [
            name
            for name in archive.namelist()
            if re.match(
                r"xl/worksheets/sheet\d+\.xml$",
                name,
            )
        ]

        for sheet_name in sorted(sheet_names):

            root = ET.fromstring(
                archive.read(sheet_name)
            )

            for row in root.iter():

                tag = row.tag

                if isinstance(tag, str):
                    tag = tag.rsplit("}", 1)[-1]

                if tag != "row":
                    continue

                values: list[str] = []

                for cell in row:

                    cell_tag = cell.tag

                    if isinstance(cell_tag, str):
                        cell_tag = cell_tag.rsplit(
                            "}",
                            1,
                        )[-1]

                    if cell_tag != "c":
                        continue

                    cell_type = cell.attrib.get("t")
                    value = ""

                    for child in cell:

                        child_tag = child.tag

                        if isinstance(child_tag, str):
                            child_tag = child_tag.rsplit(
                                "}",
                                1,
                            )[-1]

                        if child_tag == "v":
                            value = child.text or ""

                    if (
                        cell_type == "s"
                        and value.isdigit()
                    ):

                        index = int(value)

                        if 0 <= index < len(shared_strings):
                            value = shared_strings[index]

                    values.append(value)

                if any(
                    value.strip()
                    for value in values
                ):
                    rows.append(
                        "\t".join(values)
                    )

        return "\n".join(rows)


def extract_pptx_text(
    data: bytes,
) -> str:

    with zipfile.ZipFile(
        io.BytesIO(data)
    ) as archive:

        slide_names = [
            name
            for name in archive.namelist()
            if re.match(
                r"ppt/slides/slide\d+\.xml$",
                name,
            )
        ]

        slides: list[str] = []

        for name in sorted(slide_names):

            root = ET.fromstring(
                archive.read(name)
            )

            texts: list[str] = []

            for element in root.iter():

                tag = element.tag

                if isinstance(tag, str):
                    tag = tag.rsplit("}", 1)[-1]

                if tag == "t":

                    value = (
                        element.text or ""
                    ).strip()

                    if value:
                        texts.append(value)

            if texts:
                slides.append(
                    " ".join(texts)
                )

        return "\n\n".join(slides)


def extract_pdf_text(
    data: bytes,
) -> str:

    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "PDF support requires the pypdf package."
        ) from error

    reader = PdfReader(
        io.BytesIO(data)
    )

    pages: list[str] = []

    for page in reader.pages:

        text = (
            page.extract_text()
            or ""
        ).strip()

        pages.append(text)

    return "\n\n".join(pages)


def extract_document_text(
    *,
    filename: str,
    data: bytes,
) -> str:

    suffix = (
        Path(filename)
        .suffix
        .lower()
    )

    if suffix in {".txt", ".csv"}:

        return data.decode(
            "utf-8",
            errors="replace",
        )

    if suffix == ".rtf":

        text = data.decode(
            "utf-8",
            errors="replace",
        )

        text = re.sub(
            r"\\'[0-9a-fA-F]{2}",
            " ",
            text,
        )

        text = re.sub(
            r"\\[a-zA-Z]+\d* ?",
            " ",
            text,
        )

        return re.sub(
            r"[{}]",
            "",
            text,
        )

    if suffix == ".docx":
        return extract_docx_text(data)

    if suffix == ".xlsx":
        return extract_xlsx_text(data)

    if suffix == ".pptx":
        return extract_pptx_text(data)

    if suffix == ".pdf":
        return extract_pdf_text(data)

    if suffix == ".doc":
        raise RuntimeError(
            "Legacy .doc files require a document conversion "
            "component before their text can be reviewed."
        )

    if suffix == ".xls":
        raise RuntimeError(
            "Legacy .xls files require a spreadsheet conversion "
            "component before their content can be reviewed."
        )

    if suffix == ".ppt":
        raise RuntimeError(
            "Legacy .ppt files require a presentation conversion "
            "component before their content can be reviewed."
        )

    raise RuntimeError(
        f"Unsupported document type: {suffix or 'unknown'}"
    )


def document_to_pages(
    *,
    filename: str,
    data: bytes,
) -> list[dict[str, Any]]:

    text = extract_document_text(
        filename=filename,
        data=data,
    ).strip()

    if not text:
        raise ValueError(
            "The uploaded document contains no extractable text."
        )

    pages = document_text_to_pages(text)
    pages = normalize_document_pages(pages)

    if not pages:
        raise ValueError(
            "The uploaded document produced no usable document pages."
        )

    return pages


# ============================================================
# DOCUMENT INTAKE
# ============================================================

def extract_document_pages(
    request: ChatRequest,
) -> list[dict[str, Any]]:

    if request.document_pages:

        pages = normalize_document_pages(
            request.document_pages
        )

        if pages:
            return pages

    if request.document_text:

        text = str(
            request.document_text
        ).strip()

        if text:
            pages = document_text_to_pages(text)
            return normalize_document_pages(pages)

    return []


# ============================================================
# GENERATED WORK → DOCUMENT PAGES
# ============================================================

def generated_work_to_pages(
    generated: Any,
) -> list[dict[str, Any]]:

    """
    Converts the intelligence response into the same document-page
    structure used by uploaded documents.

    This is deliberately format-tolerant because the intelligence
    layer may return structured pages or generated document text.
    """

    if isinstance(generated, dict):

        direct_pages = generated.get("pages")

        if isinstance(direct_pages, list):

            pages = normalize_document_pages(
                direct_pages
            )

            if pages:
                return pages

        for key in (
            "document_pages",
            "prepared_pages",
            "content_pages",
        ):

            candidate = generated.get(key)

            if isinstance(candidate, list):

                pages = normalize_document_pages(
                    candidate
                )

                if pages:
                    return pages

        for key in (
            "document_text",
            "prepared_work",
            "document",
            "content",
            "text",
            "reply",
            "response",
            "message",
        ):

            candidate = generated.get(key)

            if isinstance(candidate, str):
                text = candidate.strip()

                if text:
                    pages = document_text_to_pages(
                        text
                    )

                    pages = normalize_document_pages(
                        pages
                    )

                    if pages:
                        return pages

    if isinstance(generated, list):

        pages = normalize_document_pages(
            generated
        )

        if pages:
            return pages

    if isinstance(generated, str):

        text = generated.strip()

        if text:

            pages = document_text_to_pages(
                text
            )

            pages = normalize_document_pages(
                pages
            )

            if pages:
                return pages

    raise ValueError(
        "The intelligence response did not contain usable document work."
    )


async def generate_work_from_form(
    *,
    ada: AdaResponse,
    request: ChatRequest,
    customer_request: str,
    application_context: str | None,
) -> list[dict[str, Any]]:

    """
    Ask the existing intelligence layer to prepare the customer's
    requested work.

    No keyword routing is performed here.

    The selected service and complete customer form information are
    passed directly into the intelligence layer.
    """

    result = await asyncio.to_thread(
        ada.respond,
        message=customer_request,
        service=request.service,
        event="form_submitted_create_work",
        context=(
            (
                application_context + "\n\n"
                if application_context
                else ""
            )
            + (
                "WORKFLOW INSTRUCTION:\n"
                "Prepare the customer's requested work now. "
                "Return the complete prepared work, not merely a "
                "confirmation that work will be prepared."
            )
        ),
    )

    return generated_work_to_pages(result)


# ============================================================
# PAGE HELPERS
# ============================================================

def page_number(
    page: dict[str, Any],
    fallback: int,
) -> int:

    try:
        return int(
            page.get(
                "page_number",
                fallback,
            )
        )
    except Exception:
        return fallback


def page_content(
    page: dict[str, Any],
) -> str:

    return str(
        page.get(
            "content",
            "",
        )
        or ""
    )


def make_review_pages(
    document_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    result: list[dict[str, Any]] = []

    for position, page in enumerate(
        document_pages,
        start=1,
    ):

        result.append(
            {
                "page_number": page_number(
                    page,
                    position,
                ),
                "position": position,
                "status": "queued",
                "content": page_content(page),
                "review": "",
                "error": None,
            }
        )

    return result


# ============================================================
# JOB CREATION
# ============================================================

def create_review_job(
    *,
    job_id: str,
    customer_id: str | None,
    service: str | None,
    original_request: str,
    context: str | None,
    client_request_id: str | None,
    document_pages: list[dict[str, Any]],
) -> dict[str, Any]:

    job = {
        "job_id": job_id,
        "customer_id": customer_id,
        "service": service,
        "original_request": original_request,
        "context": context,
        "client_request_id": client_request_id,

        "status": "reviewing",

        "review_started": True,
        "review_finished": False,
        "review_error": None,

        "progress": {
            "completed": 0,
            "total": len(document_pages),
        },

        "document_pages": document_pages,

        "review_pages": make_review_pages(
            document_pages
        ),

        "assembled_review": "",

        "current_version": 1,
        "version_id": f"{job_id}:1",

        "approved": False,
        "paid": False,
    }

    _jobs[job_id] = job

    return job


def reset_review_state(
    job: dict[str, Any],
    document_pages: list[dict[str, Any]],
) -> None:

    job["document_pages"] = document_pages

    job["review_pages"] = make_review_pages(
        document_pages
    )

    job["assembled_review"] = ""

    job["status"] = "reviewing"

    job["review_started"] = True

    job["review_finished"] = False

    job["review_error"] = None

    job["approved"] = False

    job["progress"] = {
        "completed": 0,
        "total": len(document_pages),
    }


# ============================================================
# LIVE REVIEW
# ============================================================

def build_live_review(
    job: dict[str, Any],
) -> str:

    parts: list[str] = []

    for page in job.get(
        "review_pages",
        [],
    ):

        review = str(
            page.get(
                "review",
                "",
            )
            or ""
        ).strip()

        if not review:
            continue

        number = page.get(
            "page_number"
        )

        parts.append(
            f"PAGE {number}\n\n{review}"
        )

    if not parts:
        return ""

    return (
        "COMPLETE DOCUMENT REVIEW\n\n"
        + "\n\n".join(parts)
    )


# ============================================================
# REVIEW CALLBACK
# ============================================================

def make_review_progress_callback(
    job_id: str,
):

    def callback(
        update: dict[str, Any],
    ) -> None:

        job = _jobs.get(job_id)

        if job is None:
            return

        update_type = str(
            update.get("type", "")
        ).strip().lower()

        number = update.get(
            "page_number"
        )

        position = update.get(
            "position"
        )

        total = update.get(
            "total_pages"
        )

        if total is not None:

            try:
                job["progress"]["total"] = int(total)
            except Exception:
                pass

        if update_type == "page_started":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == str(number):

                    page["status"] = "reviewing"
                    break

            job["status"] = "reviewing"

        elif update_type == "page_completed":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == str(number):

                    page["status"] = "reviewed"

                    if "content" in update:
                        page["content"] = str(
                            update.get(
                                "content"
                            )
                            or ""
                        )

                    page["review"] = str(
                        update.get(
                            "review",
                            "",
                        )
                        or ""
                    )

                    page["error"] = None

                    break

            if position is not None:

                try:
                    job["progress"]["completed"] = int(
                        position
                    )
                except Exception:
                    pass

            job["assembled_review"] = (
                build_live_review(job)
            )

        elif update_type == "page_error":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == str(number):

                    page["status"] = "error"

                    page["error"] = str(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )

                    break

            job["assembled_review"] = (
                build_live_review(job)
            )

        elif update_type == "review_completed":

            assembled = str(
                update.get(
                    "assembled_review",
                    "",
                )
                or ""
            ).strip()

            job["assembled_review"] = (
                assembled
                or build_live_review(job)
            )

            job["status"] = "review_complete"

            job["review_finished"] = True

            job["progress"]["completed"] = (
                job["progress"]["total"]
            )

    return callback


# ============================================================
# REVIEW WORKER
# ============================================================

async def run_review_job(
    job_id: str,
) -> None:

    job = _jobs.get(job_id)

    if job is None:
        return

    try:

        ada = get_session(
            customer_id=job.get(
                "customer_id"
            ),
            job_id=job_id,
            service=job.get(
                "service"
            ),
        )

        pages = job.get(
            "document_pages",
            [],
        )

        if not pages:
            raise ValueError(
                "Cannot review an empty document."
            )

        print()
        print("=" * 78)
        print("DOCUMENT REVIEW INTELLIGENCE STARTED")
        print("=" * 78)
        print("Job:", job_id)
        print("Pages:", len(pages))
        print("Service:", job.get("service"))
        print("=" * 78)

        callback = make_review_progress_callback(
            job_id
        )

        result = await asyncio.to_thread(
            ada.review_document_pages,
            pages=pages,
            service=job.get("service"),
            context=job.get("context"),
            customer_request=job.get(
                "original_request"
            ),
            event="send_for_review",
            progress_callback=callback,
        )

        if not isinstance(result, dict):
            raise TypeError(
                "AdaResponse returned an invalid review result."
            )

        returned_pages = result.get("pages")

        if isinstance(
            returned_pages,
            list,
        ):

            for returned in returned_pages:

                if not isinstance(
                    returned,
                    dict,
                ):
                    continue

                number = returned.get(
                    "page_number"
                )

                for stored in job[
                    "review_pages"
                ]:

                    if str(
                        stored["page_number"]
                    ) == str(number):

                        if "review" in returned:
                            stored["review"] = str(
                                returned.get(
                                    "review"
                                )
                                or ""
                            )

                        if "content" in returned:
                            stored["content"] = str(
                                returned.get(
                                    "content"
                                )
                                or ""
                            )

                        stored["status"] = "reviewed"

                        break

        assembled = str(
            result.get(
                "assembled_review",
                "",
            )
            or ""
        ).strip()

        job["assembled_review"] = (
            assembled
            or build_live_review(job)
        )

        job["status"] = "review_complete"

        job["review_finished"] = True

        job["review_error"] = None

        job["progress"]["completed"] = (
            job["progress"]["total"]
        )

        print()
        print("=" * 78)
        print("DOCUMENT REVIEW INTELLIGENCE COMPLETE")
        print("=" * 78)
        print("Job:", job_id)
        print("=" * 78)

    except asyncio.CancelledError:

        print(
            "Review task cancelled:",
            job_id,
        )

        raise

    except Exception as error:

        job["status"] = "review_error"

        job["review_finished"] = True

        job["review_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

        traceback.print_exc()


# ============================================================
# REVIEW TASK MANAGEMENT
# ============================================================

def ensure_review_started(
    job_id: str,
) -> bool:

    job = _jobs.get(job_id)

    if job is None:
        return False

    task = _review_tasks.get(
        job_id
    )

    if (
        task is not None
        and not task.done()
    ):
        return False

    if job.get("status") != "reviewing":
        return False

    if not job.get("document_pages"):
        return False

    task = asyncio.create_task(
        run_review_job(job_id)
    )

    _review_tasks[job_id] = task

    return True


# ============================================================
# CUSTOMER PAGES
# ============================================================

@app.get("/")
async def customer_home():

    file = find_file("index.html")

    if file is None:
        return error_response(
            stage="CUSTOMER_HOME",
            error="index.html was not found.",
            status_code=500,
            error_code="INDEX_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/index.html")
async def customer_index():
    return await customer_home()


@app.get("/conversation.html")
async def customer_conversation():

    file = find_file("conversation.html")

    if file is None:
        return error_response(
            stage="CONVERSATION_PAGE",
            error="conversation.html was not found.",
            status_code=404,
            error_code="CONVERSATION_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/workspace.html")
async def customer_workspace():

    file = find_file("workspace.html")

    if file is None:
        return error_response(
            stage="WORKSPACE_PAGE",
            error="workspace.html was not found.",
            status_code=404,
            error_code="WORKSPACE_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/review.html")
async def customer_review():

    file = find_file("review.html")

    if file is None:
        return error_response(
            stage="REVIEW_PAGE",
            error="review.html was not found.",
            status_code=404,
            error_code="REVIEW_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "success": True,
        "status": "ok",
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
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
        "active_review_tasks": len(_review_tasks),
        "active_correction_tasks": len(
            _correction_tasks
        ),
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):

    try:

        filename = (
            str(
                file.filename
                or "uploaded_document"
            )
            .strip()
        )

        data = await file.read()

        if not data:
            return error_response(
                stage="DOCUMENT_UPLOAD",
                error="The uploaded file is empty.",
                status_code=400,
                error_code="EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD_BYTES:
            return error_response(
                stage="DOCUMENT_UPLOAD",
                error="The uploaded document is too large.",
                status_code=413,
                error_code="FILE_TOO_LARGE",
            )

        pages = await asyncio.to_thread(
            document_to_pages,
            filename=filename,
            data=data,
        )

        upload_job_id = (
            str(job_id or "").strip()
            or str(uuid.uuid4())
        )

        return {
            "success": True,
            "filename": filename,
            "job_id": upload_job_id,
            "customer_id": customer_id,
            "client_request_id": client_request_id,
            "service": service,
            "total_pages": len(pages),
            "document_pages": pages,
            "message": (
                "Document received and converted "
                "into reviewable pages."
            ),
        }

    except Exception as error:

        return error_response(
            stage="DOCUMENT_UPLOAD",
            error=error,
            status_code=400,
            error_code="DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    print()
    print("=" * 78)
    print("WORKSPACE SEND → FASTAPI")
    print("=" * 78)

    message = str(
        request.message or ""
    ).strip()

    event = normalized_event(
        request.event
    )

    document_pages = extract_document_pages(
        request
    )

    work_creation_requested = (
        is_work_creation_event(request)
    )

    print("Event:", event or "(none)")
    print(
        "Service:",
        request.service or "(none)",
    )
    print(
        "Document pages:",
        len(document_pages),
    )
    print(
        "Create work:",
        work_creation_requested,
    )

    # ========================================================
    # INTELLIGENCE
    # ========================================================

    if not request.activate_intelligence:

        return error_response(
            stage="INTELLIGENCE_ACTIVATION",
            error="Intelligence activation is disabled.",
            status_code=400,
            error_code="INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return error_response(
            stage="INTELLIGENCE_CONFIGURATION",
            error="AdaResponse is not configured.",
            status_code=503,
            error_code="INTELLIGENCE_NOT_CONFIGURED",
        )

    # ========================================================
    # JOB ID
    # ========================================================

    job_id = (
        str(
            request.job_id or ""
        ).strip()
        or str(uuid.uuid4())
    )

    application_context = (
        build_application_context(
            request
        )
    )

    # ========================================================
    # GUIDANCE
    # ========================================================

    if request.guidance_only:

        if not message:

            return error_response(
                stage="GUIDANCE_VALIDATION",
                error="The guidance message is empty.",
                status_code=400,
                error_code="EMPTY_GUIDANCE_MESSAGE",
            )

        try:

            ada = get_session(
                customer_id=request.customer_id,
                job_id=job_id,
                service=request.service,
            )

            reply = ada.respond(
                message=message,
                service=request.service,
                event=request.event,
                context=application_context,
            )

            return {
                "success": True,
                "reply": str(
                    reply or ""
                ).strip(),
                "job_id": job_id,
                "created_work": False,
            }

        except Exception as error:

            return error_response(
                stage="GUIDANCE_RESPONSE",
                error=error,
                status_code=500,
                error_code="GUIDANCE_ERROR",
            )

    # ========================================================
    # FORM → CREATE WORK → REVIEW
    #
    # THIS IS THE FIX.
    #
    # A form submission does NOT arrive with document_pages.
    #
    # The intelligence layer must first create the requested
    # work. That generated work is then converted into pages.
    # Only after that is the review job created.
    # ========================================================

    if work_creation_requested and not document_pages:

        complete_request = build_customer_request(
            message=message,
            service=request.service,
            form_data=request.form_data,
            context=request.context,
        )

        if not complete_request:

            return error_response(
                stage="WORK_CREATION_INPUT",
                error=(
                    "The customer's service request "
                    "contains no usable information."
                ),
                status_code=400,
                error_code="EMPTY_WORK_REQUEST",
            )

        try:

            ada = get_session(
                customer_id=request.customer_id,
                job_id=job_id,
                service=request.service,
            )

            print()
            print("=" * 78)
            print("FORM SUBMISSION → WORK CREATION")
            print("=" * 78)
            print("Job:", job_id)
            print("Service:", request.service)
            print("=" * 78)

            generated_pages = await generate_work_from_form(
                ada=ada,
                request=request,
                customer_request=complete_request,
                application_context=application_context,
            )

            if not generated_pages:

                raise ValueError(
                    "No document pages were produced from the customer's request."
                )

            job = create_review_job(
                job_id=job_id,
                customer_id=request.customer_id,
                service=request.service,
                original_request=complete_request,
                context=application_context,
                client_request_id=request.client_request_id,
                document_pages=generated_pages,
            )

            started = ensure_review_started(
                job_id
            )

            print()
            print("=" * 78)
            print("WORK CREATED → REVIEW JOB CREATED")
            print("=" * 78)
            print("Job:", job_id)
            print("Pages:", len(generated_pages))
            print("Review task started:", started)
            print("=" * 78)

            return {
                "success": True,
                "reply": (
                    "Your request has been prepared "
                    "and sent into document review."
                ),
                "job_id": job_id,
                "service": request.service,
                "created_work": True,
                "work_created": True,
                "review_started": True,
                "status": job["status"],
                "total_pages": len(
                    generated_pages
                ),
                "progress": job["progress"],
                "review_url": (
                    "/review.html?job_id="
                    + job_id
                ),
                "document_pages": generated_pages,
            }

        except Exception as error:

            return error_response(
                stage="FORM_WORK_CREATION",
                error=error,
                status_code=500,
                error_code="WORK_CREATION_ERROR",
            )

    # ========================================================
    # DOCUMENT REVIEW
    #
    # Existing uploaded/generated page path.
    # ========================================================

    if document_pages:

        complete_request = build_customer_request(
            message=message,
            service=request.service,
            form_data=request.form_data,
            context=request.context,
        )

        job = _jobs.get(job_id)

        if job is None:

            job = create_review_job(
                job_id=job_id,
                customer_id=request.customer_id,
                service=request.service,
                original_request=complete_request,
                context=application_context,
                client_request_id=request.client_request_id,
                document_pages=document_pages,
            )

        else:

            active_task = _review_tasks.get(
                job_id
            )

            if (
                active_task is not None
                and not active_task.done()
                and job.get("status") == "reviewing"
            ):

                return {
                    "success": True,
                    "reply": (
                        "Your document is already "
                        "being reviewed page by page."
                    ),
                    "job_id": job_id,
                    "service": (
                        request.service
                        or job.get("service")
                    ),
                    "created_work": True,
                    "review_started": True,
                    "status": "reviewing",
                    "total_pages": len(
                        job.get(
                            "document_pages",
                            [],
                        )
                    ),
                    "progress": job["progress"],
                    "review_url": (
                        "/review.html?job_id="
                        + job_id
                    ),
                }

            reset_review_state(
                job,
                document_pages,
            )

            job["customer_id"] = (
                request.customer_id
            )

            job["service"] = (
                request.service
                or job.get("service")
            )

            job["original_request"] = (
                complete_request
            )

            job["context"] = (
                application_context
            )

            job["client_request_id"] = (
                request.client_request_id
            )

        started = ensure_review_started(
            job_id
        )

        return {
            "success": True,
            "reply": (
                "Your document has been received. "
                "It is now being reviewed page by page."
            ),
            "job_id": job_id,
            "service": (
                request.service
                or job.get("service")
            ),
            "created_work": True,
            "review_started": True,
            "status": job["status"],
            "total_pages": len(document_pages),
            "progress": job["progress"],
            "review_url": (
                "/review.html?job_id="
                + job_id
            ),
        }

    # ========================================================
    # REVIEW REQUEST WITHOUT DOCUMENT
    #
    # This remains a genuine error only when neither:
    #
    #   1. form work creation
    #   2. document pages
    #
    # is available.
    # ========================================================

    if (
        request.create_work
        and event in REVIEW_EVENTS
        and not document_pages
        and not work_creation_requested
    ):

        return error_response(
            stage="DOCUMENT_INTAKE",
            error=(
                "The workspace requested review, "
                "but no document pages or work-creation "
                "request were supplied."
            ),
            status_code=400,
            error_code="NO_DOCUMENT_OR_WORK_REQUEST",
        )

    # ========================================================
    # NORMAL CONVERSATION
    # ========================================================

    if not message:

        return error_response(
            stage="CHAT_VALIDATION",
            error="The chat message is empty.",
            status_code=400,
            error_code="EMPTY_MESSAGE",
        )

    try:

        ada = get_session(
            customer_id=request.customer_id,
            job_id=job_id,
            service=request.service,
        )

        reply = ada.respond(
            message=message,
            service=request.service,
            event=request.event,
            context=application_context,
        )

        return {
            "success": True,
            "reply": str(
                reply or ""
            ).strip(),
            "job_id": job_id,
            "service": (
                request.service
                or ada.service
            ),
            "created_work": False,
        }

    except Exception as error:

        return error_response(
            stage="ADA_RESPONSE",
            error=error,
            status_code=500,
            error_code="ADA_RESPONSE_ERROR",
        )


# ============================================================
# REVIEW STATE
# ============================================================

@app.get("/api/review")
async def review(
    job_id: str,
):

    job_id = str(
        job_id or ""
    ).strip()

    if not job_id:

        return error_response(
            stage="REVIEW",
            error="job_id is required.",
            status_code=400,
            error_code="JOB_ID_REQUIRED",
        )

    job = _jobs.get(job_id)

    if job is None:

        return error_response(
            stage="REVIEW",
            error="The requested review job does not exist.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if (
        job.get("status") == "reviewing"
        and job.get("document_pages")
    ):

        ensure_review_started(job_id)

    return {
        "success": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "current_version": job["current_version"],
        "version_id": job["version_id"],
        "progress": job["progress"],
        "document_pages": job["document_pages"],
        "review_pages": job["review_pages"],
        "assembled_review": job["assembled_review"],
        "approved": job["approved"],
        "paid": job["paid"],
        "error": job["review_error"],
    }


# ============================================================
# CORRECTION CALLBACK
# ============================================================

def make_correction_callback(
    job_id: str,
):

    def callback(
        update: dict[str, Any],
    ) -> None:

        job = _jobs.get(job_id)

        if job is None:
            return

        update_type = str(
            update.get("type", "")
        ).strip().lower()

        number = update.get(
            "page_number"
        )

        if update_type == "correction_page_started":

            for page in job["document_pages"]:

                if str(
                    page.get("page_number")
                ) == str(number):

                    page["status"] = "correcting"
                    break

        elif update_type == "correction_page_completed":

            for page in job["document_pages"]:

                if str(
                    page.get("page_number")
                ) == str(number):

                    if "content" in update:
                        page["content"] = str(
                            update.get(
                                "content"
                            )
                            or ""
                        )

                    page["status"] = "corrected"
                    break

            position = update.get("position")
            total = update.get("total_pages")

            try:
                completed = int(position or 0)
            except Exception:
                completed = 0

            try:
                total_pages = int(
                    total
                    or len(
                        job["document_pages"]
                    )
                )
            except Exception:
                total_pages = len(
                    job["document_pages"]
                )

            job["progress"] = {
                "completed": completed,
                "total": total_pages,
            }

    return callback


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: CorrectionRequest,
):

    job_id = str(
        request.job_id or ""
    ).strip()

    instruction = str(
        request.instruction or ""
    ).strip()

    if not job_id:

        return error_response(
            stage="CORRECTION",
            error="job_id is required.",
            status_code=400,
            error_code="JOB_ID_REQUIRED",
        )

    if not instruction:

        return error_response(
            stage="CORRECTION",
            error="Correction instruction is empty.",
            status_code=400,
            error_code="EMPTY_CORRECTION",
        )

    job = _jobs.get(job_id)

    if job is None:

        return error_response(
            stage="CORRECTION",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if job.get("status") == "reviewing":

        return error_response(
            stage="CORRECTION",
            error="The document is still being reviewed.",
            status_code=409,
            error_code="REVIEW_STILL_RUNNING",
        )

    if not job.get("document_pages"):

        return error_response(
            stage="CORRECTION",
            error="There is no document available for correction.",
            status_code=409,
            error_code="NO_DOCUMENT",
        )

    job["current_version"] += 1

    job["version_id"] = (
        f"{job_id}:{job['current_version']}"
    )

    job["approved"] = False

    job["status"] = "correcting"

    job["review_started"] = False

    job["review_finished"] = False

    job["review_error"] = None

    job["assembled_review"] = ""

    job["progress"] = {
        "completed": 0,
        "total": len(
            job["document_pages"]
        ),
    }

    job["correction_instruction"] = instruction

    old_task = _correction_tasks.pop(
        job_id,
        None,
    )

    if (
        old_task is not None
        and not old_task.done()
    ):
        old_task.cancel()

    task = asyncio.create_task(
        run_correction_job(
            job_id,
            instruction,
        )
    )

    _correction_tasks[job_id] = task

    return {
        "success": True,
        "job_id": job_id,
        "status": "correcting",
        "version_id": job["version_id"],
    }


# ============================================================
# CORRECTION WORKER
# ============================================================

async def run_correction_job(
    job_id: str,
    instruction: str,
) -> None:

    job = _jobs.get(job_id)

    if job is None:
        return

    try:

        ada = get_session(
            customer_id=job.get(
                "customer_id"
            ),
            job_id=job_id,
            service=job.get(
                "service"
            ),
        )

        callback = make_correction_callback(
            job_id
        )

        result = await asyncio.to_thread(
            ada.correct_document,
            document_pages=job[
                "document_pages"
            ],
            correction=instruction,
            service=job.get(
                "service"
            ),
            context=job.get(
                "context"
            ),
            progress_callback=callback,
        )

        if not isinstance(result, dict):

            raise TypeError(
                "AdaResponse returned an invalid correction result."
            )

        corrected_pages = result.get(
            "pages",
            []
        )

        if not isinstance(
            corrected_pages,
            list,
        ) or not corrected_pages:

            raise ValueError(
                "AdaResponse returned no corrected document pages."
            )

        corrected_pages = normalize_document_pages(
            corrected_pages
        )

        if not corrected_pages:

            raise ValueError(
                "Corrected document contains no usable pages."
            )

        job["document_pages"] = corrected_pages

        job["review_pages"] = make_review_pages(
            corrected_pages
        )

        job["assembled_review"] = ""

        job["status"] = "reviewing"

        job["review_started"] = True

        job["review_finished"] = False

        job["review_error"] = None

        job["progress"] = {
            "completed": 0,
            "total": len(corrected_pages),
        }

        existing_review_task = _review_tasks.pop(
            job_id,
            None,
        )

        if (
            existing_review_task is not None
            and not existing_review_task.done()
        ):
            existing_review_task.cancel()

        ensure_review_started(job_id)

        print(
            "Correction complete; "
            "corrected version returned for review:",
            job_id,
        )

    except asyncio.CancelledError:

        print(
            "Correction task cancelled:",
            job_id,
        )

        raise

    except Exception as error:

        job["status"] = "correction_error"

        job["review_finished"] = True

        job["review_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

        traceback.print_exc()


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: ApprovalRequest,
):

    job = _jobs.get(
        request.job_id
    )

    if job is None:

        return error_response(
            stage="APPROVAL",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if request.version_id != job["version_id"]:

        return error_response(
            stage="APPROVAL",
            error="The supplied document version does not match.",
            status_code=409,
            error_code="VERSION_MISMATCH",
        )

    if job.get("status") != "review_complete":

        return error_response(
            stage="APPROVAL",
            error="The document review is not complete.",
            status_code=409,
            error_code="REVIEW_NOT_COMPLETE",
        )

    job["approved"] = True

    job["status"] = "approved"

    return {
        "success": True,
        "job_id": request.job_id,
        "version_id": request.version_id,
        "approved": True,
        "status": "approved",
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str,
):

    job = _jobs.get(job_id)

    if job is None:

        return error_response(
            stage="DOWNLOAD",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if version_id != job["version_id"]:

        return error_response(
            stage="DOWNLOAD",
            error="Version mismatch.",
            status_code=409,
            error_code="VERSION_MISMATCH",
        )

    if not job.get("approved"):

        return error_response(
            stage="DOWNLOAD",
            error=(
                "The current document version "
                "has not been approved."
            ),
            status_code=409,
            error_code="DOCUMENT_NOT_APPROVED",
        )

    if not job.get("paid"):

        return error_response(
            stage="DOWNLOAD",
            error=(
                "Payment and final download workflow "
                "has not been connected yet."
            ),
            status_code=409,
            error_code="PAYMENT_NOT_CONNECTED",
        )

    return error_response(
        stage="DOWNLOAD",
        error=(
            "Final document generation has not "
            "been connected yet."
        ),
        status_code=409,
        error_code="DOWNLOAD_GENERATION_NOT_CONNECTED",
    )


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    try:

        key = session_key(
            customer_id,
            job_id,
        )

        session = _sessions.get(key)

        if session is not None:
            session.clear_history()

        return {
            "success": True,
            "message": "Conversation cleared.",
        }

    except Exception as error:

        return error_response(
            stage="CLEAR_CHAT",
            error=error,
            status_code=500,
            error_code="CLEAR_CHAT_ERROR",
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER")
    print("FASTAPI DOCUMENT WORK + REVIEW INTELLIGENCE")
    print("=" * 78)

    print("API:", "FastAPI")
    print("Intelligence:", "AdaResponse")
    print("Model:", get_ada_model())
    print("Configured:", is_configured())

    print("Workspace:", "/workspace.html")
    print("Upload:", "/api/upload")
    print("Chat:", "/api/chat")
    print("Review:", "/api/review")
    print("Correction:", "/api/correct")
    print("Approval:", "/api/approve")

    print(
        "Form work creation:",
        "ENABLED",
    )

    print(
        "Document intake:",
        "ENABLED",
    )

    print(
        "Page-by-page intelligence:",
        "ENABLED",
    )

    print(
        "Progressive review:",
        "ENABLED",
    )

    print(
        "Automatic correction re-review:",
        "ENABLED",
    )

    print(
        "Keyword intelligence:",
        "DISABLED",
    )

    print("=" * 78)
    print()


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        "ada_api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
