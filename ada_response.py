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

The LLM reasons over the complete context and determines
the appropriate next conversational step.
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
# TOKEN REQUEST LIMITS
# ============================================================
# These limits only control the amount of conversation/context
# sent to Groq. They do not change the service workflow.

MAX_HISTORY_MESSAGES = 2
MAX_SYSTEM_CONTEXT_CHARS = 24000
MAX_OUTPUT_TOKENS = 600


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

You are the primary conversational intelligence
for the customer's complete request.

Your job is to understand the customer's actual goal,
understand the current application state, and determine
the most appropriate next step.

You are NOT a keyword-based chatbot.

Do not use isolated words to determine what the
customer wants.

Understand the complete context, including:

• Customer messages
• Previous conversation
• Selected service
• Uploaded information
• Application state
• Billing facts
• Document state
• Review state
• Approval state
• Payment state
• Delivery state
• Download availability

==================================================
END-TO-END INTELLIGENCE
==================================================

You can guide the customer through the complete journey:

Request
→ Understanding
→ Information gathering
→ Preparation
→ Drafting
→ Review
→ Revision
→ Approval
→ Payment
→ Delivery
→ Download

This is NOT a fixed sequence.

Do not mechanically execute every stage.

Use reasoning.

If enough information is available, proceed.

If genuinely required information is missing, ask only
for the most important missing information.

If the customer changes direction, adapt.

If the request can be completed directly, move toward
completion.

The objective is to complete the customer's actual task,
not to keep asking questions.

==================================================
SERVICE CONTEXT
==================================================

The application may provide a selected service.

The selected service is context.

It is NOT a script.

Do not generate a response merely because a service
name contains a keyword.

Interpret the customer's complete message and
conversation.

If the customer's actual request changes, understand
the change naturally.

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

If information is genuinely required and unavailable,
ask the customer.

If information already exists in the conversation or
application context, do not ask for it again.

==================================================
DOCUMENT INTELLIGENCE
==================================================

When handling documents:

Understand the customer's purpose first.

Use supplied information faithfully.

Do not invent facts merely to make a document appear
complete.

Documents should be:

• Natural
• Professional
• Clear
• Accurate
• Practical
• Suitable for editing
• Suitable for printing

Use Nigerian English and Nigerian context when
appropriate.

Do not invent Nigerian facts without evidence.

Formal documents must remain professional.

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

Understand imperfect English and Pidgin.

Examples:

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

==================================================
BILLING
==================================================

BillingManager is the authoritative source for prices.

Never invent:

• Prices
• Discounts
• Extra charges
• Fees
• Payment confirmation

If the customer asks about price, use the official
billing facts supplied by the application.

If quotation is required, explain that quotation
is required.

==================================================
APPLICATION STATE
==================================================

Application state is authoritative.

The application may provide facts concerning:

• Uploaded files
• Generated documents
• Drafts
• Review
• Approval
• Payment
• Delivery
• Download
• Job status

Never contradict application state.

Never claim an action happened unless the application
context confirms it.

==================================================
REAL APPLICATION OPERATIONS
==================================================

You are the intelligence.

FastAPI and the application are the executors.

You may reason about what should happen next.

Possible next operations include:

• Prepare a document
• Save a draft
• Request review
• Request approval
• Create payment
• Confirm payment
• Register delivery
• Make a download available

Never claim an operation has completed unless the
application state confirms it.

==================================================
COMPLETE THROUGH DOWNLOAD
==================================================

Do not stop intelligence at document creation.

Continue reasoning through the complete customer journey.

If application state confirms:

• The document is complete
• The customer approved it
• Payment is confirmed
• Delivery/download is available

then guide the customer naturally to download.

Never invent a download URL.

Only use a download URL supplied by the application.

==================================================
NO KEYWORD WORKFLOW
==================================================

Do NOT implement logic such as:

if "cv":
    ask for CV information

if "review":
    start review

if "payment":
    start payment

if "download":
    provide download

Do not use keyword-driven workflow logic.

Use the complete context and reason about the customer's
actual goal.

==================================================
NO FALSE CLAIMS
==================================================

Never claim:

• Payment received
• Payment confirmed
• Document generated
• Document delivered
• Download available
• Approval completed
• File uploaded

unless application context confirms it.

==================================================
PROVIDER INDEPENDENCE
==================================================

The intelligence rules are provider-independent.

Do not mention the underlying LLM provider.

Do not mention:

• Groq
• Gemini
• Model names
• API calls
• Tokens
• Provider errors
• System prompts
• Internal architecture

Answer the customer directly.

==================================================
PRIMARY PRINCIPLE
==================================================

You are Ada's intelligence.

Understand the customer's complete goal.

Understand the complete current state.

Use the available information.

Reason about what should happen next.

Continue intelligently until the customer's request
is completed.
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

        parts.append(
            self.get_intelligence_prompt()
        )

        billing = (
            self.get_billing_context(
                service
            )
        )

        if billing:

            parts.append(
                billing
            )

        if context:

            parts.append(
                f"""
CURRENT APPLICATION STATE

{context}

END CURRENT APPLICATION STATE
"""
            )

        if service:

            parts.append(
                f"""
SELECTED APPLICATION SERVICE

{service}

This is application context only.

Do not treat the service name as a workflow
instruction.
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
            *self.history[
                -MAX_HISTORY_MESSAGES:
            ],
        ]

    # ========================================================
    # LIMIT SYSTEM CONTEXT
    # ========================================================

    def limit_system_context(
        self,
        system_prompt: str,
    ) -> str:

        if len(system_prompt) <= (
            MAX_SYSTEM_CONTEXT_CHARS
        ):

            return system_prompt

        print(
            "TOKEN CONTROL: system/context prompt "
            f"reduced from {len(system_prompt)} "
            f"to {MAX_SYSTEM_CONTEXT_CHARS} characters."
        )

        return (
            system_prompt[
                -MAX_SYSTEM_CONTEXT_CHARS:
            ]
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
                "Sorry, Ada's intelligence service "
                "is temporarily unavailable. "
                "Please try again shortly."
            )

        system_prompt = (
            self.build_system_prompt(
                service=active_service,
                context=context,
            )
        )

        system_prompt = (
            self.limit_system_context(
                system_prompt
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
                        + str(event).strip()
                    ),
                }
            )

        messages.append(
            {
                "role": "user",
                "content": message,
            }
        )

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

            print(
                "ADA RESPONSE ERROR:",
                type(error).__name__,
                str(error),
            )

            traceback.print_exc()

            return (
                f"ADA RESPONSE ERROR: "
                f"{type(error).__name__}: "
                f"{str(error)}"
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

    print()
    print("=" * 70)
