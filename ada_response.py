"""
Naija Pocket Business Center
ADA RESPONSE ENGINE

END-TO-END DOCUMENT INTELLIGENCE
================================

ACTIVE ARCHITECTURE

Workspace
    ↓
FastAPI
    ↓
AdaResponse
    ↓
Groq
    ↓
FastAPI Review State
    ↓
Review Page

CORE RULE
---------

AdaResponse is the intelligence layer.

The application owns the customer's document.

AdaResponse does NOT own the permanent document state.

A document is never silently truncated because an individual
Groq request has a character or token limit.

LONG DOCUMENT PROCESSING
------------------------

A complete document may contain many pages.

Each page is preserved in full by the application.

If an individual page is too large for one intelligence request,
that page is divided into bounded review chunks:

    COMPLETE PAGE
         ↓
    CHUNK 1 → GROQ
         ↓
    CHUNK 2 → GROQ
         ↓
    CHUNK 3 → GROQ
         ↓
         ...
         ↓
    CHUNK N → GROQ
         ↓
    PAGE REVIEW
         ↓
    ONE REVIEW CARD

The original page content is NEVER replaced by the chunks.

The chunks exist only for intelligence processing.

The returned review card contains the complete original page
content plus the assembled review findings.

DOCUMENT PRESERVATION
---------------------

The application remains authoritative for:

    - complete document
    - current document version
    - page content
    - page order
    - review state
    - approval state
    - payment state
    - delivery state

AdaResponse only reasons over supplied information.

CORRECTIONS
-----------

The application supplies the current document version.

AdaResponse processes the correction request.

The application remains responsible for saving the corrected
version.

AdaResponse never restores an older document version.

SECURITY
--------

Technical errors are logged server-side.

Internal architecture is never exposed to customers unless:

    ADA_EXPOSE_ERRORS=true
"""

from __future__ import annotations

import os
import re
import traceback
from typing import Any, Callable

try:
    from groq import Groq
except ImportError:
    Groq = None

from ada_prompt_manager import AdaPromptManager
from billing_manager import BillingManager


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_MODEL = "openai/gpt-oss-20b"

MODEL = (
    os.getenv("GROQ_MODEL")
    or DEFAULT_MODEL
).strip()

API_KEY = (
    os.getenv("GROQ_API_KEY")
    or os.getenv("GROQ_APIKEY")
    or ""
).strip()

EXPOSE_ERRORS_TO_CLIENT = (
    os.getenv(
        "ADA_EXPOSE_ERRORS",
        "false",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)


# ============================================================
# INTELLIGENCE LIMITS
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 12000

MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_MESSAGE_CHARS = 1800

MAX_USER_MESSAGE_CHARS = 5000
MAX_CONTEXT_CHARS = 5000

MAX_DOCUMENT_PAGES = 1000

# ------------------------------------------------------------
# REVIEW CHUNKING
# ------------------------------------------------------------

# Maximum raw document characters placed into one Groq
# review request.
#
# IMPORTANT:
# This is NOT a document limit.
# It is only an individual intelligence-request limit.
# Long pages are divided into multiple requests.
# ------------------------------------------------------------

REVIEW_CHUNK_CHARS = 6500

# Small overlap allows a sentence/paragraph crossing a chunk
# boundary to remain understandable to both requests.
REVIEW_CHUNK_OVERLAP_CHARS = 500

# Maximum amount of previous chunk review information passed
# into the next chunk.
REVIEW_CHUNK_CONTINUITY_CHARS = 1800

# Maximum review output from one chunk.
REVIEW_CHUNK_OUTPUT_TOKENS = 500

# Final page-review assembly request.
REVIEW_ASSEMBLY_INPUT_CHARS = 7000
REVIEW_ASSEMBLY_OUTPUT_TOKENS = 700

# Maximum review findings carried from previous pages.
REVIEW_PAGE_CONTINUITY_CHARS = 2200

# ------------------------------------------------------------
# CORRECTION
# ------------------------------------------------------------

CORRECTION_INPUT_CHARS = 7000
CORRECTION_OUTPUT_TOKENS = 1000

# Maximum amount of a page used when deciding whether a
# correction appears localized.
CORRECTION_ANALYSIS_CHARS = 3500

# ------------------------------------------------------------
# NORMAL CHAT
# ------------------------------------------------------------

NORMAL_OUTPUT_TOKENS = 700


# ============================================================
# EVENTS
# ============================================================

REVIEW_EVENTS = {
    "review",
    "review_requested",
    "review_called",
    "open_review",
    "review_page",
    "review_document",
    "send_for_review",
    "send_review",
}

CORRECTION_EVENTS = {
    "review_correction",
    "document_correction",
    "revise_work",
    "correction",
}


# ============================================================
# ERROR
# ============================================================

class AdaResponseError(Exception):
    """
    Controlled application error raised by AdaResponse.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        category: str = "APPLICATION",
        status_code: int | None = None,
        original: Exception | None = None,
    ):
        super().__init__(message)

        self.stage = stage
        self.category = category
        self.status_code = status_code
        self.original = original


# ============================================================
# GROQ CLIENT
# ============================================================

_client = None


def get_client():
    """
    Lazily create the Groq client.
    """

    global _client

    if _client is not None:
        return _client

    if Groq is None:
        raise AdaResponseError(
            "The groq package is not installed.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    if not API_KEY:
        raise AdaResponseError(
            "GROQ_API_KEY is missing.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    try:
        _client = Groq(
            api_key=API_KEY
        )

        return _client

    except Exception as error:
        raise AdaResponseError(
            "Groq client initialization failed.",
            stage="CLIENT_INITIALIZATION",
            category="GROQ_CLIENT",
            original=error,
        ) from error


# ============================================================
# PUBLIC CONFIGURATION
# ============================================================

def get_ada_model() -> str:
    return MODEL


def is_configured() -> bool:
    return (
        Groq is not None
        and bool(API_KEY)
    )


# ============================================================
# TEXT UTILITIES
# ============================================================

def safe_text(value: Any) -> str:
    """
    Convert arbitrary input into safe stripped text.
    """

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def compact_text(
    text: Any,
    maximum: int,
) -> str:
    """
    Compact INTERNAL context only.

    This helper must never be used to replace the original
    customer document content.

    Long document pages are handled by chunking instead.
    """

    text = safe_text(text)

    if not text:
        return ""

    if len(text) <= maximum:
        return text

    if maximum <= 100:
        return text[:maximum]

    marker = (
        "\n\n"
        "[INTERNAL CONTEXT COMPACTED]\n\n"
    )

    available = maximum - len(marker)

    if available <= 0:
        return text[:maximum]

    first = int(
        available * 0.65
    )

    last = available - first

    return (
        text[:first]
        + marker
        + text[-last:]
    )


def split_text_into_chunks(
    text: str,
    *,
    maximum: int = REVIEW_CHUNK_CHARS,
    overlap: int = REVIEW_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """
    Split long text into bounded intelligence chunks.

    The original text is never modified.

    The splitter attempts to break at:

        1. paragraph boundaries
        2. newline boundaries
        3. sentence boundaries
        4. whitespace
        5. hard character boundary as final fallback

    Overlap is used only for intelligence continuity.
    """

    text = safe_text(text)

    if not text:
        return []

    if maximum <= 0:
        return [text]

    if overlap < 0:
        overlap = 0

    if overlap >= maximum:
        overlap = maximum // 4

    if len(text) <= maximum:
        return [text]

    chunks = []

    start = 0
    text_length = len(text)

    while start < text_length:

        remaining = text_length - start

        if remaining <= maximum:
            chunks.append(
                text[start:].strip()
            )
            break

        candidate_end = start + maximum

        window = text[start:candidate_end]

        # ----------------------------------------------------
        # Prefer paragraph break.
        # ----------------------------------------------------

        break_position = window.rfind(
            "\n\n"
        )

        # ----------------------------------------------------
        # Then ordinary newline.
        # ----------------------------------------------------

        if break_position < int(
            maximum * 0.55
        ):
            break_position = window.rfind(
                "\n"
            )

        # ----------------------------------------------------
        # Then sentence punctuation.
        # ----------------------------------------------------

        if break_position < int(
            maximum * 0.55
        ):
            sentence_matches = list(
                re.finditer(
                    r"[.!?]\s+",
                    window,
                )
            )

            if sentence_matches:
                break_position = (
                    sentence_matches[-1].end()
                )

        # ----------------------------------------------------
        # Then whitespace.
        # ----------------------------------------------------

        if break_position < int(
            maximum * 0.55
        ):
            break_position = window.rfind(
                " "
            )

        # ----------------------------------------------------
        # Final hard boundary.
        # ----------------------------------------------------

        if break_position <= 0:
            break_position = maximum

        end = start + break_position

        chunk = text[
            start:end
        ].strip()

        if not chunk:
            end = candidate_end

            chunk = text[
                start:end
            ].strip()

        if chunk:
            chunks.append(
                chunk
            )

        # ----------------------------------------------------
        # Advance while retaining a small overlap.
        # ----------------------------------------------------

        next_start = end - overlap

        if next_start <= start:
            next_start = end

        start = next_start

    return chunks


# ============================================================
# GROQ ERROR CLASSIFICATION
# ============================================================

def get_error_status_code(
    error: Exception,
) -> int | None:

    value = getattr(
        error,
        "status_code",
        None,
    )

    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def classify_groq_error(
    error: Exception,
) -> str:

    status = get_error_status_code(
        error
    )

    message = safe_text(
        error
    ).lower()

    name = (
        type(error)
        .__name__
        .lower()
    )

    if status == 429:
        return "GROQ_RATE_LIMIT"

    if (
        "rate" in name
        or "rate limit" in message
    ):
        return "GROQ_RATE_LIMIT"

    if status == 401:
        return "GROQ_AUTHENTICATION"

    if status == 403:
        return "GROQ_PERMISSION"

    if status == 404:
        return "GROQ_NOT_FOUND"

    if status == 400:
        return "GROQ_BAD_REQUEST"

    if status == 413:
        return "GROQ_REQUEST_TOO_LARGE"

    if status is not None and status >= 500:
        return "GROQ_SERVER_ERROR"

    if (
        "timeout" in name
        or "connection" in name
        or "network" in name
    ):
        return "NETWORK"

    return "GROQ_REQUEST_ERROR"


def log_error(
    title: str,
    error: Exception,
    *,
    stage: str,
    category: str | None = None,
    page_number: int | None = None,
    chunk_number: int | None = None,
    event: str | None = None,
):
    """
    Server-side diagnostic logging.
    """

    print()
    print("=" * 78)
    print("ADA ERROR:", title)
    print("=" * 78)

    print(
        "Stage:",
        stage,
    )

    print(
        "Category:",
        category
        or classify_groq_error(error),
    )

    if page_number is not None:
        print(
            "Page:",
            page_number,
        )

    if chunk_number is not None:
        print(
            "Chunk:",
            chunk_number,
        )

    if event:
        print(
            "Event:",
            event,
        )

    print(
        "Model:",
        MODEL,
    )

    print(
        "API key configured:",
        bool(API_KEY),
    )

    print(
        "Error:",
        compact_text(
            error,
            1500,
        ),
    )

    traceback.print_exc()

    print("=" * 78)
    print()


def client_error_message(
    error: Exception,
) -> str:
    """
    Convert an internal error into a safe customer message.
    """

    if not EXPOSE_ERRORS_TO_CLIENT:
        return (
            "I could not process your request right now. "
            "Please try again."
        )

    category = getattr(
        error,
        "category",
        None,
    )

    if not category:
        category = classify_groq_error(
            error
        )

    return (
        "Technical error detected.\n\n"
        f"Category: {category}\n"
        f"Error: {compact_text(error, 1200)}"
    )


# ============================================================
# DOCUMENT PAGE NORMALIZATION
# ============================================================

def normalize_document_pages(
    pages: Any,
) -> list[dict[str, Any]]:
    """
    Normalize supported page formats.

    IMPORTANT:

    Page content is NOT compacted.

    Page content is preserved exactly as supplied except for
    outer whitespace normalization.

    The original page content is returned in the page object.
    """

    if pages is None:
        return []

    if not isinstance(
        pages,
        list,
    ):
        pages = [pages]

    result = []

    for index, item in enumerate(
        pages,
        start=1,
    ):

        # ----------------------------------------------------
        # Plain string page.
        # ----------------------------------------------------

        if isinstance(
            item,
            str,
        ):

            content = item.strip()

            if not content:
                continue

            result.append(
                {
                    "page_number": index,
                    "content": content,
                    "title": "",
                }
            )

            continue

        # ----------------------------------------------------
        # Dictionary page.
        # ----------------------------------------------------

        if isinstance(
            item,
            dict,
        ):

            raw_page_number = (
                item.get(
                    "page_number"
                )
                or item.get(
                    "page"
                )
                or index
            )

            try:
                page_number = int(
                    raw_page_number
                )
            except Exception:
                page_number = index

            content = (
                item.get("content")
                or item.get("text")
                or item.get("page_content")
                or item.get("document_text")
                or ""
            )

            content = safe_text(
                content
            )

            if not content:
                continue

            result.append(
                {
                    "page_number":
                        page_number,

                    "content":
                        content,

                    "title":
                        safe_text(
                            item.get(
                                "title"
                            )
                        ),
                }
            )

    if len(result) > MAX_DOCUMENT_PAGES:
        raise AdaResponseError(
            (
                "The document contains too many pages "
                "for this processing session."
            ),
            stage="DOCUMENT_NORMALIZATION",
            category="DOCUMENT_TOO_LARGE",
        )

    return result


# ============================================================
# DOCUMENT TEXT → PAGES
# ============================================================

def document_text_to_pages(
    document: str,
) -> list[dict[str, Any]]:
    """
    Convert a complete text document into logical pages.

    Explicit PAGE markers are respected.

    Without markers, the complete document becomes one logical
    page.

    It is NOT truncated to fit a Groq request.
    """

    document = safe_text(
        document
    )

    if not document:
        return []

    marker_pattern = re.compile(
        r"(?:^|\n)"
        r"(?:={2,}\s*)?"
        r"(?:PAGE|Page)"
        r"\s*(\d+)"
        r"(?:\s*={2,})?"
        r"\s*(?:\n|$)",
        re.IGNORECASE,
    )

    matches = list(
        marker_pattern.finditer(
            document
        )
    )

    if matches:

        pages = []

        for index, match in enumerate(
            matches
        ):

            start = match.end()

            if index + 1 < len(matches):
                end = matches[
                    index + 1
                ].start()
            else:
                end = len(document)

            content = document[
                start:end
            ].strip()

            if content:

                pages.append(
                    {
                        "page_number":
                            int(
                                match.group(1)
                            ),

                        "content":
                            content,

                        "title":
                            "",
                    }
                )

        if pages:
            return pages

    return [
        {
            "page_number": 1,
            "content": document,
            "title": "",
        }
    ]


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:
    """
    End-to-end document intelligence layer.

    Public operations:

        respond()
        review_page()
        review_document_pages()
        correct_document()

    AdaResponse does not own permanent document state.
    """

    def __init__(
        self,
        service: str | None = None,
    ):

        self.service = (
            safe_text(service)
            or None
        )

        self.prompt_manager = (
            AdaPromptManager()
        )

        self.billing = (
            BillingManager()
        )

        self.history: list[
            dict[str, str]
        ] = []

    # ========================================================
    # SERVICE
    # ========================================================

    def set_service(
        self,
        service: str | None,
    ):
        service = safe_text(
            service
        )

        if service:
            self.service = service

    def normalize_service(
        self,
        service: str | None,
    ) -> str | None:

        service = safe_text(
            service
        )

        if not service:
            return self.service

        try:

            normalized = (
                self.billing
                .normalize_service(
                    service
                )
            )

            return (
                safe_text(
                    normalized
                )
                or service
            )

        except Exception as error:

            log_error(
                "SERVICE NORMALIZATION FAILED",
                error,
                stage="SERVICE_NORMALIZATION",
                category="BILLING",
            )

            return service

    # ========================================================
    # BILLING CONTEXT
    # ========================================================

    def get_billing_context(
        self,
        service: str | None,
    ) -> str:

        service = self.normalize_service(
            service
        )

        if not service:
            return ""

        try:

            item = (
                self.billing
                .get_service(
                    service
                )
            )

        except Exception as error:

            log_error(
                "BILLING LOOKUP FAILED",
                error,
                stage="BILLING_LOOKUP",
                category="BILLING",
            )

            return (
                "OFFICIAL BILLING FACTS\n"
                "Billing information unavailable.\n"
                "Do not invent a price."
            )

        if not item:

            return (
                "OFFICIAL BILLING FACTS\n"
                "No billing record found.\n"
                "Do not invent a price."
            )

        price = item.get(
            "price",
            0,
        )

        billing_type = item.get(
            "billing"
        )

        if billing_type == "fixed":

            pricing = (
                f"Official price: ₦{price:,}\n"
                "Billing type: fixed"
            )

        elif billing_type == "per_page":

            pricing = (
                f"Official price: ₦{price:,} per page\n"
                "Billing type: per_page"
            )

        elif billing_type == "quotation":

            pricing = (
                "Official price: quotation required\n"
                "Billing type: quotation"
            )

        else:

            pricing = (
                f"Official price: ₦{price:,}\n"
                f"Billing type: {billing_type}"
            )

        return (
            "OFFICIAL BILLING FACTS\n"
            f"Service: {service}\n"
            f"{pricing}\n"
            "BillingManager is authoritative."
        )

    # ========================================================
    # CORE INTELLIGENCE PROMPT
    # ========================================================

    def get_intelligence_prompt(
        self,
    ) -> str:

        return (
            "You are Ada, the intelligent "
            "customer-facing assistant of "
            "Naija Pocket Business Center.\n\n"

            "Understand the customer's actual request "
            "and reason intelligently about the work.\n\n"

            "Do NOT use keyword matching to determine "
            "customer intent.\n\n"

            "Use the selected service, customer request, "
            "document material and application state "
            "as reasoning context.\n\n"

            "DOCUMENT REVIEW\n"
            "===============\n"
            "A document can contain many pages.\n\n"

            "Every supplied page belongs to the same "
            "customer document unless the application "
            "explicitly says otherwise.\n\n"

            "A page may also be divided into multiple "
            "intelligence chunks because of request limits.\n\n"

            "A chunk is NOT a separate document.\n"
            "A chunk is NOT a separate customer job.\n\n"

            "When reviewing chunks, reason about the "
            "material supplied while preserving its "
            "relationship to the complete page.\n\n"

            "Look for genuine issues involving:\n"
            "- correctness\n"
            "- completeness\n"
            "- relevance\n"
            "- grammar\n"
            "- clarity\n"
            "- consistency\n"
            "- contradictions\n"
            "- structure\n"
            "- visible formatting problems\n"
            "- compliance with the customer's request\n\n"

            "Do not invent facts.\n"
            "Do not invent missing pages.\n"
            "Do not claim that content is missing unless "
            "the supplied material establishes that.\n\n"

            "DOCUMENT PRESERVATION\n"
            "=====================\n"
            "The application owns the complete document.\n\n"

            "Never delete customer document content.\n"
            "Never silently summarize a customer page "
            "in place of its original content.\n"
            "Never replace a page with a review.\n"
            "Never invent pages.\n\n"

            "An LLM request limit is NOT a document limit.\n\n"

            "CORRECTION MODE\n"
            "===============\n"
            "When a correction is requested, work from "
            "the current document version supplied by "
            "the application.\n\n"

            "Apply the requested correction without "
            "unnecessarily changing unrelated content.\n\n"

            "Do not revert to an earlier version.\n\n"

            "BILLING\n"
            "=======\n"
            "BillingManager is authoritative for pricing.\n"
            "Never invent a price.\n\n"

            "CUSTOMER COMMUNICATION\n"
            "=======================\n"
            "Speak naturally, warmly and practically.\n\n"

            "Never mention Groq, API calls, token limits, "
            "system prompts or internal architecture."
        )

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    def build_system_prompt(
        self,
        *,
        service: str | None = None,
        context: str | None = None,
    ) -> str:

        active_service = (
            self.normalize_service(
                service
            )
        )

        parts = []

        # ----------------------------------------------------
        # Existing prompt manager.
        # ----------------------------------------------------

        try:

            central = (
                self.prompt_manager
                .build_prompt(
                    service=active_service
                )
            )

            if central:

                parts.append(
                    compact_text(
                        central,
                        7500,
                    )
                )

        except Exception as error:

            log_error(
                "PROMPT MANAGER FAILED",
                error,
                stage="PROMPT_MANAGER",
                category="PROMPT_MANAGER",
            )

        # ----------------------------------------------------
        # Core intelligence rules.
        # ----------------------------------------------------

        parts.append(
            self.get_intelligence_prompt()
        )

        # ----------------------------------------------------
        # Billing.
        # ----------------------------------------------------

        billing = (
            self.get_billing_context(
                active_service
            )
        )

        if billing:
            parts.append(
                billing
            )

        # ----------------------------------------------------
        # Application state.
        # ----------------------------------------------------

        if context:

            parts.append(
                "CURRENT APPLICATION STATE\n"
                + compact_text(
                    context,
                    MAX_CONTEXT_CHARS,
                )
            )

        # ----------------------------------------------------
        # Selected service.
        # ----------------------------------------------------

        if active_service:

            parts.append(
                "SELECTED SERVICE\n"
                + active_service
            )

        return compact_text(
            "\n\n".join(parts),
            MAX_SYSTEM_PROMPT_CHARS,
        )

    # ========================================================
    # HISTORY
    # ========================================================

    def add_history(
        self,
        role: str,
        content: str,
    ):

        role = safe_text(
            role
        )

        content = safe_text(
            content
        )

        if not role or not content:
            return

        self.history.append(
            {
                "role": role,
                "content": content,
            }
        )

        self.history = (
            self.history[
                -MAX_HISTORY_MESSAGES:
            ]
        )

    def clear_history(self):
        self.history.clear()

    # ========================================================
    # GROQ
    # ========================================================

    def call_groq(
        self,
        *,
        messages: list[dict[str, str]],
        output_tokens: int,
        stage: str,
        page_number: int | None = None,
        chunk_number: int | None = None,
        event: str | None = None,
    ) -> str:

        client = get_client()

        print()
        print("=" * 78)
        print("ADA INTELLIGENCE → GROQ")
        print("=" * 78)
        print("Stage:", stage)
        print("Model:", MODEL)
        print("Page:", page_number)
        print("Chunk:", chunk_number)
        print("Messages:", len(messages))
        print("Output tokens:", output_tokens)
        print("=" * 78)

        try:

            response = (
                client
                .chat
                .completions
                .create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.2,
                    max_completion_tokens=(
                        output_tokens
                    ),
                )
            )

        except Exception as error:

            category = (
                classify_groq_error(
                    error
                )
            )

            log_error(
                "GROQ REQUEST FAILED",
                error,
                stage=stage,
                category=category,
                page_number=page_number,
                chunk_number=chunk_number,
                event=event,
            )

            raise AdaResponseError(
                str(error),
                stage=stage,
                category=category,
                status_code=(
                    get_error_status_code(
                        error
                    )
                ),
                original=error,
            ) from error

        if not response:

            raise AdaResponseError(
                "Groq returned no response.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

        if not response.choices:

            raise AdaResponseError(
                "Groq returned no choices.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

        content = safe_text(
            response
            .choices[0]
            .message
            .content
        )

        if not content:

            raise AdaResponseError(
                "Groq returned empty content.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage:

            print(
                "Prompt tokens:",
                getattr(
                    usage,
                    "prompt_tokens",
                    None,
                ),
            )

            print(
                "Completion tokens:",
                getattr(
                    usage,
                    "completion_tokens",
                    None,
                ),
            )

            print(
                "Total tokens:",
                getattr(
                    usage,
                    "total_tokens",
                    None,
                ),
            )

        print(
            "Ada intelligence response received."
        )

        return content

    # ========================================================
    # REVIEW CHUNK PROMPT
    # ========================================================

    def build_review_chunk_prompt(
        self,
        *,
        page_number: int,
        total_pages: int,
        chunk_number: int,
        total_chunks: int,
        chunk_content: str,
        previous_chunk_review: str,
        previous_page_reviews: str,
        customer_request: str,
    ) -> str:

        return (
            "CUSTOMER DOCUMENT REVIEW\n\n"

            "You are reviewing a portion of one page "
            "of a complete customer document.\n\n"

            f"DOCUMENT PAGE: {page_number} OF {total_pages}\n"
            f"PAGE REVIEW CHUNK: {chunk_number} "
            f"OF {total_chunks}\n\n"

            "CUSTOMER'S REVIEW REQUEST:\n"
            f"{compact_text(customer_request, 2600)}\n\n"

            "CURRENT CHUNK CONTENT:\n"
            f"{chunk_content}\n\n"

            "PREVIOUS CHUNK REVIEW CONTINUITY:\n"
            f"{compact_text(previous_chunk_review, 1600)}\n\n"

            "PREVIOUS PAGE REVIEW CONTINUITY:\n"
            f"{compact_text(previous_page_reviews, 1800)}\n\n"

            "TASK\n"
            "====\n"

            "Review the supplied material intelligently.\n\n"

            "Treat this as part of the same document page, "
            "not as a separate job.\n\n"

            "Look for genuine issues involving:\n"
            "- correctness\n"
            "- completeness\n"
            "- relevance\n"
            "- grammar\n"
            "- clarity\n"
            "- consistency\n"
            "- contradictions\n"
            "- structure\n"
            "- visible formatting problems\n"
            "- compliance with the customer's request\n\n"

            "Pay attention to information carried through "
            "the previous chunk and previous pages.\n\n"

            "Do not invent facts.\n"
            "Do not invent content outside the supplied material.\n"
            "Do not assume that a chunk boundary means the "
            "document has ended.\n\n"

            "Return concise review findings for this chunk.\n\n"

            "If there is no genuine issue in this chunk, "
            "say that the supplied material is satisfactory."
        )

    # ========================================================
    # REVIEW PAGE ASSEMBLY PROMPT
    # ========================================================

    def build_review_assembly_prompt(
        self,
        *,
        page_number: int,
        total_pages: int,
        chunk_reviews: list[str],
        customer_request: str,
        previous_page_reviews: str,
    ) -> str:

        findings = []

        for index, review in enumerate(
            chunk_reviews,
            start=1,
        ):

            findings.append(
                f"CHUNK {index} REVIEW:\n"
                f"{review}"
            )

        return (
            "PAGE REVIEW ASSEMBLY\n\n"

            f"PAGE {page_number} OF {total_pages}\n\n"

            "CUSTOMER'S REVIEW REQUEST:\n"
            f"{compact_text(customer_request, 2500)}\n\n"

            "PREVIOUS PAGE CONTINUITY:\n"
            f"{compact_text(previous_page_reviews, 1800)}\n\n"

            "INDIVIDUAL CHUNK REVIEWS:\n"
            f"{compact_text(chr(10).join(findings), REVIEW_ASSEMBLY_INPUT_CHARS)}\n\n"

            "TASK\n"
            "====\n"

            "Combine the chunk-level review findings into "
            "one coherent review for this complete page.\n\n"

            "Remove duplicate findings caused by chunk overlap.\n\n"

            "Do not invent new issues.\n\n"

            "Keep genuine findings from all chunks.\n\n"

            "Consider cross-chunk consistency when combining "
            "the findings.\n\n"

            "The result will become the review attached to "
            "this page's review card.\n\n"

            "Do not reproduce the page content.\n\n"

            "Return the coherent page review only."
        )

    # ========================================================
    # REVIEW ONE PAGE
    # ========================================================

    def review_page(
        self,
        *,
        page_number: int,
        total_pages: int,
        page_content: str,
        service: str | None,
        context: str | None,
        customer_request: str,
        previous_reviews: str = "",
        event: str = "review",
    ) -> str:

        page_content = safe_text(
            page_content
        )

        if not page_content:

            return (
                "No text content was supplied "
                "for this page."
            )

        # ----------------------------------------------------
        # NEVER truncate page_content.
        #
        # Long content is chunked instead.
        # ----------------------------------------------------

        chunks = split_text_into_chunks(
            page_content,
            maximum=REVIEW_CHUNK_CHARS,
            overlap=REVIEW_CHUNK_OVERLAP_CHARS,
        )

        if not chunks:

            return (
                "No reviewable text was supplied "
                "for this page."
            )

        system_prompt = (
            self.build_system_prompt(
                service=service,
                context=context,
            )
        )

        chunk_reviews = []

        previous_chunk_review = ""

        total_chunks = len(
            chunks
        )

        for chunk_index, chunk in enumerate(
            chunks,
            start=1,
        ):

            prompt = (
                self.build_review_chunk_prompt(
                    page_number=page_number,
                    total_pages=total_pages,
                    chunk_number=chunk_index,
                    total_chunks=total_chunks,
                    chunk_content=chunk,
                    previous_chunk_review=(
                        previous_chunk_review
                    ),
                    previous_page_reviews=(
                        previous_reviews
                    ),
                    customer_request=(
                        customer_request
                    ),
                )
            )

            messages = [
                {
                    "role":
                        "system",
                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",
                    "content":
                        prompt,
                },
            ]

            review = self.call_groq(
                messages=messages,
                output_tokens=(
                    REVIEW_CHUNK_OUTPUT_TOKENS
                ),
                stage="REVIEW_CHUNK",
                page_number=page_number,
                chunk_number=chunk_index,
                event=event,
            )

            review = safe_text(
                review
            )

            chunk_reviews.append(
                review
            )

            previous_chunk_review = (
                compact_text(
                    review,
                    REVIEW_CHUNK_CONTINUITY_CHARS,
                )
            )

        # ----------------------------------------------------
        # One chunk does not need a second assembly request.
        # ----------------------------------------------------

        if len(chunk_reviews) == 1:

            return chunk_reviews[0]

        # ----------------------------------------------------
        # Multiple chunks require intelligent assembly.
        # ----------------------------------------------------

        assembly_prompt = (
            self.build_review_assembly_prompt(
                page_number=page_number,
                total_pages=total_pages,
                chunk_reviews=chunk_reviews,
                customer_request=(
                    customer_request
                ),
                previous_page_reviews=(
                    previous_reviews
                ),
            )
        )

        assembly_messages = [
            {
                "role":
                    "system",
                "content":
                    system_prompt,
            },
            {
                "role":
                    "user",
                "content":
                    assembly_prompt,
            },
        ]

        assembled = self.call_groq(
            messages=assembly_messages,
            output_tokens=(
                REVIEW_ASSEMBLY_OUTPUT_TOKENS
            ),
            stage="REVIEW_PAGE_ASSEMBLY",
            page_number=page_number,
            event=event,
        )

        return safe_text(
            assembled
        )

    # ========================================================
    # REVIEW COMPLETE DOCUMENT
    # ========================================================

    def review_document_pages(
        self,
        *,
        pages: list[Any],
        service: str | None = None,
        context: str | None = None,
        customer_request: str = "",
        event: str = "review",
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Review every supplied document page.

        Long pages are internally chunked.

        The application receives one final review card for each
        actual document page.

        The original page content is returned unchanged.

        Progress callbacks:

            page_started
            page_completed
            page_error
            review_completed
        """

        normalized_pages = (
            normalize_document_pages(
                pages
            )
        )

        if not normalized_pages:

            raise AdaResponseError(
                "No document pages were supplied.",
                stage="REVIEW_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        total_pages = len(
            normalized_pages
        )

        page_results = []

        continuity_items = []

        for position, page in enumerate(
            normalized_pages,
            start=1,
        ):

            page_number = int(
                page.get(
                    "page_number",
                    position,
                )
            )

            content = safe_text(
                page.get(
                    "content"
                )
            )

            # ------------------------------------------------
            # Count chunks for application visibility.
            # ------------------------------------------------

            chunks = split_text_into_chunks(
                content,
                maximum=REVIEW_CHUNK_CHARS,
                overlap=REVIEW_CHUNK_OVERLAP_CHARS,
            )

            chunk_count = len(
                chunks
            )

            # ------------------------------------------------
            # Notify application that this actual page has
            # entered review.
            # ------------------------------------------------

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "page_started",

                        "page_number":
                            page_number,

                        "position":
                            position,

                        "total_pages":
                            total_pages,

                        "chunk_count":
                            chunk_count,

                        "status":
                            "processing",

                        # IMPORTANT:
                        # Complete original page content.
                        "content":
                            content,
                    }
                )

            previous_reviews = "\n\n".join(
                continuity_items
            )

            previous_reviews = compact_text(
                previous_reviews,
                REVIEW_PAGE_CONTINUITY_CHARS,
            )

            try:

                review = self.review_page(
                    page_number=page_number,
                    total_pages=total_pages,
                    page_content=content,
                    service=service,
                    context=context,
                    customer_request=(
                        customer_request
                    ),
                    previous_reviews=(
                        previous_reviews
                    ),
                    event=event,
                )

            except Exception as error:

                if progress_callback:

                    progress_callback(
                        {
                            "type":
                                "page_error",

                            "page_number":
                                page_number,

                            "position":
                                position,

                            "total_pages":
                                total_pages,

                            "chunk_count":
                                chunk_count,

                            "status":
                                "error",

                            "error":
                                client_error_message(
                                    error
                                ),
                        }
                    )

                raise

            review = safe_text(
                review
            )

            # ------------------------------------------------
            # ONE CARD PER ACTUAL PAGE.
            #
            # The original complete page content is retained.
            # ------------------------------------------------

            result = {
                "type":
                    "review_card",

                "page_number":
                    page_number,

                "position":
                    position,

                "total_pages":
                    total_pages,

                "chunk_count":
                    chunk_count,

                "content":
                    content,

                "review":
                    review,

                "status":
                    "reviewed",
            }

            page_results.append(
                result
            )

            # ------------------------------------------------
            # Preserve only review intelligence for continuity.
            # ------------------------------------------------

            continuity_items.append(
                (
                    f"PAGE {page_number} REVIEW:\n"
                    f"{review}"
                )
            )

            continuity_items = (
                continuity_items[
                    -6:
                ]
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Send the completed actual page card immediately.
            # ------------------------------------------------

            if progress_callback:

                progress_callback(
                    result
                )

        # ----------------------------------------------------
        # Assemble complete review.
        # ----------------------------------------------------

        assembled_review = (
            self.assemble_review(
                page_results
            )
        )

        complete_result = {
            "type":
                "review_completed",

            "status":
                "completed",

            "total_pages":
                total_pages,

            "pages":
                page_results,

            "assembled_review":
                assembled_review,
        }

        if progress_callback:

            progress_callback(
                complete_result
            )

        return {
            "pages":
                page_results,

            "total_pages":
                total_pages,

            "assembled_review":
                assembled_review,

            "status":
                "completed",
        }

    # ========================================================
    # REVIEW ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_review(
        page_results: list[
            dict[str, Any]
        ],
    ) -> str:
        """
        Assemble review findings only.

        This does NOT assemble or replace the customer document.
        """

        ordered = sorted(
            page_results,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts = []

        for item in ordered:

            page_number = item.get(
                "page_number"
            )

            review = safe_text(
                item.get(
                    "review"
                )
            )

            if not review:
                continue

            parts.append(
                "PAGE "
                + str(page_number)
                + "\n\n"
                + review
            )

        if not parts:

            return (
                "The document was reviewed, "
                "but no review findings were returned."
            )

        return (
            "COMPLETE DOCUMENT REVIEW\n\n"
            + "\n\n".join(parts)
        )

    # ========================================================
    # CORRECTION PAGE PROMPT
    # ========================================================

    def build_correction_page_prompt(
        self,
        *,
        page_number: int,
        total_pages: int,
        page_content: str,
        correction: str,
    ) -> str:

        return (
            "CUSTOMER DOCUMENT CORRECTION\n\n"

            "This is the CURRENT version of one page "
            "of the customer's document.\n\n"

            f"PAGE {page_number} OF {total_pages}\n\n"

            "CUSTOMER'S CORRECTION REQUEST:\n"
            f"{compact_text(correction, 4000)}\n\n"

            "CURRENT PAGE CONTENT:\n"
            f"{page_content}\n\n"

            "TASK\n"
            "====\n"

            "Apply the customer's requested correction.\n\n"

            "Rules:\n"
            "- Preserve existing facts.\n"
            "- Do not invent facts.\n"
            "- Do not remove unrelated useful content.\n"
            "- Do not revert to an older version.\n"
            "- Preserve useful structure.\n"
            "- Return corrected page content only.\n"
            "- Do not explain the correction process.\n"
            "- Do not mention internal processing."
        )

    # ========================================================
    # CORRECTION CHUNK PROMPT
    # ========================================================

    def build_correction_chunk_prompt(
        self,
        *,
        page_number: int,
        total_pages: int,
        chunk_number: int,
        total_chunks: int,
        chunk_content: str,
        correction: str,
    ) -> str:

        return (
            "CUSTOMER DOCUMENT CORRECTION\n\n"

            f"PAGE {page_number} OF {total_pages}\n"
            f"CORRECTION CHUNK {chunk_number} "
            f"OF {total_chunks}\n\n"

            "CUSTOMER'S CORRECTION REQUEST:\n"
            f"{compact_text(correction, 3500)}\n\n"

            "CURRENT PAGE CHUNK:\n"
            f"{chunk_content}\n\n"

            "TASK\n"
            "====\n"

            "Determine whether this supplied chunk requires "
            "a change to satisfy the customer's correction.\n\n"

            "If the requested correction affects this chunk, "
            "apply it while preserving unrelated content.\n\n"

            "If the requested correction does not affect this "
            "chunk, preserve the supplied content.\n\n"

            "Do not invent facts.\n"
            "Do not remove unrelated content.\n"
            "Do not return an explanation.\n\n"

            "Return the resulting chunk content only."
        )

    # ========================================================
    # CORRECTION TARGET DETECTION
    # ========================================================

    def find_correction_target_pages(
        self,
        *,
        pages: list[dict[str, Any]],
        correction: str,
    ) -> list[int]:
        """
        Use Ada intelligence to identify which pages are likely
        affected by a correction.

        IMPORTANT:

        This is NOT keyword matching.

        If the correction is ambiguous, all pages are returned
        so that the application does not risk losing a required
        correction.

        This method is intentionally conservative.
        """

        if len(pages) <= 1:
            return [0]

        correction = safe_text(
            correction
        )

        if not correction:
            return list(
                range(len(pages))
            )

        # ----------------------------------------------------
        # Build a bounded page index for reasoning.
        #
        # This is metadata/context, not the permanent document.
        # ----------------------------------------------------

        page_index_parts = []

        for index, page in enumerate(
            pages
        ):

            page_number = page.get(
                "page_number",
                index + 1,
            )

            content = safe_text(
                page.get(
                    "content"
                )
            )

            page_preview = compact_text(
                content,
                CORRECTION_ANALYSIS_CHARS,
            )

            page_index_parts.append(
                (
                    f"PAGE {page_number}\n"
                    f"{page_preview}"
                )
            )

        index_text = "\n\n".join(
            page_index_parts
        )

        system_prompt = (
            self.build_system_prompt()
            + "\n\n"
            "CORRECTION TARGET ANALYSIS\n"
            "The application needs to determine which "
            "document pages may be affected by a customer's "
            "correction.\n\n"
            "Do not use simple keyword matching.\n"
            "Reason about the customer's actual request.\n"
            "If uncertain, return ALL pages.\n"
            "Never exclude a page merely because the correction "
            "does not contain an exact word from that page."
        )

        prompt = (
            "CUSTOMER CORRECTION:\n"
            f"{compact_text(correction, 3500)}\n\n"

            "DOCUMENT PAGE INDEX:\n"
            f"{compact_text(index_text, 6500)}\n\n"

            "Return the page numbers that may need correction.\n\n"

            "Return ONLY a comma-separated list of page numbers.\n"
            "Example: 1,3,4\n\n"

            "If the correction could reasonably affect the "
            "whole document, return ALL page numbers."
        )

        messages = [
            {
                "role":
                    "system",
                "content":
                    system_prompt,
            },
            {
                "role":
                    "user",
                "content":
                    prompt,
            },
        ]

        try:

            result = self.call_groq(
                messages=messages,
                output_tokens=250,
                stage="CORRECTION_TARGET_ANALYSIS",
                event="document_correction",
            )

        except Exception:

            # ------------------------------------------------
            # Safety-first fallback:
            # correct all pages rather than risking omission.
            # ------------------------------------------------

            return list(
                range(len(pages))
            )

        numbers = []

        for match in re.findall(
            r"\b\d+\b",
            result,
        ):

            try:
                value = int(
                    match
                )
            except Exception:
                continue

            if 1 <= value <= len(pages):
                numbers.append(
                    value - 1
                )

        numbers = sorted(
            set(numbers)
        )

        # ----------------------------------------------------
        # Ambiguous/invalid result:
        # process every page.
        # ----------------------------------------------------

        if not numbers:
            return list(
                range(len(pages))
            )

        return numbers

    # ========================================================
    # CORRECT ONE PAGE
    # ========================================================

    def correct_page(
        self,
        *,
        page_number: int,
        total_pages: int,
        page_content: str,
        correction: str,
        service: str | None,
        context: str | None,
    ) -> str:
        """
        Correct one complete page.

        Long pages are chunked.

        The complete original page is never silently truncated.
        """

        page_content = safe_text(
            page_content
        )

        chunks = split_text_into_chunks(
            page_content,
            maximum=CORRECTION_INPUT_CHARS,
            overlap=REVIEW_CHUNK_OVERLAP_CHARS,
        )

        if not chunks:
            return page_content

        system_prompt = (
            self.build_system_prompt(
                service=service,
                context=context,
            )
        )

        # ----------------------------------------------------
        # One request for ordinary-size pages.
        # ----------------------------------------------------

        if len(chunks) == 1:

            prompt = (
                self.build_correction_page_prompt(
                    page_number=page_number,
                    total_pages=total_pages,
                    page_content=chunks[0],
                    correction=correction,
                )
            )

            messages = [
                {
                    "role":
                        "system",
                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",
                    "content":
                        prompt,
                },
            ]

            return safe_text(
                self.call_groq(
                    messages=messages,
                    output_tokens=(
                        CORRECTION_OUTPUT_TOKENS
                    ),
                    stage="CORRECTION_PAGE",
                    page_number=page_number,
                    event="document_correction",
                )
            )

        # ----------------------------------------------------
        # Long page:
        # correct chunks independently.
        # ----------------------------------------------------

        corrected_chunks = []

        total_chunks = len(
            chunks
        )

        for chunk_index, chunk in enumerate(
            chunks,
            start=1,
        ):

            prompt = (
                self.build_correction_chunk_prompt(
                    page_number=page_number,
                    total_pages=total_pages,
                    chunk_number=chunk_index,
                    total_chunks=total_chunks,
                    chunk_content=chunk,
                    correction=correction,
                )
            )

            messages = [
                {
                    "role":
                        "system",
                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",
                    "content":
                        prompt,
                },
            ]

            corrected = self.call_groq(
                messages=messages,
                output_tokens=(
                    CORRECTION_OUTPUT_TOKENS
                ),
                stage="CORRECTION_CHUNK",
                page_number=page_number,
                chunk_number=chunk_index,
                event="document_correction",
            )

            corrected_chunks.append(
                safe_text(
                    corrected
                )
            )

        # ----------------------------------------------------
        # Remove overlap duplication.
        #
        # Because correction chunks overlap, simply joining
        # them would duplicate boundary text.
        #
        # A lightweight boundary merge is used here.
        # ----------------------------------------------------

        return self.merge_overlapping_chunks(
            corrected_chunks
        )

    # ========================================================
    # OVERLAPPING CHUNK MERGE
    # ========================================================

    @staticmethod
    def merge_overlapping_chunks(
        chunks: list[str],
    ) -> str:
        """
        Merge corrected chunks while removing obvious repeated
        boundary text.

        This operates only on corrected intelligence output.
        """

        if not chunks:
            return ""

        merged = chunks[0].strip()

        for current in chunks[1:]:

            current = current.strip()

            if not current:
                continue

            # ------------------------------------------------
            # Find the largest suffix/prefix overlap.
            # ------------------------------------------------

            maximum = min(
                len(merged),
                len(current),
                REVIEW_CHUNK_OVERLAP_CHARS,
            )

            overlap_found = 0

            for size in range(
                maximum,
                99,
                -1,
            ):

                suffix = merged[
                    -size:
                ]

                prefix = current[
                    :size
                ]

                if suffix == prefix:
                    overlap_found = size
                    break

            if overlap_found:

                merged += current[
                    overlap_found:
                ]

            else:

                merged += (
                    "\n\n"
                    + current
                )

        return merged.strip()

    # ========================================================
    # CORRECT DOCUMENT
    # ========================================================

    def correct_document(
        self,
        *,
        document_pages: list[Any],
        correction: str,
        service: str | None,
        context: str | None,
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:
        """
        Correct the CURRENT document version.

        The application must provide the current version.

        AdaResponse never retrieves or restores an older version.

        By default, correction targeting is intelligently
        determined.

        If targeting is ambiguous, every page is processed to
        avoid silently missing a requested correction.
        """

        pages = normalize_document_pages(
            document_pages
        )

        if not pages:

            raise AdaResponseError(
                "There is no document to correct.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        correction = safe_text(
            correction
        )

        if not correction:

            raise AdaResponseError(
                "Correction instruction is empty.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_CORRECTION",
            )

        total_pages = len(
            pages
        )

        # ----------------------------------------------------
        # Determine potentially affected pages using Ada
        # reasoning rather than keyword matching.
        # ----------------------------------------------------

        target_indexes = (
            self.find_correction_target_pages(
                pages=pages,
                correction=correction,
            )
        )

        target_indexes = set(
            target_indexes
        )

        corrected_pages = []

        for position, page in enumerate(
            pages,
            start=1,
        ):

            page_number = int(
                page.get(
                    "page_number",
                    position,
                )
            )

            original_content = safe_text(
                page.get(
                    "content"
                )
            )

            # ------------------------------------------------
            # Page not selected:
            # preserve it exactly.
            # ------------------------------------------------

            if (
                position - 1
                not in target_indexes
            ):

                preserved_page = {
                    "type":
                        "corrected_page",

                    "page_number":
                        page_number,

                    "position":
                        position,

                    "total_pages":
                        total_pages,

                    "content":
                        original_content,

                    "status":
                        "unchanged",
                }

                corrected_pages.append(
                    preserved_page
                )

                if progress_callback:

                    progress_callback(
                        preserved_page
                    )

                continue

            # ------------------------------------------------
            # Page selected for correction.
            # ------------------------------------------------

            chunks = split_text_into_chunks(
                original_content,
                maximum=CORRECTION_INPUT_CHARS,
                overlap=REVIEW_CHUNK_OVERLAP_CHARS,
            )

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "correction_page_started",

                        "page_number":
                            page_number,

                        "position":
                            position,

                        "total_pages":
                            total_pages,

                        "chunk_count":
                            len(chunks),

                        "status":
                            "processing",
                    }
                )

            try:

                corrected = self.correct_page(
                    page_number=page_number,
                    total_pages=total_pages,
                    page_content=original_content,
                    correction=correction,
                    service=service,
                    context=context,
                )

            except Exception as error:

                if progress_callback:

                    progress_callback(
                        {
                            "type":
                                "correction_page_error",

                            "page_number":
                                page_number,

                            "position":
                                position,

                            "total_pages":
                                total_pages,

                            "status":
                                "error",

                            "error":
                                client_error_message(
                                    error
                                ),
                        }
                    )

                raise

            corrected = safe_text(
                corrected
            )

            # ------------------------------------------------
            # Safety check:
            # if the intelligence layer somehow returns empty
            # content, never destroy the original page.
            # ------------------------------------------------

            if not corrected:

                corrected = original_content

            corrected_page = {
                "type":
                    "corrected_page",

                "page_number":
                    page_number,

                "position":
                    position,

                "total_pages":
                    total_pages,

                "content":
                    corrected,

                "status":
                    "corrected",
            }

            corrected_pages.append(
                corrected_page
            )

            if progress_callback:

                progress_callback(
                    corrected_page
                )

        # ----------------------------------------------------
        # Reassemble the CURRENT corrected document.
        # ----------------------------------------------------

        document_text = (
            self.assemble_document(
                corrected_pages
            )
        )

        complete_result = {
            "type":
                "correction_completed",

            "status":
                "completed",

            "total_pages":
                total_pages,

            "pages":
                corrected_pages,

            "document_text":
                document_text,
        }

        if progress_callback:

            progress_callback(
                complete_result
            )

        return {
            "pages":
                corrected_pages,

            "total_pages":
                total_pages,

            "document_text":
                document_text,

            "status":
                "completed",
        }

    # ========================================================
    # DOCUMENT ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_document(
        pages: list[
            dict[str, Any]
        ],
    ) -> str:
        """
        Reassemble the complete document.

        No page is summarized here.
        No page is discarded here.
        """

        ordered = sorted(
            pages,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts = []

        for page in ordered:

            content = safe_text(
                page.get(
                    "content"
                )
            )

            if content:
                parts.append(
                    content
                )

        return "\n\n".join(
            parts
        ).strip()

    # ========================================================
    # NORMAL CHAT
    # ========================================================

    def respond(
        self,
        message: str,
        service: str | None = None,
        event: str | None = None,
        context: str | None = None,
    ) -> str:
        """
        Normal conversational intelligence.

        Used outside the page-by-page document review operation.
        """

        message = safe_text(
            message
        )

        if not message:

            return (
                "Please tell me what you "
                "would like me to help you with."
            )

        if service:
            self.set_service(
                service
            )

        active_service = (
            self.normalize_service(
                self.service
            )
        )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        messages = [
            {
                "role":
                    "system",
                "content":
                    system_prompt,
            }
        ]

        for item in self.history[
            -MAX_HISTORY_MESSAGES:
        ]:

            messages.append(
                {
                    "role":
                        item["role"],

                    "content":
                        compact_text(
                            item["content"],
                            MAX_HISTORY_MESSAGE_CHARS,
                        ),
                }
            )

        if event:

            messages.append(
                {
                    "role":
                        "system",

                    "content":
                        (
                            "CURRENT APPLICATION EVENT\n"
                            + safe_text(
                                event
                            )
                        ),
                }
            )

        messages.append(
            {
                "role":
                    "user",

                "content":
                    compact_text(
                        message,
                        MAX_USER_MESSAGE_CHARS,
                    ),
            }
        )

        response = self.call_groq(
            messages=messages,
            output_tokens=NORMAL_OUTPUT_TOKENS,
            stage="NORMAL_RESPONSE",
            event=event,
        )

        self.add_history(
            "user",
            message,
        )

        self.add_history(
            "assistant",
            response,
        )

        return response

    # ========================================================
    # EVENT HELPERS
    # ========================================================

    @staticmethod
    def is_review_event(
        event: str | None,
    ) -> bool:

        return (
            safe_text(
                event
            ).lower()
            in REVIEW_EVENTS
        )

    @staticmethod
    def is_correction_event(
        event: str | None,
    ) -> bool:

        return (
            safe_text(
                event
            ).lower()
            in CORRECTION_EVENTS
        )


# ============================================================
# DIAGNOSTIC
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA RESPONSE INTELLIGENCE ENGINE")
    print("=" * 78)

    print(
        "Model:",
        MODEL,
    )

    print(
        "Groq package:",
        "READY"
        if Groq is not None
        else "MISSING",
    )

    print(
        "API key:",
        "CONFIGURED"
        if API_KEY
        else "MISSING",
    )

    print(
        "Page-by-page review:",
        "ENABLED",
    )

    print(
        "Long-page chunking:",
        "ENABLED",
    )

    print(
        "Progressive review cards:",
        "ENABLED",
    )

    print(
        "Review continuity:",
        "ENABLED",
    )

    print(
        "Complete document preservation:",
        "ENABLED",
    )

    print(
        "Correction intelligence:",
        "ENABLED",
    )

    print(
        "Keyword intelligence:",
        "DISABLED",
    )

    print(
        "Permanent document ownership:",
        "APPLICATION",
    )

    print("=" * 78)
