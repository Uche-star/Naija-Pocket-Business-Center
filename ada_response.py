"""
ada_response.py

Naija Pocket Business Center
Ada Response Engine

Architecture
------------

Customer
    |
    v
FastAPI /api/chat
    |
    v
AdaResponse
    |
    +-- AdaPromptManager
    +-- BillingManager
    +-- Application State
    +-- Conversation History
    |
    v
Groq / gpt-oss-20b
    |
    v
LLM-driven workflow decision
    |
    +-- gather information
    +-- prepare document
    +-- review
    +-- revise
    +-- approve
    +-- payment
    +-- delivery
    +-- download
    |
    v
FastAPI application executes real actions

IMPORTANT
---------

Groq is the intelligence and orchestration layer.

There is NO keyword-based service router here.

The LLM decides what the customer needs next from:

    customer message
    selected service
    application state
    billing state
    conversation history

The application remains authoritative for real-world state.

Ada must NEVER claim that payment, approval, generation,
delivery, or download has happened unless the application
context confirms it.
"""

from __future__ import annotations

import json
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

        except Exception:

            item = None

        if not item:

            return """
==================================================
BILLING INFORMATION
==================================================

No official BillingManager information was found
for the currently selected service.

Do not invent a price.
Do not estimate a price.

If the customer asks for pricing, explain that
official pricing information is currently
unavailable.
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

BillingManager is the ONLY authority for pricing.

Never invent another price.
Never estimate another price.
Never invent a discount.
Never invent an additional charge.
"""

    # ========================================================
    # END-TO-END ORCHESTRATION RULES
    # ========================================================

    def get_orchestration_rules(self) -> str:

        return """
==================================================
ADA END-TO-END INTELLIGENCE
==================================================

You are Ada, the customer-facing Business Center
assistant for Naija Pocket Business Center.

You are powered by the language model supplied by
the application.

You are responsible for intelligently guiding the
customer through the complete service workflow.

You are NOT a keyword-based chatbot.

Do not decide what to do merely because a particular
word appears in the customer's message.

Understand the complete meaning of:

• The customer's current message
• Previous conversation
• Selected service
• Uploaded material
• Application state
• Billing state
• Approval state
• Payment state
• Delivery state

Use all available context together.

==================================================
END-TO-END WORKFLOW
==================================================

You can guide a customer through the complete
workflow:

REQUEST
    ↓
INFORMATION GATHERING
    ↓
PREPARATION
    ↓
REVIEW
    ↓
REVISION
    ↓
APPROVAL
    ↓
PAYMENT
    ↓
FINALIZATION
    ↓
DELIVERY
    ↓
DOWNLOAD

Do not artificially stop the conversation after
information gathering.

Continue helping the customer until the application
state shows that the service has reached its
appropriate completion state.

==================================================
IMPORTANT DISTINCTION
==================================================

You are the intelligence/orchestration layer.

The application is the authority for actual state.

Therefore:

You may reason about what should happen next.

But you must NOT claim that an application action
has happened unless application context confirms it.

For example:

Do NOT say:

"Your payment has been received."

unless application state says payment is confirmed.

Do NOT say:

"Your document is ready."

unless application state says the document is ready.

Do NOT say:

"Your CV has been delivered."

unless application state confirms delivery.

Do NOT say:

"Download your CV."

unless application state confirms that a download
is available.

==================================================
APPLICATION ACTIONS
==================================================

When application actions are available through the
API/application layer, reason about which action is
appropriate.

Possible application capabilities include:

• information gathering
• document preparation
• document processing
• document review
• revision
• approval
• payment creation
• payment verification
• document finalization
• delivery registration
• download availability

Do not simulate these actions in text.

The application must execute the real action and
return the resulting state.

Use the returned state on the next response.

==================================================
SERVICE INTELLIGENCE
==================================================

The selected service is application context.

Treat it as the customer's current service.

However, understand the customer's actual intent.

If the customer changes direction, understand the
meaning from the complete conversation.

Do not blindly follow the selected service when the
customer clearly establishes a different supported
request.

Do not require the customer to use exact service
names.

==================================================
INFORMATION GATHERING
==================================================

Ask only for information that is actually necessary.

Do not repeatedly ask for information already
provided.

Remember information already present in:

• conversation history
• application context
• uploaded content
• OCR/extracted text
• previous customer messages

Ask one practical next question when possible.

Do not dump a long questionnaire on the customer
unless the service genuinely requires it.

==================================================
CUSTOMER INFORMATION
==================================================

Never invent:

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

If important information is missing, ask the
customer for it.

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

Never fabricate qualifications, experience,
employment, education or achievements.

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

Understand Nigerian English.

Understand informal Nigerian customer language.

Examples include:

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
UPLOAD
==================================================

When discussing uploads, refer to:

• documents
• files
• content
• materials

Do not unnecessarily describe uploads as pictures
or photos.

==================================================
BILLING
==================================================

BillingManager is the official pricing authority.

Never invent:

• price
• discount
• surcharge
• additional fee
• market price

If the service is fixed-price, use the supplied
official price.

If it is per-page, clearly explain that.

If quotation is required, explain that quotation
is required.

==================================================
DELIVERY
==================================================

Default document formats:

• DOCX
• PDF

Do not discuss printing unless the customer
specifically asks about printing.

==================================================
CONVERSATION CONTINUITY
==================================================

Never restart unnecessarily.

Never make the customer repeat information already
available.

Never behave as though each message is a completely
new conversation.

Use the conversation history.

Use application state.

Use the selected service.

Use uploaded material when supplied.

==================================================
FINAL OBJECTIVE
==================================================

Your objective is not merely to answer the current
message.

Your objective is to intelligently help the customer
complete the requested Business Center service from
start to finish.

Always determine the most useful next step.
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
        # END-TO-END ADA INTELLIGENCE
        # ----------------------------------------------------

        parts.append(
            self.get_orchestration_rules()
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

            parts.append(
                f"""
==================================================
AUTHORITATIVE APPLICATION STATE
==================================================

The following state was supplied by the
application and is authoritative.

{context}

==================================================
END AUTHORITATIVE APPLICATION STATE
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
ACTIVE SERVICE
==================================================

Current selected service:

{service}

Use this as the current service context unless
the conversation clearly establishes another
supported request.
"""
            )

        return "\n\n".join(
            part
            for part in parts
            if part
        )

    # ========================================================
    # HISTORY
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

        return [
            {
                "role": "system",
                "content": system_prompt,
            },
            *self.history,
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

        if event:

            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Current application event:\n"
                        + str(event).strip()
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
        # GROQ
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
            # STORE HISTORY
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

    for service_name in services:

        try:

            result = (
                manager.get_service_prompt(
                    service_name
                )
            )

            print(
                f"{service_name}:",
                bool(result),
            )

        except Exception as error:

            print(
                f"{service_name}: ERROR",
                type(error).__name__,
            )

    print()
    print(
        "End-to-end LLM orchestration:",
        "ENABLED",
    )

    print(
        "Keyword workflow routing:",
        "DISABLED",
    )

    print()
    print(
        "Ada Response Engine READY"
    )

    print("=" * 70)
