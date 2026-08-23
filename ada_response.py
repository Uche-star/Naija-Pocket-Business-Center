"""
ada_response.py

Naija Pocket Business Center
Ada Response Engine

This is the customer-facing intelligence layer used by:

    ada_api.py
        |
        v
    AdaResponse
        |
        +-- AdaPromptManager
        |
        +-- BillingManager context supplied by API
        +-- Conversation history
        |
        v
    Groq / gpt-oss-20b

The old AdaController / AdaAIEngine chain is NOT used.

The former prompt intelligence is restored through
AdaPromptManager.
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
    os.getenv(
        "GROQ_MODEL"
    )
    or
    DEFAULT_MODEL
).strip()


API_KEY = (
    os.getenv(
        "GROQ_API_KEY"
    )
    or
    os.getenv(
        "GROQ_APIKEY"
    )
    or
    ""
).strip()


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

    return bool(
        get_client()
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

        self.max_history_messages = 12

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

        normalized = (
            self.billing.normalize_service(
                service
            )
        )

        if normalized:

            return normalized

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

        item = (
            self.billing.get_service(
                service
            )
        )

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

Always understand the customer's actual request
before responding.

Do not behave like a keyword-only menu.

Do not restart the conversation unnecessarily.

Do not ask the customer to repeat information
that is already available in the conversation or
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
different supported service, understand the
request intelligently and respond appropriately.

Do not blindly follow a keyword if the surrounding
conversation clearly establishes the customer's
meaning.

==================================================
BILLING
==================================================

BillingManager is the ONLY official source of
service prices.

Never guess a price.

Never estimate a price.

Never create a market price.

Never invent a discount.

Never invent an extra charge.

If a customer asks for a price, use the official
BillingManager information supplied to you.

If billing is per page, state that clearly.

If quotation is required, explain that a quotation
is required.

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

Never add Nigerian facts merely to make a document
appear Nigerian.

==================================================
WORKFLOW
==================================================

Ada should naturally guide the customer through:

Request
→ Information gathering
→ Preparation
→ Review
→ Revision
→ Approval
→ Payment
→ Delivery

Do not claim that:

• Payment has been received
• Payment has been confirmed
• A document has been delivered
• A document is ready

unless the application context explicitly confirms it.

==================================================
DELIVERY
==================================================

Default document delivery formats are:

• DOCX
• PDF

Do not discuss printing unless the customer
specifically asks about printing.

==================================================
IMPORTANT
==================================================

The application context supplied with each request
is authoritative for application state.

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
        # 1. FORMER CENTRAL PROMPT MANAGER
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
        # 2. ADA RESPONSE RULES
        # ----------------------------------------------------

        parts.append(
            self.get_response_rules()
        )

        # ----------------------------------------------------
        # 3. BILLING MANAGER
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
        # 4. APPLICATION STATE
        # ----------------------------------------------------

        if context:

            parts.append(
                f"""
==================================================
CURRENT APPLICATION STATE
==================================================

{context}

==================================================
END APPLICATION STATE
==================================================
"""
            )

        # ----------------------------------------------------
        # 5. ACTIVE SERVICE
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
the customer's conversation clearly establishes
another supported request.
"""
            )

        return "\n\n".join(
            part
            for part in parts
            if part
        )

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

        self.history.append(
            {
                "role": role,
                "content": str(content),
            }
        )

        # Keep the conversation bounded.

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
    # BUILD MESSAGES
    # ========================================================

    def build_messages(
        self,
        system_prompt: str,
    ):

        messages = [

            {
                "role":
                    "system",

                "content":
                    system_prompt,
            }

        ]

        messages.extend(
            self.history
        )

        return messages

    # ========================================================
    # FORMAT PROVIDER ERROR
    # ========================================================

    @staticmethod
    def format_provider_error(
        error: Exception,
    ) -> str:

        """
        Produce a useful diagnostic string for ada_api.py.

        We deliberately do not expose API keys or complete
        request payloads.

        Groq's SDK exposes status_code and response on
        APIStatusError subclasses, including rate-limit,
        authentication, bad-request and other API failures.
        """

        error_type = (
            type(error).__name__
        )

        status_code = getattr(
            error,
            "status_code",
            None,
        )

        response = getattr(
            error,
            "response",
            None,
        )

        parts = [
            f"error_type={error_type}"
        ]

        if status_code is not None:

            parts.append(
                f"status_code={status_code}"
            )

        # ----------------------------------------------------
        # Try to obtain the provider's response body.
        # ----------------------------------------------------

        response_text = None

        if response is not None:

            try:

                response_text = (
                    response.text
                )

            except Exception:

                response_text = None

        if response_text:

            response_text = (
                str(response_text)
                .strip()
            )

            # Never include an API key if a provider response
            # somehow contains one.

            if API_KEY:

                response_text = (
                    response_text.replace(
                        API_KEY,
                        "[REDACTED]",
                    )
                )

            parts.append(
                "provider_response="
                + response_text
            )

        # ----------------------------------------------------
        # Fallback to exception message.
        # ----------------------------------------------------

        error_message = str(
            error
        ).strip()

        if error_message:

            if API_KEY:

                error_message = (
                    error_message.replace(
                        API_KEY,
                        "[REDACTED]",
                    )
                )

            parts.append(
                "message="
                + error_message
            )

        return " | ".join(
            parts
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

            raise RuntimeError(
                "Groq client is not configured. "
                "Check the GROQ_API_KEY environment variable "
                "and the groq package installation."
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
        # HISTORY
        # ----------------------------------------------------

        messages = (
            self.build_messages(
                system_prompt
            )
        )

        # ----------------------------------------------------
        # CURRENT EVENT
        # ----------------------------------------------------

        if active_event:

            messages.append(
                {
                    "role":
                        "system",

                    "content":
                        (
                            "Current application event: "
                            + active_event
                        ),
                }
            )

        # ----------------------------------------------------
        # CUSTOMER MESSAGE
        # ----------------------------------------------------

        messages.append(
            {
                "role":
                    "user",

                "content":
                    message,
            }
        )

        # ----------------------------------------------------
        # CALL GROQ
        # ----------------------------------------------------

        try:

            response = (
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.4,
                    max_tokens=1800,
                )
            )

        except Exception as error:

            # ------------------------------------------------
            # IMPORTANT
            #
            # Do NOT swallow the provider failure here.
            #
            # ada_api.py already has a diagnostic exception
            # handler. Raising this error allows /api/chat to
            # report the actual failure instead of returning
            # success=True with a fake Ada reply.
            # ------------------------------------------------

            diagnostic = (
                self.format_provider_error(
                    error
                )
            )

            print(
                "ADA RESPONSE ERROR:",
                diagnostic,
            )

            traceback.print_exc()

            raise RuntimeError(
                diagnostic
            ) from error

        # ----------------------------------------------------
        # EXTRACT REPLY
        # ----------------------------------------------------

        reply = ""

        if response.choices:

            choice = (
                response.choices[0]
            )

            if choice.message:

                reply = (
                    choice.message.content
                    or
                    ""
                )

        reply = str(
            reply
        ).strip()

        if not reply:

            reply = (
                "I am ready to help with your "
                "request. Please tell me what "
                "you would like me to do next."
            )

        # ----------------------------------------------------
        # STORE CONVERSATION
        # ----------------------------------------------------

        self.add_history(
            "user",
            message,
        )

        self.add_history(
            "assistant",
            reply,
        )

        return reply


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "=" * 70
    )

    print(
        "NAIJA POCKET BUSINESS CENTER"
    )

    print(
        "ADA RESPONSE ENGINE"
    )

    print(
        "=" * 70
    )

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

    print(
        "CV Prompt:",
        bool(
            manager.get_service_prompt(
                "cv"
            )
        ),
    )

    print(
        "Cover Letter Prompt:",
        bool(
            manager.get_service_prompt(
                "cover_letter"
            )
        ),
    )

    print(
        "Business Prompt:",
        bool(
            manager.get_service_prompt(
                "business"
            )
        ),
    )

    print(
        "Academic Prompt:",
        bool(
            manager.get_service_prompt(
                "academic"
            )
        ),
    )

    print(
        "Document Processing:",
        bool(
            manager.get_service_prompt(
                "document_processing"
            )
        ),
    )

    print(
        "Review Prompt:",
        bool(
            manager.get_service_prompt(
                "review"
            )
        ),
    )

    print(
        "Workflow Prompt:",
        bool(
            manager.get_service_prompt(
                "workflow"
            )
        ),
    )

    print(
        "Delivery Prompt:",
        bool(
            manager.get_service_prompt(
                "delivery"
            )
        ),
    )

    print()

    print(
        "Ada Response Engine READY"
    )

    print(
        "=" * 70
    )
