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
        +-- Conversation history
        |
        v
    Groq / gpt-oss-20b

The old AdaController / AdaAIEngine chain is NOT used.

This version is intentionally optimized for Groq's
8,000-token-per-minute request limit.

The permanent Ada intelligence remains centralized in
AdaPromptManager.

The response engine adds only the application-specific
instructions that are necessary for the current request.
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
# MODEL CLIENT
# ============================================================

_client = None


def get_client():
    """
    Create the Groq client once and reuse it.
    """

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

        # ----------------------------------------------------
        # Keep history deliberately small.
        #
        # The permanent intelligence is already contained
        # in AdaPromptManager. We do not need a large
        # conversation history on every Groq request.
        # ----------------------------------------------------

        self.history: list[
            dict[str, str]
        ] = []

        self.max_history_messages = 6

        # ----------------------------------------------------
        # Prevent unusually large application context from
        # consuming the Groq request budget.
        # ----------------------------------------------------

        self.max_context_characters = 6000

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

        return str(service).strip()

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
                "BILLING CONTEXT ERROR:",
                type(error).__name__,
                str(error),
            )

            return ""

        if not item:

            return """
OFFICIAL BILLING:
No BillingManager information is available for
this service. Never invent or estimate a price.
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

        return (
            "OFFICIAL BILLING INFORMATION\n"
            f"Service: {service}\n"
            f"{pricing}\n"
            "BillingManager is the only authority "
            "for official prices. Never invent a price."
        )

    # ========================================================
    # LIGHTWEIGHT RESPONSE RULES
    # ========================================================

    def get_response_rules(self) -> str:
        """
        These are intentionally short.

        The former implementation repeated many permanent
        Ada rules that are already supplied by
        AdaPromptManager.

        Repeating them consumed thousands of tokens.
        """

        return """
CUSTOMER RESPONSE RULES

You are Ada, the customer-facing assistant for
Naija Pocket Business Center.

Help the customer complete the selected Business
Center service naturally and professionally.

Use the selected service as the primary context.

Understand Nigerian English and ordinary Nigerian
informal expressions. Pidgin may be used naturally
when appropriate.

Do not behave like a keyword-only menu.

Do not ask the customer to repeat information already
available in the conversation or application context.

Ask only the next necessary question.

Never invent customer information, application state,
prices, discounts, charges, qualifications, employment
history, school information, company information,
business information, references, certificates or
registration numbers.

If required information is missing, ask for it.

BillingManager is the only authority for official
service prices.

Never claim payment, approval, delivery or completion
unless the application context explicitly confirms it.

Formal documents must remain professional, clear,
natural and suitable for editing and printing.

Follow the application context supplied with the request.
It is authoritative.
"""

    # ========================================================
    # TRIM CONTEXT
    # ========================================================

    def trim_context(
        self,
        context: str | None,
    ) -> str:

        if not context:
            return ""

        context = str(
            context
        ).strip()

        if not context:
            return ""

        if len(context) <= self.max_context_characters:
            return context

        # Keep the beginning and end because application
        # state commonly contains important information
        # at both locations.

        half = (
            self.max_context_characters // 2
        )

        return (
            context[:half]
            + "\n\n[APPLICATION CONTEXT TRIMMED]\n\n"
            + context[-half:]
        )

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
        # 1. CENTRAL ADA PROMPT
        # ----------------------------------------------------

        try:

            central_prompt = (
                self.prompt_manager.build_prompt(
                    service=service,
                )
            )

            if central_prompt:

                parts.append(
                    central_prompt.strip()
                )

        except Exception as error:

            print(
                "PROMPT MANAGER ERROR:",
                type(error).__name__,
                str(error),
            )

            traceback.print_exc()

        # ----------------------------------------------------
        # 2. SHORT RESPONSE RULES
        # ----------------------------------------------------

        parts.append(
            self.get_response_rules()
        )

        # ----------------------------------------------------
        # 3. BILLING
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

        trimmed_context = (
            self.trim_context(
                context
            )
        )

        if trimmed_context:

            parts.append(
                "CURRENT APPLICATION STATE\n"
                "Use this state as authoritative:\n\n"
                + trimmed_context
            )

        # ----------------------------------------------------
        # 5. ACTIVE SERVICE
        # ----------------------------------------------------

        if service:

            parts.append(
                "ACTIVE SERVICE\n"
                f"The customer's selected service is: {service}\n"
                "Treat it as the active workflow unless "
                "the conversation clearly establishes "
                "another supported request."
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

        content = str(
            content
        ).strip()

        if not content:
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
                "role": "system",
                "content": system_prompt,
            }
        ]

        messages.extend(
            self.history
        )

        return messages

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
        # CLIENT
        # ----------------------------------------------------

        client = get_client()

        if client is None:

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

        active_event = (
            str(event or "").strip()
        )

        if active_event:

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "CURRENT APPLICATION EVENT\n"
                        + active_event
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

            response = (
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.4,
                    max_completion_tokens=800,
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

            print(
                "ADA RESPONSE ERROR:",
                type(error).__name__,
                str(error),
            )

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
    print("=" * 70)
