"""
Naija Pocket Business Center
Ada Response Engine

END-TO-END LLM INTELLIGENCE LAYER

IMPORTANT ARCHITECTURE
----------------------

AdaResponse is the intelligence layer.

The Workspace sends the customer's message and the current
application state into AdaResponse.

Ada uses the LLM to understand:

    - what the customer wants
    - what information has already been supplied
    - what document/service is being worked on
    - what stage the work is in
    - whether the customer is asking for creation, revision,
      review, approval, payment, delivery, or another action

Ada does NOT use keyword matching to decide customer intent.

AdaPromptManager remains responsible for:
    - Ada identity
    - Nigerian context
    - writing style
    - service-specific prompts

BillingManager remains authoritative for:
    - service names
    - prices
    - billing types

APPLICATION STATE remains authoritative for:
    - customer information
    - selected service
    - submitted information
    - uploaded content
    - assembled document
    - document sections/pages
    - review state
    - approval state
    - payment state
    - delivery state
    - download state


DOCUMENT ARCHITECTURE
---------------------

A document is NOT limited to one LLM response.

Creation:

    Customer Send
         ↓
    Ada Intelligence
         ↓
    controlled document generation
         ↓
    section 1
    section 2
    section 3
    ...
         ↓
    complete assembled document


REVIEW ARCHITECTURE
-------------------

Review is also an intelligence operation.

When the customer presses Review in the Workspace, the
application should provide the assembled document through
the application context.

Ada then reviews the document in controlled batches.

    COMPLETE DOCUMENT
          ↓
    Ada determines review batches
          ↓
    REVIEW BATCH 1
          ↓
    REVIEW BATCH 2
          ↓
    REVIEW BATCH 3
          ↓
          ...
          ↓
    REVIEW RESULT ASSEMBLED
          ↓
    Customer review page

A long document is NEVER forced into one Groq request.

The batching is an LLM-request/token-control mechanism.

It does NOT:
    - delete pages
    - shorten the customer's document
    - change requested page count
    - summarize the customer's work instead of reviewing it
    - weaken Ada
    - ask the customer to repeat information already present
    - replace the document with an explanation

The complete document remains application data.

Ada reviews portions of that document within safe request
limits and then produces one coherent review result.


IMPORTANT
---------

Do not confuse:

    document pages

with:

    LLM request batches

A 20-page document may require many LLM requests.

That does NOT mean the document has been reduced to the number
of requests.

The application owns the complete document.

Ada owns the intelligence used to understand and review it.


DIAGNOSTICS
-----------

Technical errors are logged on the server.

The API key is never printed.

Document contents are never printed as diagnostics.

Provider/token limits are never allowed to destroy document
content.
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
# CLIENT ERROR VISIBILITY
# ============================================================

EXPOSE_ERRORS_TO_CLIENT = (
    os.getenv("ADA_EXPOSE_ERRORS")
    or "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ============================================================
# NORMAL RESPONSE LIMITS
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 18000
MAX_CENTRAL_PROMPT_CHARS = 12000
MAX_INTELLIGENCE_PROMPT_CHARS = 10000

MAX_CONTEXT_CHARS = 8000

MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_MESSAGE_CHARS = 1800

MAX_EVENT_CHARS = 1200
MAX_USER_MESSAGE_CHARS = 4000

MAX_OUTPUT_TOKENS = 800


# ============================================================
# DOCUMENT CREATION LIMITS
# ============================================================

DOCUMENT_SECTION_OUTPUT_TOKENS = 700

DOCUMENT_SECTION_INSTRUCTION_CHARS = 4500

DOCUMENT_RECENT_CONTEXT_CHARS = 2500

DOCUMENT_MAX_SECTIONS = 60

DOCUMENT_MIN_SECTION_CHARS = 250


# ============================================================
# REVIEW LIMITS
# ============================================================

"""
These limits control ONE review request.

They do not limit the customer's document.

A document can therefore be reviewed through many sequential
LLM requests.
"""

REVIEW_BATCH_CHARS = 8500

REVIEW_BATCH_OUTPUT_TOKENS = 650

REVIEW_INSTRUCTION_CHARS = 4000

REVIEW_MAX_BATCHES = 100

REVIEW_CONTEXT_TAIL_CHARS = 1800

REVIEW_SYNTHESIS_OUTPUT_TOKENS = 900

REVIEW_SYNTHESIS_INPUT_CHARS = 12000


# ============================================================
# EVENTS
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

REVIEW_EVENTS = {
    "review",
    "review_requested",
    "review_called",
    "open_review",
    "review_page",
    "review_document",
    "customer_review",
}

DOCUMENT_EVENTS = (
    DOCUMENT_CREATION_EVENTS
    | DOCUMENT_CORRECTION_EVENTS
)


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


def compact_text(
    text: str | None,
    maximum: int,
) -> str:

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

    result = []

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

    return safe_text(context)


# ============================================================
# ERROR HELPERS
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

    for candidate in [
        getattr(error, "body", None),
        getattr(error, "response", None),
    ]:

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

    return safe_text(
        getattr(
            error,
            "code",
            None,
        )
    )


def classify_groq_error(
    error: Exception,
) -> str:

    status = get_error_status_code(
        error
    )

    name = type(
        error
    ).__name__.lower()

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
# DIAGNOSTICS
# ============================================================

def log_error(
    title: str,
    error: Exception,
    *,
    stage: str,
    category: str | None = None,
    batch_number: int | None = None,
    section_number: int | None = None,
    event: str | None = None,
) -> None:

    status = get_error_status_code(
        error
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

    print("Stage:", stage)
    print("Category:", error_category)
    print(
        "Exception type:",
        type(error).__name__,
    )

    if status is not None:
        print(
            "HTTP status:",
            status,
        )

    code = get_error_code(
        error
    )

    if code:
        print(
            "Provider error code:",
            code,
        )

    if batch_number is not None:
        print(
            "Review batch:",
            batch_number,
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


def client_error_message(
    error: Exception,
) -> str:

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
            "Technical error detected.\n\n"
            f"Category: {category}\n"
            f"HTTP status: {status}\n"
            f"Model: {MODEL}\n"
            f"Error: {message}"
        )

    return (
        "Technical error detected.\n\n"
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

        self.history = []

    # ========================================================
    # SERVICE
    # ========================================================

    def set_service(
        self,
        service: str | None,
    ) -> None:

        service = safe_text(
            service
        )

        if service:
            self.service = service

    # ========================================================
    # SERVICE NORMALIZATION
    # ========================================================

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
                self.billing.normalize_service(
                    service
                )
            )

            normalized = safe_text(
                normalized
            )

            return normalized or service

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
                "No BillingManager record was found.\n"
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
    # GENERAL INTELLIGENCE PROMPT
    # ========================================================

    def get_intelligence_prompt(
        self,
    ) -> str:

        return """
You are Ada, the intelligent customer-facing assistant
of Naija Pocket Business Center.

You are an LLM reasoning assistant.

Your job is to understand the customer's meaning from the
complete conversation and current application state.

Do not decide intent by keyword matching.

Do not follow a fixed script when the application state
already contains the information required to continue.

IMPORTANT INFORMATION RULE
---------------------------

If the customer has already supplied information and that
information is present in the application state, USE IT.

Do not repeatedly ask for the same information.

For example, if the application already contains the topic,
required length, academic level, formatting instructions,
sources, or uploaded material for a seminar paper, do not
ask the customer for those details again merely because the
current message is short.

APPLICATION STATE
-----------------

Application state is authoritative.

It may contain:

- customer information
- selected service
- customer request
- form information
- uploaded content
- existing document
- document sections
- page information
- creation state
- review state
- correction requests
- approval state
- payment state
- delivery state
- download state

Use the state to understand what has already happened.

DOCUMENT INTEGRITY
------------------

A document can be larger than one LLM response.

The application may therefore divide document work into
controlled LLM requests.

Those requests are parts of one document.

Never interpret an LLM output limit as a customer document
page limit.

Never delete pages because of an output limit.

Never shorten the customer's requested work simply because
one request has reached its safe output size.

Never replace requested work with a summary unless the
customer explicitly requests a summary.

REVIEW
------

Review means examining the actual existing document.

Review is not a request to regenerate the document from
scratch.

When the application supplies an existing document, inspect
it.

For a large document, the application may provide the
document to you in sequential review batches.

Treat all batches as parts of the same document.

Identify:

- missing requirements
- factual inconsistencies
- structural problems
- weak explanations
- repetition
- formatting problems
- unclear sections
- language problems
- academic/business quality issues
- places requiring correction

Do not invent defects merely to produce a longer review.

Do not claim to have reviewed pages that were not supplied
to the current request.

CORRECTIONS
-----------

When the customer requests a correction, understand exactly
what they want changed.

Preserve everything that does not need changing.

Do not regenerate unrelated parts merely because a correction
was requested.

BILLING
-------

BillingManager is authoritative for prices.

Never invent a price, discount, payment confirmation, or
payment status.

APPROVAL
--------

Never claim that a document is approved unless application
state says it is approved.

PAYMENT
-------

Never claim payment succeeded unless application state
confirms payment.

DELIVERY
--------

Never claim delivery occurred unless application state
confirms delivery.

DOWNLOAD
--------

Never invent a download link.

CUSTOMER COMMUNICATION
----------------------

Be warm, clear, professional, practical, and Nigerian in
tone where appropriate.

Understand Nigerian English, Pidgin, informal writing,
short messages, imperfect spelling, and follow-up messages.

Do not mention:

- Groq
- Gemini
- model names
- API calls
- tokens
- system prompts
- internal architecture
- internal diagnostics
"""


    # ========================================================
    # REVIEW PROMPT
    # ========================================================

    def get_review_prompt(
        self,
    ) -> str:

        return """
REVIEW INTELLIGENCE INSTRUCTIONS

You are reviewing an existing customer document.

This is a REVIEW operation.

Do NOT recreate the entire document.

Do NOT ask the customer to provide information that is
already contained in the application state.

Your task is to intelligently inspect the supplied portion
of the customer's existing document.

Treat the supplied text as part of one larger document.

REVIEW FOR:

1. Compliance with the customer's original request.
2. Whether required sections are present.
3. Logical organization.
4. Clarity.
5. Accuracy based only on supplied facts.
6. Internal consistency.
7. Repetition.
8. Weak or incomplete explanations.
9. Grammar and language quality.
10. Formatting/structure problems.
11. Academic or professional quality appropriate to the
    selected service.
12. Missing information that genuinely prevents completion.

IMPORTANT:

Do not invent problems.

Do not invent facts.

Do not invent missing pages.

Do not claim to have inspected material that was not supplied.

If the supplied portion is satisfactory, say so internally
and identify only meaningful issues.

The application may send multiple sequential review batches.

Each batch belongs to the same document.

Maintain continuity between batches.

When asked for a review result, produce a useful review
rather than a generic statement such as "please provide the
topic and length."

The customer may already have provided those details.
Use the application state.
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

        parts = []

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

                parts.append(
                    compact_text(
                        central_prompt,
                        MAX_CENTRAL_PROMPT_CHARS,
                    )
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

        parts.append(
            compact_text(
                self.get_intelligence_prompt(),
                MAX_INTELLIGENCE_PROMPT_CHARS,
            )
        )

        billing = self.get_billing_context(
            active_service
        )

        if billing:
            parts.append(
                billing
            )

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

        if active_service:

            parts.append(
                "CURRENT SELECTED SERVICE\n"
                + active_service
            )

        return compact_text(
            "\n\n".join(
                part
                for part in parts
                if part
            ),
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

        self.history = self.history[
            -MAX_HISTORY_MESSAGES:
        ]

    def clear_history(
        self,
    ) -> None:

        self.history.clear()

    # ========================================================
    # EVENTS
    # ========================================================

    @staticmethod
    def normalize_event(
        event: str | None,
    ) -> str:

        return safe_text(
            event
        ).lower()

    @classmethod
    def is_document_event(
        cls,
        event: str | None,
    ) -> bool:

        return (
            cls.normalize_event(event)
            in DOCUMENT_EVENTS
        )

    @classmethod
    def is_review_event(
        cls,
        event: str | None,
    ) -> bool:

        return (
            cls.normalize_event(event)
            in REVIEW_EVENTS
        )

    # ========================================================
    # PAGE COUNT
    # ========================================================

    @staticmethod
    def extract_page_count(
        text: str,
    ) -> int | None:

        text = safe_text(
            text
        )

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
    # BUILD NORMAL MESSAGES
    # ========================================================

    def build_messages(
        self,
        system_prompt: str,
    ) -> list[dict[str, str]]:

        messages = [
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
    # GROQ REQUEST
    # ========================================================

    def call_groq(
        self,
        *,
        messages: list[dict[str, str]],
        output_tokens: int,
        stage: str,
        event: str | None = None,
        batch_number: int | None = None,
        section_number: int | None = None,
    ) -> str:

        try:

            client = get_client()

        except Exception as error:

            log_error(
                "GROQ CLIENT UNAVAILABLE",
                error,
                stage="CLIENT_INITIALIZATION",
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "GROQ_CLIENT"
                ),
                batch_number=batch_number,
                section_number=section_number,
                event=event,
            )

            raise

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
                batch_number=batch_number,
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

        if (
            response is None
            or not getattr(
                response,
                "choices",
                None,
            )
        ):

            raise AdaResponseError(
                "Groq returned no usable response.",
                stage=stage,
                category="GROQ_EMPTY_RESPONSE",
            )

        content = safe_text(
            response.choices[0].message.content
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

        if usage is not None:

            print(
                "Groq prompt tokens:",
                getattr(
                    usage,
                    "prompt_tokens",
                    None,
                ),
            )

            print(
                "Groq completion tokens:",
                getattr(
                    usage,
                    "completion_tokens",
                    None,
                ),
            )

            print(
                "Groq total tokens:",
                getattr(
                    usage,
                    "total_tokens",
                    None,
                ),
            )

        return content

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
        correction: bool,
    ) -> str:

        if total_sections:

            position = (
                f"SECTION {section_number} "
                f"OF {total_sections}"
            )

        else:

            position = (
                f"SECTION {section_number}"
            )

        instruction = f"""
Prepare one controlled section of the customer's requested
document.

{position}

SERVICE:
{safe_text(service) or "Not specified"}

CUSTOMER REQUEST:
{compact_text(original_request, 3500)}

{"This is a correction. Preserve unaffected content." if correction else ""}

IMPORTANT:
- Use information already supplied.
- Do not invent facts.
- Do not repeatedly ask for information already available.
- Do not discuss the generation process.
- Produce document content.
- Continue naturally from the preceding section.
"""

        if previous_tail:

            instruction += (
                "\n\nPRECEDING SECTION END FOR CONTINUITY:\n"
                + compact_text(
                    previous_tail,
                    DOCUMENT_RECENT_CONTEXT_CHARS,
                )
                + "\n\nContinue naturally."
            )

        return compact_text(
            instruction,
            DOCUMENT_SECTION_INSTRUCTION_CHARS,
        )

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
        correction: bool,
        event: str | None,
    ) -> str:

        instruction = (
            self.build_document_section_instruction(
                original_request=original_request,
                service=service,
                section_number=section_number,
                total_sections=total_sections,
                previous_tail=previous_tail,
                correction=correction,
            )
        )

        return self.call_groq(
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": instruction,
                },
            ],
            output_tokens=DOCUMENT_SECTION_OUTPUT_TOKENS,
            stage="DOCUMENT_GROQ_REQUEST",
            section_number=section_number,
            event=event,
        )

    # ========================================================
    # DOCUMENT ASSEMBLY
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

    @staticmethod
    def assemble_document(
        sections: list[str],
    ) -> str:

        return "\n\n".join(
            safe_text(section)
            for section in sections
            if safe_text(section)
        ).strip()

    # ========================================================
    # COMPLETE DOCUMENT GENERATION
    # ========================================================

    def generate_complete_document(
        self,
        *,
        original_request: str,
        service: str | None,
        context: str | None,
        correction: bool = False,
        event: str | None = None,
    ) -> str:

        active_service = (
            self.normalize_service(
                service
            )
        )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        requested_sections = (
            self.extract_page_count(
                original_request
            )
        )

        previous_tail = ""

        sections = []

        section_number = 1

        maximum_sections = (
            requested_sections
            if requested_sections is not None
            else DOCUMENT_MAX_SECTIONS
        )

        while section_number <= maximum_sections:

            section = (
                self.generate_document_section(
                    system_prompt=system_prompt,
                    original_request=original_request,
                    service=active_service,
                    section_number=section_number,
                    total_sections=requested_sections,
                    previous_tail=previous_tail,
                    correction=correction,
                    event=event,
                )
            )

            section = (
                self.clean_document_section(
                    section
                )
            )

            if not section:

                raise AdaResponseError(
                    "Document section returned no content.",
                    stage="DOCUMENT_SECTION_EMPTY",
                    category="DOCUMENT_GENERATION",
                )

            sections.append(
                section
            )

            previous_tail = section[
                -DOCUMENT_RECENT_CONTEXT_CHARS:
            ]

            if requested_sections is not None:

                if section_number >= requested_sections:
                    break

            else:

                if len(section) >= DOCUMENT_MIN_SECTION_CHARS:

                    # For open-ended generation, a section that
                    # appears complete is allowed to finish.
                    break

            section_number += 1

        document = (
            self.assemble_document(
                sections
            )
        )

        if not document:

            raise AdaResponseError(
                "Document assembly returned empty content.",
                stage="DOCUMENT_ASSEMBLY",
                category="DOCUMENT_ASSEMBLY",
            )

        return document

    # ========================================================
    # REVIEW BATCHING
    # ========================================================

    @staticmethod
    def split_for_review(
        document: str,
    ) -> list[str]:

        document = safe_text(
            document
        )

        if not document:
            return []

        """
        First preference:
        split on existing page/section boundaries.

        Second preference:
        controlled character batches.

        The actual document is never modified.
        Only the text sent to each LLM review request is split.
        """

        page_patterns = [
            r"\n\s*---\s*PAGE\s+\d+\s*---\s*\n",
            r"\n\s*PAGE\s+\d+\s*\n",
            r"\f",
        ]

        pieces = [document]

        for pattern in page_patterns:

            if len(pieces) == 1:

                pieces = re.split(
                    pattern,
                    document,
                    flags=re.IGNORECASE,
                )

        pieces = [
            safe_text(piece)
            for piece in pieces
            if safe_text(piece)
        ]

        batches = []

        current = ""

        for piece in pieces:

            if len(piece) <= REVIEW_BATCH_CHARS:

                if (
                    current
                    and len(current) + len(piece) + 2
                    > REVIEW_BATCH_CHARS
                ):

                    batches.append(
                        current.strip()
                    )

                    current = ""

                current += (
                    ("\n\n" if current else "")
                    + piece
                )

                continue

            # ------------------------------------------------
            # A single page/section is larger than the safe
            # review request.
            # ------------------------------------------------

            words = piece.split()

            chunk = ""

            for word in words:

                candidate = (
                    word
                    if not chunk
                    else chunk + " " + word
                )

                if len(candidate) > REVIEW_BATCH_CHARS:

                    if chunk:
                        batches.append(
                            chunk.strip()
                        )

                    chunk = word

                else:

                    chunk = candidate

            if chunk:
                batches.append(
                    chunk.strip()
                )

        if current:
            batches.append(
                current.strip()
            )

        return batches

    # ========================================================
    # REVIEW BATCH INSTRUCTION
    # ========================================================

    def build_review_batch_instruction(
        self,
        *,
        original_request: str,
        service: str | None,
        batch_number: int,
        total_batches: int,
        document_batch: str,
        previous_review_tail: str,
    ) -> str:

        instruction = f"""
REVIEW THE EXISTING CUSTOMER DOCUMENT.

This is review batch {batch_number} of {total_batches}.

SERVICE:
{safe_text(service) or "Not specified"}

ORIGINAL CUSTOMER REQUEST:
{compact_text(original_request, 2500)}

{self.get_review_prompt()}

DOCUMENT PORTION BEING REVIEWED
--------------------------------

{document_batch}

--------------------------------
END DOCUMENT PORTION

Review this portion intelligently.

Do not regenerate the document.

Do not ask for information already contained in the
original request or application state.

If an issue is found, explain:
- where the issue occurs
- what is wrong
- what should be improved

If the portion is satisfactory, do not invent problems.
"""

        if previous_review_tail:

            instruction += (
                "\n\nPREVIOUS REVIEW CONTEXT:\n"
                + compact_text(
                    previous_review_tail,
                    REVIEW_CONTEXT_TAIL_CHARS,
                )
                + "\n\n"
                "Use this only to maintain continuity."
            )

        return compact_text(
            instruction,
            REVIEW_INSTRUCTION_CHARS
            + REVIEW_BATCH_CHARS,
        )

    # ========================================================
    # REVIEW ONE BATCH
    # ========================================================

    def review_one_batch(
        self,
        *,
        system_prompt: str,
        original_request: str,
        service: str | None,
        batch_number: int,
        total_batches: int,
        document_batch: str,
        previous_review_tail: str,
        event: str | None,
    ) -> str:

        review_instruction = (
            self.build_review_batch_instruction(
                original_request=original_request,
                service=service,
                batch_number=batch_number,
                total_batches=total_batches,
                document_batch=document_batch,
                previous_review_tail=previous_review_tail,
            )
        )

        review_system = (
            system_prompt
            + "\n\n"
            + self.get_review_prompt()
        )

        return self.call_groq(
            messages=[
                {
                    "role": "system",
                    "content": compact_text(
                        review_system,
                        MAX_SYSTEM_PROMPT_CHARS,
                    ),
                },
                {
                    "role": "user",
                    "content": review_instruction,
                },
            ],
            output_tokens=REVIEW_BATCH_OUTPUT_TOKENS,
            stage="REVIEW_GROQ_REQUEST",
            batch_number=batch_number,
            event=event,
        )

    # ========================================================
    # REVIEW SYNTHESIS
    # ========================================================

    def synthesize_review(
        self,
        *,
        original_request: str,
        service: str | None,
        review_results: list[str],
        context: str | None,
        event: str | None,
    ) -> str:

        review_text = "\n\n".join(
            f"REVIEW BATCH {index + 1}\n"
            f"{result}"
            for index, result in enumerate(
                review_results
            )
        )

        synthesis_instruction = f"""
CREATE THE FINAL CUSTOMER REVIEW.

SERVICE:
{safe_text(service) or "Not specified"}

ORIGINAL CUSTOMER REQUEST:
{compact_text(original_request, 3000)}

The following are sequential review findings from the same
customer document:

{compact_text(
    review_text,
    REVIEW_SYNTHESIS_INPUT_CHARS,
)}

Create one coherent review result.

IMPORTANT:

- Combine duplicate findings.
- Do not invent findings.
- Do not claim to have reviewed content not supplied.
- Do not ask again for information already present.
- Separate important corrections from minor suggestions.
- Preserve the fact that the underlying document remains
  complete.
- This is a review of the existing document, not a request
  to regenerate the whole document.

If there are no meaningful problems, clearly say that the
document is ready for customer review/approval rather than
inventing defects.

Give the customer a clear, useful result.
"""

        if context:

            synthesis_instruction += (
                "\n\nRELEVANT APPLICATION STATE:\n"
                + compact_text(
                    context,
                    REVIEW_SYNTHESIS_INPUT_CHARS // 3,
                )
            )

        synthesis_instruction = compact_text(
            synthesis_instruction,
            REVIEW_SYNTHESIS_INPUT_CHARS,
        )

        return self.call_groq(
            messages=[
                {
                    "role": "system",
                    "content": (
                        self.build_system_prompt(
                            service=service,
                            context=None,
                        )
                        + "\n\n"
                        + self.get_review_prompt()
                    ),
                },
                {
                    "role": "user",
                    "content": synthesis_instruction,
                },
            ],
            output_tokens=REVIEW_SYNTHESIS_OUTPUT_TOKENS,
            stage="REVIEW_SYNTHESIS_REQUEST",
            event=event,
        )

    # ========================================================
    # COMPLETE DOCUMENT REVIEW
    # ========================================================

    def review_complete_document(
        self,
        *,
        original_request: str,
        service: str | None,
        context: str | None,
        existing_document: str | None = None,
        event: str | None = None,
    ) -> str:

        """
        Intelligent review coordinator.

        The document is obtained from either:

            existing_document

        or, if that argument is not separately supplied,
        from the application context.

        The context is deliberately not blindly compacted before
        extraction because doing so could destroy document data.
        """

        document = safe_text(
            existing_document
        )

        if not document:

            document = (
                self.extract_document_from_context(
                    context
                )
            )

        if not document:

            return (
                "I’m ready to review your document. "
                "I just need the current document to be available "
                "in the workspace before I can inspect it."
            )

        active_service = (
            self.normalize_service(
                service
            )
        )

        # ----------------------------------------------------
        # REVIEW BATCH CREATION
        # ----------------------------------------------------

        batches = (
            self.split_for_review(
                document
            )
        )

        if not batches:

            return (
                "I could not find document content to review."
            )

        if len(batches) > REVIEW_MAX_BATCHES:

            raise AdaResponseError(
                "Document requires more review batches than the "
                "configured safety maximum.",
                stage="REVIEW_BATCHING",
                category="REVIEW_LIMIT",
            )

        print()
        print("=" * 78)
        print(
            "ADA INTELLIGENT DOCUMENT REVIEW STARTED"
        )
        print("=" * 78)
        print(
            "Service:",
            active_service,
        )
        print(
            "Review batches:",
            len(batches),
        )
        print(
            "Review batch character limit:",
            REVIEW_BATCH_CHARS,
        )
        print(
            "Document character count:",
            len(document),
        )
        print("=" * 78)
        print()

        # ----------------------------------------------------
        # SYSTEM PROMPT
        # ----------------------------------------------------

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=None,
            )
        )

        review_results = []

        previous_review_tail = ""

        # ----------------------------------------------------
        # SEQUENTIAL REVIEW
        # ----------------------------------------------------

        for index, document_batch in enumerate(
            batches,
            start=1,
        ):

            print(
                f"Reviewing batch {index} "
                f"of {len(batches)}"
            )

            result = (
                self.review_one_batch(
                    system_prompt=system_prompt,
                    original_request=original_request,
                    service=active_service,
                    batch_number=index,
                    total_batches=len(batches),
                    document_batch=document_batch,
                    previous_review_tail=previous_review_tail,
                    event=event,
                )
            )

            result = safe_text(
                result
            )

            if result:

                review_results.append(
                    result
                )

                previous_review_tail = result[
                    -REVIEW_CONTEXT_TAIL_CHARS:
                ]

        if not review_results:

            raise AdaResponseError(
                "No review result was produced.",
                stage="REVIEW_ASSEMBLY",
                category="REVIEW_GENERATION",
            )

        # ----------------------------------------------------
        # FINAL INTELLIGENT SYNTHESIS
        # ----------------------------------------------------

        final_review = (
            self.synthesize_review(
                original_request=original_request,
                service=active_service,
                review_results=review_results,
                context=context,
                event=event,
            )
        )

        print()
        print(
            "=" * 78
        )
        print(
            "ADA INTELLIGENT DOCUMENT REVIEW COMPLETE"
        )
        print(
            "Review batches processed:",
            len(review_results),
        )
        print(
            "=" * 78
        print()

        return safe_text(
            final_review
        )

    # ========================================================
    # DOCUMENT EXTRACTION FROM APPLICATION CONTEXT
    # ========================================================

    @staticmethod
    def extract_document_from_context(
        context: str | None,
    ) -> str:

        """
        Attempts to locate the existing document in the
        application context without depending on a controller.

        Supported textual context patterns include:

            COMPLETE DOCUMENT:
            ...

            EXISTING DOCUMENT:
            ...

            DOCUMENT CONTENT:
            ...

            ASSEMBLED DOCUMENT:
            ...

        If none of those markers exist, the context itself is
        treated as document content only when it clearly looks
        like document material rather than a small state object.
        """

        context = safe_text(
            context
        )

        if not context:
            return ""

        markers = [
            "COMPLETE DOCUMENT:",
            "EXISTING DOCUMENT:",
            "DOCUMENT CONTENT:",
            "ASSEMBLED DOCUMENT:",
            "CURRENT DOCUMENT:",
        ]

        upper = context.upper()

        for marker in markers:

            position = upper.find(
                marker
            )

            if position >= 0:

                document = context[
                    position + len(marker):
                ].strip()

                if document:

                    return document

        # ----------------------------------------------------
        # JSON-like application context
        # ----------------------------------------------------

        try:

            import json

            parsed = json.loads(
                context
            )

            if isinstance(
                parsed,
                dict,
            ):

                for key in [
                    "complete_document",
                    "existing_document",
                    "document",
                    "document_content",
                    "assembled_document",
                    "current_document",
                    "content",
                ]:

                    value = parsed.get(
                        key
                    )

                    if isinstance(
                        value,
                        str,
                    ) and value.strip():

                        return value.strip()

        except Exception:
            pass

        return ""

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
                event=event_normalized,
            )
        )

    # ========================================================
    # REVIEW RESPONSE
    # ========================================================

    def respond_with_review(
        self,
        *,
        message: str,
        service: str | None,
        event: str | None,
        context: str | None,
    ) -> str:

        """
        Review is deliberately separate from document creation.

        The existing document is inspected in batches.

        Ada is not asked to regenerate the document.
        """

        return (
            self.review_complete_document(
                original_request=message,
                service=service,
                context=context,
                existing_document=None,
                event=self.normalize_event(event),
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

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        messages = (
            self.build_messages(
                system_prompt
            )
        )

        if event:

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "CURRENT APPLICATION EVENT\n"
                        + compact_text(
                            event,
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

        response = (
            self.call_groq(
                messages=messages,
                output_tokens=MAX_OUTPUT_TOKENS,
                stage="NORMAL_GROQ_REQUEST",
                event=event,
            )
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
    # MAIN RESPONSE
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

        if service:
            self.set_service(
                service
            )

        if not message:

            message = (
                "Please continue with the customer's "
                "current request using the available "
                "application state."
            )

        event_normalized = (
            self.normalize_event(
                event
            )
        )

        print()
        print("=" * 78)
        print(
            "ADA RESPONSE START"
        )
        print("=" * 78)
        print(
            "Service:",
            self.service,
        )
        print(
            "Event:",
            event_normalized,
        )
        print(
            "Message characters:",
            len(message),
        )
        print(
            "Model:",
            MODEL,
        )
        print("=" * 78)
        print()

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
                event=event_normalized,
            )

            return client_error_message(
                error
            )

        try:

            active_service = (
                self.normalize_service(
                    self.service
                )
            )

            # =================================================
            # FIRST PRIORITY:
            # REVIEW
            # =================================================
            #
            # Review must be checked before ordinary document
            # generation so that pressing Review does not
            # accidentally trigger a new document.
            #

            if self.is_review_event(
                event_normalized
            ):

                result = (
                    self.respond_with_review(
                        message=message,
                        service=active_service,
                        event=event_normalized,
                        context=context,
                    )
                )

                self.add_history(
                    "user",
                    message,
                )

                self.add_history(
                    "assistant",
                    result,
                )

                return result

            # =================================================
            # SECOND PRIORITY:
            # DOCUMENT CREATION / CORRECTION
            # =================================================

            if event_normalized in DOCUMENT_EVENTS:

                result = (
                    self.respond_with_document(
                        message=message,
                        service=active_service,
                        event=event_normalized,
                        context=context,
                    )
                )

                self.add_history(
                    "user",
                    message,
                )

                self.add_history(
                    "assistant",
                    "[Complete document prepared.]",
                )

                return result

            # =================================================
            # NORMAL INTELLIGENCE
            # =================================================

            return self.respond_normal(
                message=message,
                service=active_service,
                event=event_normalized,
                context=context,
            )

        except Exception as error:

            log_error(
                "ADA RESPONSE FAILED",
                error,
                stage=(
                    error.stage
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "ADA_RESPONSE"
                ),
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "APPLICATION"
                ),
                event=event_normalized,
            )

            return client_error_message(
                error
            )


# ============================================================
# DIAGNOSTIC STARTUP
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
        "INTELLIGENT CREATION + INTELLIGENT REVIEW"
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

    print(
        "Document section output tokens:",
        DOCUMENT_SECTION_OUTPUT_TOKENS,
    )

    print(
        "Review batch characters:",
        REVIEW_BATCH_CHARS,
    )

    print(
        "Review batch output tokens:",
        REVIEW_BATCH_OUTPUT_TOKENS,
    )

    print(
        "Review maximum batches:",
        REVIEW_MAX_BATCHES,
    )

    print()

    try:

        manager = AdaPromptManager()

        print(
            "Prompt Manager:",
            "READY",
        )

        try:

            print(
                "Identity:",
                bool(
                    manager.get_identity_prompt()
                ),
            )

        except Exception as error:

            log_error(
                "IDENTITY PROMPT DIAGNOSTIC FAILED",
                error,
                stage="PROMPT_MANAGER_IDENTITY",
                category="PROMPT_MANAGER",
            )

        try:

            print(
                "Nigerian Context:",
                bool(
                    manager.get_nigerian_context_prompt()
                ),
            )

        except Exception as error:

            log_error(
                "NIGERIAN CONTEXT DIAGNOSTIC FAILED",
                error,
                stage="PROMPT_MANAGER_CONTEXT",
                category="PROMPT_MANAGER",
            )

    except Exception as error:

        log_error(
            "PROMPT MANAGER DIAGNOSTIC FAILED",
            error,
            stage="PROMPT_MANAGER_DIAGNOSTIC",
            category="PROMPT_MANAGER",
        )

    print()

    try:

        billing = BillingManager()

        print(
            "Billing Manager:",
            "READY",
        )

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

    try:

        get_client()

        print(
            "Groq Client:",
            "READY",
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

    print(
        "Ada End-to-End Intelligence:",
        "READY",
    )

    print(
        "Keyword Intent Matching:",
        "DISABLED",
    )

    print(
        "LLM Intent Reasoning:",
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
        "Intelligent Document Review:",
        "ENABLED",
    )

    print(
        "Review Batch Processing:",
        "ENABLED",
    )

    print(
        "Review Synthesis:",
        "ENABLED",
    )

    print(
        "Document Truncation:",
        "DISABLED",
    )

    print(
        "Document Page Deletion:",
        "DISABLED",
    )

    print(
        "Repeated Information Requests:",
        "DISCOURAGED BY INTELLIGENCE",
    )

    print(
        "Groq Rate-Limit Diagnostics:",
        "ENABLED",
    )

    print(
        "Provider Error Diagnostics:",
        "ENABLED",
    )

    print()
    print("=" * 78)
    print(
        "ADA INTELLIGENCE ENGINE READY"
    )
    print("=" * 78)
