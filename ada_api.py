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

DEBUG = os.getenv("ADA_DEBUG_ERRORS", "true").lower() in {
    "1", "true", "yes", "on"
}

MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)

# REVIEW DISPLAY chunking only.
# It is NOT a customer page-count requirement.
REVIEW_CHUNK_CHARS = int(
    os.getenv("ADA_REVIEW_CHUNK_CHARS", "7000")
)

REVIEW_MIN_CHARS = int(
    os.getenv("ADA_REVIEW_MIN_CHARS", "2500")
)

BASE = Path(__file__).resolve().parent


# ============================================================
# DATABASE DOCUMENT STORAGE
# ============================================================

DOCUMENT_ROOT = BASE / "data" / "documents"
DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)

ADMIN_KEY = os.getenv(
    "NPBC_ADMIN_KEY",
    "npbc_admin_2026",
)


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

app = FastAPI(
    title="Naija Pocket Business Center",
    version="intelligence-first-v9",
)

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

    text = re.sub(
        r"```(?:markdown|md|text)?",
        "",
        text,
        flags=re.I,
    )

    return text.replace("```", "").strip()


def application_error(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):
    print(f"[{stage}] {type(error).__name__}: {error}")

    if isinstance(error, Exception):
        traceback.print_exc()

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_type": (
                type(error).__name__
                if isinstance(error, Exception)
                else "ApplicationError"
            ),
            "error_message": (
                str(error)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# ============================================================
# TARGETED PERSISTENCE HELPER
# ============================================================

def persisted_record_to_dict(
    record: Any,
) -> dict[str, Any]:
    """
    Convert a database record such as sqlite3.Row into a normal
    dictionary.

    This is used only by the Review -> Approve persistence and
    recovery flow so persisted records can be read safely without
    changing database.py.
    """

    if record is None:
        return {}

    if isinstance(record, dict):
        return dict(record)

    try:
        return dict(record)
    except Exception:
        pass

    try:
        keys = record.keys()
        return {
            key: record[key]
            for key in keys
        }
    except Exception:
        return {}


# ============================================================
# DOCUMENT STORAGE
# ============================================================

def save_document_to_storage(
    job_id_str: str,
) -> dict[str, Any]:
    """
    Persist the final reviewed document before the review is
    considered complete.

    This uses the existing local document storage and the existing
    save_customer_work() database workflow. No second storage
    system is created.

    The returned metadata is also recorded in the runtime job so
    approval can continue using the exact persisted version.
    """

    job_id_str = str(job_id_str).strip()

    if not job_id_str:
        raise ValueError(
            "Cannot save a document without a job ID."
        )

    job = _jobs.get(job_id_str)

    if not job:
        raise RuntimeError(
            f"Job {job_id_str} is not available for persistence."
        )

    job_id = safe_int(job_id_str)

    if job_id is None:
        raise ValueError(
            f"Invalid job ID: {job_id_str}"
        )

    document_text = clean_text(
        job.get("document_text", "")
    )

    if not document_text:
        raise ValueError(
            f"Job {job_id_str} has no document text to save."
        )

    version = (
        safe_int(
            job.get("current_version"),
            1,
        )
        or 1
    )

    try:
        latest_record = get_latest_work(
            job_id
        )
    except Exception as error:
        raise RuntimeError(
            "Could not read the existing saved work record "
            f"for job {job_id_str}: {error}"
        ) from error

    latest = persisted_record_to_dict(
        latest_record
    )

    # --------------------------------------------------------
    # If this exact version has already been persisted and its
    # storage reference still exists, reuse it.
    # --------------------------------------------------------

    if latest:

        latest_version = (
            safe_int(
                latest.get("version"),
                0,
            )
            or 0
        )

        latest_reference = str(
            latest.get(
                "storage_reference"
            )
            or ""
        ).strip()

        if (
            latest_version >= version
            and latest_reference
        ):

            existing_path = Path(
                latest_reference
            )

            if existing_path.exists() and existing_path.is_file():

                saved_version = latest_version
                storage_reference = str(
                    existing_path
                )
                work_id = safe_int(
                    latest.get("id"),
                    None,
                )

                version_id = (
                    f"{job_id_str}:{saved_version}"
                )

                job["current_version"] = (
                    saved_version
                )
                job["version_id"] = (
                    version_id
                )
                job["saved_version"] = (
                    saved_version
                )
                job["storage_reference"] = (
                    storage_reference
                )
                job["work_id"] = work_id

                print(
                    "[STORAGE] Existing saved version reused "
                    f"job={job_id_str} "
                    f"version={saved_version} "
                    f"work_id={work_id} "
                    f"path={storage_reference}"
                )

                return {
                    "success": True,
                    "job_id": job_id_str,
                    "version": saved_version,
                    "version_id": version_id,
                    "storage_reference": storage_reference,
                    "work_id": work_id,
                }

    # --------------------------------------------------------
    # Create/use the existing document storage directory.
    # --------------------------------------------------------

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

    if not filepath.exists() or not filepath.is_file():
        raise RuntimeError(
            "The reviewed document was not successfully written "
            f"to storage: {filepath}"
        )

    # --------------------------------------------------------
    # Existing database persistence mechanism.
    # Do NOT create another storage system.
    # --------------------------------------------------------

    work_id = save_customer_work(
        job_id=job_id,
        work_title=(
            job.get(
                "service",
                "Business Document",
            )
            or "Business Document"
        ),
        work_type="document",
        storage_type="local_file",
        storage_reference=str(filepath),
        work_status="completed",
        notes=(
            f"Version {version} finalized after review"
        ),
    )

    if not work_id:
        raise RuntimeError(
            "The reviewed document was written to storage, "
            "but the persistent work record could not be created."
        )

    # --------------------------------------------------------
    # Read the actual persisted work record back.
    #
    # save_customer_work() determines the database version, so
    # the persisted record is the authoritative version.
    # --------------------------------------------------------

    try:
        persisted_record = get_latest_work(
            job_id
        )
    except Exception as error:
        raise RuntimeError(
            "The document was saved, but the persisted work "
            f"record could not be verified: {error}"
        ) from error

    persisted = persisted_record_to_dict(
        persisted_record
    )

    if not persisted:
        raise RuntimeError(
            "The document was saved, but no persisted work "
            "record could be verified."
        )

    saved_version = (
        safe_int(
            persisted.get("version"),
            version,
        )
        or version
    )

    storage_reference = str(
        persisted.get(
            "storage_reference"
        )
        or filepath
    ).strip()

    persisted_work_id = safe_int(
        persisted.get("id"),
        work_id,
    )

    if not storage_reference:
        raise RuntimeError(
            "The persisted work record has no storage reference."
        )

    persisted_path = Path(
        storage_reference
    )

    if not persisted_path.exists() or not persisted_path.is_file():
        raise RuntimeError(
            "The persisted storage reference does not point to "
            f"a valid document: {storage_reference}"
        )

    version_id = (
        f"{job_id_str}:{saved_version}"
    )

    # --------------------------------------------------------
    # Record the authoritative persistent state in _jobs.
    # --------------------------------------------------------

    job["current_version"] = (
        saved_version
    )

    job["version_id"] = (
        version_id
    )

    job["saved_version"] = (
        saved_version
    )

    job["storage_reference"] = (
        storage_reference
    )

    job["work_id"] = (
        persisted_work_id
    )

    print(
        "[STORAGE] saved and verified "
        f"job={job_id_str} "
        f"version={saved_version} "
        f"work_id={persisted_work_id} "
        f"chars={len(document_text)} "
        f"path={storage_reference}"
    )

    return {
        "success": True,
        "job_id": job_id_str,
        "version": saved_version,
        "version_id": version_id,
        "storage_reference": storage_reference,
        "work_id": persisted_work_id,
    }


# ============================================================
# PERSISTENT APPROVAL RECOVERY
# ============================================================

def recover_saved_job_for_approval(
    supplied_job_id: str,
    supplied_version_id: str,
) -> dict[str, Any] | None:
    """
    Recover the exact reviewed/saved document when the runtime
    job dictionary no longer contains it.

    Approval is tied to job_id + version_id.

    The saved work record is used as persistent recovery data.
    The recovered document, review pages, current version,
    version ID, storage reference and work ID are restored into
    _jobs before the normal approval flow continues.
    """

    numeric_job_id = safe_int(
        supplied_job_id,
        None,
    )

    if numeric_job_id is None:
        print(
            "[APPROVAL] Persistent recovery skipped: "
            f"job_id={supplied_job_id!r} is not numeric"
        )
        return None

    # --------------------------------------------------------
    # Recover the persisted job record.
    # --------------------------------------------------------

    try:
        persisted_job_record = get_job(
            numeric_job_id
        )
    except Exception as error:
        print(
            "[APPROVAL] Could not read persisted job "
            f"job_id={numeric_job_id}: {error}"
        )
        persisted_job_record = None

    persisted_job = persisted_record_to_dict(
        persisted_job_record
    )

    # --------------------------------------------------------
    # Recover the persisted work record.
    # --------------------------------------------------------

    try:
        work_record = get_latest_work(
            numeric_job_id
        )
    except Exception as error:
        print(
            "[APPROVAL] Could not read saved work "
            f"job_id={numeric_job_id}: {error}"
        )
        work_record = None

    work = persisted_record_to_dict(
        work_record
    )

    if not work:
        print(
            "[APPROVAL] No saved work record available for "
            f"job_id={numeric_job_id}"
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

    # --------------------------------------------------------
    # The approval request must identify the exact persisted
    # version.
    # --------------------------------------------------------

    if supplied_version_id:
        if supplied_version_id != expected_version_id:
            print(
                "[APPROVAL] Saved work version mismatch: "
                f"requested={supplied_version_id} "
                f"saved={expected_version_id}"
            )
            return None

    storage_reference = str(
        work.get(
            "storage_reference"
        )
        or ""
    ).strip()

    if not storage_reference:
        print(
            "[APPROVAL] Saved work has no storage reference: "
            f"job_id={numeric_job_id}"
        )
        return None

    # --------------------------------------------------------
    # Resolve and secure the persisted document path.
    # --------------------------------------------------------

    filepath = Path(
        storage_reference
    )

    try:

        document_root = (
            DOCUMENT_ROOT.resolve()
        )

        resolved_filepath = (
            filepath.resolve()
        )

        if not resolved_filepath.is_relative_to(
            document_root
        ):
            print(
                "[APPROVAL] Saved work path rejected: "
                f"path={resolved_filepath}"
            )
            return None

        filepath = resolved_filepath

    except Exception as error:
        print(
            "[APPROVAL] Saved work path could not be resolved: "
            f"{error}"
        )
        return None

    if not filepath.exists() or not filepath.is_file():
        print(
            "[APPROVAL] Saved document file is missing: "
            f"path={filepath}"
        )
        return None

    # --------------------------------------------------------
    # Restore the exact saved document.
    # --------------------------------------------------------

    try:
        document_text = clean_text(
            filepath.read_text(
                encoding="utf-8"
            )
        )
    except Exception as error:
        print(
            "[APPROVAL] Could not read saved document: "
            f"{error}"
        )
        return None

    if not document_text:
        print(
            "[APPROVAL] Saved document is empty: "
            f"job_id={numeric_job_id}"
        )
        return None

    # --------------------------------------------------------
    # Rebuild the review pages from the exact saved document.
    # --------------------------------------------------------

    pages = text_to_review_pages(
        document_text
    )

    if not pages:
        print(
            "[APPROVAL] Saved document has no review pages: "
            f"job_id={numeric_job_id}"
        )
        return None

    # --------------------------------------------------------
    # Recover metadata from the persisted job/work records.
    #
    # database.py returns sqlite3.Row records, so these values
    # are read from the converted dictionaries.
    # --------------------------------------------------------

    service = None
    customer_id = None
    original_request = ""
    context = None

    if persisted_job:

        service = (
            persisted_job.get("service")
            or persisted_job.get("service_type")
            or persisted_job.get("work_title")
        )

        customer_id = (
            persisted_job.get(
                "customer_id"
            )
        )

        original_request = clean_text(
            persisted_job.get(
                "original_request",
                persisted_job.get(
                    "customer_request",
                    "",
                ),
            )
        )

        context_value = persisted_job.get(
            "context"
        )

        if context_value:
            context = str(
                context_value
            ).strip()

    if not service:
        service = (
            work.get(
                "work_title"
            )
            or "Business Document"
        )

    work_id = safe_int(
        work.get("id"),
        None,
    )

    # --------------------------------------------------------
    # Restore the complete approval-ready runtime job.
    # --------------------------------------------------------

    job = {
        "job_id": supplied_job_id,
        "customer_id": customer_id,
        "service": service,
        "original_request": original_request,
        "context": context,

        "status": "review_complete",

        "review_started": True,
        "review_finished": True,
        "review_error": None,

        "progress": {
            "completed": len(pages),
            "total": len(pages),
        },

        "document_text": document_text,

        "document_pages": pages,

        "review_pages": make_review_pages(
            pages
        ),

        "assembled_review": "",

        "current_version": saved_version,
        "version_id": expected_version_id,

        "saved_version": saved_version,
        "storage_reference": str(filepath),
        "work_id": work_id,

        "approved": False,
        "paid": False,
    }

    # --------------------------------------------------------
    # If persisted job data contains matching version state,
    # retain it.
    # --------------------------------------------------------

    persisted_version = safe_int(
        persisted_job.get(
            "current_version"
        ),
        saved_version,
    )

    if (
        persisted_version is not None
        and persisted_version == saved_version
    ):
        job["current_version"] = (
            persisted_version
        )

    persisted_version_id = str(
        persisted_job.get(
            "version_id",
            "",
        )
        or ""
    ).strip()

    if (
        persisted_version_id
        == expected_version_id
    ):
        job["version_id"] = (
            persisted_version_id
        )

    if persisted_job.get(
        "approved"
    ):
        job["approved"] = True

    if persisted_job.get(
        "paid"
    ):
        job["paid"] = True

    # --------------------------------------------------------
    # IMPORTANT:
    # Restore the recovered job into the runtime dictionary
    # before returning so the existing approval flow continues
    # normally.
    # --------------------------------------------------------

    _jobs[supplied_job_id] = job

    print(
        "[APPROVAL] PERSISTENT RECOVERY SUCCESS "
        f"job_id={supplied_job_id} "
        f"version_id={job['version_id']} "
        f"version={job['current_version']} "
        f"work_id={job.get('work_id')} "
        f"storage_reference={job.get('storage_reference')} "
        f"pages={len(pages)}"
    )

    return job


# ============================================================
# PAGE NORMALIZATION
# ============================================================

def normalize_document_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    if pages is None:
        return []

    if isinstance(pages, str):
        text = clean_text(pages)

        if text:
            return [
                {
                    "page_number": 1,
                    "position": 1,
                    "content": text,
                }
            ]

        return []

    if not isinstance(pages, list):
        return []

    output: list[dict[str, Any]] = []

    for index, item in enumerate(pages, 1):

        if isinstance(item, dict):

            content = clean_text(
                item.get(
                    "content",
                    item.get(
                        "text",
                        item.get(
                            "document_text",
                            "",
                        ),
                    ),
                )
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
                output.append(
                    {
                        "page_number": index,
                        "position": index,
                        "content": content,
                    }
                )

    return output


def text_to_review_pages(
    text: str,
) -> list[dict[str, Any]]:

    text = clean_text(text)

    if not text:
        return []

    paragraphs = [
        p.strip()
        for p in re.split(
            r"\n\s*\n",
            text,
        )
        if p.strip()
    ]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current
        nonlocal current_length

        if current:
            chunks.append(
                "\n\n".join(current).strip()
            )

            current = []
            current_length = 0

    for paragraph in paragraphs:

        if len(paragraph) <= REVIEW_CHUNK_CHARS:

            if (
                current
                and current_length
                + len(paragraph)
                + 2
                > REVIEW_CHUNK_CHARS
            ):
                flush()

            current.append(paragraph)
            current_length += len(paragraph) + 2

            continue

        for sentence in re.split(
            r"(?<=[.!?])\s+",
            paragraph,
        ):

            sentence = sentence.strip()

            if not sentence:
                continue

            if (
                current
                and current_length
                + len(sentence)
                + 1
                > REVIEW_CHUNK_CHARS
            ):
                flush()

            current.append(sentence)
            current_length += len(sentence) + 1

    flush()

    if (
        len(chunks) >= 2
        and len(chunks[-1]) < REVIEW_MIN_CHARS
    ):
        if (
            len(chunks[-2])
            + len(chunks[-1])
            + 2
            <= REVIEW_CHUNK_CHARS
        ):
            chunks[-2] += (
                "\n\n" + chunks[-1]
            )
            chunks.pop()

    return [
        {
            "page_number": i,
            "position": i,
            "content": chunk,
        }
        for i, chunk in enumerate(chunks, 1)
        if chunk.strip()
    ]


def normalize_pages_for_review(
    text: str,
    supplied_pages: Any = None,
) -> list[dict[str, Any]]:

    text = clean_text(text)

    if text:
        return text_to_review_pages(text)

    return normalize_document_pages(
        supplied_pages
    )


def normalize_pages(
    pages: Any,
) -> list[dict[str, Any]]:
    return normalize_document_pages(pages)


# ============================================================
# SESSION
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

    ada = _sessions.get(key)

    if ada is None:

        ada = AdaResponse(
            service=service
        )

        _sessions[key] = ada

    elif service:

        setter = getattr(
            ada,
            "set_service",
            None,
        )

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
# CUSTOMER REQUEST
# ============================================================

def build_customer_request(
    request: Chat,
) -> str:

    parts: list[str] = []

    if request.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + request.service.strip()
        )

    if request.form_data:

        information: list[str] = []

        for key, value in request.form_data.items():

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

            information.append(
                f"{label}: {value_text}"
            )

        if information:
            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(information)
            )

    if (
        request.context
        and request.context.strip()
    ):
        parts.append(
            "ADDITIONAL CUSTOMER CONTEXT:\n"
            + request.context.strip()
        )

    if (
        request.message
        and request.message.strip()
    ):
        parts.append(
            "CUSTOMER REQUEST:\n"
            + request.message.strip()
        )

    return "\n\n".join(parts).strip()


def build_context(
    request: Chat,
) -> str | None:

    parts: list[str] = []

    if (
        request.context
        and request.context.strip()
    ):
        parts.append(
            request.context.strip()
        )

    if request.customer_id:
        parts.append(
            "CUSTOMER ID:\n"
            + request.customer_id
        )

    if request.client_request_id:
        parts.append(
            "CLIENT REQUEST ID:\n"
            + request.client_request_id
        )

    result = "\n\n".join(parts).strip()

    return result or None


# ============================================================
# INTELLIGENCE RESULT EXTRACTION
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
    depth: int = 0,
) -> tuple[str, list[dict[str, Any]]]:

    if depth > 4 or value is None:
        return "", []

    if isinstance(value, str):

        text = clean_text(value)

        return (
            text,
            text_to_review_pages(text)
            if text
            else [],
        )

    if isinstance(value, list):

        pages = normalize_pages(value)

        if pages:

            text = "\n\n".join(
                p["content"]
                for p in pages
            )

            return (
                text,
                text_to_review_pages(text),
            )

        return "", []

    if isinstance(value, dict):

        for key in _TEXT_KEYS:

            candidate = value.get(key)

            if (
                isinstance(candidate, str)
                and clean_text(candidate)
            ):

                text = clean_text(
                    candidate
                )

                return (
                    text,
                    text_to_review_pages(text),
                )

        for key in _PAGE_KEYS:

            candidate = value.get(key)

            text, pages = _extract_from_value(
                candidate,
                depth + 1,
            )

            if text or pages:
                return text, pages

        for key, candidate in value.items():

            if (
                key in _TEXT_KEYS
                or key in _PAGE_KEYS
            ):
                continue

            text, pages = _extract_from_value(
                candidate,
                depth + 1,
            )

            if text or pages:
                return text, pages

        return "", []

    try:
        data = vars(value)
    except Exception:
        data = None

    if isinstance(data, dict):
        return _extract_from_value(
            data,
            depth + 1,
        )

    for key in _TEXT_KEYS + _PAGE_KEYS:

        try:
            candidate = getattr(
                value,
                key,
                None,
            )
        except Exception:
            candidate = None

        text, pages = _extract_from_value(
            candidate,
            depth + 1,
        )

        if text or pages:
            return text, pages

    return "", []


def extract_complete_document(
    result: Any,
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
]:

    text, pages = _extract_from_value(
        result
    )

    if not text:
        raise ValueError(
            "The intelligence completed the operation "
            "but returned no usable document content."
        )

    pages = text_to_review_pages(text)

    if not pages:
        raise ValueError(
            "Usable document text was returned but "
            "no review pages could be constructed."
        )

    metadata: dict[str, Any] = {}

    if isinstance(result, dict):

        metadata = {
            k: v
            for k, v in result.items()
            if k not in (
                _TEXT_KEYS
                + _PAGE_KEYS
            )
        }

    return (
        text,
        pages,
        metadata,
    )


# ============================================================
# DOCUMENT CREATION CALLER
# ============================================================

async def _call_method_flexibly(
    method: Any,
    kwargs: dict[str, Any],
) -> Any:

    try:
        signature = inspect.signature(
            method
        )

    except (TypeError, ValueError):

        return await asyncio.to_thread(
            method,
            **kwargs,
        )

    parameters = signature.parameters

    accepts_kwargs = any(
        p.kind
        == inspect.Parameter.VAR_KEYWORD
        for p in parameters.values()
    )

    if accepts_kwargs:
        call_kwargs = kwargs

    else:
        call_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in parameters
        }

    return await asyncio.to_thread(
        method,
        **call_kwargs,
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

    for method_name in (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    ):

        method = getattr(
            ada,
            method_name,
            None,
        )

        if not callable(method):
            continue

        attempted.append(
            method_name
        )

        result = await _call_method_flexibly(
            method,
            kwargs,
        )

        try:
            return extract_complete_document(
                result
            )

        except ValueError:
            continue

    respond = getattr(
        ada,
        "respond",
        None,
    )

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

        return extract_complete_document(
            result
        )

    raise AttributeError(
        "AdaResponse has no usable document creation method. "
        f"Methods checked: "
        f"{', '.join(attempted) or 'none'}."
    )


# ============================================================
# JOBS / REVIEW
# ============================================================

def make_review_pages(
    pages: Any,
) -> list[dict[str, Any]]:

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
        for i, page in enumerate(
            normalized,
            1,
        )
    ]


def synchronize_job_document(
    job: dict[str, Any],
) -> None:

    pages = normalize_pages(
        job.get(
            "document_pages",
            [],
        )
    )

    job["document_pages"] = pages

    if (
        len(
            job.get(
                "review_pages",
                [],
            )
        )
        != len(pages)
    ):
        job["review_pages"] = (
            make_review_pages(pages)
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

    normalized = text_to_review_pages(
        document_text
    )

    if not normalized:
        normalized = normalize_pages(
            pages
        )

    if not normalized:
        raise ValueError(
            "No complete document content "
            "was returned by intelligence."
        )

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
        "approved": False,
        "paid": False,
    }

    _jobs[job_id] = job

    print(
        f"[JOB] created "
        f"job={job_id} "
        f"document_chars={len(document_text)} "
        f"total_pages={len(normalized)}"
    )

    return job


def review_callback(
    job_id: str,
):

    def callback(
        update: dict[str, Any]
    ):

        job = _jobs.get(
            job_id
        )

        if not job:
            return

        update_type = event_value(
            update.get("type")
        )

        page_number = str(
            update.get(
                "page_number",
                "",
            )
        )

        if update_type == "page_started":

            for page in job[
                "review_pages"
            ]:

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = (
                        "reviewing"
                    )

        elif update_type == "page_completed":

            for page in job[
                "review_pages"
            ]:

                if str(
                    page["page_number"]
                ) != page_number:
                    continue

                page["status"] = (
                    "reviewed"
                )

                page["review"] = clean_text(
                    update.get(
                        "review",
                        "",
                    )
                )

                if update.get(
                    "content"
                ) is not None:

                    page["content"] = (
                        clean_text(
                            update.get(
                                "content"
                            )
                        )
                    )

                page["error"] = None

            try:
                completed = int(
                    update.get(
                        "position",
                        0,
                    )
                )
            except Exception:
                completed = 0

            if completed:
                job[
                    "progress"
                ][
                    "completed"
                ] = min(
                    completed,
                    len(
                        job[
                            "document_pages"
                        ]
                    ),
                )

        elif update_type == "page_error":

            for page in job[
                "review_pages"
            ]:

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = (
                        "error"
                    )

                    page["error"] = str(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )

        elif update_type == "review_completed":

            total = len(
                job["document_pages"]
            )

            # ------------------------------------------------
            # IMPORTANT:
            # The intelligence callback only reports that its
            # review operation has completed.
            #
            # The document is NOT considered application-level
            # review_complete here.
            #
            # run_review() must persist the final document first.
            # ------------------------------------------------

            job["progress"] = {
                "completed": total,
                "total": total,
            }

            job["assembled_review"] = (
                clean_text(
                    update.get(
                        "assembled_review",
                        "",
                    )
                )
            )

            job["status"] = "reviewing"
            job["review_finished"] = False
            job["review_error"] = None

    return callback


async def run_review(
    job_id: str
):

    job = _jobs.get(
        job_id
    )

    if not job:
        return

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

        pages = normalize_pages(
            job["document_pages"]
        )

        method = getattr(
            ada,
            "review_document_pages",
            None,
        )

        if not callable(method):
            raise AttributeError(
                "AdaResponse has no "
                "review_document_pages() method."
            )

        result = await _call_method_flexibly(
            method,
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
                "event": "send_for_review",
                "progress_callback": (
                    review_callback(job_id)
                ),
            },
        )

        if isinstance(result, dict):

            returned_pages = normalize_pages(
                result.get(
                    "pages",
                    [],
                )
            )

            if returned_pages:

                returned_text = (
                    "\n\n".join(
                        p["content"]
                        for p in returned_pages
                    )
                )

                if returned_text:

                    job["document_text"] = (
                        returned_text
                    )

                    job["document_pages"] = (
                        text_to_review_pages(
                            returned_text
                        )
                    )

                    job["review_pages"] = (
                        make_review_pages(
                            job[
                                "document_pages"
                            ]
                        )
                    )

            job["assembled_review"] = (
                clean_text(
                    result.get(
                        "assembled_review",
                        job.get(
                            "assembled_review",
                            "",
                        ),
                    )
                )
            )

        total = len(
            job["document_pages"]
        )

        job["progress"] = {
            "completed": total,
            "total": total,
        }

        # ----------------------------------------------------
        # IMPORTANT:
        # Persist the final reviewed document FIRST.
        #
        # If persistence fails, review does NOT become
        # review_complete and approval cannot proceed.
        # ----------------------------------------------------

        saved = save_document_to_storage(
            job_id
        )

        if not saved.get(
            "success"
        ):
            raise RuntimeError(
                "The reviewed document could not be persisted."
            )

        # ----------------------------------------------------
        # Only after successful persistence do we mark the
        # review as complete.
        # ----------------------------------------------------

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

        job["status"] = (
            "review_complete"
        )

        job["review_started"] = True
        job["review_finished"] = True
        job["review_error"] = None

        print(
            f"[REVIEW] job={job_id} "
            f"total_pages={total} "
            f"persisted_version={saved['version']} "
            f"version_id={saved['version_id']} "
            f"work_id={saved['work_id']}"
        )

    except asyncio.CancelledError:
        raise

    except Exception as error:

        job["status"] = (
            "review_error"
        )

        job["review_finished"] = True

        job["review_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

        traceback.print_exc()


def start_review(
    job_id: str
) -> bool:

    job = _jobs.get(
        job_id
    )

    if (
        not job
        or not job.get(
            "document_pages"
        )
        or job.get(
            "status"
        ) != "reviewing"
    ):
        return False

    existing = _review_tasks.get(
        job_id
    )

    if existing and not existing.done():
        return False

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(job_id)
        )
    )

    return True


def make_job_response(
    job: dict[str, Any]
) -> dict[str, Any]:

    synchronize_job_document(
        job
    )

    pages = job[
        "document_pages"
    ]

    progress = job.get(
        "progress",
        {},
    )

    return {
        "success": True,
        "job_id": job["job_id"],
        "customer_id": job.get(
            "customer_id"
        ),
        "service": job.get(
            "service"
        ),
        "status": job.get(
            "status"
        ),
        "current_version": job.get(
            "current_version",
            1,
        ),
        "version_id": job.get(
            "version_id"
        ),
        "review_started": job.get(
            "review_started",
            False,
        ),
        "review_finished": job.get(
            "review_finished",
            False,
        ),
        "approved": job.get(
            "approved",
            False,
        ),
        "paid": job.get(
            "paid",
            False,
        ),
        "progress": {
            "completed": int(
                progress.get(
                    "completed",
                    0,
                )
            ),
            "total": len(pages),
        },
        "total_pages": len(pages),
        "document_pages": pages,
        "pages": pages,
        "review_pages": job.get(
            "review_pages",
            [],
        ),
        "document_text": job.get(
            "document_text",
            "",
        ),
        "assembled_review": job.get(
            "assembled_review",
            "",
        ),
        "error": job.get(
            "review_error"
        ),
        "review_url": (
            f"/review.html?"
            f"job_id={job['job_id']}"
        ),
    }


# ============================================================
# DOCUMENT EXTRACTION / UPLOAD
# ============================================================

def extract_document(
    data: bytes,
    filename: str,
) -> str:

    suffix = Path(
        filename
    ).suffix.lower()

    if suffix in {
        ".txt",
        ".csv",
    }:

        return data.decode(
            "utf-8",
            "replace",
        )

    if suffix == ".pdf":

        from pypdf import PdfReader

        reader = PdfReader(
            io.BytesIO(data)
        )

        return "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    if suffix in {
        ".docx",
        ".xlsx",
        ".pptx",
    }:

        with zipfile.ZipFile(
            io.BytesIO(data)
        ) as archive:

            names = archive.namelist()

            if suffix == ".docx":

                names = (
                    ["word/document.xml"]
                    if "word/document.xml"
                    in names
                    else []
                )

            elif suffix == ".pptx":

                names = [
                    n
                    for n in names
                    if re.match(
                        r"ppt/slides/slide\d+\.xml",
                        n,
                    )
                ]

            else:

                names = [
                    n
                    for n in names
                    if re.match(
                        r"xl/worksheets/sheet\d+\.xml",
                        n,
                    )
                ]

            texts: list[str] = []

            for name in sorted(
                names
            ):

                root = ET.fromstring(
                    archive.read(name)
                )

                values = [
                    element.text or ""
                    for element in root.iter()
                    if (
                        isinstance(
                            element.tag,
                            str,
                        )
                        and element.tag.rsplit(
                            "}",
                            1,
                        )[-1]
                        == "t"
                    )
                ]

                if values:
                    texts.append(
                        " ".join(values)
                    )

            return "\n\n".join(
                texts
            )

    raise RuntimeError(
        "Unsupported document type: "
        f"{suffix or 'unknown'}"
    )


def uploaded_document_pages(
    filename: str,
    data: bytes,
) -> list[dict[str, Any]]:

    text = clean_text(
        extract_document(
            data,
            filename,
        )
    )

    if not text:
        raise ValueError(
            "The uploaded document "
            "contains no extractable text."
        )

    return text_to_review_pages(
        text
    )


# ============================================================
# HTML / HEALTH
# ============================================================

def serve_html(
    filename: str
):

    path = find_file(
        filename
    )

    if not path:
        return application_error(
            "PAGE",
            f"{filename} was not found.",
            404,
            "HTML_NOT_FOUND",
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
async def index():
    return serve_html(
        "index.html"
    )


@app.get("/conversation.html")
async def conversation():
    return serve_html(
        "conversation.html"
    )


@app.get("/workspace.html")
async def workspace():
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
            return application_error(
                "UPLOAD",
                "The uploaded file is empty.",
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:
            return application_error(
                "UPLOAD",
                "The uploaded document is too large.",
                413,
                "FILE_TOO_LARGE",
            )

        filename = (
            file.filename
            or "document"
        )

        pages = await asyncio.to_thread(
            uploaded_document_pages,
            filename,
            data,
        )

        job_id_value = (
            str(job_id or "").strip()
            or str(uuid.uuid4())
        )

        text = "\n\n".join(
            p["content"]
            for p in pages
        )

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

        return application_error(
            "UPLOAD",
            error,
            400,
            "DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat
):

    if not request.activate_intelligence:

        return application_error(
            "INTELLIGENCE",
            "Intelligence activation is disabled.",
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return application_error(
            "INTELLIGENCE",
            "Intelligence is not configured.",
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    job_id = (
        str(request.job_id or "").strip()
        or str(uuid.uuid4())
    )

    context = build_context(
        request
    )

    customer_request = (
        build_customer_request(
            request
        )
    )

    pages = normalize_pages(
        request.document_pages
    )

    document_text = clean_text(
        request.document_text
    )

    if not document_text and pages:

        document_text = "\n\n".join(
            p["content"]
            for p in pages
        )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        if request.guidance_only:

            if not request.message.strip():

                return application_error(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await _call_method_flexibly(
                ada.respond,
                {
                    "message": request.message.strip(),
                    "service": request.service,
                    "event": request.event,
                    "context": context,
                },
            )

            return {
                "success": True,
                "reply": clean_text(reply),
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

        if create_requested:

            if document_text or pages:

                complete_text = (
                    document_text
                    or "\n\n".join(
                        p["content"]
                        for p in pages
                    )
                )

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    complete_text,
                    pages,
                )

            else:

                if not customer_request:

                    return application_error(
                        "WORK_CREATION",
                        "The customer request contains no usable information.",
                        400,
                        "EMPTY_WORK_REQUEST",
                    )

                (
                    complete_text,
                    created_pages,
                    metadata,
                ) = await create_document_with_intelligence(
                    ada,
                    request,
                    customer_request,
                    context,
                )

                print(
                    "[PAG-INPUT] "
                    f"document_text_chars="
                    f"{len(complete_text)}"
                )

                print(
                    "[PAG-INPUT] "
                    f"generated_pages="
                    f"{len(created_pages)}"
                )

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    complete_text,
                    created_pages,
                )

                job[
                    "intelligence_metadata"
                ] = metadata

            started = start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "reply": (
                        "Your work has been prepared "
                        "and sent for review."
                    ),
                    "created_work": True,
                    "work_created": True,
                    "review_started": started,
                }
            )

            return response

        if pages or document_text:

            complete_text = (
                document_text
                or "\n\n".join(
                    p["content"]
                    for p in pages
                )
            )

            existing_job = _jobs.get(
                job_id
            )

            if existing_job:

                existing_job[
                    "document_text"
                ] = complete_text

                existing_job[
                    "document_pages"
                ] = text_to_review_pages(
                    complete_text
                )

                existing_job[
                    "review_pages"
                ] = make_review_pages(
                    existing_job[
                        "document_pages"
                    ]
                )

                existing_job[
                    "progress"
                ] = {
                    "completed": 0,
                    "total": len(
                        existing_job[
                            "document_pages"
                        ]
                    ),
                }

                existing_job[
                    "status"
                ] = "reviewing"

                existing_job[
                    "review_started"
                ] = True

                existing_job[
                    "review_finished"
                ] = False

                existing_job[
                    "review_error"
                ] = None

                existing_job[
                    "approved"
                ] = False

                existing_job[
                    "paid"
                ] = False

                job = existing_job

            else:

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    complete_text,
                    pages,
                )

            started = start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "reply": (
                        "Your document has been received "
                        "and is being reviewed."
                    ),
                    "created_work": True,
                    "review_started": started,
                }
            )

            return response

        if not request.message.strip():

            return application_error(
                "CHAT",
                "The chat message is empty.",
                400,
                "EMPTY_MESSAGE",
            )

        reply = await _call_method_flexibly(
            ada.respond,
            {
                "message": request.message.strip(),
                "service": request.service,
                "event": request.event,
                "context": context,
            },
        )

        return {
            "success": True,
            "reply": clean_text(reply),
            "job_id": job_id,
            "service": (
                request.service
                or getattr(
                    ada,
                    "service",
                    None,
                )
            ),
            "created_work": False,
        }

    except Exception as error:

        return application_error(
            "CHAT",
            error,
            500,
            "CHAT_ERROR",
        )


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def get_review(
    job_id: str
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "REVIEW",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    return make_job_response(
        job
    )


@app.get("/api/review/pages")
async def get_review_pages(
    job_id: str
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "REVIEW_PAGES",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    synchronize_job_document(
        job
    )

    return {
        "success": True,
        "job_id": job_id,
        "current_version": job[
            "current_version"
        ],
        "version_id": job[
            "version_id"
        ],
        "status": job[
            "status"
        ],
        "total_pages": len(
            job["document_pages"]
        ),
        "document_text": job.get(
            "document_text",
            "",
        ),
        "pages": job[
            "document_pages"
        ],
        "document_pages": job[
            "document_pages"
        ],
        "review_pages": job[
            "review_pages"
        ],
        "progress": job[
            "progress"
        ],
        "approved": job[
            "approved"
        ],
        "paid": job[
            "paid"
        ],
    }


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction
):

    job = _jobs.get(
        request.job_id
    )

    if not job:

        return application_error(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    instruction = (
        request.instruction.strip()
    )

    if not instruction:

        return application_error(
            "CORRECTION",
            "Correction instruction is empty.",
            400,
            "EMPTY_CORRECTION",
        )

    if job.get(
        "status"
    ) in {
        "reviewing",
        "correcting",
    }:

        return application_error(
            "CORRECTION",
            "The document is still being processed.",
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    if not job.get(
        "document_pages"
    ):

        return application_error(
            "CORRECTION",
            "There is no document available for correction.",
            409,
            "NO_DOCUMENT",
        )

    job["current_version"] += 1

    job["version_id"] = (
        f"{request.job_id}:"
        f"{job['current_version']}"
    )

    job.update(
        {
            "status": "correcting",
            "approved": False,
            "paid": False,
            "review_started": False,
            "review_finished": False,
            "review_error": None,
            "correction_instruction": instruction,
            "progress": {
                "completed": 0,
                "total": len(
                    job["document_pages"]
                ),
            },
        }
    )

    async def correction_worker():

        try:

            ada = get_session(
                job.get(
                    "customer_id"
                ),
                request.job_id,
                job.get(
                    "service"
                ),
            )

            method = getattr(
                ada,
                "correct_document",
                None,
            )

            if not callable(method):

                raise AttributeError(
                    "AdaResponse has no "
                    "correct_document() method."
                )

            result = await _call_method_flexibly(
                method,
                {
                    "document_pages": normalize_pages(
                        job[
                            "document_pages"
                        ]
                    ),
                    "correction": instruction,
                    "service": job.get(
                        "service"
                    ),
                    "context": job.get(
                        "context"
                    ),
                    "progress_callback": None,
                },
            )

            (
                corrected_text,
                corrected_pages,
                metadata,
            ) = extract_complete_document(
                result
            )

            job[
                "document_text"
            ] = corrected_text

            job[
                "document_pages"
            ] = corrected_pages

            job[
                "review_pages"
            ] = make_review_pages(
                corrected_pages
            )

            job[
                "intelligence_metadata"
            ] = metadata

            job[
                "status"
            ] = "reviewing"

            job[
                "review_started"
            ] = True

            job[
                "review_finished"
            ] = False

            job[
                "review_error"
            ] = None

            job[
                "progress"
            ] = {
                "completed": 0,
                "total": len(
                    corrected_pages
                ),
            }

            start_review(
                request.job_id
            )

        except Exception as error:

            job[
                "status"
            ] = "correction_error"

            job[
                "review_error"
            ] = {
                "type": type(error).__name__,
                "message": str(error),
            }

            traceback.print_exc()

    old_task = _correction_tasks.get(
        request.job_id
    )

    if old_task and not old_task.done():
        old_task.cancel()

    _correction_tasks[
        request.job_id
    ] = asyncio.create_task(
        correction_worker()
    )

    return {
        "success": True,
        "job_id": request.job_id,
        "status": "correcting",
        "version_id": job[
            "version_id"
        ],
        "current_version": job[
            "current_version"
        ],
        "message": (
            "Correction has started. "
            "The corrected document will "
            "be reviewed again."
        ),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval
):

    supplied_job_id = str(
        request.job_id or ""
    ).strip()

    supplied_version_id = str(
        request.version_id or ""
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
            "Document version ID is required.",
            400,
            "VERSION_ID_REQUIRED",
        )

    # --------------------------------------------------------
    # FIRST:
    # Resolve the exact active runtime job by job_id.
    # --------------------------------------------------------

    job = _jobs.get(
        supplied_job_id
    )

    resolved_job_id = supplied_job_id

    # --------------------------------------------------------
    # SECOND:
    # If the job ID is not present in memory, try the exact
    # version_id against the active runtime jobs.
    #
    # This does NOT identify a job by service name.
    # --------------------------------------------------------

    if not job:

        for candidate_job_id, candidate_job in _jobs.items():

            candidate_version_id = str(
                candidate_job.get(
                    "version_id",
                    ""
                )
            ).strip()

            if (
                candidate_version_id
                == supplied_version_id
            ):

                job = candidate_job

                resolved_job_id = str(
                    candidate_job.get(
                        "job_id",
                        candidate_job_id
                    )
                ).strip()

                print(
                    "[APPROVAL] "
                    f"Recovered active job by version_id: "
                    f"supplied_job_id={supplied_job_id} "
                    f"resolved_job_id={resolved_job_id} "
                    f"version_id={supplied_version_id}"
                )

                break

    # --------------------------------------------------------
    # THIRD:
    # If the version_id contains job_id:version, try the job
    # portion directly in the active runtime dictionary.
    # --------------------------------------------------------

    if not job:

        if ":" in supplied_version_id:

            possible_job_id = (
                supplied_version_id
                .split(":", 1)[0]
                .strip()
            )

            if possible_job_id:

                candidate_job = _jobs.get(
                    possible_job_id
                )

                if candidate_job:

                    candidate_version_id = str(
                        candidate_job.get(
                            "version_id",
                            ""
                        )
                    ).strip()

                    if (
                        candidate_version_id
                        == supplied_version_id
                    ):

                        job = candidate_job
                        resolved_job_id = (
                            possible_job_id
                        )

                        print(
                            "[APPROVAL] "
                            f"Recovered active job from version_id: "
                            f"job_id={resolved_job_id} "
                            f"version_id={supplied_version_id}"
                        )

    # --------------------------------------------------------
    # FOURTH — PERSISTENT RECOVERY:
    #
    # The reviewed document was already saved after review.
    # If the runtime process no longer has the job, recover the
    # exact saved version from the database/storage.
    # --------------------------------------------------------

    if not job:

        job = recover_saved_job_for_approval(
            supplied_job_id,
            supplied_version_id,
        )

        if job:

            resolved_job_id = str(
                job.get(
                    "job_id",
                    supplied_job_id,
                )
            ).strip()

    # --------------------------------------------------------
    # FINAL JOB NOT FOUND
    # --------------------------------------------------------

    if not job:

        print(
            "[APPROVAL] JOB_NOT_FOUND "
            f"supplied_job_id={supplied_job_id} "
            f"version_id={supplied_version_id} "
            f"active_jobs={list(_jobs.keys())}"
        )

        return application_error(
            "APPROVAL",
            "The exact reviewed document could not be recovered.",
            404,
            "JOB_NOT_FOUND",
        )

    # --------------------------------------------------------
    # EXACT VERSION CHECK
    # --------------------------------------------------------

    current_version_id = str(
        job.get(
            "version_id",
            ""
        )
    ).strip()

    if (
        not current_version_id
        or supplied_version_id
        != current_version_id
    ):

        return application_error(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    # --------------------------------------------------------
    # REVIEW MUST BE COMPLETE
    # --------------------------------------------------------

    if (
        job.get("status")
        not in {
            "review_complete",
            "approved",
            "paid",
        }
    ):

        return application_error(
            "APPROVAL",
            "The document review is not complete.",
            409,
            "REVIEW_NOT_COMPLETE",
        )

    # --------------------------------------------------------
    # DOCUMENT MUST EXIST
    # --------------------------------------------------------

    synchronize_job_document(
        job
    )

    if not job.get(
        "document_pages"
    ):

        return application_error(
            "APPROVAL",
            "The reviewed document contains no usable pages.",
            409,
            "DOCUMENT_NOT_AVAILABLE",
        )

    # --------------------------------------------------------
    # APPROVE THE EXACT CURRENT VERSION
    # --------------------------------------------------------

    job["approved"] = True
    job["status"] = "approved"

    print(
        "[APPROVAL] APPROVED "
        f"job_id={resolved_job_id} "
        f"version_id={current_version_id}"
    )

    return {
        "success": True,
        "job_id": resolved_job_id,
        "version_id": current_version_id,
        "current_version": job[
            "current_version"
        ],
        "approved": True,
        "status": "approved",
        "total_pages": len(
            job["document_pages"]
        ),
        "pages": job[
            "document_pages"
        ],
        "payment_url": (
            f"/payment.html?"
            f"job_id={resolved_job_id}"
            f"&version_id={current_version_id}"
        ),
    }


# ============================================================
# PAYMENT: CREATE
# ============================================================

@app.post("/api/payment/create")
async def api_create_payment(
    req: PaymentCreate
):

    if req.amount <= 0:

        return application_error(
            "PAYMENT",
            "Invalid amount",
            400,
        )

    job_id = safe_int(
        req.job_id
    )

    if job_id is None:

        return application_error(
            "PAYMENT",
            "Invalid job ID",
            400,
            "INVALID_JOB_ID",
        )

    job = get_job(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT",
            "Job not found",
            404,
        )

    existing = get_latest_payment(
        job_id
    )

    if (
        existing
        and existing.get(
            "payment_status"
        )
        == "pending"
    ):

        return {
            "success": True,
            "payment_id": existing["id"],
            "status": "pending",
        }

    ref = (
        f"NPBC-{job_id}-"
        f"{int(datetime.now(timezone.utc).timestamp())}"
    )

    payment_id = create_payment(
        job_id=job_id,
        amount=req.amount,
        payment_method=req.payment_method,
        payment_status="pending",
        currency="NGN",
        payment_reference=ref,
    )

    if not payment_id:

        return application_error(
            "PAYMENT",
            "Failed to create payment",
            500,
        )

    return {
        "success": True,
        "payment_id": payment_id,
        "status": "pending",
        "payment_reference": ref,
    }


# ============================================================
# PAYMENT: EXISTING FRONTEND COMPLETE ROUTE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    job_id: str,
    version_id: str,
    amount: float | None = None,
    payment_method: str = "bank_transfer",
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return application_error(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "approved"
    ):

        return application_error(
            "PAYMENT",
            "The document must be approved before payment.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

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

    existing = get_latest_payment(
        numeric_job_id
    )

    if existing:

        existing_status = existing.get(
            "payment_status"
        )

        if existing_status == "paid":

            job["paid"] = True
            job["status"] = "paid"

            activated_work = get_activated_work(
                numeric_job_id
            )

            response = {
                "success": True,
                "job_id": job_id,
                "version_id": version_id,
                "payment_id": existing["id"],
                "paid": True,
                "status": "paid",
                "payment_status": "paid",
                "total_pages": len(
                    job["document_pages"]
                ),
                "download_activated": bool(
                    activated_work
                ),
            }

            if activated_work:

                response["download_url"] = (
                    f"/api/download?"
                    f"job_id={job_id}"
                    f"&version_id={version_id}"
                )

            else:

                response["message"] = (
                    "Payment is confirmed. "
                    "The saved document is awaiting "
                    "Back Office download activation."
                )

            return response

        if existing_status == "pending":

            return {
                "success": True,
                "job_id": job_id,
                "version_id": version_id,
                "payment_id": existing["id"],
                "paid": False,
                "status": "pending",
                "payment_status": "pending",
                "total_pages": len(
                    job["document_pages"]
                ),
                "message": (
                    "Payment has been recorded "
                    "and is awaiting confirmation."
                ),
            }

    if amount is None:

        possible_amount = (
            job.get("amount")
            or job.get("price")
            or job.get("service_amount")
        )

        try:
            amount = (
                float(possible_amount)
                if possible_amount is not None
                else None
            )
        except (
            TypeError,
            ValueError,
        ):
            amount = None

    if amount is None or amount <= 0:

        return application_error(
            "PAYMENT",
            (
                "Payment amount is required "
                "to create the pending payment record."
            ),
            400,
            "PAYMENT_AMOUNT_REQUIRED",
        )

    reference = (
        f"NPBC-{numeric_job_id}-"
        f"{int(datetime.now(timezone.utc).timestamp())}"
    )

    payment_id = create_payment(
        job_id=numeric_job_id,
        amount=amount,
        payment_method=payment_method,
        payment_status="pending",
        currency="NGN",
        payment_reference=reference,
    )

    if not payment_id:

        return application_error(
            "PAYMENT",
            "Failed to create payment record.",
            500,
            "PAYMENT_CREATE_FAILED",
        )

    job["paid"] = False
    job["status"] = "approved"

    return {
        "success": True,
        "job_id": job_id,
        "version_id": version_id,
        "payment_id": payment_id,
        "payment_reference": reference,
        "paid": False,
        "status": "pending",
        "payment_status": "pending",
        "total_pages": len(
            job["document_pages"]
        ),
        "message": (
            "Payment has been recorded "
            "and is awaiting confirmation."
        ),
    }


# ============================================================
# PAYMENT: STATUS
# ============================================================

@app.get("/api/payment/status")
async def api_payment_status(
    job_id: str
):

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

    payment = get_latest_payment(
        numeric_job_id
    )

    if not payment:

        return {
            "success": True,
            "status": "none",
            "payment_status": "none",
        }

    status = payment.get(
        "payment_status"
    )

    job = _jobs.get(
        job_id
    )

    if job:

        if status == "paid":

            job["paid"] = True
            job["status"] = "paid"

        elif status == "pending":

            job["paid"] = False

    activated_work = get_activated_work(
        numeric_job_id
    )

    return {
        "success": True,
        "payment_id": payment["id"],
        "status": status,
        "payment_status": status,
        "download_activated": bool(
            activated_work
        ),
    }


# ============================================================
# PAYMENT: EXISTING FRONTEND STATE
# ============================================================

@app.get("/api/payment")
async def payment_state(
    job_id: str,
    version_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT_STATE",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return application_error(
            "PAYMENT_STATE",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "PAYMENT_STATE",
            "Invalid job ID.",
            400,
            "INVALID_JOB_ID",
        )

    payment = get_latest_payment(
        numeric_job_id
    )

    payment_status = (
        payment.get(
            "payment_status"
        )
        if payment
        else "none"
    )

    if payment_status == "paid":

        job["paid"] = True
        job["status"] = "paid"

    elif payment_status == "pending":

        job["paid"] = False

        if job["status"] == "paid":
            job["status"] = "approved"

    activated_work = get_activated_work(
        numeric_job_id
    )

    return {
        "success": True,
        "job_id": job_id,
        "version_id": version_id,
        "status": (
            "paid"
            if payment_status == "paid"
            else job["status"]
        ),
        "approved": job[
            "approved"
        ],
        "paid": payment_status == "paid",
        "payment_status": payment_status,
        "payment_id": (
            payment["id"]
            if payment
            else None
        ),
        "total_pages": len(
            job["document_pages"]
        ),
        "payment_complete": (
            payment_status == "paid"
        ),
        "download_activated": bool(
            activated_work
        ),
    }


# ============================================================
# BACK OFFICE: JOBS
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs():

    try:

        jobs = get_back_office_jobs()

        return {
            "success": True,
            "jobs": jobs,
            "total": len(jobs),
        }

    except Exception as error:

        return application_error(
            "BACK_OFFICE",
            error,
            500,
            "BACK_OFFICE_LOAD_FAILED",
        )


# ============================================================
# BACK OFFICE: ACTIVATE DOWNLOAD
# ============================================================

@app.post("/api/back-office/activate-download")
async def back_office_activate_download(
    req: DownloadActivation
):

    if req.admin_key != ADMIN_KEY:

        return application_error(
            "BACK_OFFICE",
            "Invalid admin key.",
            403,
            "INVALID_ADMIN_KEY",
        )

    work_id = safe_int(
        req.work_id
    )

    if work_id is None:

        return application_error(
            "BACK_OFFICE",
            "Invalid work record ID.",
            400,
            "INVALID_WORK_ID",
        )

    try:

        success = activate_work_download(
            work_id
        )

        if not success:

            return application_error(
                "BACK_OFFICE",
                "The saved document could not be activated.",
                404,
                "WORK_RECORD_NOT_FOUND",
            )

        activated_work = None

        jobs = get_back_office_jobs()

        for item in jobs:

            if safe_int(
                item.get("work_id")
                or item.get("id")
            ) == work_id:

                activated_work = item
                break

        print(
            "[BACK_OFFICE] DOWNLOAD ACTIVATED "
            f"work_id={work_id}"
        )

        return {
            "success": True,
            "work_id": work_id,
            "download_activated": True,
            "work": activated_work,
            "message": (
                "Download has been activated for "
                "the selected saved document."
            ),
        }

    except Exception as error:

        return application_error(
            "BACK_OFFICE",
            error,
            500,
            "DOWNLOAD_ACTIVATION_FAILED",
        )


# ============================================================
# BACK OFFICE: CONFIRM PAYMENT
# ============================================================

@app.post("/api/payment/confirm")
async def api_confirm_payment(
    req: PaymentConfirm
):

    if req.admin_key != ADMIN_KEY:

        return application_error(
            "PAYMENT",
            "Invalid admin key",
            403,
            "INVALID_ADMIN_KEY",
        )

    payment = get_payment(
        req.payment_id
    )

    if not payment:

        return application_error(
            "PAYMENT",
            "Payment not found",
            404,
            "PAYMENT_NOT_FOUND",
        )

    current_status = payment.get(
        "payment_status"
    )

    if current_status == "paid":

        return {
            "success": True,
            "payment_id": req.payment_id,
            "status": "paid",
            "message": (
                "Payment was already confirmed."
            ),
        }

    success = update_payment_status(
        req.payment_id,
        "paid",
    )

    if not success:

        return application_error(
            "PAYMENT",
            "Failed to confirm payment",
            500,
            "PAYMENT_CONFIRMATION_FAILED",
        )

    payment_job_id = safe_int(
        payment.get(
            "job_id"
        )
    )

    if payment_job_id is not None:

        update_job_status(
            payment_job_id,
            "paid",
        )

        for key, job in _jobs.items():

            if safe_int(
                job.get(
                    "job_id"
                )
            ) == payment_job_id:

                job["paid"] = True
                job["status"] = "paid"

    return {
        "success": True,
        "payment_id": req.payment_id,
        "status": "paid",
    }


# ============================================================
# SECURE DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str,
):

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "DOWNLOAD",
            "Invalid job ID.",
            400,
            "INVALID_JOB_ID",
        )

    # --------------------------------------------------------
    # PAYMENT MUST BE CONFIRMED
    # --------------------------------------------------------

    payment = get_latest_payment(
        numeric_job_id
    )

    if (
        not payment
        or payment.get(
            "payment_status"
        )
        != "paid"
    ):

        return application_error(
            "DOWNLOAD",
            "Payment has not been confirmed.",
            403,
            "PAYMENT_NOT_CONFIRMED",
        )

    # --------------------------------------------------------
    # CURRENT JOB VERSION CHECK
    # --------------------------------------------------------

    job = _jobs.get(
        job_id
    )

    if job:

        if (
            version_id
            != job["version_id"]
        ):

            return application_error(
                "DOWNLOAD",
                "Version mismatch.",
                409,
                "VERSION_MISMATCH",
            )

        if not job.get(
            "approved"
        ):

            return application_error(
                "DOWNLOAD",
                "The current document version has not been approved.",
                409,
                "DOCUMENT_NOT_APPROVED",
            )

    # --------------------------------------------------------
    # IMPORTANT:
    # DOWNLOAD MUST USE THE EXACT SAVED AND ACTIVATED
    # WORK RECORD.
    #
    # It must NOT use get_latest_work().
    # --------------------------------------------------------

    work = get_activated_work(
        numeric_job_id
    )

    if not work:

        return application_error(
            "DOWNLOAD",
            (
                "Payment is confirmed, but the saved document "
                "has not yet been activated for download."
            ),
            403,
            "DOWNLOAD_NOT_ACTIVATED",
        )

    # --------------------------------------------------------
    # ACTIVATED VERSION MUST MATCH REQUESTED VERSION
    # --------------------------------------------------------

    activated_version = safe_int(
        work.get(
            "version"
        ),
        1,
    ) or 1

    requested_version = None

    if ":" in str(version_id):

        requested_version = safe_int(
            str(version_id).rsplit(
                ":",
                1
            )[1],
            None,
        )

    if (
        requested_version is not None
        and activated_version
        != requested_version
    ):

        return application_error(
            "DOWNLOAD",
            (
                "The activated saved document "
                "does not match the requested version."
            ),
            409,
            "ACTIVATED_VERSION_MISMATCH",
        )

    # --------------------------------------------------------
    # STORAGE REFERENCE
    # --------------------------------------------------------

    storage_reference = str(
        work.get(
            "storage_reference"
        )
        or ""
    ).strip()

    if not storage_reference:

        return application_error(
            "DOWNLOAD",
            "The document has no storage reference.",
            404,
            "STORAGE_REFERENCE_MISSING",
        )

    filepath = Path(
        storage_reference
    )

    # --------------------------------------------------------
    # PATH SECURITY
    # --------------------------------------------------------

    try:

        document_root = (
            DOCUMENT_ROOT.resolve()
        )

        resolved_filepath = (
            filepath.resolve()
        )

        if not resolved_filepath.is_relative_to(
            document_root
        ):

            return application_error(
                "DOWNLOAD",
                "Invalid file path.",
                403,
                "INVALID_FILE_PATH",
            )

        filepath = resolved_filepath

    except Exception:

        return application_error(
            "DOWNLOAD",
            "Invalid file path.",
            403,
            "INVALID_FILE_PATH",
        )

    if not filepath.exists():

        return application_error(
            "DOWNLOAD",
            "File not found on server.",
            404,
            "FILE_NOT_FOUND",
        )

    if not filepath.is_file():

        return application_error(
            "DOWNLOAD",
            "Storage reference is not a file.",
            404,
            "INVALID_STORAGE_OBJECT",
        )

    # --------------------------------------------------------
    # FINAL DOWNLOAD
    # --------------------------------------------------------

    version = activated_version

    print(
        "[DOWNLOAD] SERVING "
        f"job_id={job_id} "
        f"version={version} "
        f"work_id={work.get('id')} "
        f"path={filepath}"
    )

    return FileResponse(
        filepath,
        filename=(
            f"NPBC_Job"
            f"{job_id}_v"
            f"{version}.txt"
        ),
        media_type="text/plain",
    )


# ============================================================
# CLEAR CHAT / STARTUP
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    ada = _sessions.get(
        job_key(
            customer_id,
            job_id,
        )
    )

    if ada:

        clear_method = getattr(
            ada,
            "clear_history",
            None,
        )

        if callable(clear_method):
            clear_method()

    return {
        "success": True,
        "message": "Conversation cleared.",
    }


@app.on_event("startup")
async def startup():

    print("=" * 70)
    print(
        "NAIJA POCKET BUSINESS CENTER — FASTAPI"
    )
    print(
        "Architecture: INTELLIGENCE-FIRST"
    )
    print(
        "Intelligence:",
        get_ada_model(),
    )
    print(
        "Configured:",
        is_configured(),
    )
    print(
        "Customer page-count requirement: DISABLED"
    )
    print(
        "Global page assumption: DISABLED"
    )
    print(
        "Complete document preservation: ENABLED"
    )
    print(
        "Review workflow: ENABLED"
    )
    print(
        "Review pagination source: COMPLETE DOCUMENT TEXT"
    )
    print(
        "Persistent document storage: ENABLED"
    )
    print(
        "Database payment confirmation: ENABLED"
    )
    print(
        "Back Office document activation: ENABLED"
    )
    print(
        "Secure download: ENABLED"
    )
    print("=" * 70)


# ============================================================
# MAIN
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
