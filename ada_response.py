"""
Naija Pocket Business Center
Ada Response Engine

END-TO-END LLM INTELLIGENCE LAYER

This module is Ada's intelligence layer.

The selected service provides context.
It does NOT create a scripted keyword workflow.

The application remains authoritative for all state.

Ada reasons about:
- customer messages
- selected service
- application state
- document state
- workflow events
- supplied document content

REVIEW ARCHITECTURE

Customer
↓
Workspace
↓
AdaResponse
↓
Review intelligence
↓
Controlled document batches
↓
LLM review of EVERY batch
↓
Application retains EVERY result
↓
Complete review
↓
Workspace

IMPORTANT DOCUMENT RULE

A document is one document even when it is processed in
multiple LLM requests.

Groq request limits are execution limits only.

They must NEVER cause Ada to:
- delete pages
- truncate the customer's document
- summarize instead of reviewing
- invent missing content
- silently stop after an arbitrary batch count
- claim the document is complete when it was not supplied

If the application cannot safely process the complete document,
Ada must report that the application needs the missing content.
It must NOT pretend the document was fully reviewed.

BILLING

BillingManager remains authoritative for:
- service names
- prices
- billing types

PROMPTS

AdaPromptManager remains responsible for:
- identity
- Nigerian context
- service guidance
- writing style

APPLICATION STATE

Application state remains authoritative for:
- selected service
- customer information
- uploaded files
- document content
- document pages
- review state
- approval state
- payment state
- delivery state
- download state

The LLM must never invent application state.
"""

from future import annotations

import os
import re
import traceback
from typing import Any

============================================================

GROQ

============================================================

try:
from groq import Groq
except ImportError:
Groq = None

============================================================

APPLICATION COMPONENTS

============================================================

from ada_prompt_manager import AdaPromptManager
from billing_manager import BillingManager

============================================================

CONFIGURATION

============================================================

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
os.getenv("ADA_EXPOSE_ERRORS")
or "false"
).strip().lower() in {
"1",
"true",
"yes",
"on",
}

============================================================

NORMAL CONVERSATION LIMITS

============================================================

MAX_SYSTEM_PROMPT_CHARS = 18000
MAX_CENTRAL_PROMPT_CHARS = 12000
MAX_INTELLIGENCE_PROMPT_CHARS = 10000
MAX_CONTEXT_CHARS = 12000

MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_MESSAGE_CHARS = 1800

MAX_EVENT_CHARS = 1500
MAX_USER_MESSAGE_CHARS = 5000

MAX_OUTPUT_TOKENS = 900

============================================================

REVIEW LIMITS

============================================================

REVIEW_BATCH_INPUT_CHARS = 10000
REVIEW_BATCH_OUTPUT_TOKENS = 900
REVIEW_BATCH_CONTEXT_CHARS = 3000

REVIEW_MIN_BATCH_CHARS = 200

This is NOT used to truncate a document.

It is only a protection against an obviously broken

infinite-loop condition.

REVIEW_MAX_BATCHES_SAFETY = 10000

============================================================

DOCUMENT GENERATION

============================================================

DOCUMENT_SECTION_OUTPUT_TOKENS = 700
DOCUMENT_SECTION_INSTRUCTION_CHARS = 4500
DOCUMENT_RECENT_CONTEXT_CHARS = 2200

DOCUMENT_MAX_SECTIONS = 30
DOCUMENT_MIN_SECTION_CHARS = 250

============================================================

EVENTS

============================================================

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
}

DOCUMENT_EVENTS = (
DOCUMENT_CREATION_EVENTS
| DOCUMENT_CORRECTION_EVENTS
)

ALL_REVIEW_EVENTS = (
REVIEW_EVENTS
| {"review_correction"}
)

============================================================

ERROR

============================================================

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

============================================================

GROQ CLIENT

============================================================

_client = None

def get_client():

global _client

if _client is not None:
    return _client

if Groq is None:
    raise AdaResponseError(
        "The groq Python package is not installed.",
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

============================================================

PUBLIC HELPERS

============================================================

def get_ada_model() -> str:
return MODEL

def is_configured() -> bool:
return (
Groq is not None
and bool(API_KEY)
)

============================================================

TEXT HELPERS

============================================================

def safe_text(value: Any) -> str:

if value is None:
    return ""

if isinstance(value, str):
    return value.strip()

return str(value).strip()

def compact_text(
text: str | None,
maximum: int,
) -> str:

text = safe_text(text)

if not text:
    return ""

if len(text) <= maximum:
    return text

if maximum < 300:
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

def diagnostic_value(
value: Any,
maximum: int = 1500,
) -> str:

return compact_text(
    safe_text(value),
    maximum,
)

============================================================

HISTORY

============================================================

def compact_history(
history: list[dict[str, str]],
) -> list[dict[str, str]]:

result = []

for item in history[
    -MAX_HISTORY_MESSAGES:
]:

    role = safe_text(
        item.get("role")
    )

    content = compact_text(
        item.get("content"),
        MAX_HISTORY_MESSAGE_CHARS,
    )

    if role not in {
        "system",
        "user",
        "assistant",
    }:
        continue

    if not content:
        continue

    result.append(
        {
            "role": role,
            "content": content,
        }
    )

return result

============================================================

APPLICATION CONTEXT

============================================================

def prepare_application_context(
context: str | None,
) -> str:

return compact_text(
    safe_text(context),
    MAX_CONTEXT_CHARS,
)

============================================================

ERROR HELPERS

============================================================

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
batch_number: int | None = None,
total_batches: int | None = None,
event: str | None = None,
) -> None:

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
    category
    or classify_groq_error(error),
)

print(
    "Exception:",
    type(error).__name__,
)

status = get_error_status_code(
    error
)

if status is not None:
    print(
        "HTTP status:",
        status,
    )

if batch_number is not None:

    if total_batches is not None:
        print(
            "Review progress:",
            f"{batch_number}/{total_batches}",
        )
    else:
        print(
            "Review batch:",
            batch_number,
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
    diagnostic_value(error),
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

category = (
    error.category
    if isinstance(
        error,
        AdaResponseError,
    )
    else classify_groq_error(error)
)

text = diagnostic_value(
    error,
    1200,
)

if status is not None:

    return (
        "Technical error detected.\n\n"
        f"Category: {category}\n"
        f"HTTP status: {status}\n"
        f"Model: {MODEL}\n"
        f"Error: {text}"
    )

return (
    "Technical error detected.\n\n"
    f"Category: {category}\n"
    f"Error: {text}"
)

============================================================

ADA RESPONSE

============================================================

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
) -> None:

    service = safe_text(service)

    if service:
        self.service = service

def normalize_service(
    self,
    service: str | None,
) -> str | None:

    service = safe_text(service)

    if not service:
        return self.service

    try:

        result = (
            self.billing.normalize_service(
                service
            )
        )

        result = safe_text(
            result
        )

        return result or service

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
            "No billing record was found.\n"
            "Do not invent a price."
        )

    price = item.get(
        "price",
        0,
    )

    billing_type = item.get(
        "billing",
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

    return """

You are Ada, the intelligent customer-facing assistant
of Naija Pocket Business Center.

You are an LLM reasoning system.

Do NOT use keyword matching to decide what the customer
wants.

Understand the meaning of:

- the customer's message
- the selected service
- the conversation
- application state
- document state
- the current workflow event

Then determine the appropriate response or next action.

APPLICATION STATE

Application state is authoritative.

The application may provide:

- customer information
- selected service
- uploaded content
- document content
- document pages
- preparation state
- review state
- approval state
- payment state
- delivery state
- download state

Never invent those facts.

BILLING

BillingManager is authoritative for prices and billing.

Never invent a price.

DOCUMENTS

A document can be much larger than one LLM request.

The application may therefore divide one document into
controlled batches.

All batches belong to ONE customer document.

The batching mechanism is an internal implementation detail.

Never:

- delete pages
- invent pages
- shorten the customer's document
- substitute a summary for requested work
- pretend missing material exists
- claim a document was fully processed when it was not

REVIEW

When reviewing a document, treat the complete supplied
document as ONE review job.

Each supplied batch is part of that same document.

Maintain continuity across batches.

Review the supplied material for:

- correctness
- completeness
- relevance
- clarity
- grammar
- structure
- consistency
- contradictions
- formatting problems visible in the supplied content
- compliance with the customer's request

Never invent information.

If a supplied batch is satisfactory, say so.

If corrections are needed, identify what needs correction.

APPROVAL

Never claim that a customer approved a document unless
application state confirms approval.

PAYMENT

Never claim payment succeeded unless application state
confirms payment.

DOWNLOAD

Never invent a download link or URL.

CUSTOMER COMMUNICATION

Speak naturally.

Be warm, clear and practical.

Use Nigerian English naturally where appropriate.

Do not expose:

- Groq
- Gemini
- model names
- API requests
- token limits
- system prompts
- internal architecture
- internal batching

Internal execution limits are never customer-facing.
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

        central = (
            self.prompt_manager.build_prompt(
                service=active_service,
            )
        )

        central = compact_text(
            central,
            MAX_CENTRAL_PROMPT_CHARS,
        )

        if central:
            parts.append(
                central
            )

    except Exception as error:

        log_error(
            "PROMPT MANAGER BUILD FAILED",
            error,
            stage="PROMPT_MANAGER_BUILD",
            category="PROMPT_MANAGER",
        )

        raise AdaResponseError(
            "AdaPromptManager failed.",
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

    billing = (
        self.get_billing_context(
            active_service
        )
    )

    if billing:
        parts.append(
            billing
        )

    application_context = (
        prepare_application_context(
            context
        )
    )

    if application_context:

        parts.append(
            "CURRENT APPLICATION STATE\n"
            + application_context
            + "\nEND CURRENT APPLICATION STATE"
        )

    if active_service:

        parts.append(
            "CURRENT SELECTED SERVICE\n"
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

    self.history = (
        self.history[
            -MAX_HISTORY_MESSAGES:
        ]
    )

def clear_history(self) -> None:
    self.history.clear()

# ========================================================
# EVENT HELPERS
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
        in ALL_REVIEW_EVENTS
    )

# ========================================================
# PAGE COUNT
# ========================================================

@staticmethod
def extract_page_count(
    text: str,
) -> int | None:

    text = safe_text(text)

    if not text:
        return None

    patterns = [
        (
            r"(?:number\s+of\s+pages|page\s+count|pages)"
            r"\s*(?:is|=|:|-)?\s*(\d+)"
        ),
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
                value = int(match)

            except Exception:
                continue

            if 1 <= value <= 10000:
                return value

    return None

# ========================================================
# DOCUMENT BATCHING
# ========================================================

@staticmethod
def split_review_batches(
    document: str,
    batch_chars: int = REVIEW_BATCH_INPUT_CHARS,
) -> list[str]:

    """
    Split a document into batches without discarding
    document content.

    IMPORTANT:
    This function has NO arbitrary "100 batch" truncation.

    If a document is larger than expected, every portion
    continues to be processed.
    """

    document = safe_text(
        document
    )

    if not document:
        return []

    if batch_chars <= 0:
        raise ValueError(
            "batch_chars must be greater than zero."
        )

    if len(document) <= batch_chars:
        return [document]

    batches = []

    start = 0
    length = len(document)

    while start < length:

        end = min(
            start + batch_chars,
            length,
        )

        if end < length:

            paragraph_break = (
                document.rfind(
                    "\n\n",
                    start,
                    end,
                )
            )

            line_break = (
                document.rfind(
                    "\n",
                    start,
                    end,
                )
            )

            sentence_break = (
                document.rfind(
                    ". ",
                    start,
                    end,
                )
            )

            candidate = max(
                paragraph_break,
                line_break,
                sentence_break,
            )

            if candidate > start + REVIEW_MIN_BATCH_CHARS:
                if candidate == paragraph_break:
                    end = candidate + 2

                elif candidate == line_break:
                    end = candidate + 1

                else:
                    end = candidate + 2

        chunk = document[
            start:end
        ].strip()

        if chunk:
            batches.append(
                chunk
            )

        if end <= start:
            raise AdaResponseError(
                "Document batching could not advance.",
                stage="DOCUMENT_BATCHING",
                category="DOCUMENT_INTEGRITY",
            )

        start = end

        if len(batches) > REVIEW_MAX_BATCHES_SAFETY:

            raise AdaResponseError(
                "Document exceeded the safe batching guard.",
                stage="DOCUMENT_BATCHING",
                category="DOCUMENT_SIZE",
            )

    return batches

# ========================================================
# REVIEW DOCUMENT EXTRACTION
# ========================================================

@staticmethod
def extract_review_document(
    context: str | None,
) -> str:

    """
    Extract the actual document BEFORE any generic
    application-context compaction is performed.

    This is critical.

    The document itself must never be passed through
    compact_text(), because doing so would destroy the
    very material Ada is supposed to review.
    """

    context = safe_text(
        context
    )

    if not context:
        return ""

    patterns = [
        r"DOCUMENT_CONTENT\s*:\s*(.*)",
        r"DOCUMENT_TEXT\s*:\s*(.*)",
        r"FULL_DOCUMENT\s*:\s*(.*)",
        r"REVIEW_DOCUMENT\s*:\s*(.*)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            context,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:

            document = safe_text(
                match.group(1)
            )

            if document:
                return document

    return ""

# ========================================================
# REVIEW PROMPT
# ========================================================

def build_review_prompt(
    self,
    *,
    service: str | None,
    batch_number: int,
    total_batches: int,
    document_batch: str,
    previous_review: str = "",
    customer_request: str = "",
) -> str:

    """
    Build one review request.

    Progress is always one-based:

        1/1
        1/2
        2/2

    There is deliberately no 0/1 customer-review state.
    """

    progress = (
        f"{batch_number}/{total_batches}"
    )

    return f"""

REVIEW THE CUSTOMER'S EXISTING DOCUMENT.

This is ONE document being reviewed in controlled
processing batches.

SERVICE:
{safe_text(service) or "Not specified"}

CUSTOMER'S REVIEW REQUEST:
{
safe_text(customer_request)
or
"Review the document carefully and identify anything that needs attention."
}

CURRENT REVIEW PROGRESS:
{progress}

DOCUMENT CONTENT FOR THIS REVIEW PART:
{document_batch}

PREVIOUS REVIEW CONTEXT:
{
previous_review
or
"This is the first review part."
}

REVIEW INSTRUCTIONS

Review the supplied content intelligently.

Check for:

1. correctness
2. completeness
3. relevance
4. structure
5. clarity
6. grammar
7. consistency
8. contradictions
9. formatting or structure problems visible in the text
10. compliance with the customer's request

Do not invent facts.

Do not rewrite the entire document unless the customer
specifically requested rewriting.

Do not treat this review part as a separate document.

Maintain continuity with the previous review context.

If this part is satisfactory, clearly state that.

If corrections are required, identify exactly what needs
attention.

Return useful review findings.

Do not mention internal implementation details.
"""

# ========================================================
# GROQ REQUEST
# ========================================================

def call_groq(
    self,
    *,
    messages: list[dict[str, str]],
    output_tokens: int,
    stage: str,
    batch_number: int | None = None,
    total_batches: int | None = None,
    event: str | None = None,
) -> str:

    try:

        client = get_client()

    except Exception as error:

        log_error(
            "GROQ CLIENT UNAVAILABLE",
            error,
            stage=(
                error.stage
                if isinstance(
                    error,
                    AdaResponseError,
                )
                else "CLIENT_INITIALIZATION"
            ),
            category=(
                error.category
                if isinstance(
                    error,
                    AdaResponseError,
                )
                else "GROQ_CLIENT"
            ),
            batch_number=batch_number,
            total_batches=total_batches,
            event=event,
        )

        raise

    print()
    print("=" * 78)
    print("ADA GROQ REQUEST")
    print("=" * 78)
    print("Stage:", stage)
    print("Model:", MODEL)
    print("Messages:", len(messages))
    print(
        "Requested output tokens:",
        output_tokens,
    )

    if batch_number is not None:

        if total_batches is not None:
            print(
                "Review progress:",
                f"{batch_number}/{total_batches}",
            )
        else:
            print(
                "Batch:",
                batch_number,
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
            "GROQ REQUEST FAILED",
            error,
            stage=stage,
            category=category,
            batch_number=batch_number,
            total_batches=total_batches,
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
        "Groq response received."
    )
    print("=" * 78)
    print()

    return content

# ========================================================
# COMPLETE DOCUMENT REVIEW
# ========================================================

def review_document(
    self,
    *,
    document: str,
    service: str | None = None,
    context: str | None = None,
    customer_request: str = "",
    event: str | None = "review",
) -> str:

    document = safe_text(
        document
    )

    if not document:

        return (
            "There is no document content available "
            "for review yet."
        )

    active_service = (
        self.normalize_service(
            service
        )
    )

    # IMPORTANT:
    # The full document is used for batching.
    # It is NOT compacted to MAX_CONTEXT_CHARS.
    #
    # Only ordinary application context is compacted.

    system_prompt = (
        self.build_system_prompt(
            service=active_service,
            context=None,
        )
    )

    batches = (
        self.split_review_batches(
            document
        )
    )

    if not batches:

        return (
            "There is no document content available "
            "for review yet."
        )

    total_batches = len(
        batches
    )

    print()
    print("=" * 78)
    print("ADA INTELLIGENCE REVIEW STARTED")
    print("=" * 78)
    print(
        "Document characters:",
        len(document),
    )
    print(
        "Review batches:",
        total_batches,
    )
    print(
        "Service:",
        active_service,
    )
    print("=" * 78)
    print()

    review_results = []

    previous_review = ""

    for index, batch in enumerate(
        batches,
        start=1,
    ):

        # ------------------------------------------------
        # One-based progress.
        #
        # A one-batch document is:
        #     1/1
        #
        # A two-batch document is:
        #     1/2
        #     2/2
        #
        # There is no 0/1 review request.
        # ------------------------------------------------

        review_prompt = (
            self.build_review_prompt(
                service=active_service,
                batch_number=index,
                total_batches=total_batches,
                document_batch=batch,
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
                "content": review_prompt,
            },
        ]

        try:

            result = self.call_groq(
                messages=messages,
                output_tokens=REVIEW_BATCH_OUTPUT_TOKENS,
                stage="REVIEW_BATCH",
                batch_number=index,
                total_batches=total_batches,
                event=event,
            )

        except Exception as error:

            log_error(
                "REVIEW BATCH FAILED",
                error,
                stage=(
                    error.stage
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "REVIEW_BATCH"
                ),
                category=(
                    error.category
                    if isinstance(
                        error,
                        AdaResponseError,
                    )
                    else "DOCUMENT_REVIEW"
                ),
                batch_number=index,
                total_batches=total_batches,
                event=event,
            )

            raise

        result = safe_text(
            result
        )

        if not result:

            raise AdaResponseError(
                (
                    "Review batch returned empty content "
                    f"at {index}/{total_batches}."
                ),
                stage="REVIEW_BATCH_EMPTY",
                category="DOCUMENT_REVIEW",
            )

        # ------------------------------------------------
        # EVERY result is retained.
        # ------------------------------------------------

        review_results.append(
            (
                f"Review of document part "
                f"{index}/{total_batches}:\n\n"
                f"{result}"
            )
        )

        # Only the previous REVIEW RESULT is compacted.
        # The actual customer document is never compacted.
        previous_review = compact_text(
            result,
            REVIEW_BATCH_CONTEXT_CHARS,
        )

        print(
            "Review batch completed:",
            f"{index}/{total_batches}",
        )

    # ----------------------------------------------------
    # Every batch must have a result.
    # ----------------------------------------------------

    if len(review_results) != total_batches:

        raise AdaResponseError(
            (
                "The review did not produce a result "
                "for every document batch."
            ),
            stage="REVIEW_COMPLETENESS_CHECK",
            category="DOCUMENT_INTEGRITY",
        )

    complete_review = (
        "\n\n".join(
            review_results
        ).strip()
    )

    if not complete_review:

        return (
            "The document was received, but I could "
            "not produce a review result."
        )

    print()
    print("=" * 78)
    print("ADA INTELLIGENCE REVIEW COMPLETE")
    print("=" * 78)
    print(
        "Batches supplied:",
        total_batches,
    )
    print(
        "Batches reviewed:",
        len(review_results),
    )
    print(
        "Document characters preserved:",
        len(document),
    )
    print("=" * 78)
    print()

    return complete_review

# ========================================================
# DOCUMENT GENERATION INSTRUCTION
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

        label = (
            f"SECTION {section_number} "
            f"OF {total_sections}"
        )

    else:

        label = (
            f"SECTION {section_number}"
        )

    action = (
        "Revise the existing customer document."
        if correction
        else
        "Prepare the customer's requested document."
    )

    return compact_text(
        f"""

{action}

{label}

SERVICE:
{safe_text(service) or "Not specified"}

CUSTOMER REQUEST:
{compact_text(original_request, 3500)}

Generate ONLY the document content for this section.

Rules:

- preserve customer facts
- do not invent facts
- do not summarize
- do not explain the generation process
- do not mention internal limits
- continue naturally from previous material
- do not repeat completed material unnecessarily

PREVIOUS SECTION CONTEXT:
{compact_text(
previous_tail,
DOCUMENT_RECENT_CONTEXT_CHARS
)}
""",
DOCUMENT_SECTION_INSTRUCTION_CHARS,
)

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

    system_prompt = (
        self.build_system_prompt(
            service=active_service,
            context=context,
        )
    )

    requested_pages = (
        self.extract_page_count(
            original_request
        )
    )

    maximum_sections = (
        requested_pages
        if requested_pages is not None
        else DOCUMENT_MAX_SECTIONS
    )

    sections = []

    previous_tail = ""

    if existing_work:

        previous_tail = safe_text(
            existing_work
        )[
            -DOCUMENT_RECENT_CONTEXT_CHARS:
        ]

    for section_number in range(
        1,
        maximum_sections + 1,
    ):

        instruction = (
            self.build_document_section_instruction(
                original_request=original_request,
                service=active_service,
                section_number=section_number,
                total_sections=requested_pages,
                previous_tail=previous_tail,
                correction=correction,
            )
        )

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": instruction,
            },
        ]

        section = self.call_groq(
            messages=messages,
            output_tokens=DOCUMENT_SECTION_OUTPUT_TOKENS,
            stage="DOCUMENT_SECTION",
            batch_number=section_number,
            total_batches=(
                requested_pages
                if requested_pages is not None
                else maximum_sections
            ),
            event=event,
        )

        section = safe_text(
            section
        )

        if not section:

            raise AdaResponseError(
                "Document section returned empty content.",
                stage="DOCUMENT_SECTION_EMPTY",
                category="DOCUMENT_GENERATION",
            )

        sections.append(
            section
        )

        previous_tail = section[
            -DOCUMENT_RECENT_CONTEXT_CHARS:
        ]

        if requested_pages is not None:

            if section_number >= requested_pages:
                break

        else:

            if (
                len(section)
                < DOCUMENT_MIN_SECTION_CHARS
            ):
                break

    return "\n\n".join(
        sections
    ).strip()

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

    response = self.call_groq(
        messages=messages,
        output_tokens=MAX_OUTPUT_TOKENS,
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

    if service:

        self.set_service(
            service
        )

    active_service = (
        self.normalize_service(
            self.service
        )
    )

    event_normalized = (
        self.normalize_event(
            event
        )
    )

    print()
    print("=" * 78)
    print("ADA RESPONSE")
    print("=" * 78)
    print(
        "Service:",
        active_service,
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

        # ------------------------------------------------
        # REVIEW
        # ------------------------------------------------

        if self.is_review_event(
            event_normalized
        ):

            # CRITICAL:
            # Extract the complete document FIRST.
            #
            # Do not call prepare_application_context()
            # before this point because that function is
            # intentionally allowed to compact ordinary
            # application context.
            review_document = (
                self.extract_review_document(
                    context
                )
            )

            if review_document:

                result = (
                    self.review_document(
                        document=review_document,
                        service=active_service,
                        context=None,
                        customer_request=message,
                        event=event_normalized,
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

            # The application has not supplied the actual
            # document. Ada must NOT invent one.

            return self.respond_normal(
                message=message,
                service=active_service,
                event=event_normalized,
                context=context,
            )

        # ------------------------------------------------
        # DOCUMENT CREATION
        # ------------------------------------------------

        if event_normalized in DOCUMENT_EVENTS:

            result = (
                self.generate_complete_document(
                    original_request=message,
                    service=active_service,
                    context=context,
                    correction=(
                        event_normalized
                        in DOCUMENT_CORRECTION_EVENTS
                    ),
                    event=event_normalized,
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

        # ------------------------------------------------
        # NORMAL INTELLIGENCE
        # ------------------------------------------------

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

============================================================

DIAGNOSTIC STARTUP

============================================================

if name == "main":

print()
print("=" * 78)
print("NAIJA POCKET BUSINESS CENTER")
print("ADA END-TO-END INTELLIGENCE ENGINE")
print("=" * 78)
print()

print(
    "Model:",
    get_ada_model(),
)

print(
    "Groq package:",
    "READY"
    if Groq is not None
    else "MISSING",
)

print(
    "Groq API key:",
    "CONFIGURED"
    if API_KEY
    else "MISSING",
)

print(
    "Ada intelligence:",
    "ENABLED",
)

print(
    "Keyword decision workflow:",
    "DISABLED",
)

print(
    "LLM review intelligence:",
    "ENABLED",
)

print(
    "Review batch coordination:",
    "ENABLED",
)

print(
    "One-based review progress:",
    "ENABLED",
)

print(
    "Review 0/1 state:",
    "DISABLED",
)

print(
    "Complete document preservation:",
    "ENABLED",
)

print(
    "Review document truncation:",
    "DISABLED",
)

print(
    "Review arbitrary batch truncation:",
    "DISABLED",
)

print(
    "Document sequential generation:",
    "ENABLED",
)

print(
    "Normal context compaction:",
    "ENABLED",
)

print(
    "Customer document compaction:",
    "DISABLED",
)

print(
    "Customer document truncation:",
    "DISABLED",
)

print(
    "Review batch input:",
    f"{REVIEW_BATCH_INPUT_CHARS} characters",
)

print(
    "Review batch output:",
    f"{REVIEW_BATCH_OUTPUT_TOKENS} tokens",
)

print()
print("=" * 78)
print("DIAGNOSTIC INITIALIZATION COMPLETE")
print("=" * 78)

"""
