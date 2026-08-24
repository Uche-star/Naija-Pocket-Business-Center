"""
ada_response.py

Naija Pocket Business Center
Ada Response Engine

Customer-facing intelligence layer:

    ada_api.py
        |
        v
    AdaResponse
        |
        +-- AdaPromptManager
        +-- BillingManager
        +-- bounded conversation history
        |
        v
    Groq / gpt-oss-20b

IMPORTANT:
- Render is the deployment platform.
- FastAPI is the API layer.
- Groq is the intelligence provider.
- AdaPromptManager remains the central prompt intelligence.
- BillingManager remains the official pricing authority.
- The old AdaController / AdaAIEngine chain is not used here.

This version deliberately limits the amount of prompt/history sent to
Groq so that the request stays safely below the current Groq TPM limit.
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
# PROMPT MANAGER
# ============================================================

from ada_prompt_manager import AdaPromptManager


# ============================================================
# BILLING
# ============================================================

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
# REQUEST SIZE PROTECTION
# ============================================================

# Groq previously reported:
#
#   TPM limit: 8000
#   Requested: 10142
#
# Therefore this engine deliberately stays well below that limit.

MAX_SYSTEM_PROMPT_CHARS = 14000
MAX_HISTORY_MESSAGES = 6
MAX_HISTORY_CHARS = 6000
MAX_MESSAGE_CHARS = 6000
MAX_CONTEXT_CHARS = 5000

MAX_OUTPUT_TOKENS = 1000


# ============================================================
# MODEL CLIENT
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

def get_ada_model():
    return MODEL


def is_configured():
    return get_client() is not None


# ============================================================
# TEXT LIMITER
# ============================================================

def limit_text(
    text: Any,
    maximum: int,
) -> str:

    if text is None:
        return ""

    text = str(text).strip()

    if len(text) <= maximum:
        return text

    return (
        text[:maximum]
        + "\n\n[Additional content omitted to keep "
          "the request within the model context limit.]"
    )


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:

    # ========================================================
    # INITIALIZE
    # ========================================================

    def __init__(
        self,
        service: str | None = None,
    ):

        self.service = service

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
    ):

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
    ):

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

        except Exception as error:

            print(
                "SERVICE NORMALIZATION ERROR:",
                type(error).__name__,
                str(error),
            )

        return service

    # ========================================================
    # BILLING CONTEXT
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
                "BILLING ERROR:",
                type(error).__name__,
                str(error),
            )

            return """
==================================================
BILLING INFORMATION
==================================================

Billing information could not be loaded.

Do not invent or estimate a price.
"""

        if not item:

            return """
==================================================
BILLING INFORMATION
==================================================

No official BillingManager information was found
for the currently selected service.

Do not invent a price.
Do not estimate a price.
"""

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

        return f"""
==================================================
OFFICIAL BILLING INFORMATION
==================================================

Service: {service}

{pricing}

BillingManager is the ONLY authority for the
official price.

Never invent another price.
"""

    # ========================================================
    # CORE RESPONSE RULES
    # ========================================================

    def get_response_rules(self) -> str:

        return """
==================================================
ADA RESPONSE ENGINE
==================================================

You are Ada, the customer-facing Business Center
assistant for Naija Pocket Business Center.

You are not a generic chatbot.

Your job is to help the customer complete their
requested Business Center service.

Understand the customer's actual request before
responding.

Do not behave like a keyword-only menu.

Do not restart the conversation unnecessarily.

Do not ask the customer to repeat information that
is already available in the conversation or
application context.

Ask only the next necessary question.

==================================================
CUSTOMER INFORMATION
==================================================

Never invent customer information.

Never manufacture:

• Names
• Phone numbers
• Addresses
• Email addresses
• Qualifications
• Employment history
• School information
• Company information
• Business information
• Financial information
• Statistics
• References
• Certificates
• Registration numbers

If required information is missing, ask for it.

==================================================
SERVICE HANDLING
==================================================

The selected service is supplied by the application.

Use the selected service as the primary context.

If the customer's message clearly indicates a
different supported service, understand the request
intelligently.

Do not blindly follow a keyword when the surrounding
conversation clearly establishes another meaning.

==================================================
BILLING
==================================================

BillingManager is the ONLY official source of
service prices.

Never guess a price.

Never estimate a price.

Never invent a discount or extra charge.

If the customer asks about price, use the official
BillingManager information supplied to you.

==================================================
COMMUNICATION
==================================================

Be:

• Warm
• Friendly
• Respectful
• Professional
• Clear
• Practical
• Reassuring

Understand Nigerian English and informal Nigerian
customer language.

Understand expressions such as:

"I need CV."

"How much CV?"

"Abeg help me."

"I wan do project."

"How much una dey charge?"

"I want to type this."

"Help me write this."

Do not require perfect English.

Pidgin may be used naturally when appropriate.

Do not overuse Pidgin.

Formal documents must remain professional.

==================================================
DOCUMENT QUALITY
==================================================

Documents should be:

• Natural
• Professional
• Clear
• Accurate
• Practical
• Suitable for editing
• Suitable for printing
• Appropriate for Nigeria when relevant

Never invent Nigerian facts merely to make a document
appear Nigerian.

==================================================
WORKFLOW
==================================================

Naturally guide the customer through:

Request
→ Information gathering
→ Preparation
→ Review
→ Revision
→ Approval
→ Payment
→ Delivery

Do not claim that payment has been received,
confirmed, or that a document has been delivered
unless the application context explicitly confirms it.

==================================================
DELIVERY
==================================================

Default document delivery formats are:

• DOCX
• PDF

Do not discuss printing unless the customer asks.

==================================================
APPLICATION STATE
==================================================

The application context supplied with each request
is authoritative.

Use it.

Do not contradict it.

Do not invent application state.
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
        # CENTRAL PROMPT MANAGER
        # ----------------------------------------------------

        try:

            central_prompt = (
                self.prompt_manager.build_prompt(
                    service=service,
                )
            )

            if central_prompt:

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
        # ADA RULES
        # ----------------------------------------------------

        parts.append(
            self.get_response_rules()
        )

        # ----------------------------------------------------
        # BILLING
        # ----------------------------------------------------

        billing_context = (
            self.get_billing_context(
                service
            )
        )

        if billing_context:

            parts.append(
                billing_context
            )

        # ----------------------------------------------------
        # APPLICATION STATE
        # ----------------------------------------------------

        if context:

            safe_context = limit_text(
                context,
                MAX_CONTEXT_CHARS,
            )

            parts.append(
                f"""
==================================================
CURRENT APPLICATION STATE
==================================================

{safe_context}

==================================================
END APPLICATION STATE
==================================================
"""
            )

        # ----------------------------------------------------
        # ACTIVE SERVICE
        # ----------------------------------------------------

        if service:

            parts.append(
                f"""
==================================================
ACTIVE CUSTOMER SERVICE
==================================================

The customer is currently using:

{service}

Treat this service as the active workflow unless
the conversation clearly establishes another
supported request.
"""
            )

        prompt = "\n\n".join(
            part
            for part in parts
            if part
        )

        # ----------------------------------------------------
        # FINAL PROMPT SIZE PROTECTION
        # ----------------------------------------------------

        prompt = limit_text(
            prompt,
            MAX_SYSTEM_PROMPT_CHARS,
        )

        return prompt

    # ========================================================
    # CONVERSATION HISTORY
    # ========================================================

    def add_history(
        self,
        role: str,
        content: str,
    ):

        if not content:
            return

        safe_content = limit_text(
            content,
            MAX_MESSAGE_CHARS,
        )

        self.history.append(
            {
                "role": role,
                "content": safe_content,
            }
        )

        if len(
            self.history
        ) > self.max_history_messages:

            self.history = (
                self.history[
                    -self.max_history_messages:
                ]
            )

    # ========================================================
    # CLEAR HISTORY
    # ========================================================

    def clear_history(self):

        self.history.clear()

    # ========================================================
    # BUILD HISTORY
    # ========================================================

    def build_messages(
        self,
        system_prompt: str,
    ):

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        total_history_chars = 0

        # Most recent messages first.
        # We stop when the history budget is reached.

        for item in reversed(
            self.history
        ):

            content = limit_text(
                item.get(
                    "content",
                    "",
                ),
                MAX_MESSAGE_CHARS,
            )

            if not content:
                continue

            role = item.get(
                "role",
                "user",
            )

            proposed_size = (
                total_history_chars
                + len(content)
            )

            if (
                proposed_size
                > MAX_HISTORY_CHARS
            ):

                break

            messages.insert(
                1,
                {
                    "role": role,
                    "content": content,
                },
            )

            total_history_chars = (
                proposed_size
            )

        return messages

    # ========================================================
    # GROQ CALL
    # ========================================================

    def _call_groq(
        self,
        client,
        messages,
    ):

        return (
            client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0.4,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        )

    # ========================================================
    # EXTRACT REPLY
    # ========================================================

    def _extract_reply(
        self,
        response,
    ) -> str:

        if not response:
            return ""

        if not response.choices:
            return ""

        choice = response.choices[0]

        if not choice.message:
            return ""

        content = (
            choice.message.content
            or ""
        )

        return str(
            content
        ).strip()

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

        # ----------------------------------------------------
        # SERVICE
        # ----------------------------------------------------

        if service:

            self.set_service(
                service
            )

        active_service = (
            self.normalize_service(
                self.service
            )
        )

        # ----------------------------------------------------
        # LIMIT CUSTOMER MESSAGE
        # ----------------------------------------------------

        message = limit_text(
            message,
            MAX_MESSAGE_CHARS,
        )

        # ----------------------------------------------------
        # EVENT
        # ----------------------------------------------------

        active_event = (
            str(event).strip()
            if event
            else ""
        )

        # ----------------------------------------------------
        # CLIENT
        # ----------------------------------------------------

        client = get_client()

        if client is None:

            print(
                "ADA RESPONSE ERROR: "
                "Groq client is not configured."
            )

            return (
                "Sorry, Ada's intelligence service "
                "is temporarily unavailable. "
                "Please try again shortly."
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
        # BUILD MESSAGES
        # ----------------------------------------------------

        messages = (
            self.build_messages(
                system_prompt
            )
        )

        # ----------------------------------------------------
        # CURRENT APPLICATION EVENT
        # ----------------------------------------------------

        if active_event:

            safe_event = limit_text(
                active_event,
                1500,
            )

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Current application event:\n"
                        + safe_event
                    ),
                }
            )

        # ----------------------------------------------------
        # CUSTOMER MESSAGE
        # ----------------------------------------------------

        messages.append(
            {
                "role": "user",
                "content": message,
            }
        )

        # ----------------------------------------------------
        # CALL GROQ
        # ----------------------------------------------------

        try:

            response = self._call_groq(
                client,
                messages,
            )

            reply = (
                self._extract_reply(
                    response
                )
            )

            if not reply:

                reply = (
                    "I am ready to help with your "
                    "request. Please tell me what "
                    "you would like me to do next."
                )

            # ------------------------------------------------
            # STORE CONVERSATION
            # ------------------------------------------------

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

            error_type = (
                type(error).__name__
            )

            error_text = str(
                error
            )

            print(
                "ADA RESPONSE ERROR:",
                error_type,
                error_text,
            )

            traceback.print_exc()

            # ------------------------------------------------
            # IMPORTANT:
            #
            # Do not expose provider diagnostics to the
            # customer.
            # ------------------------------------------------

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
    print("ADA RESPONSE ENGINE")
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

    services_to_test = [
        "cv",
        "cover_letter",
        "business",
        "academic",
        "document_processing",
        "review",
        "workflow",
        "delivery",
    ]

    for service_name in services_to_test:

        try:

            available = bool(
                manager.get_service_prompt(
                    service_name
                )
            )

        except Exception:

            available = False

        print(
            f"{service_name}:",
            available,
        )

    print()

    print(
        "Ada Response Engine READY"
    )

    print(
        "Maximum system prompt:",
        MAX_SYSTEM_PROMPT_CHARS,
        "characters",
    )

    print(
        "Maximum history:",
        MAX_HISTORY_MESSAGES,
        "messages",
    )

    print(
        "Maximum output:",
        MAX_OUTPUT_TOKENS,
        "tokens",
    )

    print("=" * 70)
