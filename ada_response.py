"""
Naija Pocket Business Center
Ada Response Engine

END-TO-END LLM INTELLIGENCE LAYER

Groq is the intelligence.

AdaResponse does not use keyword matching to determine
what the customer wants.

AdaPromptManager remains responsible for Ada's existing
identity, Nigerian context, writing style, and service
prompts.

BillingManager supplies factual billing information.

FastAPI/application state supplies factual information
about uploads, documents, approval, payment, delivery,
and download.

TOKEN CONTROL
-------------
The intelligence remains the same, but each Groq request
is deliberately kept small.

The application does NOT send the entire historical prompt
and conversation on every request.

The objective is to keep every transaction comfortably
below Groq's current 8,000-token TPM request limit while
preserving the existing intelligence and service context.
"""

from __future__ import annotations

import os
import traceback


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
# These limits apply only to what is sent to Groq.
#
# They do NOT change the customer's information.
# They do NOT change the service.
# They do NOT change the workflow.
# They simply prevent unnecessarily large prompts.
# ============================================================

MAX_SYSTEM_CHARS = 16000
MAX_CENTRAL_PROMPT_CHARS = 9000
MAX_INTELLIGENCE_PROMPT_CHARS = 6000
MAX_CONTEXT_CHARS = 2500
MAX_HISTORY_MESSAGES = 2
MAX_HISTORY_MESSAGE_CHARS = 1200
MAX_OUTPUT_TOKENS = 800


# ============================================================
# CLIENT
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
# TOKEN CONTROL HELPERS
# ============================================================

def compact_text(
    text: str | None,
    maximum: int,
) -> str:

    """
    Keep the beginning and end of a prompt while removing
    unnecessary middle content when the source is too large.

    This protects both:
        - general identity/instructions at the beginning
        - service-specific information that may appear later
    """

    if not text:
        return ""

    text = str(text).strip()

    if len(text) <= maximum:
        return text

    if maximum < 200:
        return text[:maximum]

    first_part = int(
        maximum * 0.70
    )

    last_part = (
        maximum
        - first_part
    )

    return (
        text[:first_part]
        + "\n\n"
        "[TOKEN CONTROL: middle of oversized prompt "
        "removed to keep this request within the "
        "provider request limit.]\n\n"
        + text[-last_part:]
    )


def compact_history(
    history: list[dict[str, str]],
) -> list[dict[str, str]]:

    """
    Keep only the most recent customer/assistant exchange.
    """

    recent = history[
        -MAX_HISTORY_MESSAGES:
    ]

    result: list[dict[str, str]] = []

    for item in recent:

        role = str(
            item.get("role", "")
        ).strip()

        content = compact_text(
            item.get("content", ""),
            MAX_HISTORY_MESSAGE_CHARS,
        )

        if role and content:

            result.append(
                {
                    "role": role,
                    "content": content,
                }
            )

    return result


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:

    def __init__(
        self,
        service: str | None = None,
    ):

        self.service = (
            str(service).strip()
            if service
            else None
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

        # Only the latest exchange is required.
        # The service form/application context carries
        # the important current information.
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

        if service:
            self.service = (
                str(service).strip()
            )

    # ========================================================
    # NORMALIZE SERVICE
    # ========================================================

    def normalize_service(
        self,
        service: str | None,
    ) -> str | None:

        if not service:
            return self.service

        try:

            normalized = (
                self.billing.normalize_service(
                    service
                )
            )

            if normalized:
                return normalized

        except Exception:
            pass

        return str(service).strip()

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

        except Exception:

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
            "BillingManager is authoritative for price."
        )

    # ========================================================
    # END-TO-END INTELLIGENCE
    # ========================================================

    def get_intelligence_prompt(self) -> str:

        return """
You are Ada, the intelligent customer-facing
assistant of Naija Pocket Business Center.

Understand the customer's complete goal and the
current application state.

You are NOT a keyword-based chatbot.

The selected service is context, not a script.

Use the customer's supplied information faithfully.
Never invent personal, business, academic, financial,
document, payment, approval, delivery, or download facts.

The customer may communicate in Nigerian English,
informal English, or Pidgin. Understand imperfect English.

The application may provide:
- selected service
- customer information
- form information
- uploaded files
- document state
- review state
- approval state
- payment state
- delivery state
- download state

Application state is authoritative.

BillingManager is authoritative for prices.

Never invent prices, discounts, payment confirmation,
document completion, approval, delivery, or download
availability.

The overall customer journey is:

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
appropriate next step.

If the application provides enough information,
do not unnecessarily ask another question.

When a service form has supplied the required
information, use that information rather than starting
a long question-by-question conversation.

Continue helping until the customer's request is
completed.

Never claim that an application operation happened
unless the application state confirms it.

Never invent a download URL.

Do not mention:
- Groq
- Gemini
- model names
- API calls
- tokens
- system prompts
- internal architecture
- provider errors

Answer the customer directly.

Be warm, clear, practical, professional, and concise.
"""

    # ========================================================
    # BUILD SYSTEM PROMPT
    # ========================================================

    def build_system_prompt(
        self,
        service: str | None = None,
        context: str | None = None,
    ) -> str:

        service = (
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
                    service=service,
                )
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

            print(
                "PROMPT MANAGER ERROR:",
                type(error).__name__,
                str(error),
            )

            traceback.print_exc()

        # ----------------------------------------------------
        # COMPACT END-TO-END INTELLIGENCE
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
                service
            )
        )

        if billing:

            parts.append(
                billing
            )

        # ----------------------------------------------------
        # APPLICATION STATE
        # ----------------------------------------------------

        if context:

            context = compact_text(
                context,
                MAX_CONTEXT_CHARS,
            )

            parts.append(
                "CURRENT APPLICATION STATE\n\n"
                + context
                + "\n\nEND CURRENT APPLICATION STATE"
            )

        # ----------------------------------------------------
        # SERVICE
        # ----------------------------------------------------

        if service:

            parts.append(
                "SELECTED SERVICE\n"
                + str(service)
            )

        # ----------------------------------------------------
        # FINAL SYSTEM LIMIT
        # ----------------------------------------------------

        system_prompt = "\n\n".join(
            part
            for part in parts
            if part
        )

        system_prompt = compact_text(
            system_prompt,
            MAX_SYSTEM_CHARS,
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

        if not content:
            return

        self.history.append(
            {
                "role": role,
                "content": str(content),
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

    def clear_history(self) -> None:

        self.history.clear()

    # ========================================================
    # BUILD MESSAGES
    # ========================================================

    def build_messages(
        self,
        system_prompt: str,
    ) -> list[dict[str, str]]:

        return [
            {
                "role": "system",
                "content": system_prompt,
            },
            *compact_history(
                self.history
            ),
        ]

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

        message = (
            str(message or "")
            .strip()
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

        client = get_client()

        if client is None:

            return (
                "Sorry, the intelligence service "
                "is temporarily unavailable. "
                "Please try again shortly."
            )

        # ----------------------------------------------------
        # BUILD SMALL REQUEST
        # ----------------------------------------------------

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
                            str(event).strip(),
                            600,
                        )
                    ),
                }
            )

        messages.append(
            {
                "role": "user",
                "content": compact_text(
                    message,
                    2500,
                ),
            }
        )

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

                    reply = (
                        choice.message.content
                        or ""
                    )

            reply = str(
                reply
            ).strip()

            if not reply:

                reply = (
                    "I am ready to help. "
                    "Please tell me what you "
                    "would like to do next."
                )

            self.add_history(
                "user",
                message,
            )

            self.add_history(
                "assistant",
                reply,
            )

            return reply

        except Exception as error:

            # REAL ERROR IS SHOWN IN SERVER LOGS.
            # No error is hidden from the developer.

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

            return (
                "Sorry, I could not process your request "
                "right now. Please try again in a moment."
            )


# ============================================================
# TEST
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
            "READY" if available else "MISSING",
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
        "Token Control:",
        "ENABLED",
    )

    print(
        "Maximum System Prompt:",
        f"{MAX_SYSTEM_CHARS} characters",
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
