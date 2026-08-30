"""
Naija Pocket Business Center
INTELLIGENCE-FIRST DOCUMENT ENGINE

TOKEN CONTROLLED COMPLETE DOCUMENT ENGINE
==========================================

CORE RULES
----------

1. A customer's requested work is ONE document.
2. Multi-page documents remain fully supported.
3. Generation happens before pagination.
4. Pagination NEVER calls Groq.
5. Review NEVER calls Groq once per page.
6. Review uses ONE intelligence request for the document.
7. Corrections operate on the CURRENT complete document.
8. Corrections return the COMPLETE corrected document.
9. Document workflows do not make an unnecessary preliminary
   conversational Groq request.
10. Large repeated prompts are aggressively controlled.

TOKEN DESIGN
------------

The previous implementation could repeatedly send:

    large system prompt
    + customer request
    + supplied material
    + previous document
    + continuation request

That becomes expensive when continuation occurs.

This version therefore:

- keeps system prompts compact
- keeps ordinary chat prompts compact
- avoids duplicated document material
- sends only a document tail for continuation
- uses one review request
- keeps pagination completely local
- does not use page count as a Groq-call multiplier

IMPORTANT
---------

This file does NOT change the document ownership model.

The application remains responsible for storing and displaying
the complete document and its resulting pages.
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

# Lower-token default model.
#
# Can still be overridden:
#
# GROQ_MODEL=openai/gpt-oss-20b
#
DEFAULT_MODEL = "llama-3.1-8b-instant"

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
# TOKEN / CONTEXT CONTROL
# ============================================================

# IMPORTANT:
# Keep the system prompt small.
#
# The old implementation allowed a 12,000 character system
# prompt before every request. That alone can consume thousands
# of input tokens repeatedly.

MAX_SYSTEM_PROMPT_CHARS = 6500

MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_MESSAGE_CHARS = 900

MAX_USER_MESSAGE_CHARS = 4500
MAX_CONTEXT_CHARS = 2200

# Do not use page count as an intelligence limit.
MAX_DOCUMENT_PAGES = 1000


# ============================================================
# GENERATION
# ============================================================

# Keep the request itself compact.
GENERATION_REQUEST_CHARS = 8500

# A single generation can produce substantially more than the
# previous 3000-token allocation.
#
# This reduces continuation frequency for normal documents.
GENERATION_OUTPUT_TOKENS = 5000

# Long documents remain supported.
#
# This is NOT a page limit.
# It is only a protection against an accidentally endless model
# continuation loop.
MAX_GENERATION_PARTS = 20

END_OF_DOCUMENT_MARKER = "[END OF DOCUMENT]"
CONTINUE_MARKER = "[CONTINUE]"

# Only the tail is needed to continue a document.
CONTINUATION_TAIL_CHARS = 3000


# ============================================================
# REVIEW
# ============================================================

# Review remains one request.
REVIEW_REQUEST_CHARS = 12000
REVIEW_OUTPUT_TOKENS = 700


# ============================================================
# CORRECTION
# ============================================================

CORRECTION_REQUEST_CHARS = 10500
CORRECTION_OUTPUT_TOKENS = 4500


# ============================================================
# PAGE CONSTRUCTION
# ============================================================

# Local structural pagination only.
#
# This does NOT mean "maximum document length".
# It is simply the preferred visual page chunk size.
DEFAULT_PAGE_CHARS = 7000


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

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def compact_text(
    value: Any,
    maximum: int,
) -> str:

    text = safe_text(value)

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

    last = available - first

    return (
        text[:first]
        + marker
        + text[-last:]
    )


def split_for_intelligence(
    text: str,
    maximum: int,
) -> list[str]:

    text = safe_text(text)

    if not text:
        return []

    if len(text) <= maximum:
        return [text]

    parts: list[str] = []

    start = 0
    length = len(text)

    while start < length:

        if length - start <= maximum:

            part = text[start:].strip()

            if part:
                parts.append(part)

            break

        end = start + maximum
        window = text[start:end]

        positions = [
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("? "),
            window.rfind("! "),
            window.rfind(" "),
        ]

        usable = [
            position
            for position in positions
            if position >= int(
                maximum * 0.55
            )
        ]

        if usable:
            boundary = max(usable)
        else:
            boundary = maximum

        part = text[
            start:start + boundary
        ].strip()

        if part:
            parts.append(part)

        next_start = start + boundary

        if next_start <= start:
            next_start = end

        start = next_start

    return parts


# ============================================================
# GENERATION MARKERS
# ============================================================

def contains_end_marker(
    text: str,
) -> bool:

    return (
        END_OF_DOCUMENT_MARKER.lower()
        in safe_text(text).lower()
    )


def contains_continue_marker(
    text: str,
) -> bool:

    return (
        CONTINUE_MARKER.lower()
        in safe_text(text).lower()
    )


def remove_generation_markers(
    text: str,
) -> str:

    cleaned = safe_text(text)

    if not cleaned:
        return ""

    cleaned = re.sub(
        re.escape(END_OF_DOCUMENT_MARKER),
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        re.escape(CONTINUE_MARKER),
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    return cleaned.strip()


# ============================================================
# ERROR CLASSIFICATION
# ============================================================

def classify_error(
    error: Exception,
) -> str:

    status = getattr(
        error,
        "status_code",
        None,
    )

    message = safe_text(
        error
    ).lower()

    if status == 429:
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

    if status and status >= 500:
        return "GROQ_SERVER_ERROR"

    if "timeout" in message:
        return "NETWORK"

    if "connection" in message:
        return "NETWORK"

    return "GROQ_REQUEST_ERROR"


def log_error(
    title: str,
    error: Exception,
    *,
    stage: str,
):

    print()
    print("=" * 78)
    print("ADA ERROR:", title)
    print("=" * 78)

    print("Stage:", stage)
    print("Category:", classify_error(error))
    print("Model:", MODEL)
    print("API key configured:", bool(API_KEY))

    print(
        "Error:",
        compact_text(
            error,
            1000,
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

    return (
        "Technical error detected.\n\n"
        f"Category: {classify_error(error)}\n"
        f"Error: {compact_text(error, 800)}"
    )


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
            )

            return (
                "OFFICIAL BILLING FACTS\n"
                "Billing information unavailable.\n"
                "Do not invent pricing."
            )

        if not item:

            return (
                "OFFICIAL BILLING FACTS\n"
                "No billing record found.\n"
                "Do not invent pricing."
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
            f"{pricing}"
        )


    # ========================================================
    # COMPACT INTELLIGENCE RULES
    # ========================================================

    def intelligence_rules(self) -> str:

        return """
You are the intelligent customer-facing assistant
of Naija Pocket Business Center.

Understand meaning, not keywords.

The selected service is context only.
It does not force a scripted conversation.

Ask only for information genuinely needed.

DOCUMENTS
---------
When enough information exists, produce the requested work.

Do not substitute:
- a plan for a requested document
- a summary for requested work
- an introduction for a complete document

Long documents are ONE document even when internally continued.

Never invent customer-specific facts.

DOCUMENT PRESERVATION
---------------------
The complete document is the source of truth.

Never replace it with a summary, excerpt, review, page preview,
or explanation.

REVIEW
------
Review the complete document as one document.
Do not rewrite it.
Do not invent problems.

CORRECTION
----------
Use the CURRENT complete document.
Apply the customer's requested correction.
Preserve unrelated useful content.
Return the COMPLETE corrected document.

COMMUNICATION
-------------
Be natural, clear and warm.
Use Nigerian English naturally where appropriate.
Never expose internal architecture or token mechanics.
"""


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

        parts: list[str] = []

        # Keep prompt-manager contribution controlled.
        try:

            prompt = (
                self.prompt_manager
                .build_prompt(
                    service=active_service
                )
            )

            if prompt:

                parts.append(
                    compact_text(
                        prompt,
                        3000,
                    )
                )

        except Exception as error:

            log_error(
                "PROMPT MANAGER FAILED",
                error,
                stage="PROMPT_MANAGER",
            )

        parts.append(
            self.intelligence_rules()
        )

        if active_service:

            parts.append(
                "SERVICE: "
                + active_service
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
                "APPLICATION STATE:\n"
                + compact_text(
                    context,
                    MAX_CONTEXT_CHARS,
                )
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

        role = safe_text(role)
        content = safe_text(content)

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
    # GROQ CALL
    # ========================================================

    def call_groq(
        self,
        *,
        messages: list[dict[str, str]],
        output_tokens: int,
        stage: str,
        event: str | None = None,
    ) -> str:

        client = get_client()

        print()
        print("=" * 78)
        print("INTELLIGENCE REQUEST")
        print("=" * 78)

        print("Stage:", stage)
        print("Model:", MODEL)
        print("Messages:", len(messages))
        print("Output token allowance:", output_tokens)

        if event:
            print("Event:", event)

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
                    max_completion_tokens=output_tokens,
                )
            )

        except Exception as error:

            log_error(
                "INTELLIGENCE REQUEST FAILED",
                error,
                stage=stage,
            )

            raise AdaResponseError(
                str(error),
                stage=stage,
                category=classify_error(error),
                status_code=getattr(
                    error,
                    "status_code",
                    None,
                ),
                original=error,
            ) from error

        if not response:
            raise AdaResponseError(
                "No intelligence response returned.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        if not response.choices:
            raise AdaResponseError(
                "No intelligence choice returned.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        content = safe_text(
            response
            .choices[0]
            .message
            .content
        )

        if not content:

            raise AdaResponseError(
                "Intelligence returned empty content.",
                stage=stage,
                category="EMPTY_RESPONSE",
            )

        print(
            "Intelligence response received."
        )

        return content


    # ========================================================
    # GENERATION PROMPT
    # ========================================================

    def build_generation_prompt(
        self,
        *,
        service: str,
        customer_request: str,
        supplied_material: str = "",
        previous_tail: str = "",
        continuation: bool = False,
    ) -> str:

        if continuation:

            instruction = f"""
CONTINUE THE SAME DOCUMENT.

Do not restart.
Do not repeat previous sections.
Continue naturally from the supplied ending.

DOCUMENT ENDING:

{compact_text(
    previous_tail,
    CONTINUATION_TAIL_CHARS,
)}

If the document is now complete, end with:

{END_OF_DOCUMENT_MARKER}

If more document content is genuinely required, end with:

{CONTINUE_MARKER}

Return only document content and the marker.
"""

        else:

            instruction = f"""
CREATE THE REQUESTED DOCUMENT.

Produce the actual work, not a plan or explanation.

If the document is complete, end with:

{END_OF_DOCUMENT_MARKER}

If the document genuinely needs continuation, end with:

{CONTINUE_MARKER}

Return only document content and the marker.
"""

        return (
            "CREATE DOCUMENT\n\n"

            "SERVICE:\n"
            f"{self.active_service_for_prompt(service)}\n\n"

            "CUSTOMER REQUEST:\n"
            f"{compact_text(customer_request, 4000)}\n\n"

            "SUPPLIED MATERIAL:\n"
            f"{compact_text(supplied_material, 2500)}\n\n"

            + instruction
        )


    @staticmethod
    def active_service_for_prompt(
        service: str | None,
    ) -> str:

        return (
            safe_text(service)
            or "General Business Center Service"
        )


    # ========================================================
    # COMPLETE DOCUMENT GENERATION
    # ========================================================

    def generate_document(
        self,
        *,
        service: str | None,
        customer_request: str,
        supplied_material: str = "",
        context: str | None = None,
        progress_callback: Callable[
            [dict[str, Any]],
            None
        ] | None = None,
    ) -> dict[str, Any]:

        active_service = (
            self.normalize_service(
                service
            )
            or safe_text(service)
            or self.service
            or "General Business Center Service"
        )

        customer_request = safe_text(
            customer_request
        )

        supplied_material = safe_text(
            supplied_material
        )

        if not customer_request:

            raise AdaResponseError(
                "No customer request was supplied.",
                stage="GENERATION_INTAKE",
                category="EMPTY_REQUEST",
            )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        document_parts: list[str] = []

        completed = False

        for part_number in range(
            1,
            MAX_GENERATION_PARTS + 1,
        ):

            # IMPORTANT:
            #
            # We DO NOT resend the complete accumulated document.
            #
            # Only its ending is supplied for continuation.
            #
            # This is one of the major token-saving changes.

            current_document = (
                "\n\n".join(
                    document_parts
                ).strip()
            )

            previous_tail = (
                current_document[
                    -CONTINUATION_TAIL_CHARS:
                ]
                if current_document
                else ""
            )

            prompt = (
                self.build_generation_prompt(
                    service=active_service,
                    customer_request=customer_request,
                    supplied_material=supplied_material,
                    previous_tail=previous_tail,
                    continuation=bool(
                        document_parts
                    ),
                )
            )

            generated = self.call_groq(
                messages=[
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
                            compact_text(
                                prompt,
                                GENERATION_REQUEST_CHARS,
                            ),
                    },
                ],
                output_tokens=GENERATION_OUTPUT_TOKENS,
                stage="DOCUMENT_GENERATION",
            )

            generated = safe_text(
                generated
            )

            if not generated:

                raise AdaResponseError(
                    "Generation returned empty content.",
                    stage="DOCUMENT_GENERATION",
                    category="EMPTY_GENERATION_PART",
                )

            model_declared_complete = (
                contains_end_marker(
                    generated
                )
            )

            has_continue = (
                contains_continue_marker(
                    generated
                )
            )

            clean_generated = (
                remove_generation_markers(
                    generated
                )
            )

            if clean_generated:

                document_parts.append(
                    clean_generated
                )

            current_document = (
                "\n\n".join(
                    document_parts
                ).strip()
            )

            print(
                "[GEN]"
                f" part={part_number}"
                f" response_chars={len(generated)}"
                f" document_chars={len(current_document)}"
                f" complete={model_declared_complete}"
            )

            if progress_callback:

                progress_callback(
                    {
                        "type":
                            "generation_progress",

                        "part":
                            part_number,

                        "status":
                            (
                                "completed"
                                if model_declared_complete
                                else "continuing"
                            ),

                        "content_length":
                            len(current_document),

                        "internal":
                            True,
                    }
                )

            if model_declared_complete:

                completed = True
                break

            # A model that omits both markers is not trusted to
            # have completed the document.
            #
            # Continue the SAME document rather than creating
            # another document.

            if not has_continue:

                print(
                    "[GEN] No END marker."
                    " Continuing same document."
                )

        if not completed:

            raise AdaResponseError(
                (
                    "Document generation reached the internal "
                    "continuation limit before receiving "
                    "[END OF DOCUMENT]."
                ),
                stage="DOCUMENT_GENERATION",
                category="GENERATION_LIMIT",
            )

        document_text = (
            "\n\n".join(
                document_parts
            ).strip()
        )

        if not document_text:

            raise AdaResponseError(
                "No document content was generated.",
                stage="DOCUMENT_GENERATION",
                category="EMPTY_DOCUMENT",
            )

        # ====================================================
        # PAGINATION
        #
        # LOCAL ONLY.
        #
        # ZERO GROQ CALLS.
        # ====================================================

        pages = (
            self.document_to_pages(
                document_text
            )
        )

        print(
            "[PAG]"
            f" document_chars={len(document_text)}"
            f" pages={len(pages)}"
        )

        if not pages:

            raise AdaResponseError(
                "Document was generated but no pages could be created.",
                stage="DOCUMENT_PAGINATION",
                category="EMPTY_PAGE_COLLECTION",
            )

        if len(pages) > MAX_DOCUMENT_PAGES:

            raise AdaResponseError(
                "Document exceeded the maximum supported page count.",
                stage="DOCUMENT_PAGINATION",
                category="PAGE_LIMIT",
            )

        result = {
            "type":
                "document_completed",

            "status":
                "completed",

            "service":
                active_service,

            "document_text":
                document_text,

            "pages":
                pages,

            "total_pages":
                len(pages),
        }

        if progress_callback:
            progress_callback(result)

        return result


    # ========================================================
    # DOCUMENT -> PAGES
    # ========================================================

    @staticmethod
    def document_to_pages(
        document_text: str,
    ) -> list[dict[str, Any]]:

        document_text = safe_text(
            document_text
        )

        if not document_text:
            return []

        # ----------------------------------------------------
        # EXPLICIT PAGE MARKERS
        # ----------------------------------------------------

        pattern = re.compile(
            r"(?:^|\n)"
            r"(?:={2,}\s*)?"
            r"PAGE\s+(\d+)"
            r"(?:\s*={2,})?"
            r"\s*(?:\n|$)",
            re.IGNORECASE,
        )

        matches = list(
            pattern.finditer(
                document_text
            )
        )

        if matches:

            pages: list[
                dict[str, Any]
            ] = []

            for index, match in enumerate(
                matches
            ):

                start = match.end()

                if index + 1 < len(matches):

                    end = matches[
                        index + 1
                    ].start()

                else:

                    end = len(
                        document_text
                    )

                content = (
                    document_text[
                        start:end
                    ].strip()
                )

                if content:

                    try:
                        page_number = int(
                            match.group(1)
                        )
                    except Exception:
                        page_number = (
                            len(pages) + 1
                        )

                    pages.append(
                        {
                            "page_number":
                                page_number,

                            "content":
                                content,

                            "status":
                                "ready",
                        }
                    )

            if pages:
                return pages

        # ----------------------------------------------------
        # STRUCTURAL PAGINATION
        #
        # STILL NO GROQ.
        # ----------------------------------------------------

        parts = split_for_intelligence(
            document_text,
            DEFAULT_PAGE_CHARS,
        )

        pages = []

        for index, part in enumerate(
            parts,
            start=1,
        ):

            if not part:
                continue

            pages.append(
                {
                    "page_number":
                        index,

                    "content":
                        part,

                    "status":
                        "ready",
                }
            )

        return pages


    # ========================================================
    # NORMALIZE EXISTING PAGES
    # ========================================================

    @staticmethod
    def normalize_document_pages(
        pages: Any,
    ) -> list[dict[str, Any]]:

        if pages is None:
            return []

        if isinstance(
            pages,
            str,
        ):

            return AdaResponse.document_to_pages(
                pages
            )

        if not isinstance(
            pages,
            list,
        ):

            pages = [pages]

        result: list[
            dict[str, Any]
        ] = []

        for index, item in enumerate(
            pages,
            start=1,
        ):

            if isinstance(
                item,
                str,
            ):

                content = safe_text(
                    item
                )

                if content:

                    result.append(
                        {
                            "page_number":
                                index,

                            "content":
                                content,

                            "status":
                                "ready",
                        }
                    )

                continue

            if isinstance(
                item,
                dict,
            ):

                content = safe_text(
                    item.get("content")
                    or item.get("text")
                    or item.get("page_content")
                    or item.get("document_text")
                )

                if not content:
                    continue

                try:

                    page_number = int(
                        item.get(
                            "page_number",
                            index,
                        )
                    )

                except Exception:

                    page_number = index

                status = safe_text(
                    item.get(
                        "status",
                        "ready",
                    )
                )

                if not status:
                    status = "ready"

                result.append(
                    {
                        "page_number":
                            page_number,

                        "content":
                            content,

                        "status":
                            status,
                    }
                )

        return sorted(
            result,
            key=lambda item: int(
                item["page_number"]
            ),
        )


    # ========================================================
    # DOCUMENT ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_document(
        pages: list[
            dict[str, Any]
        ],
    ) -> str:

        if not pages:
            return ""

        ordered = sorted(
            pages,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts: list[str] = []

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
    # COMPLETE DOCUMENT REVIEW
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

        normalized = (
            self.normalize_document_pages(
                pages
            )
        )

        if not normalized:

            raise AdaResponseError(
                "No complete document was supplied for review.",
                stage="REVIEW_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        total_pages = len(
            normalized
        )

        if total_pages > MAX_DOCUMENT_PAGES:

            raise AdaResponseError(
                "Document exceeds the maximum supported page count.",
                stage="REVIEW_INTAKE",
                category="PAGE_LIMIT",
            )

        complete_document = (
            self.assemble_document(
                normalized
            )
        )

        if not complete_document:

            raise AdaResponseError(
                "The supplied pages contain no document content.",
                stage="REVIEW_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        system_prompt = (
            self.build_system_prompt(
                service=service,
                context=context,
            )
        )

        # ----------------------------------------------------
        # ONE REVIEW REQUEST.
        #
        # The UI may show many pages.
        # That does NOT create many Groq requests.
        # ----------------------------------------------------

        review_prompt = (
            "REVIEW THE COMPLETE DOCUMENT.\n\n"

            "SERVICE:\n"
            f"{safe_text(service)}\n\n"

            "CUSTOMER REQUEST:\n"
            f"{compact_text(customer_request, 3000)}\n\n"

            "TOTAL DISPLAY PAGES:\n"
            f"{total_pages}\n\n"

            "COMPLETE DOCUMENT:\n"
            f"{compact_text(complete_document, 8500)}\n\n"

            "TASK:\n"
            "Check the document as ONE complete work for genuine "
            "correctness, completeness, relevance, grammar, clarity, "
            "consistency, structure and compliance.\n\n"

            "Do not rewrite the document.\n"
            "Do not reproduce the document.\n"
            "Do not invent problems.\n\n"

            "OUTPUT:\n"
            "Use PAGE N: finding when a problem belongs to a page.\n"
            "Use DOCUMENT: finding for document-wide problems.\n"
            "If there are no genuine problems, return exactly:\n"
            "NO ISSUES FOUND"
        )

        if progress_callback:

            progress_callback(
                {
                    "type":
                        "review_started",

                    "total_pages":
                        total_pages,

                    "status":
                        "processing",
                }
            )

        review = self.call_groq(
            messages=[
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
                        compact_text(
                            review_prompt,
                            REVIEW_REQUEST_CHARS,
                        ),
                },
            ],
            output_tokens=REVIEW_OUTPUT_TOKENS,
            stage="DOCUMENT_REVIEW",
            event=event,
        )

        review = safe_text(
            review
        )

        page_reviews = (
            self._parse_review_by_page(
                review,
                total_pages,
            )
        )

        page_results: list[
            dict[str, Any]
        ] = []

        for position, page in enumerate(
            normalized,
            start=1,
        ):

            page_number = page[
                "page_number"
            ]

            findings = (
                page_reviews.get(
                    page_number,
                    "",
                )
            )

            if not findings:

                findings = (
                    "No page-specific issues identified."
                )

            card = {
                "type":
                    "review_card",

                "page_number":
                    page_number,

                "position":
                    position,

                "total_pages":
                    total_pages,

                "content":
                    page["content"],

                "review":
                    findings,

                "status":
                    "reviewed",
            }

            page_results.append(
                card
            )

            if progress_callback:
                progress_callback(
                    card
                )

        assembled_review = (
            self.assemble_review(
                page_results,
                document_review=review,
            )
        )

        result = {
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

            "document":
                complete_document,

            "review_calls":
                1,
        }

        if progress_callback:
            progress_callback(
                result
            )

        return result


    # ========================================================
    # REVIEW PARSER
    # ========================================================

    @staticmethod
    def _parse_review_by_page(
        review: str,
        total_pages: int,
    ) -> dict[int, str]:

        review = safe_text(
            review
        )

        results: dict[
            int,
            str
        ] = {}

        if not review:
            return results

        if (
            review.upper()
            == "NO ISSUES FOUND"
        ):
            return results

        pattern = re.compile(
            r"(?im)"
            r"^\s*PAGE\s+(\d+)\s*:\s*"
            r"(.*?)(?="
            r"^\s*PAGE\s+\d+\s*:|"
            r"^\s*DOCUMENT\s*:|"
            r"\Z"
            r")",
            re.MULTILINE
            | re.DOTALL,
        )

        for match in pattern.finditer(
            review
        ):

            try:

                page_number = int(
                    match.group(1)
                )

            except Exception:
                continue

            if (
                page_number < 1
                or page_number > total_pages
            ):
                continue

            finding = safe_text(
                match.group(2)
            )

            if finding:

                results[
                    page_number
                ] = finding

        document_pattern = re.compile(
            r"(?is)"
            r"(?:^|\n)\s*DOCUMENT\s*:\s*"
            r"(.*)$"
        )

        document_match = (
            document_pattern.search(
                review
            )
        )

        if document_match:

            document_finding = safe_text(
                document_match.group(1)
            )

            if document_finding:

                for page_number in range(
                    1,
                    total_pages + 1,
                ):

                    existing = results.get(
                        page_number
                    )

                    if existing:

                        results[
                            page_number
                        ] = (
                            existing
                            + "\n\n"
                            + "Document-wide:\n"
                            + document_finding
                        )

                    else:

                        results[
                            page_number
                        ] = (
                            "Document-wide:\n"
                            + document_finding
                        )

        return results


    # ========================================================
    # REVIEW ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_review(
        pages: list[
            dict[str, Any]
        ],
        document_review: str = "",
    ) -> str:

        document_review = safe_text(
            document_review
        )

        if document_review:

            return (
                "COMPLETE DOCUMENT REVIEW\n\n"
                + document_review
            )

        ordered = sorted(
            pages,
            key=lambda item: int(
                item.get(
                    "page_number",
                    0,
                )
            ),
        )

        parts: list[str] = []

        for page in ordered:

            review = safe_text(
                page.get(
                    "review"
                )
            )

            if not review:
                continue

            parts.append(
                "PAGE "
                + str(
                    page.get(
                        "page_number"
                    )
                )
                + "\n\n"
                + review
            )

        if not parts:

            return (
                "The complete document was reviewed "
                "and no review findings were returned."
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

        pages = (
            self.normalize_document_pages(
                document_pages
            )
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

        current_document = (
            self.assemble_document(
                pages
            )
        )

        if not current_document:

            raise AdaResponseError(
                "The current document contains no usable content.",
                stage="CORRECTION_INTAKE",
                category="EMPTY_DOCUMENT",
            )

        active_service = (
            self.normalize_service(
                service
            )
            or safe_text(service)
            or self.service
            or "General Business Center Service"
        )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        prompt = (
            "CORRECT THE CURRENT COMPLETE DOCUMENT.\n\n"

            "SERVICE:\n"
            f"{active_service}\n\n"

            "CUSTOMER CORRECTION:\n"
            f"{compact_text(correction, 4500)}\n\n"

            "CURRENT COMPLETE DOCUMENT:\n"
            f"{compact_text(current_document, 7000)}\n\n"

            "Return the COMPLETE corrected document.\n\n"

            "Do not return only the changed section.\n"
            "Do not return a summary.\n"
            "Do not explain the correction.\n"
            "Do not remove unrelated useful content.\n"
            "Do not revert to an older version.\n"
            "Do not invent customer facts."
        )

        corrected = self.call_groq(
            messages=[
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
                        compact_text(
                            prompt,
                            CORRECTION_REQUEST_CHARS,
                        ),
                },
            ],
            output_tokens=CORRECTION_OUTPUT_TOKENS,
            stage="DOCUMENT_CORRECTION",
            event="document_correction",
        )

        corrected = safe_text(
            corrected
        )

        if not corrected:
            corrected = current_document

        corrected = remove_generation_markers(
            corrected
        )

        corrected_pages = (
            self.document_to_pages(
                corrected
            )
        )

        if not corrected_pages:

            raise AdaResponseError(
                "Corrected document could not be paginated.",
                stage="DOCUMENT_CORRECTION",
                category="EMPTY_CORRECTED_DOCUMENT",
            )

        result = {
            "type":
                "correction_completed",

            "status":
                "completed",

            "total_pages":
                len(corrected_pages),

            "pages":
                corrected_pages,

            "document_text":
                corrected,
        }

        if progress_callback:
            progress_callback(
                result
            )

        return result


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

        messages: list[
            dict[str, str]
        ] = [
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
                            "APPLICATION EVENT: "
                            + safe_text(event)
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
            output_tokens=450,
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
    print("TOKEN CONTROLLED DOCUMENT ENGINE")
    print("=" * 78)

    print("Model:", MODEL)

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
        "Complete document generation:",
        "ENABLED",
    )

    print(
        "Multi-page documents:",
        "ENABLED",
    )

    print(
        "Internal continuation:",
        "ENABLED",
    )

    print(
        "Complete document before pagination:",
        "ENABLED",
    )

    print(
        "Pagination Groq calls:",
        "0",
    )

    print(
        "Review Groq calls per document:",
        "1",
    )

    print(
        "Page-by-page review calls:",
        "DISABLED",
    )

    print(
        "Whole-document correction:",
        "ENABLED",
    )

    print(
        "Complete-document preservation:",
        "ENABLED",
    )

    print(
        "Repeated full-document continuation prompt:",
        "DISABLED",
    )

    print(
        "Large system prompt:",
        "REDUCED",
    )

    print(
        "Generation output tokens:",
        GENERATION_OUTPUT_TOKENS,
    )

    print(
        "Review output tokens:",
        REVIEW_OUTPUT_TOKENS,
    )

    print(
        "Correction output tokens:",
        CORRECTION_OUTPUT_TOKENS,
    )

    print(
        "Global page-count requirement:",
        "DISABLED",
    )

    print(
        "Keyword-only service logic:",
        "DISABLED",
    )

    print("=" * 78)
