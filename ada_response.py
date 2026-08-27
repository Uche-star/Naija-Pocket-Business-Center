"""
Naija Pocket Business Center
Ada Response Engine

END-TO-END LLM INTELLIGENCE LAYER

CURRENT ARCHITECTURE
--------------------
FastAPI
    ↓
AdaResponse
    ↓
Groq

AdaResponse is the intelligence layer.

AdaResponse does NOT use keyword matching to determine
what the customer wants.

AdaPromptManager remains responsible for:
    - Ada's identity
    - Nigerian context
    - writing style
    - existing service prompts

BillingManager remains authoritative for:
    - service names
    - prices
    - billing types

FastAPI/application state remains authoritative for:
    - customer information
    - selected service
    - uploaded content
    - document state
    - review state
    - approval state
    - payment state
    - delivery state
    - download state


IMPORTANT DOCUMENT / REVIEW ARCHITECTURE
-----------------------------------------
A complete customer document must NEVER be forced into
one LLM response.

A large document is generated in controlled sections.

    CUSTOMER REQUEST
          ↓
    ADA INTELLIGENCE
          ↓
    DOCUMENT SECTION 1
          ↓
    DOCUMENT SECTION 2
          ↓
    DOCUMENT SECTION 3
          ↓
          ...
          ↓
    COMPLETE ASSEMBLED DOCUMENT
          ↓
    REVIEW PAGE


DIAGNOSTIC ARCHITECTURE
-----------------------
Every important failure point is explicitly identified.

The application distinguishes:

    CONFIGURATION
    CLIENT INITIALIZATION
    PROMPT MANAGER
    BILLING
    APPLICATION CONTEXT
    GROQ REQUEST
    GROQ RATE LIMIT
    GROQ AUTHENTICATION
    GROQ BAD REQUEST
    GROQ SERVER ERROR
    DOCUMENT GENERATION
    DOCUMENT ASSEMBLY
    NORMAL RESPONSE

The real exception is written to the server logs.

The API key itself is NEVER printed.

Document content is NEVER printed as part of diagnostics.

TOKEN CONTROL
-------------
Token control applies to each individual LLM request.

It MUST NOT:
    - delete document pages
    - shorten completed pages
    - summarize requested work
    - replace the document with an explanation
    - weaken Ada's intelligence
    - remove important service instructions

The complete generated document is assembled by the
application before it is returned to the Review page.

NORMAL CONVERSATION
-------------------
Normal conversation continues to use a controlled
recent history.

DOCUMENT GENERATION
-------------------
Document-generation events use sequential generation.

Each generation step produces a controlled section.

The next generation step is then asked to continue from
the exact point reached by the previous section.

The final result is assembled in application memory.

This prevents a long document from being cut off by the
output limit of a single Groq request.
"""


from __future__ import annotations

import os
import re
import traceback
from typing import Any


# ============================================================
# GROQ
# ============================================================

try:
    from groq import Groq
except ImportError:
    Groq = None


# ============================================================
# APPLICATION COMPONENTS
# ============================================================

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


# ============================================================
# DIAGNOSTIC CONFIGURATION
# ============================================================

# This controls whether technical errors are included in the
# returned response while debugging.
#
# IMPORTANT:
# Keep this FALSE in normal customer-facing production use.
#
# The server logs ALWAYS contain the detailed diagnostic
# information regardless of this setting.
EXPOSE_ERRORS_TO_CLIENT = (
    os.getenv(
        "ADA_EXPOSE_ERRORS"
    )
    or "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ============================================================
# NORMAL REQUEST LIMITS
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 18000

MAX_CENTRAL_PROMPT_CHARS = 12000

MAX_INTELLIGENCE_PROMPT_CHARS = 9000

MAX_CONTEXT_CHARS = 6000

MAX_HISTORY_MESSAGES = 4

MAX_HISTORY_MESSAGE_CHARS = 1800

MAX_EVENT_CHARS = 1200

MAX_USER_MESSAGE_CHARS = 4000

MAX_OUTPUT_TOKENS = 800


# ============================================================
# DOCUMENT GENERATION LIMITS
# ============================================================

DOCUMENT_SECTION_OUTPUT_TOKENS = 700

DOCUMENT_SECTION_INSTRUCTION_CHARS = 4500

DOCUMENT_RECENT_CONTEXT_CHARS = 2200

DOCUMENT_MAX_SECTIONS = 30

DOCUMENT_MIN_SECTION_CHARS = 250


# ============================================================
# DOCUMENT EVENTS
# ============================================================

DOCUMENT_CREATION_EVENTS = {
    "form_submitted_create_work",
    "create_work",
    "document_create",
    "document_generation",
    "generate_document",
}

DOCUMENT_CORRECTION_EVENTS = {
    "review_correction",
    "document_correction",
    "revise_work",
}

DOCUMENT_EVENTS = (
    DOCUMENT_CREATION_EVENTS
    | DOCUMENT_CORRECTION_EVENTS
)


# ============================================================
# CUSTOM DIAGNOSTIC ERROR
# ============================================================

class AdaResponseError(Exception):
    """
    Internal diagnostic exception.

    This identifies WHERE the failure happened instead of
    allowing every failure to become a generic network message.
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
    Initialize the Groq client.

    Any initialization problem is explicitly diagnosed.
    """

    global _client

    if _client is not None:
        return _client

    if Groq is None:

        raise AdaResponseError(
            "The 'groq' Python package is not installed.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    if not API_KEY:

        raise AdaResponseError(
            "GROQ_API_KEY is missing from the environment.",
            stage="CLIENT_INITIALIZATION",
            category="CONFIGURATION",
        )

    try:

        _client = Groq(
            api_key=API_KEY
        )

    except Exception as error:

        raise AdaResponseError(
            "Groq client initialization failed.",
            stage="CLIENT_INITIALIZATION",
            category="GROQ_CLIENT",
            original=error,
        ) from error

    return _client


# ============================================================
# PUBLIC HELPERS
# ============================================================

def get_ada_model() -> str:
    return MODEL


def is_configured() -> bool:

    if Groq is None:
        return False

    return bool(API_KEY)


# ============================================================
# SAFE DIAGNOSTIC VALUE
# ============================================================

def diagnostic_value(
    value: Any,
    maximum: int = 2000,
) -> str:

    text = safe_text(value)

    if not text:
        return ""

    if len(text) <= maximum:
        return text

    return (
        text[:maximum]
        + "\n...[diagnostic value truncated]"
    )


# ============================================================
# TEXT HELPERS
# ============================================================

def safe_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def compact_text(
    text: str | None,
    maximum: int,
) -> str:
    """
    Compact instructions/context destined for Groq.

    IMPORTANT:

    This function is NEVER used on the assembled customer
    document.

    It is only used on LLM instructions, context, history,
    and other request metadata.
    """

    text = safe_text(text)

    if not text:
        return ""

    if maximum <= 0:
        return ""

    if len(text) <= maximum:
        return text

    if maximum < 300:
        return text[:maximum]

    marker = (
        "\n\n"
        "[REQUEST COMPACTION: non-essential middle "
        "instruction/context omitted from this LLM request.]\n\n"
    )

    available = maximum - len(marker)

    if available <= 0:
        return text[:maximum]

    first_part = int(
        available * 0.65
    )

    last_part = (
        available
        - first_part
    )

    return (
        text[:first_part]
        + marker
        + text[-last_part:]
    )


# ============================================================
# HISTORY
# ============================================================

def compact_history(
    history: list[dict[str, str]],
) -> list[dict[str, str]]:

    recent = history[
        -MAX_HISTORY_MESSAGES:
    ]

    result: list[dict[str, str]] = []

    for item in recent:

        role = safe_text(
            item.get("role")
        )

        content = compact_text(
            item.get("content"),
            MAX_HISTORY_MESSAGE_CHARS,
        )

        if not role or not content:
            continue

        if role not in {
            "user",
            "assistant",
            "system",
        }:
            continue

        result.append(
            {
                "role": role,
                "content": content,
            }
        )

    return result


# ============================================================
# APPLICATION CONTEXT
# ============================================================

def prepare_application_context(
    context: str | None,
) -> str:

    context = safe_text(context)

    if not context:
        return ""

    return compact_text(
        context,
        MAX_CONTEXT_CHARS,
    )


# ============================================================
# ERROR EXTRACTION
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


def get_error_body(
    error: Exception,
) -> str:

    candidates = [
        getattr(error, "body", None),
        getattr(error, "response", None),
    ]

    for candidate in candidates:

        if candidate is None:
            continue

        if isinstance(candidate, str):
            return diagnostic_value(
                candidate
            )

        try:

            return diagnostic_value(
                repr(candidate)
            )

        except Exception:

            pass

    return ""


def get_error_code(
    error: Exception,
) -> str:

    code = getattr(
        error,
        "code",
        None,
    )

    if code is None:
        return ""

    return safe_text(
        code
    )


def get_error_type(
    error: Exception,
) -> str:

    return type(
        error
    ).__name__


def classify_groq_error(
    error: Exception,
) -> str:

    status = get_error_status_code(
        error
    )

    name = get_error_type(
        error
    ).lower()

    message = safe_text(
        error
    ).lower()

    if status == 429:
        return "GROQ_RATE_LIMIT"

    if (
        "rate" in name
        or "ratelimit" in name
        or "rate limit" in message
    ):
        return "GROQ_RATE_LIMIT"

    if status == 401:
        return "GROQ_AUTHENTICATION"

    if "authentication" in name:
        return "GROQ_AUTHENTICATION"

    if status == 403:
        return "GROQ_PERMISSION"

    if status == 404:
        return "GROQ_NOT_FOUND"

    if status == 400:
        return "GROQ_BAD_REQUEST"

    if status == 413:
        return "GROQ_REQUEST_TOO_LARGE"

    if status == 422:
        return "GROQ_UNPROCESSABLE_REQUEST"

    if status is not None and status >= 500:
        return "GROQ_SERVER_ERROR"

    if (
        "connection" in name
        or "timeout" in name
        or "network" in name
    ):
        return "NETWORK"

    return "GROQ_REQUEST_ERROR"


# ============================================================
# ERROR LOGGING
# ============================================================

def log_error(
    title: str,
    error: Exception,
    *,
    stage: str,
    category: str | None = None,
    section_number: int | None = None,
    event: str | None = None,
) -> None:

    status_code = (
        get_error_status_code(
            error
        )
    )

    error_category = (
        category
        or classify_groq_error(error)
    )

    print()
    print("=" * 78)
    print(
        f"ADA DIAGNOSTIC ERROR: {title}"
    )
    print("=" * 78)

    print(
        "Stage:",
        stage,
    )

    print(
        "Category:",
        error_category,
    )

    print(
        "Exception type:",
        get_error_type(error),
    )

    if status_code is not None:

        print(
            "HTTP status:",
            status_code,
        )

    error_code = get_error_code(
        error
    )

    if error_code:

        print(
            "Provider error code:",
            error_code,
        )

    if section_number is not None:

        print(
            "Document section:",
            section_number,
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
        "Error message:",
        diagnostic_value(
            str(error)
        ),
    )

    body = get_error_body(
        error
    )

    if body:

        print(
            "Provider response/body:",
            body,
        )

    print("=" * 78)

    traceback.print_exc()

    print("=" * 78)
    print()


# ============================================================
# CLIENT ERROR RESPONSE
# ============================================================

def client_error_message(
    error: Exception,
) -> str:
    """
    Return a useful diagnostic response when debugging is
    explicitly enabled.

    The server logs always contain the full diagnostic.
    """

    if not EXPOSE_ERRORS_TO_CLIENT:

        return (
            "I could not process your request right now. "
            "Please try again."
        )

    status = get_error_status_code(
        error
    )

    category = classify_groq_error(
        error
    )

    message = diagnostic_value(
        str(error),
        maximum=1200,
    )

    if status is not None:

        return (
            f"Technical error detected.\n\n"
            f"Category: {category}\n"
            f"HTTP status: {status}\n"
            f"Model: {MODEL}\n"
            f"Error: {message}"
        )

    return (
        f"Technical error detected.\n\n"
        f"Category: {category}\n"
        f"Error: {message}"
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

        try:

            self.prompt_manager = (
                AdaPromptManager()
            )

        except Exception as error:

            log_error(
                "PROMPT MANAGER INITIALIZATION FAILED",
                error,
                stage="PROMPT_MANAGER_INITIALIZATION",
                category="PROMPT_MANAGER",
            )

            raise

        try:

            self.billing = (
                BillingManager()
            )

        except Exception as error:

            log_error(
                "BILLING MANAGER INITIALIZATION FAILED",
                error,
                stage="BILLING_INITIALIZATION",
                category="BILLING",
            )

            raise

        self.history: list[
            dict[str, str]
        ] = []

        self.max_history_messages = (
            MAX_HISTORY_MESSAGES
        )

    # ========================================================
    # SERVICE
    # ========================================================

    def set_service(
        self,
        service: str | None,
    ) -> None:

        service = safe_text(service)

        if service:

            self.service = service

    # ========================================================
    # SERVICE NORMALIZATION
    # ========================================================

    def normalize_service(
        self,
        service: str | None,
    ) -> str | None:

        service = safe_text(service)

        if not service:
            return self.service

        try:

            normalized = (
                self.billing.normalize_service(
                    service
                )
            )

            normalized = safe_text(
                normalized
            )

            if normalized:
                return normalized

        except Exception as error:

            log_error(
                "SERVICE NORMALIZATION FAILED",
                error,
                stage="SERVICE_NORMALIZATION",
                category="BILLING",
            )

            raise AdaResponseError(
                "BillingManager service normalization failed.",
                stage="SERVICE_NORMALIZATION",
                category="BILLING",
                original=error,
            ) from error

        return service

    # ========================================================
    # BILLING
    # ========================================================

    def get_billing_context(
        self,
        service: str | None,
    ) -> str:

        service = (
            self.normalize_service(
                service
            )
        )

        if not service:
            return ""

        try:

            item = (
                self.billing.get_service(
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

            raise AdaResponseError(
                "BillingManager lookup failed.",
                stage="BILLING_LOOKUP",
                category="BILLING",
                original=error,
            ) from error

        if not item:

            return (
                "OFFICIAL BILLING FACTS\n"
                "No BillingManager record was found for "
                "this service.\n"
                "Do not invent or estimate a price."
            )

        price = item.get(
            "price",
            0,
        )

        billing_type = item.get(
            "billing"
        )

        try:

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

        except Exception as error:

            log_error(
                "BILLING FORMAT FAILED",
                error,
                stage="BILLING_FORMAT",
                category="BILLING",
            )

            pricing = (
                "Billing information is available "
                "from BillingManager."
            )

        return (
            "OFFICIAL BILLING FACTS\n"
            f"Service: {service}\n"
            f"{pricing}\n"
            "BillingManager is authoritative for price."
        )

    # ========================================================
    # INTELLIGENCE
    # ========================================================

    def get_intelligence_prompt(
        self,
    ) -> str:

        return """
You are Ada, the intelligent customer-facing
assistant of Naija Pocket Business Center.

You are a genuine LLM reasoning assistant.

Do not use keyword matching as the method for deciding
what the customer wants.

Understand the customer's complete request together with
the current application state.

The selected service provides context.
It does not force a scripted conversation.

Use customer information faithfully.

Never invent:
- personal information
- business information
- academic information
- financial information
- document content
- document pages
- prices
- discounts
- payment confirmation
- approval
- delivery
- download availability

The customer may communicate using Nigerian English,
informal English, Pidgin, imperfect English, short
messages, and follow-up corrections.

Understand meaning rather than requiring perfect wording.

APPLICATION STATE
-----------------
The application may provide factual state concerning:

- selected service
- customer information
- form information
- uploaded files
- document content
- document page count
- document pages
- document preparation
- review
- approval
- payment
- delivery
- download

Application state is authoritative.

BILLING
-------
BillingManager is authoritative for prices.

Never invent or estimate a price.

WORKFLOW
--------
The customer's journey can include:

Request
→ Information
→ Preparation
→ Review
→ Approval
→ Payment
→ Delivery
→ Download

This is not a rigid script.

Reason about the current state and determine the
appropriate next action.

If enough information has already been supplied,
do not repeatedly ask for it.

REVIEW
------
Review is a document operation.

The complete customer document may contain multiple
pages or sections.

A document may be larger than one LLM response.

The application may therefore generate the document
in controlled sequential sections.

Every generated section is part of the SAME customer
document.

Do not summarize a document merely because it is long.

Do not intentionally shorten requested work.

Do not replace requested work with an explanation.

Preserve the customer's requested structure,
requirements, tone, facts, and instructions.

DOCUMENT INTEGRITY
------------------
Never invent customer facts.

Never invent a page that the customer did not request.

Never remove requested content merely because a single
LLM response has a size limit.

Never claim that the application deleted content.

PAYMENT
-------
Never claim payment succeeded unless application state
confirms it.

APPROVAL
--------
Never claim approval unless application state confirms it.

DELIVERY
--------
Never claim delivery occurred unless application state
confirms it.

DOWNLOAD
--------
Never invent a download URL.

CUSTOMER RESPONSE
-----------------
Answer directly.

Be warm, clear, practical, professional, and concise.

Never mention:
- Groq
- Gemini
- model names
- API calls
- tokens
- system prompts
- internal architecture
- provider errors
"""


    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    def build_system_prompt(
        self,
        service: str | None = None,
        context: str | None = None,
    ) -> str:

        active_service = (
            self.normalize_service(
                service
            )
        )

        parts: list[str] = []

        # ----------------------------------------------------
        # EXISTING PROMPT MANAGER
        # ----------------------------------------------------

        try:

            central_prompt = (
                self.prompt_manager.build_prompt(
                    service=active_service,
                )
            )

            central_prompt = safe_text(
                central_prompt
            )

            if central_prompt:

                central_prompt = compact_text(
                    central_prompt,
                    MAX_CENTRAL_PROMPT_CHARS,
                )

                parts.append(
                    central_prompt
                )

        except Exception as error:

            log_error(
                "PROMPT MANAGER BUILD FAILED",
                error,
                stage="PROMPT_MANAGER_BUILD",
                category="PROMPT_MANAGER",
            )

            raise AdaResponseError(
                "AdaPromptManager failed while building the prompt.",
                stage="PROMPT_MANAGER_BUILD",
                category="PROMPT_MANAGER",
                original=error,
            ) from error

        # ----------------------------------------------------
        # ADA INTELLIGENCE
        # ----------------------------------------------------

        intelligence_prompt = (
            self.get_intelligence_prompt()
        )

        intelligence_prompt = compact_text(
            intelligence_prompt,
            MAX_INTELLIGENCE_PROMPT_CHARS,
        )

        parts.append(
            intelligence_prompt
        )

        # ----------------------------------------------------
        # BILLING
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
        # APPLICATION STATE
        # ----------------------------------------------------

        prepared_context = (
            prepare_application_context(
                context
            )
        )

        if prepared_context:

            parts.append(
                "CURRENT APPLICATION STATE\n\n"
                + prepared_context
                + "\n\nEND CURRENT APPLICATION STATE"
            )

        # ----------------------------------------------------
        # SERVICE
        # ----------------------------------------------------

        if active_service:

            parts.append(
                "CURRENT SELECTED SERVICE\n"
                + active_service
            )

        # ----------------------------------------------------
        # FINAL PROMPT LIMIT
        # ----------------------------------------------------

        system_prompt = "\n\n".join(
            part
            for part in parts
            if part
        )

        return compact_text(
            system_prompt,
            MAX_SYSTEM_PROMPT_CHARS,
        )

    # ========================================================
    # HISTORY
    # ========================================================

    def add_history(
        self,
        role: str,
        content: str,
    ) -> None:

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

        if (
            len(self.history)
            > self.max_history_messages
        ):

            self.history = (
                self.history[
                    -self.max_history_messages:
                ]
            )

    # ========================================================
    # CLEAR HISTORY
    # ========================================================

    def clear_history(
        self,
    ) -> None:

        self.history.clear()

    # ========================================================
    # BUILD MESSAGES
    # ========================================================

    def build_messages(
        self,
        system_prompt: str,
    ) -> list[dict[str, str]]:

        messages: list[
            dict[str, str]
        ] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        messages.extend(
            compact_history(
                self.history
            )
        )

        return messages

    # ========================================================
    # EVENT NORMALIZATION
    # ========================================================

    @staticmethod
    def normalize_event(
        event: str | None,
    ) -> str:

        return safe_text(
            event
        ).lower()

    # ========================================================
    # DOCUMENT EVENT
    # ========================================================

    @classmethod
    def is_document_event(
        cls,
        event: str | None,
    ) -> bool:

        event = cls.normalize_event(
            event
        )

        return event in DOCUMENT_EVENTS

    # ========================================================
    # REVIEW EVENT
    # ========================================================

    @staticmethod
    def is_review_event(
        event: str | None,
    ) -> bool:

        event = safe_text(
            event
        ).lower()

        return event in {
            "review",
            "review_requested",
            "review_called",
            "open_review",
            "review_page",
            "review_document",
            "review_correction",
        }

    # ========================================================
    # EXTRACT PAGE COUNT
    # ========================================================

    @staticmethod
    def extract_page_count(
        text: str,
    ) -> int | None:

        text = safe_text(text)

        if not text:
            return None

        patterns = [
            r"(?:number\s+of\s+pages|page\s+count|pages)"
            r"\s*(?:is|=|:|-)?\s*(\d+)",

            r"(\d+)\s*pages?\b",
        ]

        for pattern in patterns:

            matches = re.findall(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if not matches:
                continue

            for match in matches:

                try:

                    value = int(
                        match
                    )

                except Exception:

                    continue

                if 1 <= value <= DOCUMENT_MAX_SECTIONS:

                    return value

        return None

    # ========================================================
    # DOCUMENT SECTION INSTRUCTION
    # ========================================================

    def build_document_section_instruction(
        self,
        *,
        original_request: str,
        service: str | None,
        section_number: int,
        total_sections: int | None,
        previous_tail: str,
        correction: bool = False,
    ) -> str:

        if total_sections:

            section_label = (
                f"SECTION {section_number} OF "
                f"{total_sections}"
            )

        else:

            section_label = (
                f"SECTION {section_number}"
            )

        if correction:

            opening = """
You are revising an existing customer document.

Apply the customer's requested corrections while
preserving all other important content.

Generate the requested document sequentially.

This request is one controlled generation section.
Do not summarize the complete document.
"""

        else:

            opening = """
You are preparing the customer's requested document.

Generate the document sequentially.

This request is one controlled generation section.
Do not summarize the complete document.
"""

        instruction = f"""
{opening}

{section_label}

SERVICE:
{safe_text(service) or "Not specified"}

CUSTOMER'S ORIGINAL REQUEST:
{compact_text(original_request, 3500)}

IMPORTANT:
- Preserve customer-supplied facts.
- Do not invent facts.
- Follow the service-specific instructions.
- Follow the existing document-quality instructions.
- Continue naturally from the preceding section.
- Do not repeat completed material unnecessarily.
- Do not add commentary about the generation process.
- Do not say that the response is incomplete.
- Return document content only.
"""

        if previous_tail:

            instruction += (
                "\n\nEND OF PREVIOUS SECTION "
                "FOR CONTINUITY ONLY:\n"
                + compact_text(
                    previous_tail,
                    DOCUMENT_RECENT_CONTEXT_CHARS,
                )
                + "\n\n"
                "Continue from this point."
            )

        if total_sections:

            instruction += (
                "\n\nThe requested document contains "
                f"{total_sections} section(s). "
                f"You are currently generating section "
                f"{section_number}."
            )

        else:

            instruction += (
                "\n\nContinue generating the document "
                "until this section is complete. "
                "The application will request additional "
                "sections when necessary."
            )

        return compact_text(
            instruction,
            DOCUMENT_SECTION_INSTRUCTION_CHARS,
        )

    # ========================================================
    # CALL GROQ
    # ========================================================

    def call_groq(
        self,
        *,
        messages: list[dict[str, str]],
        output_tokens: int,
        stage: str = "GROQ_REQUEST",
        section_number: int | None = None,
        event: str | None = None,
    ) -> str:

        try:

            client = get_client()

        except AdaResponseError as error:

            log_error(
                "GROQ CLIENT UNAVAILABLE",
                error,
                stage=error.stage,
                category=error.category,
                section_number=section_number,
                event=event,
            )

            raise

        except Exception as error:

            log_error(
                "GROQ CLIENT INITIALIZATION FAILED",
                error,
                stage="CLIENT_INITIALIZATION",
                category="GROQ_CLIENT",
                section_number=section_number,
                event=event,
            )

            raise AdaResponseError(
                "Groq client initialization failed.",
                stage="CLIENT_INITIALIZATION",
                category="GROQ_CLIENT",
                original=error,
            ) from error

        print()
        print(
            "ADA GROQ REQUEST"
        )
        print(
            "Stage:",
            stage,
        )
        print(
            "Model:",
            MODEL,
        )
        print(
            "Messages:",
            len(messages),
        )
        print(
            "Requested output tokens:",
            output_tokens,
        )
        print(
            "API key configured:",
            bool(API_KEY),
        )

        try:

            response = (
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=output_tokens,
                )
            )

        except Exception as error:

            category = classify_groq_error(
                error
            )

            log_error(
                "GROQ API REQUEST FAILED",
                error,
                stage=stage,
                category=category,
                section_number=section_number,
                event=event,
            )

            raise AdaResponseError(
                str(error),
                stage=stage,
                category=category,
                status_code=get_error_status_code(
                    error
                ),
                original=error,
            ) from error

        if response is None:

            error = AdaResponseError(
                "Groq returned no response object.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

            log_error(
                "EMPTY GROQ RESPONSE",
                error,
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
                section_number=section_number,
                event=event,
            )

            raise error

        if not response.choices:

            error = AdaResponseError(
                "Groq returned no choices.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

            log_error(
                "GROQ RETURNED NO CHOICES",
                error,
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
                section_number=section_number,
                event=event,
            )

            raise error

        choice = response.choices[0]

        if not choice.message:

            error = AdaResponseError(
                "Groq returned an empty message object.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

            log_error(
                "GROQ RETURNED EMPTY MESSAGE",
                error,
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
                section_number=section_number,
                event=event,
            )

            raise error

        content = safe_text(
            choice.message.content
        )

        if not content:

            error = AdaResponseError(
                "Groq returned an empty message.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

            log_error(
                "GROQ RETURNED EMPTY CONTENT",
                error,
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
                section_number=section_number,
                event=event,
            )

            raise error

        # ----------------------------------------------------
        # USAGE DIAGNOSTICS
        #
        # This is extremely useful when investigating token
        # usage without printing the actual document.
        # ----------------------------------------------------

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage is not None:

            prompt_tokens = getattr(
                usage,
                "prompt_tokens",
                None,
            )

            completion_tokens = getattr(
                usage,
                "completion_tokens",
                None,
            )

            total_tokens = getattr(
                usage,
                "total_tokens",
                None,
            )

            print(
                "Groq prompt tokens:",
                prompt_tokens,
            )

            print(
                "Groq completion tokens:",
                completion_tokens,
            )

            print(
                "Groq total tokens:",
                total_tokens,
            )

        print(
            "Groq response received successfully."
        )

        print()

        return content

    # ========================================================
    # GENERATE ONE DOCUMENT SECTION
    # ========================================================

    def generate_document_section(
        self,
        *,
        system_prompt: str,
        original_request: str,
        service: str | None,
        section_number: int,
        total_sections: int | None,
        previous_tail: str,
        correction: bool = False,
        event: str | None = None,
    ) -> str:

        try:

            section_instruction = (
                self.build_document_section_instruction(
                    original_request=original_request,
                    service=service,
                    section_number=section_number,
                    total_sections=total_sections,
                    previous_tail=previous_tail,
                    correction=correction,
                )
            )

        except Exception as error:

            log_error(
                "DOCUMENT SECTION INSTRUCTION FAILED",
                error,
                stage="DOCUMENT_SECTION_INSTRUCTION",
                category="DOCUMENT_GENERATION",
                section_number=section_number,
                event=event,
            )

            raise AdaResponseError(
                "Failed to build document section instruction.",
                stage="DOCUMENT_SECTION_INSTRUCTION",
                category="DOCUMENT_GENERATION",
                original=error,
            ) from error

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": section_instruction,
            },
        ]

        print()
        print(
            "DOCUMENT SECTION REQUEST"
        )
        print(
            "Section:",
            section_number,
        )
        print(
            "Total sections:",
            total_sections
            if total_sections
            else "open-ended",
        )
        print(
            "Correction:",
            correction,
        )
        print()

        return self.call_groq(
            messages=messages,
            output_tokens=DOCUMENT_SECTION_OUTPUT_TOKENS,
            stage="DOCUMENT_GROQ_REQUEST",
            section_number=section_number,
            event=event,
        )

    # ========================================================
    # DOCUMENT CONTINUATION CHECK
    # ========================================================

    @staticmethod
    def section_needs_continuation(
        section: str,
        *,
        total_sections: int | None,
        section_number: int,
    ) -> bool:

        if not section:
            return True

        if total_sections is not None:

            return (
                section_number
                < total_sections
            )

        tail = section[-1200:].lower()

        continuation_markers = (
            "[continue]",
            "to be continued",
            "continued",
        )

        if any(
            marker in tail
            for marker in continuation_markers
        ):
            return True

        if len(section) < DOCUMENT_MIN_SECTION_CHARS:

            return True

        return False

    # ========================================================
    # CLEAN SECTION
    # ========================================================

    @staticmethod
    def clean_document_section(
        section: str,
    ) -> str:

        section = safe_text(
            section
        )

        if not section:
            return ""

        section = re.sub(
            r"\[\s*continue\s*\]",
            "",
            section,
            flags=re.IGNORECASE,
        )

        section = re.sub(
            r"\bto be continued\b",
            "",
            section,
            flags=re.IGNORECASE,
        )

        return section.strip()

    # ========================================================
    # ASSEMBLE DOCUMENT
    # ========================================================

    @staticmethod
    def assemble_document(
        sections: list[str],
    ) -> str:

        complete_sections: list[str] = []

        for section in sections:

            section = safe_text(
                section
            )

            if not section:
                continue

            complete_sections.append(
                section
            )

        return "\n\n".join(
            complete_sections
        ).strip()

    # ========================================================
    # DOCUMENT GENERATION
    # ========================================================

    def generate_complete_document(
        self,
        *,
        original_request: str,
        service: str | None,
        context: str | None,
        correction: bool = False,
        existing_work: str | None = None,
        event: str | None = None,
    ) -> str:

        original_request = safe_text(
            original_request
        )

        active_service = (
            self.normalize_service(
                service
            )
        )

        # ----------------------------------------------------
        # BASE INTELLIGENCE
        # ----------------------------------------------------

        try:

            system_prompt = (
                self.build_system_prompt(
                    service=active_service,
                    context=context,
                )
            )

        except Exception as error:

            if isinstance(
                error,
                AdaResponseError,
            ):

                raise

            log_error(
                "DOCUMENT SYSTEM PROMPT FAILED",
                error,
                stage="DOCUMENT_SYSTEM_PROMPT",
                category="DOCUMENT_GENERATION",
                event=event,
            )

            raise AdaResponseError(
                "Document system prompt construction failed.",
                stage="DOCUMENT_SYSTEM_PROMPT",
                category="DOCUMENT_GENERATION",
                original=error,
            ) from error

        # ----------------------------------------------------
        # DETERMINE EXPLICIT SECTION COUNT
        # ----------------------------------------------------

        total_sections = (
            self.extract_page_count(
                original_request
            )
        )

        print()
        print("=" * 78)
        print(
            "DOCUMENT GENERATION STARTED"
        )
        print("=" * 78)
        print(
            "Service:",
            active_service,
        )
        print(
            "Event:",
            event,
        )
        print(
            "Correction:",
            correction,
        )
        print(
            "Requested sections:",
            total_sections
            if total_sections
            else "not explicitly specified",
        )
        print(
            "Maximum sections:",
            DOCUMENT_MAX_SECTIONS,
        )
        print("=" * 78)
        print()

        # ----------------------------------------------------
        # EXISTING WORK FOR CORRECTIONS
        # ----------------------------------------------------

        previous_tail = ""

        if existing_work:

            previous_tail = safe_text(
                existing_work
            )[
                -DOCUMENT_RECENT_CONTEXT_CHARS:
            ]

        sections: list[str] = []

        section_number = 1

        maximum_sections = (
            total_sections
            if total_sections is not None
            else DOCUMENT_MAX_SECTIONS
        )

        while (
            section_number
            <= maximum_sections
        ):

            try:

                section = (
                    self.generate_document_section(
                        system_prompt=system_prompt,
                        original_request=original_request,
                        service=active_service,
                        section_number=section_number,
                        total_sections=total_sections,
                        previous_tail=previous_tail,
                        correction=correction,
                        event=event,
                    )
                )

            except Exception as error:

                log_error(
                    "DOCUMENT SECTION GENERATION FAILED",
                    error,
                    stage="DOCUMENT_SECTION_GENERATION",
                    category=(
                        error.category
                        if isinstance(
                            error,
                            AdaResponseError,
                        )
                        else "DOCUMENT_GENERATION"
                    ),
                    section_number=section_number,
                    event=event,
                )

                raise

            section = (
                self.clean_document_section(
                    section
                )
            )

            if not section:

                error = AdaResponseError(
                    "Document section returned no content.",
                    stage="DOCUMENT_SECTION_EMPTY",
                    category="DOCUMENT_GENERATION",
                )

                log_error(
                    "EMPTY DOCUMENT SECTION",
                    error,
                    stage="DOCUMENT_SECTION_EMPTY",
                    category="DOCUMENT_GENERATION",
                    section_number=section_number,
                    event=event,
                )

                raise error

            sections.append(
                section
            )

            previous_tail = section[
                -DOCUMENT_RECENT_CONTEXT_CHARS:
            ]

            print(
                "Generated section characters:",
                len(section),
            )

            print(
                "Total assembled sections:",
                len(sections),
            )

            if total_sections is not None:

                if section_number >= total_sections:

                    break

            else:

                if not self.section_needs_continuation(
                    section,
                    total_sections=None,
                    section_number=section_number,
                ):

                    break

            section_number += 1

        # ----------------------------------------------------
        # ASSEMBLY
        # ----------------------------------------------------

        try:

            complete_document = (
                self.assemble_document(
                    sections
                )
            )

        except Exception as error:

            log_error(
                "DOCUMENT ASSEMBLY FAILED",
                error,
                stage="DOCUMENT_ASSEMBLY",
                category="DOCUMENT_ASSEMBLY",
                event=event,
            )

            raise AdaResponseError(
                "Document assembly failed.",
                stage="DOCUMENT_ASSEMBLY",
                category="DOCUMENT_ASSEMBLY",
                original=error,
            ) from error

        if not complete_document:

            error = AdaResponseError(
                "Document generation returned no document content.",
                stage="DOCUMENT_ASSEMBLY",
                category="DOCUMENT_ASSEMBLY",
            )

            log_error(
                "COMPLETE DOCUMENT IS EMPTY",
                error,
                stage="DOCUMENT_ASSEMBLY",
                category="DOCUMENT_ASSEMBLY",
                event=event,
            )

            raise error

        print()
        print("=" * 78)
        print(
            "COMPLETE DOCUMENT ASSEMBLED"
        )
        print("=" * 78)
        print(
            "Sections:",
            len(sections),
        )
        print(
            "Requested sections:",
            total_sections
            if total_sections
            else "not explicitly specified",
        )
        print(
            "Complete document characters:",
            len(complete_document),
        )
        print("=" * 78)
        print()

        # ----------------------------------------------------
        # NO COMPACTION HERE
        # ----------------------------------------------------

        return complete_document

    # ========================================================
    # DOCUMENT RESPONSE
    # ========================================================

    def respond_with_document(
        self,
        *,
        message: str,
        service: str | None,
        event: str | None,
        context: str | None,
    ) -> str:

        event_normalized = (
            self.normalize_event(
                event
            )
        )

        correction = (
            event_normalized
            in DOCUMENT_CORRECTION_EVENTS
        )

        return (
            self.generate_complete_document(
                original_request=message,
                service=service,
                context=context,
                correction=correction,
                existing_work=None,
                event=event_normalized,
            )
        )

    # ========================================================
    # NORMAL RESPONSE
    # ========================================================

    def respond_normal(
        self,
        *,
        message: str,
        service: str | None,
        event: str | None,
        context: str | None,
    ) -> str:

        active_service = (
            self.normalize_service(
                service
            )
        )

        try:

            system_prompt = (
                self.build_system_prompt(
                    service=active_service,
                    context=context,
                )
            )

        except Exception as error:

            log_error(
                "NORMAL SYSTEM PROMPT FAILED",
                error,
                stage="NORMAL_SYSTEM_PROMPT",
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "PROMPT_MANAGER"
                ),
                event=event,
            )

            raise

        messages = (
            self.build_messages(
                system_prompt
            )
        )

        current_event = safe_text(
            event
        )

        if current_event:

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "CURRENT APPLICATION EVENT\n"
                        + compact_text(
                            current_event,
                            MAX_EVENT_CHARS,
                        )
                    ),
                }
            )

        messages.append(
            {
                "role": "user",
                "content": compact_text(
                    message,
                    MAX_USER_MESSAGE_CHARS,
                ),
            }
        )

        response = self.call_groq(
            messages=messages,
            output_tokens=MAX_OUTPUT_TOKENS,
            stage="NORMAL_GROQ_REQUEST",
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
    # MAIN RESPOND
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
                "Please tell me what you would "
                "like me to help you with."
            )

        print()
        print("=" * 78)
        print(
            "ADA RESPONSE START"
        )
        print("=" * 78)
        print(
            "Service supplied:",
            service,
        )
        print(
            "Event:",
            event,
        )
        print(
            "Message characters:",
            len(message),
        )
        print(
            "Model:",
            MODEL,
        )
        print(
            "Groq package available:",
            Groq is not None,
        )
        print(
            "API key configured:",
            bool(API_KEY),
        )
        print("=" * 78)
        print()

        if service:

            self.set_service(
                service
            )

        try:

            active_service = (
                self.normalize_service(
                    self.service
                )
            )

        except Exception as error:

            log_error(
                "SERVICE SETUP FAILED",
                error,
                stage="SERVICE_SETUP",
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "SERVICE"
                ),
                event=event,
            )

            return client_error_message(
                error
            )

        # ----------------------------------------------------
        # CLIENT CONFIGURATION CHECK
        # ----------------------------------------------------

        try:

            get_client()

        except Exception as error:

            log_error(
                "GROQ CONFIGURATION CHECK FAILED",
                error,
                stage=(
                    error.stage
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "CLIENT_CONFIGURATION"
                ),
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "CONFIGURATION"
                ),
                event=event,
            )

            return client_error_message(
                error
            )

        event_normalized = (
            self.normalize_event(
                event
            )
        )

        # ====================================================
        # DOCUMENT GENERATION PATH
        # ====================================================

        if event_normalized in DOCUMENT_EVENTS:

            try:

                complete_document = (
                    self.respond_with_document(
                        message=message,
                        service=active_service,
                        event=event_normalized,
                        context=context,
                    )
                )

                if complete_document:

                    self.add_history(
                        "user",
                        message,
                    )

                    self.add_history(
                        "assistant",
                        (
                            "[Complete customer document "
                            "prepared and assembled.]"
                        ),
                    )

                    return complete_document

                error = AdaResponseError(
                    "Document generation returned no document.",
                    stage="DOCUMENT_GENERATION",
                    category="DOCUMENT_GENERATION",
                )

                log_error(
                    "DOCUMENT GENERATION RETURNED EMPTY RESULT",
                    error,
                    stage="DOCUMENT_GENERATION",
                    category="DOCUMENT_GENERATION",
                    event=event_normalized,
                )

                return client_error_message(
                    error
                )

            except Exception as error:

                log_error(
                    "ADA DOCUMENT GENERATION FAILED",
                    error,
                    stage=(
                        error.stage
                        if isinstance(
                            error,
                            AdaResponseError,
                        )
                        else "DOCUMENT_GENERATION"
                    ),
                    category=(
                        error.category
                        if isinstance(
                            error,
                            AdaResponseError,
                        )
                        else "DOCUMENT_GENERATION"
                    ),
                    event=event_normalized,
                )

                return client_error_message(
                    error
                )

        # ====================================================
        # NORMAL LLM RESPONSE
        # ====================================================

        try:

            return self.respond_normal(
                message=message,
                service=active_service,
                event=event,
                context=context,
            )

        except Exception as error:

            log_error(
                "ADA NORMAL RESPONSE FAILED",
                error,
                stage=(
                    error.stage
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "NORMAL_RESPONSE"
                ),
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "NORMAL_RESPONSE"
                ),
                event=event,
            )

            return client_error_message(
                error
            )


# ============================================================
# TEST / DIAGNOSTIC
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 78)
    print(
        "NAIJA POCKET BUSINESS CENTER"
    )
    print(
        "ADA END-TO-END RESPONSE ENGINE"
    )
    print(
        "DIAGNOSTIC MODE"
    )
    print("=" * 78)
    print()

    print(
        "Model:",
        get_ada_model(),
    )

    print(
        "Groq package available:",
        Groq is not None,
    )

    print(
        "Groq API key configured:",
        bool(API_KEY),
    )

    print(
        "Client error exposure:",
        EXPOSE_ERRORS_TO_CLIENT,
    )

    print()

    # --------------------------------------------------------
    # PROMPT MANAGER DIAGNOSTIC
    # --------------------------------------------------------

    try:

        manager = AdaPromptManager()

        print(
            "Prompt Manager:",
            "READY",
        )

        try:

            identity = (
                manager.get_identity_prompt()
            )

            print(
                "Identity:",
                bool(identity),
            )

        except Exception as error:

            log_error(
                "IDENTITY PROMPT DIAGNOSTIC FAILED",
                error,
                stage="PROMPT_MANAGER_IDENTITY",
                category="PROMPT_MANAGER",
            )

        try:

            context_prompt = (
                manager.get_nigerian_context_prompt()
            )

            print(
                "Nigerian Context:",
                bool(context_prompt),
            )

        except Exception as error:

            log_error(
                "NIGERIAN CONTEXT DIAGNOSTIC FAILED",
                error,
                stage="PROMPT_MANAGER_CONTEXT",
                category="PROMPT_MANAGER",
            )

        services = [
            "cv",
            "cover_letter",
            "business",
            "academic",
            "document_processing",
            "review",
            "workflow",
            "delivery",
        ]

        print()

        for service in services:

            try:

                available = bool(
                    manager.get_service_prompt(
                        service
                    )
                )

            except Exception as error:

                available = False

                log_error(
                    f"SERVICE PROMPT FAILED: {service}",
                    error,
                    stage="PROMPT_MANAGER_SERVICE",
                    category="PROMPT_MANAGER",
                )

            print(
                f"{service.title():25} :",
                "READY"
                if available
                else "MISSING",
            )

    except Exception as error:

        log_error(
            "PROMPT MANAGER DIAGNOSTIC FAILED",
            error,
            stage="PROMPT_MANAGER_DIAGNOSTIC",
            category="PROMPT_MANAGER",
        )

    print()

    # --------------------------------------------------------
    # BILLING DIAGNOSTIC
    # --------------------------------------------------------

    try:

        billing = BillingManager()

        print(
            "Billing Manager:",
            "READY",
        )

        print()

        for service in [
            "cv",
            "cover_letter",
            "business",
            "academic",
        ]:

            try:

                item = (
                    billing.get_service(
                        service
                    )
                )

                print(
                    f"Billing {service:18}:",
                    "FOUND"
                    if item
                    else "MISSING",
                )

            except Exception as error:

                print(
                    f"Billing {service:18}:",
                    "ERROR",
                )

                log_error(
                    f"BILLING DIAGNOSTIC FAILED: {service}",
                    error,
                    stage="BILLING_DIAGNOSTIC",
                    category="BILLING",
                )

    except Exception as error:

        log_error(
            "BILLING MANAGER DIAGNOSTIC FAILED",
            error,
            stage="BILLING_INITIALIZATION",
            category="BILLING",
        )

    print()

    # --------------------------------------------------------
    # GROQ CLIENT DIAGNOSTIC
    # --------------------------------------------------------

    try:

        client = get_client()

        print(
            "Groq Client:",
            "READY"
            if client is not None
            else "NOT READY",
        )

    except Exception as error:

        log_error(
            "GROQ CLIENT DIAGNOSTIC FAILED",
            error,
            stage=(
                error.stage
                if isinstance(
                    error,
                    AdaResponseError,
                )
                else "CLIENT_DIAGNOSTIC"
            ),
            category=(
                error.category
                if isinstance(
                    error,
                    AdaResponseError,
                )
                else "GROQ_CLIENT"
            ),
        )

    print()

    # --------------------------------------------------------
    # FINAL STATUS
    # --------------------------------------------------------

    print(
        "Ada End-to-End Intelligence:",
        "READY",
    )

    print(
        "Keyword Workflow:",
        "DISABLED",
    )

    print(
        "LLM Workflow Reasoning:",
        "ENABLED",
    )

    print(
        "Sequential Document Generation:",
        "ENABLED",
    )

    print(
        "Complete Document Assembly:",
        "ENABLED",
    )

    print(
        "Review Document Truncation:",
        "DISABLED",
    )

    print(
        "Document Content Compaction:",
        "DISABLED",
    )

    print(
        "Normal Token Control:",
        "ENABLED",
    )

    print(
        "Detailed Server Diagnostics:",
        "ENABLED",
    )

    print(
        "Groq Rate-Limit Diagnostics:",
        "ENABLED",
    )

    print(
        "Provider Error Diagnostics:",
        "ENABLED",
    )

    print(
        "Normal Maximum System Prompt:",
        f"{MAX_SYSTEM_PROMPT_CHARS} characters",
    )

    print(
        "Normal Maximum History:",
        f"{MAX_HISTORY_MESSAGES} messages",
    )

    print(
        "Normal Maximum Output:",
        f"{MAX_OUTPUT_TOKENS} tokens",
    )

    print(
        "Document Section Output:",
        f"{DOCUMENT_SECTION_OUTPUT_TOKENS} tokens",
    )

    print(
        "Maximum Document Sections:",
        DOCUMENT_MAX_SECTIONS,
    )

    print()
    print("=" * 78)
    print(
        "DIAGNOSTIC INITIALIZATION COMPLETE"
    )
    print("=" * 78)
