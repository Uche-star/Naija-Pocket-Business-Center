"""
Naija Pocket Business Center
ADA RESPONSE ENGINE

END-TO-END REVIEW INTELLIGENCE

IMPORTANT ARCHITECTURE
----------------------

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

AdaResponse is the intelligence layer.

The application owns the complete customer document.

AdaResponse does NOT own or discard the document.

For review:

    complete document
        ↓
    page 1
        ↓
    Groq
        ↓
    review result
        ↓
    page 2
        ↓
    Groq
        ↓
    review result
        ↓
    ...
        ↓
    assembled review

The Groq request is limited.

The customer's document is NOT limited.

No page is silently discarded.

No page is replaced by a summary.

No keyword decision workflow is used.
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
    os.getenv("ADA_EXPOSE_ERRORS", "false")
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

MAX_SYSTEM_PROMPT_CHARS = 16000
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_MESSAGE_CHARS = 2000

MAX_USER_MESSAGE_CHARS = 5000
MAX_CONTEXT_CHARS = 6000

# The actual document is handled separately.
# This limit controls ONE Groq review request.
REVIEW_PAGE_INPUT_CHARS = 8000

# Previous review information carried into the next page.
REVIEW_CONTINUITY_CHARS = 2500

REVIEW_OUTPUT_TOKENS = 700

# Maximum number of real document pages accepted.
MAX_DOCUMENT_PAGES = 1000

# Generated/corrected page output.
CORRECTION_OUTPUT_TOKENS = 900
CORRECTION_INPUT_CHARS = 8000


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
# PUBLIC
# ============================================================

def get_ada_model() -> str:
    return MODEL


def is_configured() -> bool:

    return (
        Groq is not None
        and bool(API_KEY)
    )


# ============================================================
# TEXT
# ============================================================

def safe_text(value: Any) -> str:

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def compact_text(
    text: Any,
    maximum: int,
) -> str:

    text = safe_text(text)

    if not text:
        return ""

    if len(text) <= maximum:
        return text

    if maximum < 100:
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

    last = (
        available - first
    )

    return (
        text[:first]
        + marker
        + text[-last:]
    )


# ============================================================
# ERROR CLASSIFICATION
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
    event: str | None = None,
):

    print()
    print("=" * 78)
    print("ADA ERROR:", title)
    print("=" * 78)

    print("Stage:", stage)

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
# PAGE NORMALIZATION
# ============================================================

def normalize_document_pages(
    pages: Any,
) -> list[dict[str, Any]]:

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
                }
            )

            continue

        if isinstance(
            item,
            dict,
        ):

            page_number = (
                item.get(
                    "page_number"
                )
                or item.get(
                    "page"
                )
                or index
            )

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
                    "page_number": int(
                        page_number
                    ),
                    "content": content,
                    "title": safe_text(
                        item.get(
                            "title"
                        )
                    ),
                }
            )

    if len(result) > MAX_DOCUMENT_PAGES:
        result = result[
            :MAX_DOCUMENT_PAGES
        ]

    return result


# ============================================================
# FALLBACK DOCUMENT SPLITTER
# ============================================================

def document_text_to_pages(
    document: str,
) -> list[dict[str, Any]]:

    document = safe_text(
        document
    )

    if not document:
        return []

    # First respect explicit page markers.
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
                    }
                )

        if pages:
            return pages

    # If there are no page markers, preserve the complete
    # document as one logical page.
    return [
        {
            "page_number": 1,
            "content": document,
        }
    ]


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:

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
    # BILLING
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
    # CORE INTELLIGENCE
    # ========================================================

    def get_intelligence_prompt(
        self,
    ) -> str:

        return (
            "You are Ada, the intelligent "
            "customer-facing assistant of "
            "Naija Pocket Business Center.\n\n"

            "You are responsible for intelligent "
            "reasoning about the customer's request.\n\n"

            "Do NOT use keyword matching to determine "
            "what the customer means.\n\n"

            "Understand the customer's actual request, "
            "selected service, supplied document, "
            "application state and workflow action.\n\n"

            "REVIEW MODE\n"
            "===========\n"
            "When the application sends document pages "
            "for review, every page belongs to ONE "
            "customer document.\n\n"

            "Review the supplied page intelligently.\n\n"

            "Check:\n"
            "- correctness\n"
            "- completeness\n"
            "- relevance\n"
            "- grammar\n"
            "- clarity\n"
            "- consistency\n"
            "- contradictions\n"
            "- structure\n"
            "- formatting problems visible in supplied text\n"
            "- compliance with the customer's request\n\n"

            "Maintain continuity with earlier pages.\n\n"

            "Do not invent facts.\n\n"

            "Do not rewrite the customer's entire document "
            "during review unless specifically asked.\n\n"

            "Do not treat a page as a separate customer job.\n\n"

            "Do not say that a page is missing unless the "
            "application actually reports that page missing.\n\n"

            "DOCUMENT PRESERVATION\n"
            "=====================\n"
            "The application owns the complete customer "
            "document.\n\n"

            "Never delete document content.\n"
            "Never silently summarize the document.\n"
            "Never replace pages with a summary.\n"
            "Never invent pages.\n\n"

            "The request limit is an LLM request limit, "
            "NOT a customer-document limit.\n\n"

            "CORRECTION MODE\n"
            "===============\n"
            "When the customer asks for a correction, "
            "reason from the current document version "
            "and the customer's correction instruction.\n\n"

            "Do not revert to an earlier version.\n\n"

            "BILLING\n"
            "=======\n"
            "BillingManager is authoritative for pricing.\n"
            "Never invent prices.\n\n"

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
                        10000,
                    )
                )

        except Exception as error:

            log_error(
                "PROMPT MANAGER FAILED",
                error,
                stage="PROMPT_MANAGER",
                category="PROMPT_MANAGER",
            )

        parts.append(
            self.get_intelligence_prompt()
        )

        billing = (
            self.get_billing_context(
                active_service
            )
        )

        if billing:
            parts.append(
                billing
            )

        if context:

            parts.append(
                "CURRENT APPLICATION STATE\n"
                + compact_text(
                    context,
                    MAX_CONTEXT_CHARS,
                )
            )

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
        print(
            "Messages:",
            len(messages),
        )
        print(
            "Output tokens:",
            output_tokens,
        )
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
    # REVIEW PAGE PROMPT
    # ========================================================

    def build_page_review_prompt(
        self,
        *,
        page_number: int,
        total_pages: int,
        page_content: str,
        previous_review: str,
        customer_request: str,
    ) -> str:

        return (
            "CUSTOMER DOCUMENT REVIEW\n\n"

            "This is ONE document.\n"
            f"This is page {page_number} "
            f"of {total_pages}.\n\n"

            "CUSTOMER'S REVIEW REQUEST:\n"
            f"{compact_text(customer_request, 3000)}\n\n"

            "CURRENT DOCUMENT PAGE:\n"
            f"{compact_text(page_content, REVIEW_PAGE_INPUT_CHARS)}\n\n"

            "PREVIOUS REVIEW CONTINUITY:\n"
            f"{compact_text(previous_review, REVIEW_CONTINUITY_CHARS)}\n\n"

            "YOUR TASK\n"
            "=========\n"

            "Intelligently review this page as part of "
            "the complete document.\n\n"

            "Look for genuine issues only.\n\n"

            "Check correctness, completeness, relevance, "
            "grammar, clarity, consistency, contradictions, "
            "structure, visible formatting problems and "
            "compliance with the customer's request.\n\n"

            "If this page is satisfactory, clearly state "
            "that it is satisfactory.\n\n"

            "If there are issues, identify them precisely.\n\n"

            "Do not invent facts.\n"

            "Do not rewrite the whole page unless the "
            "customer explicitly requested rewriting.\n\n"

            "Do not mention token limits, Groq, API calls "
            "or internal processing."
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
        previous_review: str = "",
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

        system_prompt = (
            self.build_system_prompt(
                service=service,
                context=context,
            )
        )

        prompt = (
            self.build_page_review_prompt(
                page_number=page_number,
                total_pages=total_pages,
                page_content=page_content,
                previous_review=previous_review,
                customer_request=customer_request,
            )
        )

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        return self.call_groq(
            messages=messages,
            output_tokens=REVIEW_OUTPUT_TOKENS,
            stage="REVIEW_PAGE",
            page_number=page_number,
            event=event,
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

        previous_review = ""

        for position, page in enumerate(
            normalized_pages,
            start=1,
        ):

            actual_page_number = int(
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

            if progress_callback:

                progress_callback(
                    {
                        "type": "page_started",
                        "page_number":
                            actual_page_number,
                        "position":
                            position,
                        "total_pages":
                            total_pages,
                        "content":
                            content,
                    }
                )

            try:

                review = self.review_page(
                    page_number=(
                        actual_page_number
                    ),
                    total_pages=total_pages,
                    page_content=content,
                    service=service,
                    context=context,
                    customer_request=(
                        customer_request
                    ),
                    previous_review=(
                        previous_review
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
                                actual_page_number,
                            "position":
                                position,
                            "total_pages":
                                total_pages,
                            "error":
                                str(error),
                        }
                    )

                raise

            result = {
                "page_number":
                    actual_page_number,

                "position":
                    position,

                "content":
                    content,

                "review":
                    safe_text(review),

                "status":
                    "reviewed",
            }

            page_results.append(
                result
            )

            previous_review = compact_text(
                review,
                REVIEW_CONTINUITY_CHARS,
            )

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "page_completed",

                        "page_number":
                            actual_page_number,

                        "position":
                            position,

                        "total_pages":
                            total_pages,

                        "content":
                            content,

                        "review":
                            safe_text(review),

                        "status":
                            "reviewed",
                    }
                )

        assembled_review = (
            self.assemble_review(
                page_results
            )
        )

        if progress_callback:

            progress_callback(
                {
                    "type":
                        "review_completed",

                    "total_pages":
                        total_pages,

                    "assembled_review":
                        assembled_review,
                }
            )

        return {
            "pages":
                page_results,

            "total_pages":
                total_pages,

            "assembled_review":
                assembled_review,
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

        parts = []

        for item in page_results:

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
    # CORRECTION
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

        corrected_pages = []

        total_pages = len(
            pages
        )

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

            content = safe_text(
                page.get(
                    "content"
                )
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
                    }
                )

            system_prompt = (
                self.build_system_prompt(
                    service=service,
                    context=context,
                )
            )

            prompt = (
                "CORRECT THE CUSTOMER'S CURRENT "
                "DOCUMENT VERSION.\n\n"

                f"PAGE {page_number} OF "
                f"{total_pages}\n\n"

                "CUSTOMER CORRECTION:\n"
                f"{compact_text(correction, 4000)}\n\n"

                "CURRENT PAGE CONTENT:\n"
                f"{compact_text(content, CORRECTION_INPUT_CHARS)}\n\n"

                "INSTRUCTIONS:\n"
                "- Preserve facts.\n"
                "- Do not invent facts.\n"
                "- Apply the customer's correction.\n"
                "- Do not unnecessarily change unrelated content.\n"
                "- Return the corrected page content.\n"
                "- Do not discuss the correction process.\n"
                "- Do not mention Groq or token limits."
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
                output_tokens=CORRECTION_OUTPUT_TOKENS,
                stage="CORRECTION_PAGE",
                page_number=page_number,
                event="document_correction",
            )

            corrected = safe_text(
                corrected
            )

            corrected_page = {
                "page_number":
                    page_number,

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
                    {
                        "type":
                            "correction_page_completed",
                        "page_number":
                            page_number,
                        "position":
                            position,
                        "total_pages":
                            total_pages,
                        "content":
                            corrected,
                    }
                )

        return {
            "pages":
                corrected_pages,

            "total_pages":
                total_pages,

            "document_text":
                self.assemble_document(
                    corrected_pages
                ),
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
                        "CURRENT APPLICATION EVENT\n"
                        + safe_text(
                            event
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
            output_tokens=700,
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
        "Progressive review state:",
        "SUPPORTED",
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

    print("=" * 78)
