from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
import re
import threading
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ada_response import AdaResponse, get_ada_model, is_configured
from billing_manager import BillingManager
from payments import (
    create_payment,
    get_all_payments,
    get_payment,
    get_job_payments,
    update_payment_status,
)

DEBUG = os.getenv("ADA_DEBUG_ERRORS", "true").lower() in {
    "1", "true", "yes", "on"
}

MAX_UPLOAD = int(
    os.getenv("ADA_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
)

REVIEW_CHUNK_CHARS = int(
    os.getenv("ADA_REVIEW_CHUNK_CHARS", "7000")
)

REVIEW_MIN_CHARS = int(
    os.getenv("ADA_REVIEW_MIN_CHARS", "2500")
)

BASE = Path(__file__).resolve().parent
BILLING = BillingManager()

DOWNLOAD_DIR = BASE / "generated_documents"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = BASE / "runtime_jobs.json"
STATE_LOCK = threading.RLock()

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


app = FastAPI(
    title="Naija Pocket Business Center",
    version="intelligence-first-v10",
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


def event_value(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_service_name(service: Any) -> str | None:
    value = str(service or "").strip()

    if not value:
        return None

    return BILLING.normalize_service(value)


def get_service_bill(
    service: Any,
    total_pages: int = 1,
) -> dict[str, Any]:

    pages = max(1, int(total_pages or 1))
    internal = normalize_service_name(service)

    bill = BILLING.generate_bill(
        internal or str(service or "").strip()
    )

    price = int(bill.get("price") or 0)

    billing = str(
        bill.get("billing") or "quotation"
    ).strip().lower()

    if internal is None:
        price = 0
        billing = "quotation"

    if billing == "per_page":
        amount = price * pages
    elif billing == "fixed":
        amount = price
    else:
        amount = 0

    return {
        "service": internal or bill.get("service") or service,
        "price": price,
        "billing": billing,
        "total_pages": pages,
        "amount": amount,
        "quotation_required": billing == "quotation",
    }


def application_error(
    stage: str,
    message: Any,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):

    print(f"[{stage}] {message}")

    if isinstance(message, Exception):
        traceback.print_exc()

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_type": (
                type(message).__name__
                if isinstance(message, Exception)
                else "ApplicationError"
            ),
            "error_message": (
                str(message)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


def jsonable(value: Any) -> Any:

    if isinstance(value, dict):
        return {
            str(k): jsonable(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]

    if isinstance(
        value,
        (str, int, float, bool),
    ) or value is None:
        return value

    try:
        return dict(value)
    except Exception:
        return str(value)


# ============================================================
# JOB PERSISTENCE
# ============================================================

def save_jobs() -> None:

    with STATE_LOCK:

        safe_jobs = {
            job_id: {
                key: jsonable(value)
                for key, value in job.items()
            }
            for job_id, job in _jobs.items()
        }

        temporary = STATE_FILE.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                safe_jobs,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        temporary.replace(STATE_FILE)


def load_jobs() -> None:

    if not STATE_FILE.is_file():
        return

    try:

        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, dict):
            _jobs.update(data)

        for job in _jobs.values():
            synchronize_job(job)

        print(
            f"[STATE] loaded {len(_jobs)} job(s)"
        )

    except Exception as exc:
        print(
            f"[STATE] load skipped: {exc}"
        )


# ============================================================
# DOCUMENT PAGES
# ============================================================

def normalize_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    if pages is None:
        return []

    if isinstance(pages, str):

        text = clean_text(pages)

        if not text:
            return []

        return [
            {
                "page_number": 1,
                "position": 1,
                "content": text,
            }
        ]

    if not isinstance(pages, list):
        return []

    output = []

    for index, item in enumerate(
        pages,
        1,
    ):

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

            source = dict(item)

        else:

            content = clean_text(item)
            source = {}

        if not content:
            continue

        source["page_number"] = index
        source["position"] = index
        source["content"] = content

        output.append(source)

    return output


def text_to_review_pages(
    text: str,
) -> list[dict[str, Any]]:

    text = clean_text(text)

    if not text:
        return []

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(
            r"\n\s*\n",
            text,
        )
        if paragraph.strip()
    ]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush():

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

        else:

            sentences = re.split(
                r"(?<=[.!?])\s+",
                paragraph,
            )

            for sentence in sentences:

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
        and (
            len(chunks[-2])
            + len(chunks[-1])
            + 2
            <= REVIEW_CHUNK_CHARS
        )
    ):

        chunks[-2] += (
            "\n\n" + chunks.pop()
        )

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
        if chunk
    ]


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


def synchronize_job(
    job: dict[str, Any],
) -> None:

    pages = normalize_pages(
        job.get("document_pages")
    )

    job["document_pages"] = pages

    review_pages = job.get("review_pages")

    if (
        not isinstance(review_pages, list)
        or len(review_pages) != len(pages)
    ):
        job["review_pages"] = (
            make_review_pages(pages)
        )

    job["total_pages"] = len(pages)


# ============================================================
# SESSION
# ============================================================

def job_key(
    customer_id: Any,
    job_id: Any,
) -> str:

    customer = (
        str(customer_id or "anonymous")
        .strip()
        or "anonymous"
    )

    job = (
        str(job_id or "default")
        .strip()
        or "default"
    )

    return f"{customer}:{job}"


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

    elif (
        service
        and callable(
            getattr(
                ada,
                "set_service",
                None,
            )
        )
    ):

        ada.set_service(service)

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

def resolve_service(
    request: Chat,
    ada: Any = None,
) -> str | None:

    candidates = [
        request.service
    ]

    form = request.form_data or {}

    for key in (
        "service",
        "selected_service",
        "service_name",
        "service_key",
        "selectedService",
        "serviceName",
        "selectedServiceName",
    ):
        candidates.append(
            form.get(key)
        )

    if ada is not None:
        candidates.append(
            getattr(
                ada,
                "service",
                None,
            )
        )

    candidates.extend(
        [
            request.context,
            request.event,
            request.message,
        ]
    )

    for candidate in candidates:

        normalized = normalize_service_name(
            candidate
        )

        if normalized:
            return normalized

    searchable = "\n".join(
        str(value or "")
        for value in candidates
    ).lower()

    for alias, internal in getattr(
        BILLING,
        "service_aliases",
        {},
    ).items():

        if str(alias).lower() in searchable:
            return internal

    for internal in getattr(
        BILLING,
        "price_list",
        {},
    ):

        if internal.lower() in searchable:
            return internal

    return None


def build_customer_request(
    request: Chat,
) -> str:

    parts = []

    if request.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + request.service.strip()
        )

    if request.form_data:

        values = []

        for key, value in request.form_data.items():

            value_text = str(
                value or ""
            ).strip()

            if not value_text:
                continue

            label = (
                str(key)
                .replace("_", " ")
                .title()
            )

            values.append(
                f"{label}: {value_text}"
            )

        if values:

            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(values)
            )

    if request.context:
        if request.context.strip():
            parts.append(
                "ADDITIONAL CUSTOMER CONTEXT:\n"
                + request.context.strip()
            )

    if request.message:
        if request.message.strip():
            parts.append(
                "CUSTOMER REQUEST:\n"
                + request.message.strip()
            )

    return "\n\n".join(parts).strip()


def build_context(
    request: Chat,
) -> str | None:

    parts = []

    if request.context:
        if request.context.strip():
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

TEXT_KEYS = (
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

PAGE_KEYS = (
    "pages",
    "document_pages",
    "prepared_pages",
    "content_pages",
)


def extract_result(
    value: Any,
    depth: int = 0,
) -> tuple[str, list[dict[str, Any]]]:

    if depth > 5 or value is None:
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

        text = "\n\n".join(
            page["content"]
            for page in pages
        )

        return (
            text,
            text_to_review_pages(text)
            if text
            else [],
        )

    if isinstance(value, dict):

        for key in TEXT_KEYS:

            candidate = value.get(key)

            if (
                isinstance(candidate, str)
                and clean_text(candidate)
            ):

                text = clean_text(candidate)

                return (
                    text,
                    text_to_review_pages(text),
                )

        for key in PAGE_KEYS:

            text, pages = extract_result(
                value.get(key),
                depth + 1,
            )

            if text or pages:
                return text, pages

        for key, candidate in value.items():

            if key in TEXT_KEYS or key in PAGE_KEYS:
                continue

            text, pages = extract_result(
                candidate,
                depth + 1,
            )

            if text or pages:
                return text, pages

        return "", []

    try:
        return extract_result(
            vars(value),
            depth + 1,
        )
    except Exception:
        return "", []


def complete_document(
    result: Any,
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
]:

    text, pages = extract_result(result)

    if not text:
        raise ValueError(
            "The intelligence completed the operation "
            "but returned no usable document content."
        )

    pages = text_to_review_pages(text)

    if not pages:
        raise ValueError(
            "Usable document text was returned "
            "but no review pages could be constructed."
        )

    metadata = {}

    if isinstance(result, dict):

        metadata = {
            key: value
            for key, value in result.items()
            if key not in TEXT_KEYS
            and key not in PAGE_KEYS
        }

    return text, pages, metadata


async def call_flex(
    method: Any,
    kwargs: dict[str, Any],
) -> Any:

    try:
        signature = inspect.signature(method)
        parameters = signature.parameters
    except Exception:

        return await asyncio.to_thread(
            method,
            **kwargs,
        )

    accepts_kwargs = any(
        parameter.kind
        == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    if accepts_kwargs:
        call_kwargs = kwargs
    else:
        call_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in parameters
        }

    return await asyncio.to_thread(
        method,
        **call_kwargs,
    )


async def create_document(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
):

    service = resolve_service(
        request,
        ada,
    )

    kwargs = {
        "customer_request": customer_request,
        "service": service or request.service,
        "form_data": request.form_data,
        "context": context,
        "event": request.event,
        "message": customer_request,
        "original_request": customer_request,
        "create_work": True,
    }

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

        try:

            result = await call_flex(
                method,
                kwargs,
            )

            return complete_document(
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

        result = await call_flex(
            respond,
            {
                "message": customer_request,
                "service": request.service,
                "event": request.event,
                "context": context,
            },
        )

        return complete_document(
            result
        )

    raise AttributeError(
        "No usable document creation method is available."
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

    normalized_pages = (
        text_to_review_pages(
            document_text
        )
        or normalize_pages(pages)
    )

    if not normalized_pages:
        raise ValueError(
            "No complete document content was returned by intelligence."
        )

    service = resolve_service(
        request
    )

    job = {
        "job_id": job_id,
        "customer_id": request.customer_id,
        "service": service,
        "original_request": original_request,
        "context": build_context(request),

        "status": "reviewing",
        "review_started": True,
        "review_finished": False,
        "review_error": None,

        "progress": {
            "completed": 0,
            "total": len(normalized_pages),
        },

        "document_text": document_text,
        "document_pages": normalized_pages,
        "review_pages": make_review_pages(
            normalized_pages
        ),
        "assembled_review": "",

        "current_version": 1,
        "version_id": f"{job_id}:1",

        "approved": False,

        "paid": False,
        "payment_pending": False,
        "payment_reported": False,
        "payment_verified": False,

        "payment_id": None,
        "payment_db_id": None,
    }

    _jobs[job_id] = job

    save_jobs()

    print(
        f"[JOB] created job={job_id} "
        f"service={service!r} "
        f"pages={len(normalized_pages)}"
    )

    return job


# ============================================================
# REVIEW CALLBACK
# ============================================================

def review_callback(job_id: str):

    def callback(
        *args,
        **kwargs,
    ):

        try:

            job = _jobs.get(job_id)

            if not job:
                return

            update = {}

            if (
                args
                and isinstance(
                    args[0],
                    dict,
                )
            ):
                update.update(args[0])

            update.update(
                {
                    key: value
                    for key, value in kwargs.items()
                    if value is not None
                }
            )

            if (
                args
                and not isinstance(
                    args[0],
                    dict,
                )
            ):

                if len(args) > 0:
                    update.setdefault(
                        "page_number",
                        args[0],
                    )

                if len(args) > 1:
                    update.setdefault(
                        "status",
                        args[1],
                    )

                if len(args) > 2:
                    update.setdefault(
                        "review",
                        args[2],
                    )

            update_type = event_value(
                update.get(
                    "type",
                    update.get(
                        "event",
                        update.get(
                            "status",
                            "",
                        ),
                    ),
                )
            )

            page_number = str(
                update.get(
                    "page_number",
                    update.get(
                        "page",
                        update.get(
                            "current_page",
                            "",
                        ),
                    ),
                )
                or ""
            )

            pages = job.get(
                "review_pages",
                [],
            )

            if update_type in {
                "page_started",
                "page_start",
                "started",
            }:

                for page in pages:

                    if (
                        page_number
                        and str(
                            page.get(
                                "page_number"
                            )
                        )
                        == page_number
                    ):

                        page["status"] = "reviewing"

            elif update_type in {
                "page_completed",
                "page_complete",
                "completed",
                "page_reviewed",
            }:

                completed = update.get(
                    "completed",
                    update.get(
                        "position",
                        update.get(
                            "current",
                            0,
                        ),
                    ),
                )

                try:
                    completed = int(completed)
                except Exception:
                    completed = 0

                for page in pages:

                    if (
                        page_number
                        and str(
                            page.get(
                                "page_number"
                            )
                        )
                        != page_number
                    ):
                        continue

                    page["status"] = "reviewed"
                    page["review"] = clean_text(
                        update.get(
                            "review"
                        )
                    )
                    page["error"] = None

                    if page_number:
                        break

                if completed:

                    job["progress"] = {
                        "completed": min(
                            completed,
                            len(
                                job[
                                    "document_pages"
                                ]
                            ),
                        ),
                        "total": len(
                            job[
                                "document_pages"
                            ]
                        ),
                    }

            elif update_type in {
                "page_error",
                "error",
                "failed",
            }:

                for page in pages:

                    if (
                        page_number
                        and str(
                            page.get(
                                "page_number"
                            )
                        )
                        != page_number
                    ):
                        continue

                    page["status"] = "error"
                    page["error"] = str(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )
                    break

            elif update_type in {
                "review_completed",
                "review_complete",
                "all_completed",
                "finished",
            }:

                total = len(
                    job["document_pages"]
                )

                job.update(
                    {
                        "status": "review_complete",
                        "review_finished": True,
                        "review_error": None,
                        "progress": {
                            "completed": total,
                            "total": total,
                        },
                        "assembled_review": clean_text(
                            update.get(
                                "assembled_review",
                                "",
                            )
                        ),
                    }
                )

                save_jobs()

        except Exception as exc:

            if DEBUG:
                print(
                    "[REVIEW CALLBACK]",
                    exc,
                )

    return callback


# ============================================================
# REVIEW WORKER
# ============================================================

async def run_review(
    job_id: str,
):

    job = _jobs.get(job_id)

    if not job:
        return

    try:

        ada = get_session(
            job.get("customer_id"),
            job_id,
            job.get("service"),
        )

        method = getattr(
            ada,
            "review_document_pages",
            None,
        )

        if not callable(method):
            raise AttributeError(
                "AdaResponse has no review_document_pages() method."
            )

        result = await call_flex(
            method,
            {
                "pages": normalize_pages(
                    job["document_pages"]
                ),
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
                "progress_callback": review_callback(
                    job_id
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

                returned_text = "\n\n".join(
                    page["content"]
                    for page in returned_pages
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

            job["assembled_review"] = clean_text(
                result.get(
                    "assembled_review",
                    job.get(
                        "assembled_review",
                        "",
                    ),
                )
            )

        total = len(
            job["document_pages"]
        )

        job.update(
            {
                "status": "review_complete",
                "review_started": True,
                "review_finished": True,
                "review_error": None,
                "progress": {
                    "completed": total,
                    "total": total,
                },
            }
        )

        save_jobs()

        print(
            f"[REVIEW] job={job_id} "
            f"pages={total}"
        )

    except asyncio.CancelledError:
        raise

    except Exception as error:

        job.update(
            {
                "status": "review_error",
                "review_finished": True,
                "review_error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        )

        save_jobs()
        traceback.print_exc()


def start_review(
    job_id: str,
) -> bool:

    job = _jobs.get(job_id)

    if (
        not job
        or not job.get(
            "document_pages"
        )
        or job.get("status")
        != "reviewing"
    ):
        return False

    existing = _review_tasks.get(
        job_id
    )

    if (
        existing
        and not existing.done()
    ):
        return False

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(job_id)
        )
    )

    return True


# ============================================================
# PAYMENT HELPERS
# ============================================================

def latest_payment(
    job_id: str,
):

    rows = get_job_payments(
        job_id
    ) or []

    if not rows:
        return None

    return jsonable(rows[0])


def get_job_payment(
    job: dict[str, Any],
):

    payment_id = job.get(
        "payment_db_id"
    )

    if not payment_id:
        return latest_payment(
            job["job_id"]
        )

    try:

        record = get_payment(
            payment_id
        )

        if record:
            return jsonable(
                record
            )

    except Exception:
        pass

    return latest_payment(
        job["job_id"]
    )


def payment_status(
    record: dict[str, Any] | None,
) -> str:

    if not record:
        return "unpaid"

    return event_value(
        record.get(
            "payment_status",
            record.get(
                "status",
                "unpaid",
            ),
        )
    )


def sync_payment(
    job: dict[str, Any],
) -> None:

    record = get_job_payment(
        job
    )

    if not record:
        return

    payment_id = record.get(
        "id"
    )

    job["payment_db_id"] = payment_id
    job["payment_id"] = payment_id

    state = payment_status(
        record
    )

    verified = state in {
        "paid",
        "verified",
        "completed",
        "complete",
    }

    pending = state in {
        "pending",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }

    job["payment_verified"] = verified
    job["paid"] = verified
    job["payment_pending"] = pending

    job["payment_reported"] = state in {
        "reported",
        "verification_pending",
        "awaiting_verification",
        "paid",
    }

    if verified:
        job["status"] = "paid"

    elif (
        pending
        and job.get(
            "review_finished"
        )
    ):
        job["status"] = (
            "payment_pending"
        )


def ready_for_payment(
    job: dict[str, Any],
) -> bool:

    if not job:
        return False

    if not job.get(
        "document_pages"
    ):
        return False

    status = event_value(
        job.get("status")
    )

    if status in {
        "review_error",
        "correction_error",
        "correcting",
        "reviewing",
    }:
        return False

    # IMPORTANT:
    # This deliberately matches the Review page's
    # definition of READY.
    if job.get(
        "review_finished"
    ) is True:
        return True

    return status in {
        "review_complete",
        "approved",
        "payment_pending",
        "paid",
    }


# ============================================================
# JOB RESPONSE
# ============================================================

def make_job_response(
    job: dict[str, Any],
) -> dict[str, Any]:

    synchronize_job(job)
    sync_payment(job)

    pages = job["document_pages"]

    bill = get_service_bill(
        job.get("service"),
        len(pages),
    )

    save_jobs()

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
        "approved": False,
        "paid": job.get(
            "paid",
            False,
        ),
        "payment_pending": job.get(
            "payment_pending",
            False,
        ),
        "payment_reported": job.get(
            "payment_reported",
            False,
        ),
        "payment_verified": job.get(
            "payment_verified",
            False,
        ),
        "payment_id": job.get(
            "payment_id"
        ),
        "progress": {
            "completed": int(
                job.get(
                    "progress",
                    {},
                ).get(
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
        **bill,
        "error": job.get(
            "review_error"
        ),
        "review_url": (
            "/review.html?"
            f"job_id={job['job_id']}"
            f"&service={job.get('service') or ''}"
        ),
    }


# ============================================================
# DOCUMENT EXTRACTION
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
                    name
                    for name in names
                    if re.match(
                        r"ppt/slides/slide\d+\.xml",
                        name,
                    )
                ]

            else:

                names = [
                    name
                    for name in names
                    if re.match(
                        r"xl/worksheets/sheet\d+\.xml",
                        name,
                    )
                ]

            texts = []

            for name in sorted(names):

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


# ============================================================
# DOWNLOAD DOCX
# ============================================================

def make_download_docx(
    job: dict[str, Any],
) -> Path:

    job_id = re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        str(
            job.get(
                "job_id"
            )
        ),
    )

    version = re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        str(
            job.get(
                "current_version",
                1,
            )
        ),
    )

    path = (
        DOWNLOAD_DIR
        / (
            "naija_pocket_business_"
            f"{job_id}_v{version}.docx"
        )
    )

    pages = normalize_pages(
        job.get(
            "document_pages"
        )
    )

    if not pages:
        raise RuntimeError(
            "There is no document content available for download."
        )

    def escape(value: Any) -> str:

        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    body = []

    for index, page in enumerate(
        pages
    ):

        paragraphs = [
            line.strip()
            for line in page[
                "content"
            ].splitlines()
            if line.strip()
        ]

        if not paragraphs:
            paragraphs = [
                page["content"]
            ]

        for paragraph in paragraphs:

            body.append(
                '<w:p><w:r>'
                '<w:t xml:space="preserve">'
                + escape(paragraph)
                + "</w:t></w:r></w:p>"
            )

        if index < len(pages) - 1:

            body.append(
                '<w:p><w:r>'
                '<w:br w:type="page"/>'
                "</w:r></w:p>"
            )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" '
        'w:right="1440" '
        'w:bottom="1440" '
        'w:left="1440"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        "</Types>"
    )

    relationships = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships"/>'
    )

    with zipfile.ZipFile(
        path,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:

        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )

        archive.writestr(
            "_rels/.rels",
            relationships,
        )

        archive.writestr(
            "word/document.xml",
            document_xml,
        )

        archive.writestr(
            "word/_rels/document.xml.rels",
            document_relationships,
        )

    return path


# ============================================================
# HTML PAGES
# ============================================================

def serve_html(
    filename: str,
):

    locations = (
        BASE / filename,
        BASE / "app" / filename,
        BASE / "static" / filename,
        BASE / "public" / filename,
        BASE / "assets" / filename,
    )

    for path in locations:

        if path.is_file():

            return FileResponse(
                path,
                media_type="text/html",
            )

    return application_error(
        "PAGE",
        f"{filename} was not found.",
        404,
        "HTML_NOT_FOUND",
    )


@app.get("/")
async def root():
    return serve_html("index.html")


@app.get("/index.html")
async def index():
    return serve_html("index.html")


@app.get("/conversation.html")
async def conversation():
    return serve_html("conversation.html")


@app.get("/workspace.html")
async def workspace():
    return serve_html("workspace.html")


@app.get("/review.html")
async def review_page():
    return serve_html("review.html")


@app.get("/payment.html")
async def payment_page():
    return serve_html("payment.html")


@app.get("/download.html")
async def download_page():
    return serve_html("download.html")


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

        document_text = clean_text(
            await asyncio.to_thread(
                extract_document,
                data,
                filename,
            )
        )

        if not document_text:

            return application_error(
                "UPLOAD",
                "The uploaded document contains no extractable text.",
                400,
                "EMPTY_DOCUMENT",
            )

        job_id_value = (
            str(job_id or "").strip()
            or str(uuid.uuid4())
        )

        normalized_service = (
            normalize_service_name(
                service
            )
            or service
        )

        request = Chat(
            message=(
                "Uploaded document: "
                + filename
            ),
            service=normalized_service,
            customer_id=customer_id,
            job_id=job_id_value,
            client_request_id=client_request_id,
            document_text=document_text,
        )

        existing = _jobs.get(
            job_id_value
        )

        if existing:

            pages = text_to_review_pages(
                document_text
            )

            existing["customer_id"] = (
                customer_id
                or existing.get(
                    "customer_id"
                )
            )

            existing["service"] = (
                normalized_service
                or existing.get(
                    "service"
                )
            )

            existing["document_text"] = (
                document_text
            )

            existing["document_pages"] = pages
            existing["review_pages"] = (
                make_review_pages(
                    pages
                )
            )

            existing["current_version"] = (
                int(
                    existing.get(
                        "current_version",
                        1,
                    )
                )
                + 1
            )

            existing["version_id"] = (
                f"{job_id_value}:"
                f"{existing['current_version']}"
            )

            existing.update(
                {
                    "status": "reviewing",
                    "review_started": True,
                    "review_finished": False,
                    "review_error": None,
                    "progress": {
                        "completed": 0,
                        "total": len(pages),
                    },
                    "approved": False,
                    "paid": False,
                    "payment_pending": False,
                    "payment_reported": False,
                    "payment_verified": False,
                    "payment_id": None,
                    "payment_db_id": None,
                }
            )

            job = existing

            save_jobs()

        else:

            job = create_job(
                job_id_value,
                request,
                f"Uploaded document: {filename}",
                document_text,
                text_to_review_pages(
                    document_text
                ),
            )

        started = start_review(
            job_id_value
        )

        response = make_job_response(
            job
        )

        response.update(
            {
                "filename": filename,
                "client_request_id": client_request_id,
                "created_work": True,
                "work_created": True,
                "review_started": started,
            }
        )

        return response

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
    request: Chat,
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
        str(
            request.job_id
            or ""
        ).strip()
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
        or ""
    )

    if not document_text and pages:

        document_text = "\n\n".join(
            page["content"]
            for page in pages
        )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        resolved_service = (
            resolve_service(
                request,
                ada,
            )
        )

        if resolved_service:

            request.service = (
                resolved_service
            )

            setter = getattr(
                ada,
                "set_service",
                None,
            )

            if callable(setter):
                setter(
                    resolved_service
                )

        if request.guidance_only:

            if not request.message.strip():

                return application_error(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await call_flex(
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

            if document_text:

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    document_text,
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
                    generated_text,
                    generated_pages,
                    metadata,
                ) = await create_document(
                    ada,
                    request,
                    customer_request,
                    context,
                )

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    generated_text,
                    generated_pages,
                )

                job[
                    "intelligence_metadata"
                ] = metadata

                save_jobs()

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

        if document_text:

            job = _jobs.get(
                job_id
            )

            if not job:

                job = create_job(
                    job_id,
                    request,
                    customer_request,
                    document_text,
                    pages,
                )

            else:

                job["document_text"] = (
                    document_text
                )

                job["document_pages"] = (
                    text_to_review_pages(
                        document_text
                    )
                )

                job["review_pages"] = (
                    make_review_pages(
                        job[
                            "document_pages"
                        ]
                    )
                )

                job.update(
                    {
                        "status": "reviewing",
                        "review_started": True,
                        "review_finished": False,
                        "review_error": None,
                        "progress": {
                            "completed": 0,
                            "total": len(
                                job[
                                    "document_pages"
                                ]
                            ),
                        },
                    }
                )

                save_jobs()

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

        reply = await call_flex(
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
    job_id: str,
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
    job_id: str,
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

    return make_job_response(
        job
    )


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction,
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

    if job.get("status") in {
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

    job["current_version"] = (
        int(
            job.get(
                "current_version",
                1,
            )
        )
        + 1
    )

    job["version_id"] = (
        f"{request.job_id}:"
        f"{job['current_version']}"
    )

    job.update(
        {
            "status": "correcting",
            "approved": False,
            "paid": False,
            "payment_pending": False,
            "payment_reported": False,
            "payment_verified": False,
            "payment_id": None,
            "payment_db_id": None,
            "review_started": False,
            "review_finished": False,
            "review_error": None,
            "correction_instruction": instruction,
        }
    )

    save_jobs()

    async def correction_worker():

        try:

            ada = get_session(
                job.get("customer_id"),
                request.job_id,
                job.get("service"),
            )

            method = getattr(
                ada,
                "correct_document",
                None,
            )

            if not callable(method):

                raise AttributeError(
                    "AdaResponse has no correct_document() method."
                )

            result = await call_flex(
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
            ) = complete_document(
                result
            )

            job.update(
                {
                    "document_text": corrected_text,
                    "document_pages": corrected_pages,
                    "review_pages": make_review_pages(
                        corrected_pages
                    ),
                    "intelligence_metadata": metadata,
                    "status": "reviewing",
                    "review_started": True,
                    "review_finished": False,
                    "review_error": None,
                    "progress": {
                        "completed": 0,
                        "total": len(
                            corrected_pages
                        ),
                    },
                }
            )

            save_jobs()

            start_review(
                request.job_id
            )

        except Exception as error:

            job.update(
                {
                    "status": "correction_error",
                    "review_error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                }
            )

            save_jobs()

            traceback.print_exc()

    old_task = _correction_tasks.get(
        request.job_id
    )

    if (
        old_task
        and not old_task.done()
    ):
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
        "version_id": job["version_id"],
        "current_version": job[
            "current_version"
        ],
        "message": (
            "Correction has started. "
            "The corrected document will be "
            "reviewed again."
        ),
    }


# ============================================================
# APPROVAL COMPATIBILITY
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval,
):

    job = _jobs.get(
        request.job_id
    )

    if not job:

        return application_error(
            "APPROVAL",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if request.version_id != job[
        "version_id"
    ]:

        return application_error(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    return {
        "success": True,
        "job_id": request.job_id,
        "version_id": request.version_id,
        "approved": False,
        "message": (
            "Customer approval is not required. "
            "Proceed to payment when the review is READY."
        ),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
    job_id: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
    amount: float | None = None,
    payment_method: str | None = None,
):

    # Supports both:
    # 1. query parameters used by review.html
    # 2. JSON bodies used by older payment.html

    if not job_id:

        try:
            body = await request.json()
        except Exception:
            body = {}

        if isinstance(body, dict):

            job_id = (
                str(
                    body.get(
                        "job_id"
                    )
                    or ""
                ).strip()
                or None
            )

            customer_id = (
                body.get(
                    "customer_id"
                )
                or customer_id
            )

            service = (
                body.get(
                    "service"
                )
                or service
            )

            amount = (
                body.get(
                    "amount"
                )
                if body.get(
                    "amount"
                ) is not None
                else amount
            )

            payment_method = (
                body.get(
                    "payment_method"
                )
                or payment_method
            )

    if not job_id:

        return application_error(
            "PAYMENT",
            "job_id is required.",
            400,
            "JOB_ID_REQUIRED",
        )

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
        customer_id
        and job.get("customer_id")
        and customer_id
        != job.get("customer_id")
    ):

        return application_error(
            "PAYMENT",
            "Customer mismatch.",
            409,
            "CUSTOMER_MISMATCH",
        )

    # THIS IS THE CRITICAL FIX.
    #
    # The customer page can show READY when:
    # review_finished == True
    #
    # The old API incorrectly required BOTH
    # review_finished AND a particular status.
    #
    # This endpoint now uses the same READY definition.

    if not ready_for_payment(
        job
    ):

        return application_error(
            "PAYMENT",
            "The complete document review is not ready for payment.",
            409,
            "REVIEW_NOT_COMPLETE",
        )

    if (
        service
        and not job.get(
            "service"
        )
    ):

        job["service"] = (
            normalize_service_name(
                service
            )
            or service
        )

    bill = get_service_bill(
        job.get("service"),
        len(
            job[
                "document_pages"
            ]
        ),
    )

    if bill[
        "quotation_required"
    ]:

        return application_error(
            "PAYMENT",
            "This service requires a quotation before payment.",
            409,
            "QUOTATION_REQUIRED",
        )

    existing = latest_payment(
        job_id
    )

    if existing:

        state = payment_status(
            existing
        )

        if state not in {
            "cancelled",
            "failed",
            "rejected",
        }:

            job["payment_db_id"] = (
                existing.get("id")
            )

            job["payment_id"] = (
                existing.get("id")
            )

            sync_payment(
                job
            )

            save_jobs()

            return {
                "success": True,
                "job_id": job_id,
                "version_id": job[
                    "version_id"
                ],
                "payment_id": job.get(
                    "payment_id"
                ),
                "status": payment_status(
                    existing
                ),
                "payment_pending": job.get(
                    "payment_pending",
                    False,
                ),
                "paid": job.get(
                    "paid",
                    False,
                ),
                "payment_verified": job.get(
                    "payment_verified",
                    False,
                ),
                **bill,
                "payment_method": (
                    existing.get(
                        "payment_method"
                    )
                    or payment_method
                    or "bank_transfer"
                ),
            }

    method = (
        payment_method
        or "bank_transfer"
    )

    try:

        # REAL payments.py DATABASE INSERT
        create_payment(
            job_id,
            bill["amount"],
            method,
        )

        record = latest_payment(
            job_id
        )

        if not record:

            raise RuntimeError(
                "Payment record was not returned after creation."
            )

        job.update(
            {
                "payment_db_id": record.get(
                    "id"
                ),
                "payment_id": record.get(
                    "id"
                ),
                "payment_pending": True,
                "payment_reported": False,
                "payment_verified": False,
                "paid": False,
                "payment_method": method,
                "payment_amount": bill[
                    "amount"
                ],
                "status": "payment_pending",
            }
        )

        save_jobs()

        return {
            "success": True,
            "job_id": job_id,
            "version_id": job[
                "version_id"
            ],
            "payment_id": record.get(
                "id"
            ),
            "payment_pending": True,
            "payment_reported": False,
            "payment_verified": False,
            "paid": False,
            "status": "pending",
            **bill,
            "payment_method": method,
            "message": (
                "Payment record created. "
                "After paying, tap "
                "I HAVE MADE PAYMENT."
            ),
        }

    except Exception as error:

        return application_error(
            "PAYMENT",
            error,
            500,
            "PAYMENT_CREATE_FAILED",
        )


# ============================================================
# PAYMENT REPORT
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    job_id: str,
    version_id: str | None = None,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT_REPORT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        and version_id
        != job["version_id"]
    ):

        return application_error(
            "PAYMENT_REPORT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    record = latest_payment(
        job_id
    )

    if record:

        job["payment_db_id"] = (
            record.get("id")
        )

        job["payment_id"] = (
            record.get("id")
        )

    if not job.get(
        "payment_db_id"
    ):

        return application_error(
            "PAYMENT_REPORT",
            "Payment has not been started. Tap MAKE PAYMENT first.",
            409,
            "PAYMENT_NOT_STARTED",
        )

    try:

        # Mark the actual database payment
        # as pending/reported.
        update_payment_status(
            job[
                "payment_db_id"
            ],
            "pending",
        )

        sync_payment(
            job
        )

        job.update(
            {
                "payment_reported": True,
                "payment_pending": True,
                "payment_verified": False,
                "paid": False,
                "status": "payment_pending",
            }
        )

        save_jobs()

        return {
            "success": True,
            "job_id": job_id,
            "version_id": job[
                "version_id"
            ],
            "payment_id": job.get(
                "payment_id"
            ),
            "payment_pending": True,
            "payment_reported": True,
            "payment_verified": False,
            "paid": False,
            "status": "pending",
            "message": (
                "Payment reported. "
                "Customer Care must verify "
                "the payment before download "
                "is unlocked."
            ),
        }

    except Exception as error:

        return application_error(
            "PAYMENT_REPORT",
            error,
            500,
            "PAYMENT_REPORT_FAILED",
        )


# ============================================================
# CUSTOMER CARE VERIFICATION
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    job_id: str,
    version_id: str,
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

    if version_id != job[
        "version_id"
    ]:

        return application_error(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "payment_db_id"
    ):

        record = latest_payment(
            job_id
        )

        if record:

            job["payment_db_id"] = (
                record.get("id")
            )

            job["payment_id"] = (
                record.get("id")
            )

    if not job.get(
        "payment_db_id"
    ):

        return application_error(
            "PAYMENT",
            "There is no payment record to verify.",
            409,
            "PAYMENT_NOT_FOUND",
        )

    try:

        # THIS is Customer Care verification.
        update_payment_status(
            job[
                "payment_db_id"
            ],
            "paid",
        )

        job.update(
            {
                "paid": True,
                "payment_verified": True,
                "payment_reported": True,
                "payment_pending": False,
                "status": "paid",
            }
        )

        save_jobs()

        bill = get_service_bill(
            job.get("service"),
            len(
                job[
                    "document_pages"
                ]
            ),
        )

        return {
            "success": True,
            "job_id": job_id,
            "version_id": version_id,
            "payment_id": job.get(
                "payment_id"
            ),
            "paid": True,
            "payment_verified": True,
            "status": "paid",
            **bill,
            "api_download_url": (
                "/api/download?"
                f"job_id={job_id}"
                f"&version_id={version_id}"
            ),
            "message": (
                "Payment verified by Customer Care. "
                "Download is now available."
            ),
        }

    except Exception as error:

        return application_error(
            "PAYMENT",
            error,
            500,
            "PAYMENT_VERIFY_FAILED",
        )


@app.post(
    "/api/customer-care/payment/verify"
)
async def customer_care_verify_payment(
    job_id: str,
    version_id: str,
):

    return await payment_complete(
        job_id=job_id,
        version_id=version_id,
    )


@app.get(
    "/api/customer-care/payments"
)
async def customer_care_payments():

    try:

        rows = (
            get_all_payments()
            or []
        )

        payments = []

        for row in rows:

            record = jsonable(
                row
            )

            state = payment_status(
                record
            )

            if state not in {
                "paid",
                "verified",
                "completed",
                "complete",
                "cancelled",
            }:

                payments.append(
                    record
                )

        return {
            "success": True,
            "count": len(payments),
            "payments": payments,
        }

    except Exception as error:

        return application_error(
            "CUSTOMER_CARE_PAYMENTS",
            error,
            500,
            "PAYMENT_LIST_FAILED",
        )


# ============================================================
# PAYMENT STATE
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

    if version_id != job[
        "version_id"
    ]:

        return application_error(
            "PAYMENT_STATE",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    sync_payment(
        job
    )

    save_jobs()

    bill = get_service_bill(
        job.get("service"),
        len(
            job[
                "document_pages"
            ]
        ),
    )

    state = (
        "paid"
        if job.get("paid")
        else (
            "pending"
            if job.get(
                "payment_pending"
            )
            else "unpaid"
        )
    )

    return {
        "success": True,
        "job_id": job_id,
        "version_id": version_id,
        "status": state,
        "approved": False,
        "paid": job.get(
            "paid",
            False,
        ),
        "payment_verified": job.get(
            "payment_verified",
            False,
        ),
        "payment_reported": job.get(
            "payment_reported",
            False,
        ),
        "payment_pending": job.get(
            "payment_pending",
            False,
        ),
        "payment_id": job.get(
            "payment_id"
        ),
        **bill,
        "payment_complete": job.get(
            "paid",
            False,
        ),
    }


@app.get(
    "/api/payment/status"
)
async def payment_status_api(
    job_id: str,
    version_id: str | None = None,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT_STATUS",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        and version_id
        != job["version_id"]
    ):

        return application_error(
            "PAYMENT_STATUS",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    sync_payment(
        job
    )

    save_jobs()

    bill = get_service_bill(
        job.get("service"),
        len(
            job[
                "document_pages"
            ]
        ),
    )

    state = (
        "paid"
        if job.get("paid")
        else (
            "pending"
            if job.get(
                "payment_pending"
            )
            else "unpaid"
        )
    )

    return {
        "success": True,
        "job_id": job_id,
        "version_id": job[
            "version_id"
        ],
        "status": state,
        "approved": False,
        "paid": job.get(
            "paid",
            False,
        ),
        "payment_verified": job.get(
            "payment_verified",
            False,
        ),
        "payment_reported": job.get(
            "payment_reported",
            False,
        ),
        "payment_pending": job.get(
            "payment_pending",
            False,
        ),
        "payment_id": job.get(
            "payment_id"
        ),
        **bill,
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "DOWNLOAD",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != job[
        "version_id"
    ]:

        return application_error(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    # Always check the real payment record
    # before allowing download.
    sync_payment(
        job
    )

    if (
        not job.get("paid")
        or not job.get(
            "payment_verified"
        )
    ):

        return application_error(
            "DOWNLOAD",
            "Payment must be verified by Customer Care before download.",
            402,
            "PAYMENT_NOT_VERIFIED",
        )

    try:

        path = make_download_docx(
            job
        )

    except Exception as error:

        return application_error(
            "DOWNLOAD",
            error,
            500,
            "DOWNLOAD_BUILD_FAILED",
        )

    return FileResponse(
        path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename=path.name,
    )


# ============================================================
# CLEAR CHAT
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

        method = getattr(
            ada,
            "clear_history",
            None,
        )

        if callable(method):
            method()

    return {
        "success": True,
        "message": "Conversation cleared.",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    load_jobs()

    print("=" * 60)
    print(
        "NAIJA POCKET BUSINESS CENTER — FASTAPI"
    )
    print(
        "Complete document preservation: ENABLED"
    )
    print(
        "Review workflow: ENABLED"
    )
    print(
        "Payment database integration: ENABLED"
    )
    print(
        "Customer approval required: NO"
    )
    print("=" * 60)


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
