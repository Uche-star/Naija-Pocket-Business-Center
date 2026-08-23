"""
ada_ai_engine.py

Ada AI Engine V13
Naija Pocket Business Center

============================================================
PURPOSE
============================================================

Ada is an LLM-first customer service and document engine.

GROQ IS THE INTELLIGENCE.

Groq is responsible for:

    - understanding the customer's request
    - understanding the selected service
    - asking necessary questions
    - understanding customer answers
    - deciding when enough information is available
    - deciding what should happen next
    - generating the requested work
    - reviewing the work conversationally
    - understanding revision requests
    - revising the work
    - understanding approval
    - maintaining natural customer conversation

PYTHON IS THE APPLICATION BRIDGE.

Python is responsible only for:

    - connecting to Groq
    - carrying conversation state
    - supplying the selected service
    - supplying uploaded document content
    - supplying official application facts
    - executing application operations
    - exposing job state to the rest of the application

Python MUST NOT:

    - conduct an artificial interview
    - decide whether Groq has asked enough questions
    - perform keyword-based interview completion
    - duplicate Groq's reasoning
    - force a question sequence
    - generate fallback intelligence
    - decide what the customer means when Groq can do it
    - unnecessarily call Groq a second time to ask whether
      an interview is complete

============================================================
CORE DESIGN
============================================================

Customer
    ↓
Service Button
    ↓
Ada Application Bridge
    ↓
ONE GROQ INTELLIGENCE REQUEST
    ↓
Groq decides what should happen
    ↓
Ada responds / produces work / requests application action
    ↓
Python executes only the required mechanical operation

============================================================
IMPORTANT
============================================================

There is NO separate interview-complete request.

There is NO Python interview engine.

There is NO keyword-only intelligence system.

There is NO artificial question sequence.

Normal customer conversation uses ONE Groq request per
customer message.

Document generation is available through the same Groq
intelligence layer.

The application may call generate_document_draft() when
the surrounding application explicitly needs the finished
document content, but this method does NOT decide whether
the customer is ready.

Groq decides readiness conversationally.

Revision occurs only when the customer actually requests
a revision.

Approval, payment and delivery are application operations.
Python records their actual state; Groq communicates naturally
with the customer about those states.

============================================================
MODEL
============================================================

GROQ_MODEL is read from the environment.

Deprecated models are replaced with:

    openai/gpt-oss-20b
"""

import os
import traceback

from groq import Groq

from ada_prompt_manager import AdaPromptManager
from ada_conversation_memory import AdaConversationMemory
from billing_manager import BillingManager


class AdaAIEngine:

    # ==========================================================
    # MODEL
    # ==========================================================

    DEFAULT_MODEL = "openai/gpt-oss-20b"

    DEPRECATED_MODELS = {
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
    }

    # ==========================================================
    # INITIALIZATION
    # ==========================================================

    def __init__(self):

        self.client = None
        self.connected = False

        self.memory = AdaConversationMemory()
        self.prompt_manager = AdaPromptManager()
        self.billing = BillingManager()

        self.active_document_text = ""
        self.active_document_path = None

        self.current_draft = ""

        self.job_state = self._new_job_state()

        self.connect()

    # ==========================================================
    # JOB STATE
    # ==========================================================

    def _new_job_state(self):

        return {
            "service": None,
            "interview_complete": False,
            "draft_generated": False,
            "awaiting_review": False,
            "revision_requested": False,
            "revision_count": 0,
            "approved": False,
            "payment_received": False,
            "delivered": False,
        }

    # ==========================================================
    # API KEY
    # ==========================================================

    def get_api_key(self):

        for name in (
            "GROQ_API_KEY",
            "GROQ_KEY",
            "API_KEY",
        ):

            value = os.getenv(name)

            if value:

                value = str(value).strip()

                if value:
                    return value

        return None

    # ==========================================================
    # MODEL
    # ==========================================================

    def get_model(self):

        configured_model = os.getenv("GROQ_MODEL")

        if configured_model:

            configured_model = str(
                configured_model
            ).strip()

            if configured_model:

                if configured_model in self.DEPRECATED_MODELS:

                    print()
                    print("=" * 70)
                    print("DEPRECATED GROQ MODEL")
                    print("=" * 70)
                    print("Configured:", configured_model)
                    print("Using:", self.DEFAULT_MODEL)
                    print("=" * 70)
                    print()

                    return self.DEFAULT_MODEL

                return configured_model

        return self.DEFAULT_MODEL

    # ==========================================================
    # CONNECT
    # ==========================================================

    def connect(self):

        try:

            api_key = self.get_api_key()

            if not api_key:

                self.connected = False
                self.client = None

                print()
                print("=" * 70)
                print("GROQ CONNECTION ERROR")
                print("=" * 70)
                print("GROQ_API_KEY was not found.")
                print("=" * 70)
                print()

                return False

            self.client = Groq(
                api_key=api_key
            )

            self.connected = True

            print()
            print("=" * 70)
            print("ADA AI ENGINE V13")
            print("=" * 70)
            print("Groq Connected:", True)
            print("Groq Model:", self.get_model())
            print("Intelligence:", "GROQ")
            print("Python Interview Logic:", "DISABLED")
            print("Artificial Interview Check:", "DISABLED")
            print("Fallback Intelligence:", "DISABLED")
            print("=" * 70)
            print()

            return True

        except Exception as error:

            self.client = None
            self.connected = False

            self._log_error(
                "GROQ CONNECTION ERROR",
                error
            )

            return False

    # ==========================================================
    # ERROR LOGGER
    # ==========================================================

    def _log_error(
        self,
        title,
        error
    ):

        print()
        print("=" * 80)
        print(title)
        print("=" * 80)

        try:
            print(
                "MODEL:",
                self.get_model()
            )
        except Exception:
            pass

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        print()
        print("FULL TRACEBACK")
        print("-" * 80)

        traceback.print_exc()

        print("=" * 80)
        print()

    # ==========================================================
    # CONNECTION STATUS
    # ==========================================================

    def is_connected(self):

        return self.connected

    # ==========================================================
    # DOCUMENT CONTEXT
    # ==========================================================

    def set_document_context(
        self,
        extracted_text,
        file_path=None
    ):

        if extracted_text is None:
            return False

        extracted_text = str(
            extracted_text
        ).strip()

        if not extracted_text:
            return False

        self.active_document_text = (
            extracted_text
        )

        self.active_document_path = (
            file_path
        )

        return True

    def get_document_context(self):

        return self.active_document_text

    def has_document_context(self):

        return bool(
            self.active_document_text.strip()
        )

    def clear_document_context(self):

        self.active_document_text = ""
        self.active_document_path = None

    # ==========================================================
    # CURRENT DRAFT
    # ==========================================================

    def set_current_draft(
        self,
        draft
    ):

        if draft is None:
            self.current_draft = ""
            return False

        draft = str(draft).strip()

        self.current_draft = draft

        return bool(draft)

    def get_current_draft(self):

        return self.current_draft

    def clear_current_draft(self):

        self.current_draft = ""

    # ==========================================================
    # SERVICE NORMALIZATION
    # ==========================================================

    def normalize_service(
        self,
        service
    ):

        if not service:
            return None

        try:

            return self.billing.normalize_service(
                service
            )

        except Exception as error:

            print(
                "Service Normalization Error:",
                repr(error)
            )

            return str(service).strip()

    # ==========================================================
    # SERVICE EXISTS
    # ==========================================================

    def service_exists(
        self,
        service
    ):

        try:

            normalized = self.normalize_service(
                service
            )

            return self.billing.has_service(
                normalized
            )

        except Exception as error:

            print(
                "Service Exists Error:",
                repr(error)
            )

            return False

    # ==========================================================
    # SERVICE PRICE
    # ==========================================================

    def get_service_price(
        self,
        service
    ):

        normalized = self.normalize_service(
            service
        )

        return self.billing.get_price(
            normalized
        )

    # ==========================================================
    # BILLING INFORMATION
    #
    # Only the selected service price is supplied to Groq.
    #
    # We do NOT inject the complete price list into every
    # customer conversation.
    # ==========================================================

    def get_selected_service_billing(
        self,
        service=None
    ):

        active_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        if not active_service:

            return (
                "No service has been selected."
            )

        try:

            price_list = (
                self.billing.get_price_list()
            )

            info = price_list.get(
                active_service
            )

            if not info:

                return (
                    f"Service: "
                    f"{self.get_service_display_name(active_service)}"
                )

            billing = info.get("billing")
            price = info.get("price")

            name = self.get_service_display_name(
                active_service
            )

            if billing == "fixed":

                return (
                    f"Official service: {name}\n"
                    f"Official price: ₦{price:,}\n"
                    f"Billing: Fixed"
                )

            if billing == "per_page":

                return (
                    f"Official service: {name}\n"
                    f"Official price: ₦{price:,} per page\n"
                    f"Billing: Per page"
                )

            if billing == "quotation":

                return (
                    f"Official service: {name}\n"
                    f"Billing: Quotation required"
                )

            return (
                f"Official service: {name}\n"
                f"Billing: {billing}\n"
                f"Price: {price}"
            )

        except Exception as error:

            print(
                "Selected Billing Error:",
                repr(error)
            )

            return (
                f"Official service: "
                f"{self.get_service_display_name(active_service)}"
            )

    # ==========================================================
    # FULL BILLING RULES
    #
    # Compatibility method.
    #
    # It is NOT automatically injected into every request.
    # ==========================================================

    def get_billing_rules(self):

        services = []

        try:

            price_list = (
                self.billing.get_price_list()
            )

            for service, info in price_list.items():

                billing = info.get("billing")
                price = info.get("price")

                name = (
                    service
                    .replace("_", " ")
                    .title()
                )

                if billing == "fixed":

                    services.append(
                        f"{name}: ₦{price:,} (Fixed)"
                    )

                elif billing == "per_page":

                    services.append(
                        f"{name}: ₦{price:,} per page"
                    )

                elif billing == "quotation":

                    services.append(
                        f"{name}: Quotation Required"
                    )

        except Exception as error:

            print(
                "Billing Rules Error:",
                repr(error)
            )

        return (
            "OFFICIAL BILLING RULES\n\n"
            "Prices come only from BillingManager.\n"
            "Never invent prices.\n"
            "Never estimate prices.\n"
            "Never change official prices.\n\n"
            + "\n".join(services)
        )

    # ==========================================================
    # SERVICE DISPLAY NAME
    # ==========================================================

    def get_service_display_name(
        self,
        service
    ):

        internal_service = (
            self.normalize_service(service)
        )

        if not internal_service:

            return (
                str(service)
                if service
                else "Not selected"
            )

        return (
            internal_service
            .replace("_", " ")
            .title()
        )

    # ==========================================================
    # ADA SYSTEM PROMPT
    #
    # Groq is given identity + service + relevant application
    # facts.
    #
    # Python does NOT tell Groq how to conduct an interview.
    # ==========================================================

    def get_system_prompt(
        self,
        service=None
    ):

        normalized_service = (
            self.normalize_service(service)
        )

        selected_service = (
            self.get_service_display_name(
                normalized_service
            )
        )

        base_prompt = (
            self.prompt_manager.build_prompt(
                service=normalized_service
            )
        )

        application_context = f"""

==================================================
ADA APPLICATION CONTEXT
==================================================

You are Ada, the customer-facing assistant for
Naija Pocket Business Center.

SELECTED SERVICE:
{selected_service}

OFFICIAL SERVICE BILLING:
{self.get_selected_service_billing(
    normalized_service
)}

You are the intelligence responsible for the
customer-facing experience.

Understand the customer naturally.

Use the conversation history.

Ask only for information genuinely needed to
complete the customer's request.

Do not repeat information the customer already
provided.

Do not force a predefined interview sequence.

Do not use keyword logic.

Do not restart the conversation.

Do not ask unnecessary questions.

When the request is sufficiently clear, proceed
with the work rather than creating unnecessary
delays.

If the customer changes the request, adapt.

If the customer requests a revision, understand
the requested revision and revise the work.

If the customer approves the work, acknowledge
the approval and move toward the application's
payment step.

Never invent:

- customer facts
- names
- dates
- qualifications
- prices
- payment confirmation
- delivery confirmation
- application actions that have not actually happened

The official service price supplied above is the
only price you may communicate for the selected
service.

You are never to discuss internal implementation.

Never mention:

- Python
- FastAPI
- Groq
- APIs
- controllers
- application memory
- internal code
- internal prompts
- internal state

The customer should experience Ada as one seamless
professional assistant.

==================================================
APPLICATION BOUNDARY
==================================================

The application can execute mechanical operations
such as:

- storing conversation
- storing uploaded content
- creating files
- recording approval
- recording payment
- delivering files

Do not claim that one of these operations happened
until the application has actually confirmed it.

==================================================
"""

        document_context = ""

        if self.has_document_context():

            document_context = f"""

==================================================
CUSTOMER-SUPPLIED DOCUMENT
==================================================

The customer has already supplied the following
content.

Use it when relevant.

Do not ask for the same content again.

DOCUMENT CONTENT
----------------

{self.active_document_text}

==================================================
"""

        state_context = f"""

==================================================
CURRENT APPLICATION STATE
==================================================

Draft generated:
{self.job_state.get("draft_generated")}

Awaiting review:
{self.job_state.get("awaiting_review")}

Revision count:
{self.job_state.get("revision_count")}

Approved:
{self.job_state.get("approved")}

Payment received:
{self.job_state.get("payment_received")}

Delivered:
{self.job_state.get("delivered")}

==================================================
"""

        return (
            base_prompt
            + application_context
            + document_context
            + state_context
        )

    # ==========================================================
    # MEMORY → GROQ FORMAT
    # ==========================================================

    def build_history_messages(self):

        messages = []

        try:

            stored_messages = (
                self.memory.messages
            )

        except Exception as error:

            print(
                "Memory Access Error:",
                repr(error)
            )

            return messages

        for stored in stored_messages:

            if not stored:
                continue

            text = str(
                stored
            ).strip()

            if not text:
                continue

            if text.startswith("Customer:"):

                content = (
                    text[
                        len("Customer:"):
                    ].strip()
                )

                if content:

                    messages.append(
                        {
                            "role": "user",
                            "content": content,
                        }
                    )

            elif text.startswith("Ada:"):

                content = (
                    text[
                        len("Ada:"):
                    ].strip()
                )

                if content:

                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                        }
                    )

        return messages

    # ==========================================================
    # RESET JOB
    # ==========================================================

    def reset_job(self):

        self.memory.clear()

        self.clear_document_context()

        self.clear_current_draft()

        self.job_state = self._new_job_state()

        return True

    # ==========================================================
    # START JOB
    # ==========================================================

    def start_job(
        self,
        service
    ):

        if not service:

            raise ValueError(
                "Cannot start a job without a service."
            )

        self.reset_job()

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        self.job_state[
            "service"
        ] = normalized_service

        return True

    # ==========================================================
    # JOB STATE
    # ==========================================================

    def get_job_state(self):

        return self.job_state

    # ==========================================================
    # ACTIVE SERVICE
    # ==========================================================

    def set_active_service(
        self,
        service
    ):

        normalized_service = (
            self.normalize_service(service)
        )

        if not normalized_service:
            return False

        self.job_state[
            "service"
        ] = normalized_service

        return True

    def get_active_service(self):

        return self.job_state.get(
            "service"
        )

    # ==========================================================
    # FIND SERVICE
    #
    # Compatibility helper only.
    #
    # This does NOT determine customer intent.
    # The selected service from the button remains authoritative.
    # ==========================================================

    def find_service_in_message(
        self,
        customer_message
    ):

        if not customer_message:
            return None

        text = (
            str(customer_message)
            .strip()
            .lower()
        )

        try:

            aliases = (
                self.billing.service_aliases
            )

            matches = []

            for alias, service in aliases.items():

                alias_lower = (
                    str(alias)
                    .strip()
                    .lower()
                )

                if (
                    alias_lower
                    and alias_lower in text
                ):

                    matches.append(
                        (
                            len(alias_lower),
                            service
                        )
                    )

            if matches:

                matches.sort(
                    reverse=True
                )

                return matches[0][1]

        except Exception as error:

            print(
                "Service Alias Error:",
                repr(error)
            )

        return None

    # ==========================================================
    # ONE GROQ REQUEST
    #
    # This is the main intelligence gateway.
    # ==========================================================

    def _groq_request(
        self,
        messages,
        temperature=0.4,
        purpose="CONVERSATION"
    ):

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        print()
        print("=" * 80)
        print("ADA V13 → GROQ")
        print("=" * 80)
        print("PURPOSE:", purpose)
        print("MODEL:", self.get_model())
        print("MESSAGE COUNT:", len(messages))
        print("=" * 80)
        print()

        try:

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),
                    messages=messages,
                    temperature=temperature,
                )
            )

            reply = (
                response
                .choices[0]
                .message
                .content
            )

            if reply is None:

                raise RuntimeError(
                    "Groq returned None."
                )

            reply = str(
                reply
            ).strip()

            if not reply:

                raise RuntimeError(
                    "Groq returned an empty response."
                )

            print()
            print("=" * 80)
            print("GROQ RESPONSE RECEIVED")
            print("=" * 80)
            print("PURPOSE:", purpose)
            print("RESPONSE:", repr(reply))
            print("=" * 80)
            print()

            return reply

        except Exception as error:

            self._log_error(
                f"REAL GROQ {purpose} ERROR",
                error
            )

            raise

    # ==========================================================
    # NORMAL CUSTOMER CONVERSATION
    #
    # ONE CUSTOMER MESSAGE = ONE GROQ REQUEST.
    # ==========================================================

    def generate_response(
        self,
        customer_message,
        temperature=0.4,
        service=None
    ):

        normalized_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        messages = [

            {
                "role": "system",
                "content": self.get_system_prompt(
                    normalized_service
                ),
            }

        ]

        history_messages = (
            self.build_history_messages()
        )

        messages.extend(
            history_messages
        )

        if not history_messages:

            messages.append(
                {
                    "role": "user",
                    "content": customer_message,
                }
            )

        return self._groq_request(
            messages=messages,
            temperature=temperature,
            purpose="CUSTOMER CONVERSATION",
        )

    # ==========================================================
    # PROCESS CUSTOMER MESSAGE
    #
    # THIS IS THE PRIMARY ADA ENTRY POINT.
    #
    # Python does not interpret the customer's message.
    #
    # Python simply:
    #
    # 1. preserves service
    # 2. stores message
    # 3. sends conversation to Groq
    # 4. stores Groq response
    #
    # Groq does the intelligence.
    # ==========================================================

    def process_message(
        self,
        customer_message,
        service=None
    ):

        if customer_message is None:

            raise ValueError(
                "Customer message cannot be None."
            )

        customer_message = (
            str(customer_message)
            .strip()
        )

        if not customer_message:

            raise ValueError(
                "Customer message cannot be empty."
            )

        # ------------------------------------------------------
        # SERVICE BUTTON IS AUTHORITATIVE
        # ------------------------------------------------------

        if service:

            supplied_service = (
                self.normalize_service(service)
            )

            if supplied_service:

                self.job_state[
                    "service"
                ] = supplied_service

        active_service = (
            self.get_active_service()
        )

        # ------------------------------------------------------
        # DO NOT USE KEYWORD SERVICE DETECTION IF A SERVICE
        # WAS ALREADY SELECTED.
        #
        # The service button already told us what the customer
        # selected.
        # ------------------------------------------------------

        if not active_service:

            detected_service = (
                self.find_service_in_message(
                    customer_message
                )
            )

            if detected_service:

                active_service = (
                    self.normalize_service(
                        detected_service
                    )
                )

                self.job_state[
                    "service"
                ] = active_service

        print()
        print("=" * 80)
        print("ADA V13 CUSTOMER MESSAGE")
        print("=" * 80)
        print("SERVICE:", active_service)
        print("CUSTOMER:", repr(customer_message))
        print("INTELLIGENCE REQUESTS: 1")
        print("=" * 80)
        print()

        # ------------------------------------------------------
        # STORE CUSTOMER MESSAGE
        # ------------------------------------------------------

        self.memory.add_customer_message(
            customer_message
        )

        # ------------------------------------------------------
        # ONE GROQ REQUEST
        # ------------------------------------------------------

        reply = self.generate_response(
            customer_message=customer_message,
            service=active_service,
        )

        # ------------------------------------------------------
        # STORE ADA RESPONSE
        # ------------------------------------------------------

        self.memory.add_ada_message(
            reply
        )

        return reply

    # ==========================================================
    # CUSTOMER HISTORY
    # ==========================================================

    def get_customer_history(self):

        return (
            self.memory.get_conversation()
        )

    # ==========================================================
    # INTERVIEW STATUS
    #
    # Compatibility only.
    #
    # There is deliberately NO Groq call here.
    # Python does not determine interview completion.
    # ==========================================================

    def interview_is_complete(self):

        return bool(
            self.job_state.get(
                "interview_complete"
            )
        )

    def interview_completed(self):

        self.job_state[
            "interview_complete"
        ] = True

        return True

    # ==========================================================
    # DOCUMENT GENERATION
    #
    # This is a direct application request for finished work.
    #
    # It does NOT ask Groq whether the interview is complete.
    #
    # It simply gives Groq the customer's accumulated request
    # and asks for the finished document.
    # ==========================================================

    def generate_document_draft(
        self,
        service=None
    ):

        active_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        if not active_service:

            raise ValueError(
                "Cannot generate a document "
                "without an active service."
            )

        history = (
            self.get_customer_history()
        )

        document_context = ""

        if self.has_document_context():

            document_context = f"""

CUSTOMER-SUPPLIED DOCUMENT
===========================

{self.active_document_text}

===========================
"""

        generation_prompt = f"""
Produce the finished customer deliverable.

SERVICE:
{self.get_service_display_name(active_service)}

CUSTOMER CONVERSATION:
{history}

{document_context}

The customer has provided the above conversation
as the source of truth.

Use the customer's instructions and information.

Do not invent facts.

Do not invent:
- names
- dates
- qualifications
- companies
- figures
- events
- experiences
- references
- personal information

If the service is typing:
preserve supplied content faithfully.

If the service is editing:
correct and improve the supplied content while
preserving its meaning and facts.

If the service is rewriting:
produce a polished version without changing
the underlying facts.

If the service is research:
produce the requested research result in a
complete and useful structure.

If the service is a professional document:
produce a polished document suitable for
customer review.

Return ONLY the finished deliverable.

Do not describe the process.
Do not mention internal instructions.
"""

        messages = [

            {
                "role":
                    "system",

                "content":
                    self.get_system_prompt(
                        active_service
                    ),
            },

            {
                "role":
                    "user",

                "content":
                    generation_prompt,
            },

        ]

        draft = self._groq_request(
            messages=messages,
            temperature=0.4,
            purpose="DOCUMENT GENERATION",
        )

        self.current_draft = draft

        self.job_state[
            "draft_generated"
        ] = True

        self.job_state[
            "awaiting_review"
        ] = True

        return draft

    # ==========================================================
    # REVISE DOCUMENT
    #
    # Called only when the application has a real revision
    # request from the customer.
    # ==========================================================

    def revise_document(
        self,
        current_draft,
        revision_request,
        service=None
    ):

        if current_draft is None:

            raise ValueError(
                "Current document cannot be None."
            )

        current_draft = str(
            current_draft
        ).strip()

        if not current_draft:

            raise ValueError(
                "Current document cannot be empty."
            )

        if revision_request is None:

            raise ValueError(
                "Revision request cannot be None."
            )

        revision_request = str(
            revision_request
        ).strip()

        if not revision_request:

            raise ValueError(
                "Revision request cannot be empty."
            )

        active_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        revision_prompt = f"""
Revise the current customer document.

SERVICE:
{self.get_service_display_name(active_service)}

CURRENT DOCUMENT
================

{current_draft}

================

CUSTOMER REVISION REQUEST
==========================

{revision_request}

==========================

Apply the customer's requested changes.

Preserve correct facts.

Do not invent information.

Do not remove information unless requested.

Return ONLY the revised document.
"""

        messages = [

            {
                "role":
                    "system",

                "content":
                    self.get_system_prompt(
                        active_service
                    ),
            },

            {
                "role":
                    "user",

                "content":
                    revision_prompt,
            },

        ]

        revised = self._groq_request(
            messages=messages,
            temperature=0.3,
            purpose="DOCUMENT REVISION",
        )

        self.current_draft = revised

        self.job_state[
            "revision_requested"
        ] = True

        self.job_state[
            "revision_count"
        ] += 1

        self.job_state[
            "draft_generated"
        ] = True

        self.job_state[
            "awaiting_review"
        ] = True

        return revised

    # ==========================================================
    # APPROVAL
    #
    # Python records the actual application event.
    #
    # It does not decide whether the customer means approval.
    # The caller invokes this after the actual approval action.
    # ==========================================================

    def approve_document(self):

        self.job_state[
            "approved"
        ] = True

        self.job_state[
            "awaiting_review"
        ] = False

        return True

    # ==========================================================
    # PAYMENT
    # ==========================================================

    def mark_payment_received(self):

        self.job_state[
            "payment_received"
        ] = True

        return True

    # ==========================================================
    # DELIVERY
    # ==========================================================

    def mark_delivered(self):

        self.job_state[
            "delivered"
        ] = True

        return True


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("ADA AI ENGINE V13 DIRECT TEST")
    print("=" * 80)
    print()

    ada = AdaAIEngine()

    print(
        "Connected:",
        ada.is_connected()
    )

    print(
        "Model:",
        ada.get_model()
    )

    print()

    if not ada.is_connected():

        print(
            "TEST STOPPED: Groq is not connected."
        )

    else:

        try:

            while True:

                customer_message = input(
                    "Customer: "
                ).strip()

                if not customer_message:
                    continue

                if customer_message.lower() in {
                    "exit",
                    "quit",
                    "stop",
                }:

                    break

                response = ada.process_message(
                    customer_message=customer_message
                )

                print()
                print("Ada:")
                print(response)
                print()

        except KeyboardInterrupt:

            print()
            print("Ada test stopped.")

        except Exception as error:

            print()
            print("=" * 80)
            print("DIRECT TEST ERROR")
            print("=" * 80)

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR:",
                repr(error)
            )

            traceback.print_exc()

            print("=" * 80)
