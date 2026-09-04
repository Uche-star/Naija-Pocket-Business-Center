from __future__ import annotations

import asyncio
import inspect
import io
import os
import re
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
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
    get_payment,
    create_payment,
    get_latest_payment,
    update_payment_status,
    save_customer_work,
    get_latest_work,
    get_activated_work,
    activate_work_download,
    get_back_office_jobs,
    update_job_status,
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

ADMIN_KEY = os.getenv(
    "NPBC_ADMIN_KEY",
    "npbc_admin_2026",
)


# ============================================================
# RUNTIME STATE
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# FASTAPI APPLICATION
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
# GENERAL HELPERS
# ============================================================

def find_file(name: str) -> Path | None:
    candidates = (
        BASE / name,
        BASE / "app" / name,
        BASE / "static" / name,
        BASE / "public" / name,
        BASE / "assets" / name,
    )

    for path in candidates:
        if path.is_file():
            return path

    return None


def event_value(value: Any) -> str:
    return clean_text(value).lower().strip()


def job_key(
    customer_id: Any,
    job_id: Any,
) -> str:
    return f"{customer_id}:{job_id}"


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    text = text.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    )

    text = re.sub(
        r"```(?:markdown|md|text)?",
        "",
        text,
        flags=re.I,
    )

    text = text.replace(
        "```",
        "",
    )

    return text.strip()


def application_error(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):
    print(
        f"[{stage}] {error}"
    )

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
        return int(
            str(value).strip()
        )
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
# PERSISTENT DOCUMENT STORAGE
# ============================================================

def save_document_to_storage(
    job_id_str: str,
) -> dict[str, Any]:

    job_id_str = str(
        job_id_str
    ).strip()

    job = _jobs.get(job_id_str)

    if not job:
        raise RuntimeError(
            f"Job {job_id_str} not available."
        )

    numeric_job_id = safe_int(
        job_id_str
    )

    if numeric_job_id is None:
        raise RuntimeError(
            f"Invalid numeric job id: {job_id_str}"
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
        DOCUMENT_ROOT /
        job_id_str
    )

    job_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filepath = (
        job_folder /
        f"v{version}.txt"
    )

    filepath.write_text(
        document_text,
        encoding="utf-8",
    )

    if not filepath.exists():
        raise RuntimeError(
            "Document file was not created."
        )

    work_id = save_customer_work(
        job_id=numeric_job_id,
        work_title=job.get(
            "service",
            "Business Document",
        ),
        work_type="document",
        storage_type="local_file",
        storage_reference=str(
            filepath
        ),
        work_status="completed",
    )

    persisted = persisted_record_to_dict(
        get_latest_work(
            numeric_job_id
        )
    )

    saved_version = (
        safe_int(
            persisted.get(
                "version"
            ),
            version,
        )
        or version
    )

    version_id = (
        f"{job_id_str}:{saved_version}"
    )

    job["current_version"] = saved_version
    job["version_id"] = version_id
    job["saved_version"] = saved_version
    job["storage_reference"] = str(
        filepath
    )
    job["work_id"] = (
        safe_int(
            persisted.get("id"),
            work_id,
        )
    )

    print(
        "[STORAGE] "
        f"saved job={job_id_str} "
        f"version={saved_version} "
        f"path={filepath}"
    )

    return {
        "success": True,
        "version": saved_version,
        "version_id": version_id,
        "storage_reference": str(filepath),
        "work_id": job["work_id"],
    }


# ============================================================
# PERSISTENT APPROVAL RECOVERY
# ============================================================

def recover_saved_job_for_approval(
    supplied_job_id: str,
    supplied_version_id: str,
) -> dict[str, Any] | None:

    supplied_job_id = str(
        supplied_job_id
    ).strip()

    supplied_version_id = str(
        supplied_version_id
    ).strip()

    numeric_job_id = safe_int(
        supplied_job_id
    )

    if numeric_job_id is None:
        return None

    persisted_job = persisted_record_to_dict(
        get_job(
            numeric_job_id
        )
    )

    work = persisted_record_to_dict(
        get_latest_work(
            numeric_job_id
        )
    )

    if not work:
        print(
            "[APPROVAL] "
            f"No saved work for job={supplied_job_id}"
        )
        return None

    saved_version = (
        safe_int(
            work.get("version"),
            1,
        )
        or 1
    )

    expected_version_id = (
        f"{supplied_job_id}:{saved_version}"
    )

    # Do not approve a different document version.
    if supplied_version_id != expected_version_id:
        print(
            "[APPROVAL] "
            f"Version mismatch: supplied="
            f"{supplied_version_id} "
            f"expected={expected_version_id}"
        )
        return None

    storage_reference = str(
        work.get(
            "storage_reference",
            "",
        )
        or ""
    ).strip()

    if not storage_reference:
        return None

    filepath = Path(
        storage_reference
    )

    # --------------------------------------------------------
    # CRITICAL PERSISTENT RECOVERY
    # --------------------------------------------------------

    if not filepath.exists():

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
            print(
                "[APPROVAL] "
                "Saved document file is missing "
                "and no database document text exists."
            )
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

    if not filepath.exists() or not filepath.is_file():
        print(
            "[APPROVAL] "
            f"Saved document file is missing: "
            f"path={filepath}"
        )
        return None

    document_text = clean_text(
        filepath.read_text(
            encoding="utf-8"
        )
    )

    if not document_text:
        return None

    pages = text_to_review_pages(
        document_text
    )

    if not pages:
        return None

    persisted_customer_id = (
        persisted_job.get(
            "customer_id"
        )
    )

    persisted_service = (
        persisted_job.get(
            "service"
        )
        or work.get(
            "work_title"
        )
        or "Business Document"
    )

    recovered_job = {
        "job_id": supplied_job_id,
        "customer_id": persisted_customer_id,
        "service": persisted_service,
        "original_request": persisted_job.get(
            "original_request",
            "",
        ),
        "context": persisted_job.get(
            "context",
            "",
        ),
        "status": "review_complete",
        "review_started": True,
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
        "assembled_review": "\n\n".join(
            page["content"]
            for page in pages
        ),
        "current_version": saved_version,
        "version_id": expected_version_id,
        "saved_version": saved_version,
        "storage_reference": str(
            filepath
        ),
        "work_id": safe_int(
            work.get("id")
        ),
        "approved": False,
        "paid": False,
    }

    # Preserve already-approved/paid state
    # when the database says it is already there.
    persisted_status = event_value(
        persisted_job.get(
            "status"
        )
    )

    if persisted_status in {
        "approved",
        "paid",
    }:
        recovered_job["approved"] = True

    if persisted_status == "paid":
        recovered_job["paid"] = True

    _jobs[supplied_job_id] = recovered_job

    print(
        "[APPROVAL] "
        f"PERSISTENT RECOVERY SUCCESS "
        f"job_id={supplied_job_id} "
        f"version_id={expected_version_id}"
    )

    return recovered_job


# ============================================================
# DOCUMENT PAGINATION
# ============================================================

def split_explicit_pages(
    text: str,
) -> list[str]:

    text = clean_text(text)

    if not text:
        return []

    parts = re.split(
        r"\n\s*(?:---+\s*)?PAGE\s+\d+\s*(?:---+)?\s*\n",
        text,
        flags=re.I,
    )

    if len(parts) <= 1:
        return []

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


def split_by_sections(
    text: str,
) -> list[str]:

    paragraphs = [
        p.strip()
        for p in re.split(
            r"\n\s*\n",
            text,
        )
        if p.strip()
    ]

    return paragraphs


def split_long_block(
    text: str,
    max_chars: int,
) -> list[str]:

    text = clean_text(text)

    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    chunks: list[str] = []
    current = ""

    for sentence in sentences:

        sentence = sentence.strip()

        if not sentence:
            continue

        if (
            current
            and len(current) + len(sentence) + 1
            > max_chars
        ):
            chunks.append(
                current.strip()
            )
            current = sentence
        else:
            current = (
                f"{current} {sentence}"
                if current
                else sentence
            )

    if current:
        chunks.append(
            current.strip()
        )

    # If a single sentence is enormous,
    # split it safely by character count.
    final_chunks: list[str] = []

    for chunk in chunks:

        if len(chunk) <= max_chars:
            final_chunks.append(
                chunk
            )
            continue

        for start in range(
            0,
            len(chunk),
            max_chars,
        ):
            piece = chunk[
                start:start + max_chars
            ].strip()

            if piece:
                final_chunks.append(
                    piece
                )

    return final_chunks


def balance_small_pages(
    chunks: list[str],
) -> list[str]:

    if len(chunks) <= 1:
        return chunks

    result: list[str] = []

    for chunk in chunks:

        if (
            result
            and len(chunk) < REVIEW_MIN_CHARS
            and len(result[-1]) + len(chunk) + 2
            <= REVIEW_CHUNK_CHARS
        ):
            result[-1] = (
                result[-1]
                + "\n\n"
                + chunk
            )
        else:
            result.append(
                chunk
            )

    return result


def paginate_generated_text(
    text: str,
) -> list[dict[str, Any]]:

    text = clean_text(text)

    if not text:
        return []

    explicit = split_explicit_pages(
        text
    )

    if explicit:
        source_blocks = explicit
    else:
        source_blocks = split_by_sections(
            text
        )

    chunks: list[str] = []

    for block in source_blocks:

        chunks.extend(
            split_long_block(
                block,
                REVIEW_CHUNK_CHARS,
            )
        )

    chunks = balance_small_pages(
        chunks
    )

    return [
        {
            "page_number": index,
            "position": index,
            "content": chunk,
        }
        for index, chunk
        in enumerate(
            chunks,
            1,
        )
    ]


def text_to_review_pages(
    text: str,
) -> list[dict[str, Any]]:

    return paginate_generated_text(
        text
    )


def normalize_document_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    if not pages:
        return []

    if isinstance(
        pages,
        str,
    ):
        return text_to_review_pages(
            pages
        )

    if isinstance(
        pages,
        dict,
    ):
        pages = (
            pages.get("pages")
            or pages.get("document_pages")
            or pages.get("content_pages")
            or []
        )

    if not isinstance(
        pages,
        list,
    ):
        return []

    normalized: list[
        dict[str, Any]
    ] = []

    for index, item in enumerate(
        pages,
        1,
    ):

        if isinstance(
            item,
            dict,
        ):
            content = clean_text(
                item.get(
                    "content",
                    item.get(
                        "text",
                        "",
                    ),
                )
            )
        else:
            content = clean_text(
                item
            )

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


def normalize_pages_for_review(
    pages: Any,
) -> list[dict[str, Any]]:

    return normalize_document_pages(
        pages
    )


def normalize_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    return normalize_document_pages(
        pages
    )


# ============================================================
# SESSION MANAGEMENT
# ============================================================

def get_session(
    customer_id: Any,
    job_id: Any,
    service: str | None = None,
) -> AdaResponse:

    key = job_key(
        customer_id,
        job_id,
    )

    ada = _sessions.get(
        key
    )

    if ada is None:

        ada = AdaResponse(
            service=service
        )

        try:
            if service and hasattr(
                ada,
                "set_service",
            ):
                ada.set_service(
                    service
                )
        except Exception:
            pass

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


class PaymentCreate(BaseModel):
    job_id: str
    customer_id: str | None = None
    order_number: str | None = None
    service: str | None = None
    amount: float
    payment_method: str = "bank_transfer"


class PaymentConfirm(BaseModel):
    payment_id: int
    admin_key: str


class DownloadActivation(BaseModel):
    work_id: int
    admin_key: str


# ============================================================
# CUSTOMER REQUEST / CONTEXT
# ============================================================

def build_customer_request(
    request: Chat,
) -> str:

    parts: list[str] = []

    if request.service:
        parts.append(
            f"Selected service: {request.service}"
        )

    if request.form_data:
        for key, value in request.form_data.items():

            value_text = clean_text(
                value
            )

            if value_text:
                parts.append(
                    f"{key}: {value_text}"
                )

    if request.context:
        parts.append(
            f"Additional context: {request.context}"
        )

    if request.message:
        parts.append(
            f"Customer request: {request.message}"
        )

    return "\n".join(
        parts
    ).strip()


def build_context(
    request: Chat,
) -> str | None:

    parts: list[str] = []

    if request.context:
        parts.append(
            request.context
        )

    if request.customer_id:
        parts.append(
            f"customer_id={request.customer_id}"
        )

    if request.client_request_id:
        parts.append(
            f"client_request_id={request.client_request_id}"
        )

    return "\n".join(
        parts
    ).strip() or None


# ============================================================
# INTELLIGENCE HELPERS
# ============================================================

_TEXT_KEYS = (
    "document_text",
    "prepared_work",
    "generated_document",
    "generated",
    "output",
    "document",
    "content",
    "text",
    "reply",
    "response",
    "message",
    "result",
    "answer",
)

_PAGE_KEYS = (
    "pages",
    "document_pages",
    "prepared_pages",
    "content_pages",
)


def _extract_from_value(
    value: Any,
) -> tuple[str, list[Any]]:

    if value is None:
        return "", []

    if isinstance(
        value,
        str,
    ):
        text = clean_text(
            value
        )
        return text, []

    if isinstance(
        value,
        list,
    ):

        page_items: list[Any] = []
        text_parts: list[str] = []

        for item in value:

            text, pages = _extract_from_value(
                item
            )

            if pages:
                page_items.extend(
                    pages
                )

            if text:
                text_parts.append(
                    text
                )

        return (
            "\n\n".join(text_parts).strip(),
            page_items,
        )

    if isinstance(
        value,
        dict,
    ):

        pages: list[Any] = []

        for key in _PAGE_KEYS:
            if key in value and value[key]:
                candidate = value[key]

                if isinstance(
                    candidate,
                    list,
                ):
                    pages.extend(
                        candidate
                    )

        for key in _TEXT_KEYS:

            if key not in value:
                continue

            text, nested_pages = (
                _extract_from_value(
                    value[key]
                )
            )

            if nested_pages:
                pages.extend(
                    nested_pages
                )

            if text:
                return (
                    text,
                    pages,
                )

        for value_item in value.values():

            text, nested_pages = (
                _extract_from_value(
                    value_item
                )
            )

            if nested_pages:
                pages.extend(
                    nested_pages
                )

            if text:
                return (
                    text,
                    pages,
                )

        return "", pages

    for key in _TEXT_KEYS:

        if hasattr(
            value,
            key,
        ):

            try:
                candidate = getattr(
                    value,
                    key,
                )

                text, pages = (
                    _extract_from_value(
                        candidate
                    )
                )

                if text or pages:
                    return (
                        text,
                        pages,
                    )

            except Exception:
                pass

    return clean_text(
        value
    ), []


def extract_complete_document(
    result: Any,
) -> tuple[
    str,
    list[dict[str, Any]],
]:

    text, raw_pages = (
        _extract_from_value(
            result
        )
    )

    pages = normalize_document_pages(
        raw_pages
    )

    if not pages and text:
        pages = text_to_review_pages(
            text
        )

    if not text and pages:
        text = "\n\n".join(
            page["content"]
            for page in pages
        )

    if not text or not pages:
        raise RuntimeError(
            "The document-generation response "
            "did not contain a usable document."
        )

    return (
        clean_text(text),
        pages,
    )


async def _call_method_flexibly(
    method: Any,
    kwargs: dict[str, Any],
) -> Any:

    try:
        signature = inspect.signature(
            method
        )

        parameters = signature.parameters

        accepts_kwargs = any(
            parameter.kind
            == inspect.Parameter.VAR_KEYWORD
            for parameter
            in parameters.values()
        )

        if accepts_kwargs:
            selected = kwargs
        else:
            selected = {
                key: value
                for key, value
                in kwargs.items()
                if key in parameters
            }

    except Exception:
        selected = kwargs

    if inspect.iscoroutinefunction(
        method
    ):
        return await method(
            **selected
        )

    return await asyncio.to_thread(
        method,
        **selected,
    )


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

    method_names = (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
        "respond",
    )

    kwargs = {
        "customer_request": customer_request,
        "service": request.service,
        "form_data": request.form_data,
        "context": context,
        "event": request.event,
        "message": request.message,
        "original_request": customer_request,
        "create_work": True,
    }

    last_error: Exception | None = None

    for method_name in method_names:

        method = getattr(
            ada,
            method_name,
            None,
        )

        if not callable(
            method
        ):
            continue

        try:

            result = await _call_method_flexibly(
                method,
                kwargs,
            )

            text, pages = (
                extract_complete_document(
                    result
                )
            )

            metadata = (
                result
                if isinstance(
                    result,
                    dict,
                )
                else {}
            )

            return (
                text,
                pages,
                metadata,
            )

        except Exception as error:

            last_error = error

            print(
                "[DOCUMENT] "
                f"{method_name} failed: "
                f"{error}"
            )

    if last_error:
        raise last_error

    raise RuntimeError(
        "No document-generation method "
        "is available."
    )


# ============================================================
# JOB / REVIEW
# ============================================================

def make_review_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    normalized = normalize_document_pages(
        pages
    )

    return [
        {
            "page_number": index,
            "position": index,
            "status": "queued",
            "content": page["content"],
            "review": "",
            "error": None,
        }
        for index, page
        in enumerate(
            normalized,
            1,
        )
    ]


def synchronize_job_document(
    job: dict[str, Any],
) -> None:

    document_text = clean_text(
        job.get(
            "document_text",
            "",
        )
    )

    pages = normalize_document_pages(
        job.get(
            "document_pages"
        )
    )

    if not pages and document_text:
        pages = text_to_review_pages(
            document_text
        )

    if pages and not document_text:
        document_text = "\n\n".join(
            page["content"]
            for page in pages
        )

    job["document_text"] = document_text
    job["document_pages"] = pages

    if not job.get(
        "review_pages"
    ):
        job["review_pages"] = make_review_pages(
            pages
        )

    job["total_pages"] = len(
        pages
    )


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

    normalized = (
        normalize_document_pages(
            pages
        )
    )

    if not normalized and document_text:
        normalized = text_to_review_pages(
            document_text
        )

    if not document_text and normalized:
        document_text = "\n\n".join(
            page["content"]
            for page in normalized
        )

    job = {
        "job_id": job_id,
        "customer_id": request.customer_id,
        "service": request.service,
        "original_request": original_request,
        "context": request.context,
        "status": "reviewing",
        "review_started": True,
        "review_finished": False,
        "review_error": None,
        "progress": {
            "completed": 0,
            "total": len(normalized),
        },
        "document_text": document_text,
        "document_pages": normalized,
        "review_pages": make_review_pages(
            normalized
        ),
        "assembled_review": "",
        "current_version": 1,
        "version_id": f"{job_id}:1",
        "saved_version": None,
        "storage_reference": None,
        "work_id": None,
        "approved": False,
        "paid": False,
    }

    _jobs[job_id] = job

    return job


async def review_callback(
    job_id: str,
    page_number: int,
    status: str = "reviewing",
    review: str = "",
    error: str | None = None,
    **kwargs,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return

    review_pages = job.get(
        "review_pages",
        [],
    )

    for page in review_pages:

        if safe_int(
            page.get(
                "page_number"
            )
        ) == page_number:

            page["status"] = status
            page["review"] = review
            page["error"] = error

            break

    completed = sum(
        1
        for page in review_pages
        if page.get("status")
        in {
            "reviewed",
            "complete",
            "approved",
        }
    )

    job["progress"] = {
        "completed": completed,
        "total": len(review_pages),
    }


async def run_review(
    job_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return

    try:

        synchronize_job_document(
            job
        )

        pages = job.get(
            "document_pages",
            [],
        )

        if not pages:
            raise RuntimeError(
                "There is no document to review."
            )

        ada = get_session(
            job.get(
                "customer_id"
            ),
            job_id,
            job.get(
                "service"
            ),
        )

        review_method = getattr(
            ada,
            "review_document_pages",
            None,
        )

        # If the current intelligence implementation
        # provides the reviewer, use it.
        if callable(
            review_method
        ):

            result = await _call_method_flexibly(
                review_method,
                {
                    "pages": pages,
                    "service": job.get(
                        "service"
                    ),
                    "context": job.get(
                        "context"
                    ),
                    "customer_request": job.get(
                        "original_request"
                    ),
                    "event": "review_document",
                    "progress_callback": (
                        review_callback
                    ),
                },
            )

            if isinstance(
                result,
                dict,
            ):

                returned_pages = (
                    result.get(
                        "pages"
                    )
                    or result.get(
                        "document_pages"
                    )
                )

                if returned_pages:

                    normalized = normalize_document_pages(
                        returned_pages
                    )

                    if normalized:
                        job["document_pages"] = normalized
                        job["document_text"] = "\n\n".join(
                            page["content"]
                            for page in normalized
                        )

                        job["review_pages"] = (
                            make_review_pages(
                                normalized
                            )
                        )

                assembled = clean_text(
                    result.get(
                        "assembled_review",
                        result.get(
                            "review",
                            "",
                        ),
                    )
                )

                if assembled:
                    job["assembled_review"] = assembled

        # ----------------------------------------------------
        # PERSIST FIRST
        # ----------------------------------------------------

        synchronize_job_document(
            job
        )

        saved = save_document_to_storage(
            job_id
        )

        job["current_version"] = (
            saved["version"]
        )

        job["version_id"] = (
            saved["version_id"]
        )

        job["saved_version"] = (
            saved["version"]
        )

        job["storage_reference"] = (
            saved["storage_reference"]
        )

        job["work_id"] = (
            saved["work_id"]
        )

        total = len(
            job["document_pages"]
        )

        job["progress"] = {
            "completed": total,
            "total": total,
        }

        job["status"] = (
            "review_complete"
        )

        job["review_finished"] = True
        job["review_error"] = None

        print(
            "[REVIEW] "
            f"completed job={job_id} "
            f"version={job['version_id']}"
        )

    except Exception as error:

        job["status"] = "review_error"
        job["review_finished"] = False
        job["review_error"] = str(
            error
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

    if not job.get(
        "document_pages"
    ):
        synchronize_job_document(
            job
        )

    if not job.get(
        "document_pages"
    ):
        return False

    if job.get(
        "status"
    ) == "review_complete":
        return True

    job["status"] = "reviewing"
    job["review_started"] = True

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(
                job_id
            )
        )
    )

    return True


def make_job_response(
    job: dict[str, Any],
) -> dict[str, Any]:

    synchronize_job_document(
        job
    )

    pages = job.get(
        "document_pages",
        [],
    )

    return {
        "success": True,
        "job_id": job.get(
            "job_id"
        ),
        "customer_id": job.get(
            "customer_id"
        ),
        "service": job.get(
            "service"
        ),
        "status": job.get(
            "status"
        ),
        "version_id": job.get(
            "version_id"
        ),
        "review_finished": job.get(
            "review_finished"
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
        "assembled_review": job.get(
            "assembled_review"
        ),
        "approved": job.get(
            "approved",
            False,
        ),
        "paid": job.get(
            "paid",
            False,
        ),
        "work_id": job.get(
            "work_id"
        ),
        "error": job.get(
            "review_error"
        ),
        "review_url": (
            f"/review.html?"
            f"job_id={job.get('job_id')}"
        ),
    }


# ============================================================
# FILE EXTRACTION
# ============================================================

def extract_document(
    data: bytes,
    filename: str,
) -> str:

    suffix = (
        Path(
            filename
        ).suffix.lower()
    )

    if suffix in {
        ".txt",
        ".csv",
        ".md",
        ".text",
    }:
        return data.decode(
            "utf-8",
            "replace",
        )

    if suffix == ".pdf":

        try:
            from pypdf import PdfReader

            reader = PdfReader(
                io.BytesIO(data)
            )

            pages = []

            for page in reader.pages:
                pages.append(
                    clean_text(
                        page.extract_text()
                        or ""
                    )
                )

            return "\n\n".join(
                page
                for page in pages
                if page
            )

        except Exception as error:
            raise RuntimeError(
                f"Unable to read PDF: {error}"
            )

    if suffix in {
        ".docx",
        ".xlsx",
        ".pptx",
    }:

        try:

            with zipfile.ZipFile(
                io.BytesIO(data)
            ) as archive:

                texts: list[str] = []

                for name in archive.namelist():

                    if not name.endswith(
                        ".xml"
                    ):
                        continue

                    if not (
                        name.startswith(
                            "word/"
                        )
                        or name.startswith(
                            "xl/"
                        )
                        or name.startswith(
                            "ppt/"
                        )
                    ):
                        continue

                    try:

                        root = ET.fromstring(
                            archive.read(
                                name
                            )
                        )

                        for element in root.iter():
                            if element.tag.endswith(
                                "}t"
                            ) and element.text:
                                texts.append(
                                    element.text
                                )

                    except Exception:
                        continue

                return clean_text(
                    "\n".join(
                        texts
                    )
                )

        except Exception as error:
            raise RuntimeError(
                f"Unable to read document: {error}"
            )

    return data.decode(
        "utf-8",
        "replace",
    )


def uploaded_document_pages(
    text: str,
) -> list[dict[str, Any]]:

    return text_to_review_pages(
        text
    )


# ============================================================
# HTML ROUTES
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
            f"{filename} not found",
            404,
            "PAGE_NOT_FOUND",
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


@app.get("/")
async def root():
    return serve_html(
        "index.html"
    )


@app.get("/index.html")
async def index_page():
    return serve_html(
        "index.html"
    )


@app.get("/conversation.html")
async def conversation_page():
    return serve_html(
        "conversation.html"
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
# HEALTH / STATUS
# ============================================================

@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "ok",
        "service": "Naija Pocket Business Center",
    }


@app.get("/api/status")
async def api_status():

    try:
        configured = bool(
            is_configured()
        )
    except Exception:
        configured = False

    return {
        "success": True,
        "status": "ok",
        "configured": configured,
        "api": "online",
        "workspace_api": True,
        "approval_api": True,
        "payment_api": True,
        "download_api": True,
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    job_id: str | None = Form(None),
    customer_id: str | None = Form(None),
    service: str | None = Form(None),
    client_request_id: str | None = Form(None),
):

    try:

        data = await file.read()

        if len(data) > MAX_UPLOAD:
            return application_error(
                "UPLOAD",
                "Uploaded file is too large.",
                413,
                "UPLOAD_TOO_LARGE",
            )

        text = extract_document(
            data,
            file.filename or "document.txt",
        )

        pages = uploaded_document_pages(
            text
        )

        if not pages:
            return application_error(
                "UPLOAD",
                "No readable document content was found.",
                400,
                "EMPTY_DOCUMENT",
            )

        job_id_value = (
            str(job_id).strip()
            if job_id
            else str(uuid.uuid4())
        )

        return {
            "success": True,
            "filename": file.filename,
            "job_id": job_id_value,
            "customer_id": customer_id,
            "service": service,
            "client_request_id": client_request_id,
            "document_text": text,
            "total_pages": len(pages),
            "document_pages": pages,
            "pages": pages,
        }

    except Exception as error:
        return application_error(
            "UPLOAD",
            error,
        )


# ============================================================
# CHAT API
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat,
):

    try:

        if not request.activate_intelligence:
            return application_error(
                "CHAT",
                "Intelligence activation is required.",
                400,
                "INTELLIGENCE_NOT_ACTIVATED",
            )

        if not is_configured():
            return application_error(
                "CHAT",
                "AI service is not configured.",
                503,
                "AI_NOT_CONFIGURED",
            )

        job_id = (
            str(
                request.job_id
                or ""
            ).strip()
            or str(
                uuid.uuid4()
            )
        )

        context = build_context(
            request
        )

        customer_request = (
            build_customer_request(
                request
            )
        )

        # ----------------------------------------------------
        # Guidance-only chat
        # ----------------------------------------------------

        if request.guidance_only:

            if not request.message.strip():
                return application_error(
                    "CHAT",
                    "Message is required.",
                    400,
                    "MESSAGE_REQUIRED",
                )

            ada = get_session(
                request.customer_id,
                job_id,
                request.service,
            )

            result = await _call_method_flexibly(
                ada.respond,
                {
                    "message": request.message,
                    "context": context,
                },
            )

            reply = clean_text(
                result
            )

            return {
                "success": True,
                "reply": reply,
                "job_id": job_id,
                "created_work": False,
            }

        create_requested = (
            request.create_work
            or event_value(
                request.event
            )
            in {
                "form_submitted_create_work",
                "create_work",
                "create_document",
                "submit_service",
                "service_submitted",
            }
        )

        supplied_pages = normalize_document_pages(
            request.document_pages
        )

        supplied_text = clean_text(
            request.document_text
            or ""
        )

        if not supplied_pages and supplied_text:
            supplied_pages = text_to_review_pages(
                supplied_text
            )

        if supplied_pages and not supplied_text:
            supplied_text = "\n\n".join(
                page["content"]
                for page in supplied_pages
            )

        # ----------------------------------------------------
        # Existing document supplied by workspace
        # ----------------------------------------------------

        if create_requested and (
            supplied_text
            or supplied_pages
        ):

            job = create_job(
                job_id,
                request,
                customer_request,
                supplied_text,
                supplied_pages,
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
                    "work_created": True,
                    "review_started": True,
                    "reply": (
                        "Your work has been prepared "
                        "and sent for review."
                    ),
                }
            )

            return response

        # ----------------------------------------------------
        # Create new document using intelligence
        # ----------------------------------------------------

        if create_requested:

            ada = get_session(
                request.customer_id,
                job_id,
                request.service,
            )

            (
                document_text,
                pages,
                metadata,
            ) = await create_document_with_intelligence(
                ada,
                request,
                customer_request,
                context,
            )

            print(
                "[DOCUMENT] "
                f"generated chars={len(document_text)} "
                f"pages={len(pages)}"
            )

            job = create_job(
                job_id,
                request,
                customer_request,
                document_text,
                pages,
            )

            job["metadata"] = metadata

            start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "created_work": True,
                    "work_created": True,
                    "review_started": True,
                    "reply": (
                        "Your work has been prepared "
                        "and sent for review."
                    ),
                }
            )

            return response

        # ----------------------------------------------------
        # Document supplied without create event
        # ----------------------------------------------------

        if supplied_pages or supplied_text:

            job = _jobs.get(
                job_id
            )

            if not job:

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    supplied_text,
                    supplied_pages,
                )

            else:

                job["document_text"] = supplied_text
                job["document_pages"] = supplied_pages
                job["review_pages"] = make_review_pages(
                    supplied_pages
                )
                job["status"] = "reviewing"
                job["review_finished"] = False

            start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "created_work": False,
                    "review_started": True,
                    "reply": (
                        "Your document has been received "
                        "and is being reviewed."
                    ),
                }
            )

            return response

        # ----------------------------------------------------
        # Normal conversation
        # ----------------------------------------------------

        if not request.message.strip():
            return application_error(
                "CHAT",
                "Message is required.",
                400,
                "MESSAGE_REQUIRED",
            )

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        result = await _call_method_flexibly(
            ada.respond,
            {
                "message": request.message,
                "context": context,
            },
        )

        return {
            "success": True,
            "reply": clean_text(
                result
            ),
            "job_id": job_id,
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
        str(job_id)
    )

    if not job:
        return application_error(
            "REVIEW",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if job.get(
        "status"
    ) == "reviewing":
        start_review(
            str(job_id)
        )

    return make_job_response(
        job
    )


@app.get("/api/review/pages")
async def get_review_pages(
    job_id: str,
):

    job = _jobs.get(
        str(job_id)
    )

    if not job:
        return application_error(
            "REVIEW_PAGES",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if job.get(
        "status"
    ) == "reviewing":
        start_review(
            str(job_id)
        )

    synchronize_job_document(
        job
    )

    return {
        "success": True,
        "job_id": job_id,
        "version_id": job.get(
            "version_id"
        ),
        "status": job.get(
            "status"
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
        "approved": job.get(
            "approved",
            False,
        ),
        "paid": job.get(
            "paid",
            False,
        ),
    }


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction,
):

    job_id = str(
        request.job_id
    ).strip()

    job = _jobs.get(
        job_id
    )

    if not job:
        return application_error(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    instruction = clean_text(
        request.instruction
    )

    if not instruction:
        return application_error(
            "CORRECTION",
            "Correction instruction is required.",
            400,
            "INSTRUCTION_REQUIRED",
        )

    if job.get(
        "status"
    ) in {
        "reviewing",
        "correcting",
    }:

        return application_error(
            "CORRECTION",
            "The document is currently being processed.",
            409,
            "JOB_BUSY",
        )

    synchronize_job_document(
        job
    )

    if not job.get(
        "document_pages"
    ):
        return application_error(
            "CORRECTION",
            "No document is available for correction.",
            400,
            "DOCUMENT_NOT_FOUND",
        )

    old_task = _correction_tasks.get(
        job_id
    )

    if old_task and not old_task.done():
        old_task.cancel()

    current_version = (
        safe_int(
            job.get(
                "current_version"
            ),
            1,
        )
        or 1
    )

    new_version = (
        current_version + 1
    )

    job["current_version"] = (
        new_version
    )

    job["version_id"] = (
        f"{job_id}:{new_version}"
    )

    job["status"] = "correcting"
    job["review_finished"] = False
    job["review_error"] = None

    async def correction_worker():

        try:

            ada = get_session(
                job.get(
                    "customer_id"
                ),
                job_id,
                job.get(
                    "service"
                ),
            )

            method = getattr(
                ada,
                "correct_document",
                None,
            )

            if not callable(
                method
            ):
                raise RuntimeError(
                    "Document correction is not available."
                )

            result = await _call_method_flexibly(
                method,
                {
                    "document_pages": job.get(
                        "document_pages"
                    ),
                    "pages": job.get(
                        "document_pages"
                    ),
                    "correction": instruction,
                    "instruction": instruction,
                    "service": job.get(
                        "service"
                    ),
                    "context": job.get(
                        "context"
                    ),
                    "progress_callback": None,
                },
            )

            corrected_text, corrected_pages = (
                extract_complete_document(
                    result
                )
            )

            job["document_text"] = (
                corrected_text
            )

            job["document_pages"] = (
                corrected_pages
            )

            job["review_pages"] = (
                make_review_pages(
                    corrected_pages
                )
            )

            job["assembled_review"] = ""
            job["approved"] = False
            job["paid"] = False

            job["status"] = "reviewing"
            job["review_started"] = True
            job["review_finished"] = False

            start_review(
                job_id
            )

        except Exception as error:

            job["status"] = "review_error"
            job["review_finished"] = False
            job["review_error"] = str(
                error
            )

            traceback.print_exc()

    _correction_tasks[job_id] = (
        asyncio.create_task(
            correction_worker()
        )
    )

    return {
        "success": True,
        "job_id": job_id,
        "version_id": job["version_id"],
        "status": "correcting",
        "review_started": True,
    }


# ============================================================
# APPROVAL
#
# THIS IS THE ENGINEER'S WORKING SIDE MERGED INTO THE
# CURRENT PERSISTENT APPROVAL SYSTEM.
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

        if not supplied_job_id:
            return application_error(
                "APPROVAL",
                "Job ID is required.",
                400,
                "JOB_ID_REQUIRED",
            )

        if not supplied_version_id:
            return application_error(
                "APPROVAL",
                "Version ID is required.",
                400,
                "VERSION_ID_REQUIRED",
            )

        print(
            "[APPROVAL] "
            f"request job_id={supplied_job_id} "
            f"version_id={supplied_version_id}"
        )

        # ----------------------------------------------------
        # 1. Try current runtime job first.
        # ----------------------------------------------------

        job = _jobs.get(
            supplied_job_id
        )

        # ----------------------------------------------------
        # 2. If Render restarted / runtime state disappeared,
        #    recover the exact saved document from persistence.
        # ----------------------------------------------------

        if not job:

            job = recover_saved_job_for_approval(
                supplied_job_id,
                supplied_version_id,
            )

        # ----------------------------------------------------
        # 3. Last defensive lookup by exact version ID.
        # ----------------------------------------------------

        if not job:

            for candidate in _jobs.values():

                if (
                    str(
                        candidate.get(
                            "version_id",
                            ""
                        )
                    ).strip()
                    == supplied_version_id
                ):
                    job = candidate
                    break

        if not job:
            return application_error(
                "APPROVAL",
                (
                    "The reviewed document could not "
                    "be recovered."
                ),
                404,
                "REVIEWED_DOCUMENT_NOT_FOUND",
            )

        synchronize_job_document(
            job
        )

        actual_version_id = str(
            job.get(
                "version_id",
                ""
            )
        ).strip()

        # ----------------------------------------------------
        # CRITICAL VERSION PROTECTION
        # ----------------------------------------------------

        if actual_version_id != supplied_version_id:

            print(
                "[APPROVAL] "
                f"VERSION MISMATCH "
                f"job={supplied_job_id} "
                f"supplied={supplied_version_id} "
                f"actual={actual_version_id}"
            )

            return application_error(
                "APPROVAL",
                (
                    "The document version has changed. "
                    "Please return to review and approve "
                    "the current version."
                ),
                409,
                "VERSION_MISMATCH",
            )

        if job.get(
            "status"
        ) not in {
            "review_complete",
            "approved",
            "paid",
        }:

            return application_error(
                "APPROVAL",
                (
                    "The document is not ready "
                    "for approval."
                ),
                409,
                "DOCUMENT_NOT_READY",
            )

        if not job.get(
            "document_pages"
        ):
            return application_error(
                "APPROVAL",
                "The reviewed document has no pages.",
                409,
                "DOCUMENT_EMPTY",
            )

        # ----------------------------------------------------
        # APPROVE
        # ----------------------------------------------------

        job["approved"] = True
        job["status"] = "approved"

        numeric_job_id = safe_int(
            supplied_job_id
        )

        if numeric_job_id is None:
            return application_error(
                "APPROVAL",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        # ----------------------------------------------------
        # PERSIST APPROVED STATE WHEN SUPPORTED
        # ----------------------------------------------------

        try:
            update_job_status(
                numeric_job_id,
                "approved",
            )
        except Exception as error:
            print(
                "[APPROVAL] "
                f"Database status update warning: "
                f"{error}"
            )

        # ----------------------------------------------------
        # ENGINEER'S WORKING PAYMENT TRANSITION
        #
        # Create/reuse the payment and return the payment
        # page URL containing job_id, version_id and payment_id.
        # ----------------------------------------------------

        payment_id = None

        try:

            existing_payment = persisted_record_to_dict(
                get_latest_payment(
                    numeric_job_id
                )
            )

            existing_status = event_value(
                existing_payment.get(
                    "status"
                )
            )

            existing_version = str(
                existing_payment.get(
                    "version_id",
                    ""
                )
                or ""
            ).strip()

            if (
                existing_payment
                and existing_status
                in {
                    "pending",
                    "created",
                    "unpaid",
                }
                and (
                    not existing_version
                    or existing_version
                    == supplied_version_id
                )
            ):

                payment_id = safe_int(
                    existing_payment.get(
                        "id"
                    )
                )

        except Exception as error:

            print(
                "[APPROVAL] "
                f"Existing payment lookup warning: "
                f"{error}"
            )

        if payment_id is None:

            # Preserve current service/payment amount when
            # one is already available in the job.
            amount = job.get(
                "amount"
            )

            if amount is None:
                amount = job.get(
                    "price"
                )

            try:
                amount = float(
                    amount
                )
            except Exception:
                amount = 0.0

            if amount <= 0:
                # The engineer's payment bridge used 5000.
                # We use that only as the safe fallback when
                # no current job amount exists.
                amount = 5000.0

            created_payment = create_payment(
                job_id=numeric_job_id,
                amount=amount,
                payment_method="bank_transfer",
            )

            payment_id = safe_int(
                created_payment
            )

            if payment_id is None:

                created_payment_dict = (
                    persisted_record_to_dict(
                        created_payment
                    )
                )

                payment_id = safe_int(
                    created_payment_dict.get(
                        "id"
                    )
                )

        if payment_id is None:
            return application_error(
                "APPROVAL",
                "Unable to create payment record.",
                500,
                "PAYMENT_CREATION_FAILED",
            )

        # ----------------------------------------------------
        # THIS IS THE IMPORTANT PAGE TRANSITION.
        # ----------------------------------------------------

        payment_url = (
            f"/payment.html?"
            f"job_id={supplied_job_id}"
            f"&version_id={supplied_version_id}"
            f"&payment_id={payment_id}"
        )

        print(
            "[APPROVAL] "
            f"SUCCESS job={supplied_job_id} "
            f"version={supplied_version_id} "
            f"payment_id={payment_id} "
            f"payment_url={payment_url}"
        )

        return {
            "success": True,
            "approved": True,
            "status": "approved",
            "job_id": supplied_job_id,
            "version_id": supplied_version_id,
            "payment_id": payment_id,
            "payment_url": payment_url,
            "review_url": (
                f"/review.html?"
                f"job_id={supplied_job_id}"
            ),
        }

    except Exception as error:

        return application_error(
            "APPROVAL",
            error,
        )


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: PaymentCreate,
):

    try:

        numeric_job_id = safe_int(
            request.job_id
        )

        if numeric_job_id is None:
            return application_error(
                "PAYMENT_CREATE",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        amount = float(
            request.amount
        )

        if amount <= 0:
            return application_error(
                "PAYMENT_CREATE",
                "Payment amount must be greater than zero.",
                400,
                "INVALID_AMOUNT",
            )

        existing = persisted_record_to_dict(
            get_latest_payment(
                numeric_job_id
            )
        )

        if existing:

            existing_status = event_value(
                existing.get(
                    "status"
                )
            )

            if existing_status in {
                "pending",
                "created",
                "unpaid",
            }:

                existing_id = safe_int(
                    existing.get(
                        "id"
                    )
                )

                if existing_id is not None:
                    return {
                        "success": True,
                        "payment_id": existing_id,
                        "status": existing_status,
                        "reused": True,
                    }

        payment_id = create_payment(
            job_id=numeric_job_id,
            amount=amount,
            payment_method=(
                request.payment_method
                or "bank_transfer"
            ),
        )

        payment_dict = (
            persisted_record_to_dict(
                payment_id
            )
        )

        actual_payment_id = (
            safe_int(
                payment_id
            )
        )

        if actual_payment_id is None:
            actual_payment_id = safe_int(
                payment_dict.get(
                    "id"
                )
            )

        return {
            "success": True,
            "payment_id": actual_payment_id,
            "status": "pending",
        }

    except Exception as error:

        return application_error(
            "PAYMENT_CREATE",
            error,
        )


# ============================================================
# PAYMENT COMPLETE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: PaymentCreate,
):

    try:

        numeric_job_id = safe_int(
            request.job_id
        )

        if numeric_job_id is None:
            return application_error(
                "PAYMENT_COMPLETE",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        job = _jobs.get(
            str(request.job_id)
        )

        if not job:

            recovered = (
                recover_saved_job_for_approval(
                    str(request.job_id),
                    (
                        request.order_number
                        or ""
                    ),
                )
            )

            if recovered:
                job = recovered

        existing = persisted_record_to_dict(
            get_latest_payment(
                numeric_job_id
            )
        )

        if existing:

            status = event_value(
                existing.get(
                    "status"
                )
            )

            if status in {
                "paid",
                "completed",
                "confirmed",
            }:

                if job:
                    job["paid"] = True
                    job["status"] = "paid"

                return {
                    "success": True,
                    "status": "paid",
                    "payment_id": safe_int(
                        existing.get(
                            "id"
                        )
                    ),
                    "job_id": str(
                        request.job_id
                    ),
                }

            if status in {
                "pending",
                "created",
                "unpaid",
            }:

                return {
                    "success": True,
                    "status": "pending",
                    "payment_id": safe_int(
                        existing.get(
                            "id"
                        )
                    ),
                    "job_id": str(
                        request.job_id
                    ),
                }

        payment_id = create_payment(
            job_id=numeric_job_id,
            amount=float(
                request.amount
            ),
            payment_method=(
                request.payment_method
                or "bank_transfer"
            ),
        )

        return {
            "success": True,
            "status": "pending",
            "payment_id": safe_int(
                payment_id
            ),
            "job_id": str(
                request.job_id
            ),
        }

    except Exception as error:

        return application_error(
            "PAYMENT_COMPLETE",
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
            return application_error(
                "PAYMENT_STATUS",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        payment = persisted_record_to_dict(
            get_latest_payment(
                numeric_job_id
            )
        )

        job = _jobs.get(
            str(job_id)
        )

        if not payment:

            return {
                "success": True,
                "job_id": job_id,
                "status": "not_found",
                "paid": False,
                "activated": False,
            }

        status = event_value(
            payment.get(
                "status"
            )
        )

        paid = status in {
            "paid",
            "completed",
            "confirmed",
        }

        if job:

            job["paid"] = paid

            if paid:
                job["status"] = "paid"

        return {
            "success": True,
            "job_id": job_id,
            "payment_id": safe_int(
                payment.get(
                    "id"
                )
            ),
            "status": status,
            "paid": paid,
            "activated": False,
        }

    except Exception as error:

        return application_error(
            "PAYMENT_STATUS",
            error,
        )


@app.get("/api/payment")
async def payment_page_api(
    job_id: str,
    version_id: str,
):

    try:

        numeric_job_id = safe_int(
            job_id
        )

        if numeric_job_id is None:
            return application_error(
                "PAYMENT",
                "Invalid job ID.",
                400,
                "INVALID_JOB_ID",
            )

        payment = persisted_record_to_dict(
            get_latest_payment(
                numeric_job_id
            )
        )

        if not payment:

            return {
                "success": True,
                "job_id": job_id,
                "version_id": version_id,
                "status": "not_found",
                "paid": False,
            }

        payment_version = str(
            payment.get(
                "version_id",
                ""
            )
            or ""
        ).strip()

        if (
            payment_version
            and payment_version
            != version_id
        ):

            return application_error(
                "PAYMENT",
                "Payment version does not match the document version.",
                409,
                "VERSION_MISMATCH",
            )

        status = event_value(
            payment.get(
                "status"
            )
        )

        return {
            "success": True,
            "job_id": job_id,
            "version_id": version_id,
            "payment_id": safe_int(
                payment.get(
                    "id"
                )
            ),
            "status": status,
            "paid": status in {
                "paid",
                "completed",
                "confirmed",
            },
        }

    except Exception as error:

        return application_error(
            "PAYMENT",
            error,
        )


# ============================================================
# BACK OFFICE
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs(
    admin_key: str | None = None,
):

    try:

        jobs = get_back_office_jobs()

        normalized = []

        for item in jobs or []:
            normalized.append(
                persisted_record_to_dict(
                    item
                )
            )

        return {
            "success": True,
            "jobs": normalized,
        }

    except Exception as error:

        return application_error(
            "BACK_OFFICE",
            error,
        )


@app.post("/api/back-office/activate-download")
async def activate_download(
    request: DownloadActivation,
):

    try:

        if request.admin_key != ADMIN_KEY:
            return application_error(
                "ACTIVATION",
                "Invalid admin key.",
                403,
                "INVALID_ADMIN_KEY",
            )

        work_id = safe_int(
            request.work_id
        )

        if work_id is None:
            return application_error(
                "ACTIVATION",
                "Invalid work ID.",
                400,
                "INVALID_WORK_ID",
            )

        result = activate_work_download(
            work_id
        )

        if not result:
            return application_error(
                "ACTIVATION",
                "Unable to activate download.",
                404,
                "WORK_NOT_FOUND",
            )

        activated = None

        for item in (
            get_back_office_jobs()
            or []
        ):

            record = (
                persisted_record_to_dict(
                    item
                )
            )

            if safe_int(
                record.get(
                    "id"
                )
            ) == work_id:

                activated = record
                break

        return {
            "success": True,
            "work_id": work_id,
            "activated": True,
            "work": activated,
        }

    except Exception as error:

        return application_error(
            "ACTIVATION",
            error,
        )


# ============================================================
# PAYMENT CONFIRMATION
# ============================================================

@app.post("/api/payment/confirm")
async def payment_confirm(
    request: PaymentConfirm,
):

    try:

        if request.admin_key != ADMIN_KEY:
            return application_error(
                "PAYMENT_CONFIRM",
                "Invalid admin key.",
                403,
                "INVALID_ADMIN_KEY",
            )

        payment_id = safe_int(
            request.payment_id
        )

        if payment_id is None:
            return application_error(
                "PAYMENT_CONFIRM",
                "Invalid payment ID.",
                400,
                "INVALID_PAYMENT_ID",
            )

        payment = persisted_record_to_dict(
            get_payment(
                payment_id
            )
        )

        if not payment:
            return application_error(
                "PAYMENT_CONFIRM",
                "Payment not found.",
                404,
                "PAYMENT_NOT_FOUND",
            )

        update_payment_status(
            payment_id,
            "paid",
        )

        numeric_job_id = safe_int(
            payment.get(
                "job_id"
            )
        )

        if numeric_job_id is not None:

            try:
                update_job_status(
                    numeric_job_id,
                    "paid",
                )
            except Exception as error:
                print(
                    "[PAYMENT_CONFIRM] "
                    f"Job status warning: {error}"
                )

            runtime_job = _jobs.get(
                str(numeric_job_id)
            )

            if runtime_job:

                runtime_job["paid"] = True
                runtime_job["approved"] = True
                runtime_job["status"] = "paid"

        return {
            "success": True,
            "payment_id": payment_id,
            "status": "paid",
            "job_id": numeric_job_id,
        }

    except Exception as error:

        return application_error(
            "PAYMENT_CONFIRM",
            error,
        )


# ============================================================
# SECURE DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    work_id: int,
    version_id: str,
):

    try:

        numeric_work_id = safe_int(
            work_id
        )

        if numeric_work_id is None:
            return application_error(
                "DOWNLOAD",
                "Invalid work ID.",
                400,
                "INVALID_WORK_ID",
            )

        work = persisted_record_to_dict(
            get_activated_work(
                numeric_work_id
            )
        )

        if not work:
            return application_error(
                "DOWNLOAD",
                "Download has not been activated.",
                403,
                "NOT_ACTIVATED",
            )

        storage_reference = str(
            work.get(
                "storage_reference",
                "",
            )
            or ""
        ).strip()

        if not storage_reference:
            return application_error(
                "DOWNLOAD",
                "Storage reference is missing.",
                404,
                "STORAGE_REFERENCE_MISSING",
            )

        filepath = Path(
            storage_reference
        )

        # ----------------------------------------------------
        # SECURITY: downloaded file must live inside the
        # application's document storage directory.
        # ----------------------------------------------------

        try:

            resolved_root = (
                DOCUMENT_ROOT
                .resolve()
            )

            resolved_file = (
                filepath
                .resolve()
            )

            if not resolved_file.is_relative_to(
                resolved_root
            ):
                return application_error(
                    "DOWNLOAD",
                    "Invalid storage location.",
                    403,
                    "INVALID_STORAGE_LOCATION",
                )

        except Exception:
            return application_error(
                "DOWNLOAD",
                "Invalid storage location.",
                403,
                "INVALID_STORAGE_LOCATION",
            )

        # ----------------------------------------------------
        # Rebuild from database if Render's local disk is gone.
        # ----------------------------------------------------

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
                    "Activated document file is missing.",
                    404,
                    "FILE_MISSING",
                )

        if not filepath.exists() or not filepath.is_file():
            return application_error(
                "DOWNLOAD",
                "Activated document file is missing.",
                404,
                "FILE_MISSING",
            )

        saved_version = safe_int(
            work.get(
                "version"
            )
        )

        if saved_version is not None:

            expected_version = (
                f"{work.get('job_id', '')}:{saved_version}"
            )

            if (
                version_id
                and ":"
                in version_id
                and version_id != expected_version
            ):

                # Compare the version number itself where
                # database job_id formatting differs.
                supplied_version = (
                    version_id.split(
                        ":"
                    )[-1]
                )

                if supplied_version != str(
                    saved_version
                ):
                    return application_error(
                        "DOWNLOAD",
                        "Requested document version does not match the activated version.",
                        409,
                        "VERSION_MISMATCH",
                    )

        filename = (
            f"NPBC_Job"
            f"{work.get('job_id', numeric_work_id)}"
            f"_v"
            f"{saved_version or 1}"
            f".txt"
        )

        print(
            "[DOWNLOAD] "
            f"serving work_id={numeric_work_id} "
            f"version_id={version_id} "
            f"path={filepath}"
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
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
):

    try:

        ada = get_session(
            customer_id,
            job_id,
            service,
        )

        clear_method = getattr(
            ada,
            "clear_history",
            None,
        )

        if callable(
            clear_method
        ):

            result = clear_method()

            if inspect.isawaitable(
                result
            ):
                await result

        return {
            "success": True,
            "cleared": True,
        }

    except Exception as error:

        return application_error(
            "CHAT_CLEAR",
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
        "[NPBC] FastAPI application starting"
    )

    print(
        "[NPBC] Intelligence-first API enabled"
    )

    try:
        configured = bool(
            is_configured()
        )
    except Exception:
        configured = False

    print(
        f"[NPBC] Intelligence configured: {configured}"
    )

    print(
        "[NPBC] Customer page-count support enabled"
    )

    print(
        "[NPBC] Complete document preservation enabled"
    )

    print(
        "[NPBC] Review system enabled"
    )

    print(
        "[NPBC] Persistent document storage enabled"
    )

    print(
        "[NPBC] Persistent approval recovery enabled"
    )

    print(
        "[NPBC] Approval -> payment transition enabled"
    )

    print(
        "[NPBC] Payment confirmation enabled"
    )

    print(
        "[NPBC] Back-office download activation enabled"
    )

    print(
        "[NPBC] Secure download enabled"
    )

    print(
        f"[NPBC] Document root: {DOCUMENT_ROOT}"
    )

    print(
        "=================================================="
    )


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "ada_api:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
        reload=False,
    )
