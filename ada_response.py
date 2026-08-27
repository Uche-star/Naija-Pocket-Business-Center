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


IMPORTANT REVIEW ARCHITECTURE
-----------------------------
A customer's complete document must NEVER be destroyed
or shortened merely to satisfy an LLM token limit.

Review is a document operation.

Therefore:

    COMPLETE DOCUMENT
          ↓
    APPLICATION STORAGE
          ↓
    REVIEW PAGE
          ↓
    ALL PAGES DISPLAYED

Groq is used for intelligence/reasoning about the request.
Groq is NOT used as the storage mechanism for the complete
document.

The application must be able to retain and display the
complete document independently of the LLM request size.

TOKEN CONTROL
-------------
Token control applies to information sent to Groq.

It must NOT remove pages from the customer's document.

It must NOT truncate the customer's supplied document.

It must NOT replace a complete document with a summary.

It must NOT weaken the service intelligence.

It must NOT remove important instructions from the
AdaPromptManager.

For normal conversation, only the necessary recent
conversation is supplied.

For large document/review operations, the application
should provide the relevant state or document information
in controlled portions rather than sending an unlimited
historical payload.

The intelligence remains LLM-based and reasoning-driven.
"""


from __future__ import annotations

import os
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
# TOKEN CONTROL
#
# These limits control LLM INPUT SIZE.
#
# They DO NOT control document storage.
#
# They DO NOT delete document pages.
#
# They DO NOT shorten the document that Review must display.
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
# REVIEW / DOCUMENT INFORMATION
#
# These are deliberately separate from normal prompt limits.
#
# A complete document may be larger than what is suitable for
# one LLM request.
#
# The document must therefore remain in application state.
# ============================================================

DOCUMENT_STATE_KEYS = {
    "document",
    "document_content",
    "document_text",
    "pages",
    "page",
    "page_count",
    "review",
    "review_content",
    "review_pages",
    "uploaded_document",
    "uploaded_content",
    "source_document",
}


# ============================================================
# GROQ CLIENT
# ============================================================

_client = None


def get_client():

    global _client

    if _client is not None:
        return _client

    if Groq is None:
        return None

    if not API_KEY:
        return None

    _client = Groq(
        api_key=API_KEY
    )

    return _client


# ============================================================
# PUBLIC HELPERS
# ============================================================

def get_ada_model() -> str:
    return MODEL


def is_configured() -> bool:
    return get_client() is not None


# ============================================================
# SAFE TEXT NORMALIZATION
# ============================================================

def safe_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


# ============================================================
# PROMPT COMPACTION
# ============================================================

def compact_text(
    text: str | None,
    maximum: int,
) -> str:
    """
    Compact LLM instructions safely.

    IMPORTANT:
    This function is intended for prompts/instructions.

    It must NOT be used to truncate the customer's actual
    document content before the document is displayed.

    The first and last sections are retained so that both
    general instructions and service-specific instructions
    have a chance of remaining available.
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
        "[INSTRUCTION COMPACTION: some non-essential "
        "middle prompt text was omitted from this LLM "
        "request. Customer document content is not "
        "stored or deleted by this operation.]\n\n"
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
# HISTORY CONTROL
# ============================================================

def compact_history(
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Return only the most recent useful conversation.

    Conversation history is deliberately limited because
    application state is authoritative for current workflow
    information.

    This does NOT affect the stored document.
    """

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
    """
    Prepare application state for an LLM request.

    The context sent to Groq is intentionally bounded.

    This function does NOT represent the customer's complete
    document.

    Complete document data must remain under application
    control so the Review page can display every page.
    """

    context = safe_text(context)

    if not context:
        return ""

    return compact_text(
        context,
        MAX_CONTEXT_CHARS,
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

            print(
                "SERVICE NORMALIZATION WARNING:",
                type(error).__name__,
                str(error),
            )

        return service

    # ========================================================
    # BILLING FACTS
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

            print(
                "BILLING LOOKUP WARNING:",
                type(error).__name__,
                str(error),
            )

            return ""

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

        except Exception:

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
    # END-TO-END INTELLIGENCE
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

The customer may communicate using:
- Nigerian English
- informal English
- Pidgin
- imperfect English
- short messages
- follow-up corrections

Understand the meaning rather than requiring perfect
wording.

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

If the application already contains enough information,
do not repeatedly ask for information that has already
been supplied.

If a service form has supplied the required information,
use it.

REVIEW
------
Review is a document operation.

The complete customer document may contain multiple
pages.

Never assume that the entire document should be returned
inside an LLM response.

Never delete, omit, invent, merge, or shorten document
pages merely because an LLM request has a token limit.

The application is responsible for retaining and displaying
the complete document.

When the application indicates that Review has been called,
reason about the review request using the available state,
but do not pretend that a page was removed merely because
it was not included in the LLM prompt.

If the application supplies a page count or review state,
respect it.

If the application confirms that all pages are available,
do not claim that some pages are missing.

DOCUMENT INTEGRITY
------------------
Customer document content must be preserved by the
application.

LLM token control is NOT permission to destroy document
content.

Never invent a page.

Never claim a page exists unless application state confirms
it.

Never claim a page was deleted unless application state
confirms it.

Never invent a download URL.

PAYMENT
-------
Never claim payment has succeeded unless application state
confirms payment.

APPROVAL
--------
Never claim customer approval unless application state
confirms approval.

DELIVERY
--------
Never claim delivery has occurred unless application state
confirms delivery.

DOWNLOAD
--------
Never invent a download link.

If download state is not confirmed, do not say that a file
is available for download.

CUSTOMER RESPONSE
-----------------
Answer the customer directly.

Be warm, clear, practical, professional, and concise.

Understand Nigerian context.

Do not unnecessarily expose internal implementation.

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
    # BUILD SYSTEM PROMPT
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
        # EXISTING CENTRAL PROMPT
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

            print()
            print(
                "PROMPT MANAGER ERROR:",
                type(error).__name__,
                str(error),
            )

            traceback.print_exc()

        # ----------------------------------------------------
        # END-TO-END INTELLIGENCE
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
        # BILLING FACTS
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
                + "\n\n"
                "END CURRENT APPLICATION STATE"
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
        # FINAL SYSTEM PROMPT
        # ----------------------------------------------------

        system_prompt = "\n\n".join(
            part
            for part in parts
            if part
        )

        system_prompt = compact_text(
            system_prompt,
            MAX_SYSTEM_PROMPT_CHARS,
        )

        return system_prompt

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
    # REVIEW EVENT DETECTION
    #
    # This does NOT decide what the customer wants.
    #
    # It only recognizes an application state label so that
    # the event can receive a dedicated instruction.
    # ========================================================

    @staticmethod
    def is_review_event(
        event: str | None,
    ) -> bool:

        event = safe_text(event).lower()

        if not event:
            return False

        review_events = {
            "review",
            "review_requested",
            "review_called",
            "open_review",
            "review_page",
            "review_document",
        }

        return event in review_events

    # ========================================================
    # REVIEW INSTRUCTION
    # ========================================================

    def build_review_instruction(
        self,
        context: str | None = None,
    ) -> str:
        """
        Adds review-specific reasoning instructions.

        IMPORTANT:
        This does not insert the complete document into the
        Groq request.

        The application must retain and display the complete
        document independently.
        """

        instruction = """
CURRENT APPLICATION EVENT: REVIEW

The customer has entered the Review stage.

Review the current application state carefully.

The document may contain multiple pages.

The complete document belongs to the customer and must
remain intact.

Do not shorten, summarize, remove, or invent document pages
because of the LLM request limit.

The Review page is responsible for displaying the complete
document supplied by the application.

Your role here is to reason about the customer's review
request and provide the appropriate customer-facing response.

If the application confirms that all document pages are
available, treat the complete document as available.

Do not claim that pages are missing unless the application
state explicitly says they are missing.

Do not generate a fake download link.

Do not claim approval or payment unless the application
state confirms it.
"""

        return compact_text(
            instruction,
            4000,
        )

    # ========================================================
    # RESPOND
    # ========================================================

    def respond(
        self,
        message: str,
        service: str | None = None,
        event: str | None = None,
        context: str | None = None,
    ) -> str:

        message = safe_text(message)

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

        client = get_client()

        if client is None:

            return (
                "The network connection is slow or unavailable. "
                "Please try again."
            )

        # ----------------------------------------------------
        # SYSTEM PROMPT
        # ----------------------------------------------------

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        # ----------------------------------------------------
        # MESSAGES
        # ----------------------------------------------------

        messages = (
            self.build_messages(
                system_prompt
            )
        )

        # ----------------------------------------------------
        # APPLICATION EVENT
        # ----------------------------------------------------

        current_event = safe_text(event)

        if current_event:

            event_instruction = (
                "CURRENT APPLICATION EVENT\n"
                + compact_text(
                    current_event,
                    MAX_EVENT_CHARS,
                )
            )

            messages.append(
                {
                    "role": "system",
                    "content": event_instruction,
                }
            )

        # ----------------------------------------------------
        # REVIEW-SPECIFIC REASONING
        # ----------------------------------------------------

        if self.is_review_event(
            current_event
        ):

            messages.append(
                {
                    "role": "system",
                    "content": (
                        self.build_review_instruction(
                            context=context
                        )
                    ),
                }
            )

        # ----------------------------------------------------
        # CUSTOMER MESSAGE
        # ----------------------------------------------------

        messages.append(
            {
                "role": "user",
                "content": compact_text(
                    message,
                    MAX_USER_MESSAGE_CHARS,
                ),
            }
        )

        # ----------------------------------------------------
        # DEBUG INFORMATION
        # ----------------------------------------------------

        print()
        print("-" * 70)
        print("ADA RESPONSE REQUEST")
        print("-" * 70)
        print(
            "Model:",
            MODEL,
        )
        print(
            "Service:",
            active_service,
        )
        print(
            "Event:",
            current_event or None,
        )
        print(
            "History messages:",
            len(
                compact_history(
                    self.history
                )
            ),
        )
        print(
            "Review event:",
            self.is_review_event(
                current_event
            ),
        )
        print("-" * 70)

        # ----------------------------------------------------
        # GROQ REQUEST
        # ----------------------------------------------------

        try:

            response = (
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
            )

            reply = ""

            if response.choices:

                choice = (
                    response.choices[0]
                )

                if choice.message:

                    reply = safe_text(
                        choice.message.content
                    )

            if not reply:

                return (
                    "I could not get a response right now. "
                    "Please try again."
                )

            # ------------------------------------------------
            # STORE ONLY CONVERSATION
            #
            # The document itself is NOT stored here as chat
            # history.
            # ------------------------------------------------

            self.add_history(
                "user",
                message,
            )

            self.add_history(
                "assistant",
                reply,
            )

            print()
            print(
                "AdaResponse returned successfully."
            )
            print()

            return reply

        except Exception as error:

            print()
            print("=" * 70)
            print("ADA RESPONSE ERROR")
            print("=" * 70)
            print(
                "Error type:",
                type(error).__name__,
            )
            print(
                "Error:",
                str(error),
            )
            print("=" * 70)

            traceback.print_exc()

            # ------------------------------------------------
            # CUSTOMER-FACING FALLBACK
            #
            # The real error remains visible in server logs.
            # ------------------------------------------------

            return (
                "The network connection is slow or unavailable. "
                "Please try again."
            )


# ============================================================
# TEST / DIAGNOSTIC
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA END-TO-END RESPONSE ENGINE")
    print("=" * 70)
    print()

    print(
        "Model:",
        get_ada_model(),
    )

    print(
        "Groq configured:",
        is_configured(),
    )

    print()

    try:

        manager = AdaPromptManager()

        print(
            "Prompt Manager:",
            "READY",
        )

        print(
            "Identity:",
            bool(
                manager.get_identity_prompt()
            ),
        )

        print(
            "Nigerian Context:",
            bool(
                manager.get_nigerian_context_prompt()
            ),
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

            except Exception:

                available = False

            print(
                f"{service.title():25} :",
                "READY"
                if available
                else "MISSING",
            )

    except Exception as error:

        print(
            "Prompt Manager diagnostic error:",
            type(error).__name__,
            str(error),
        )

    print()

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
        "Complete Document Preservation:",
        "ENABLED",
    )

    print(
        "Review Page Preservation:",
        "ENABLED",
    )

    print(
        "Document Content Truncation:",
        "DISABLED",
    )

    print(
        "Token Control:",
        "ENABLED",
    )

    print(
        "Maximum System Prompt:",
        f"{MAX_SYSTEM_PROMPT_CHARS} characters",
    )

    print(
        "Maximum Central Prompt:",
        f"{MAX_CENTRAL_PROMPT_CHARS} characters",
    )

    print(
        "Maximum Application Context:",
        f"{MAX_CONTEXT_CHARS} characters",
    )

    print(
        "Maximum History:",
        f"{MAX_HISTORY_MESSAGES} messages",
    )

    print(
        "Maximum Output:",
        f"{MAX_OUTPUT_TOKENS} tokens",
    )

    print()
    print("=" * 70)
