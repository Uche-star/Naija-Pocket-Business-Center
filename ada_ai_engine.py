"""
ada_ai_engine.py

Ada AI Engine V12
Naija Pocket Business Center

PURPOSE
-------
Ada's intelligence is deliberately kept simple.

Groq is the intelligence.

Groq handles:
    - understanding the customer's request
    - asking necessary questions
    - understanding answers
    - maintaining conversational context
    - deciding what information is needed
    - deciding when enough information has been supplied
    - responding naturally
    - generating requested documents
    - revising documents when the customer requests revision

Python handles only application operations:
    - conversation memory
    - selected service
    - document context
    - job state
    - billing information
    - approval state
    - payment state
    - delivery state

IMPORTANT
---------
There is NO separate interview_is_complete() Groq request.

There is NO keyword-only interview engine.

There is NO artificial question sequence.

There is NO fallback AI response.

One customer message produces ONE Groq conversation request.

Document generation happens only when the application
explicitly asks Groq to generate the requested document.

Document revision happens only when the customer requests
a revision.

The goal is to move the customer from:

    SERVICE SELECTION
          ↓
    CONVERSATION
          ↓
    INFORMATION COMPLETE
          ↓
    DOCUMENT
          ↓
    REVIEW
          ↓
    APPROVAL
          ↓
    PAYMENT
          ↓
    DELIVERY

as quickly as practical.
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
    # INITIALIZE
    # ==========================================================

    def __init__(self):

        self.client = None
        self.connected = False

        self.memory = AdaConversationMemory()

        self.prompt_manager = AdaPromptManager()

        self.billing = BillingManager()

        self.active_document_text = ""
        self.active_document_path = None

        self.job_state = {
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

        self.connect()

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

        configured_model = os.getenv(
            "GROQ_MODEL"
        )

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

                    print(
                        "Configured:",
                        configured_model
                    )

                    print(
                        "Using:",
                        self.DEFAULT_MODEL
                    )

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

                print()
                print("=" * 70)
                print("GROQ CONNECTION ERROR")
                print("=" * 70)

                print(
                    "GROQ_API_KEY was not found."
                )

                print("=" * 70)
                print()

                self.connected = False

                return False

            self.client = Groq(
                api_key=api_key
            )

            self.connected = True

            print()
            print("=" * 70)
            print("ADA AI ENGINE V12")
            print("=" * 70)

            print(
                "Groq Connected:",
                True
            )

            print(
                "Groq Model:",
                self.get_model()
            )

            print(
                "Conversation:",
                "DIRECT GROQ MULTI-TURN"
            )

            print(
                "Interview Check:",
                "DISABLED"
            )

            print(
                "Python Interview Logic:",
                "DISABLED"
            )

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

            normalized = (
                self.normalize_service(
                    service
                )
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

        normalized = (
            self.normalize_service(
                service
            )
        )

        return self.billing.get_price(
            normalized
        )

    # ==========================================================
    # BILLING RULES
    #
    # Used only when billing information is actually needed.
    #
    # We deliberately DO NOT inject the complete price list
    # into every conversation request.
    # ==========================================================

    def get_billing_rules(self):

        services = []

        try:

            price_list = (
                self.billing.get_price_list()
            )

            for service, info in price_list.items():

                billing = info.get(
                    "billing"
                )

                price = info.get(
                    "price"
                )

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
            "Never invent or estimate prices.\n"
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
            self.normalize_service(
                service
            )
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
    # IMPORTANT:
    #
    # This is intentionally focused.
    #
    # Do not overload every conversation request with
    # unnecessary operational instructions.
    # ==========================================================

    def get_system_prompt(
        self,
        service=None
    ):

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        base_prompt = (
            self.prompt_manager.build_prompt(
                service=normalized_service
            )
        )

        selected_service = (
            self.get_service_display_name(
                normalized_service
            )
        )

        workflow_prompt = f"""

==================================================
ADA CUSTOMER CONVERSATION
==================================================

You are Ada, the customer-facing Business Center
assistant for Naija Pocket Business Center.

SELECTED SERVICE:
{selected_service}

Your job is to understand what the customer wants
and help them complete that service efficiently.

CONVERSATION RULES
------------------

- Talk naturally.
- Understand the customer's actual request.
- Remember everything already supplied.
- Never ask for information the customer already gave.
- Ask only questions that are genuinely necessary.
- Do not force an artificial interview sequence.
- Do not restart the conversation.
- If the request is already sufficiently clear,
  move forward.
- If information is missing, ask for it naturally.
- If the customer changes the request, adapt.
- Never invent facts.
- Never invent customer information.
- Never claim a document exists unless the document
  generation operation has actually produced it.
- Never claim payment has been received unless the
  application has confirmed payment.
- Never claim delivery has happened unless the
  application has confirmed delivery.

IMPORTANT
---------

You are the intelligence layer.

Do not discuss:

- Python
- FastAPI
- Groq
- API calls
- controllers
- internal memory
- application code
- internal workflow

The customer should experience one seamless assistant.

When enough information has been supplied for the
requested work, clearly indicate that you have enough
information and that the work can proceed.

Do not perform unnecessary questioning.

Do not perform unnecessary regeneration.

==================================================
"""

        document_context = ""

        if self.has_document_context():

            document_context = f"""

==================================================
CUSTOMER DOCUMENT CONTEXT
==================================================

The customer has already supplied document content.

Use it when relevant.

Do NOT ask the customer to provide the same content
again.

DOCUMENT CONTENT
----------------

{self.active_document_text}

==================================================
"""

        return (
            base_prompt
            + workflow_prompt
            + document_context
        )

    # ==========================================================
    # MEMORY → GROQ
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

        self.job_state = {
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
            self.normalize_service(
                service
            )
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

        # ------------------------------------------------------
        # BillingManager aliases
        # ------------------------------------------------------

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

        # ------------------------------------------------------
        # Direct service names
        # ------------------------------------------------------

        try:

            for service in (
                self.billing
                .get_price_list()
                .keys()
            ):

                readable = (
                    service
                    .replace("_", " ")
                    .lower()
                )

                if readable in text:

                    return service

        except Exception as error:

            print(
                "Service List Error:",
                repr(error)
            )

        # ------------------------------------------------------
        # Common customer phrases
        # ------------------------------------------------------

        phrase_map = {

            "cv":
                "cv",

            "resume":
                "cv",

            "résumé":
                "cv",

            "cover letter":
                "cover_letter",

            "assignment":
                "assignment_typing",

            "project":
                "project_typing",

            "seminar":
                "seminar_paper",

            "business proposal":
                "business_proposal",

            "company profile":
                "company_profile",

            "invoice":
                "invoices",

            "quotation":
                "quotations",

            "meeting minutes":
                "meeting_minutes",

            "typing":
                "document_typing",

            "formatting":
                "document_formatting",

            "editing":
                "document_editing",

            "grammar":
                "grammar_correction",

            "translation":
                "translation",

            "summarize":
                "summarization",

            "summarisation":
                "summarization",

            "pdf":
                "pdf_conversion",

            "voice to text":
                "voice_to_text",

            "excel":
                "excel_spreadsheets",

            "data entry":
                "data_entry",

            "data analysis":
                "data_analysis",

            "presentation":
                "presentations",

            "presentations":
                "presentations",

            "research":
                "research_assistance",

            "research assistance":
                "research_assistance",

            "topic explanation":
                "topic_explanations",

            "topic explanations":
                "topic_explanations",
        }

        sorted_phrases = sorted(
            phrase_map.items(),
            key=lambda item: len(item[0]),
            reverse=True
        )

        for phrase, service in sorted_phrases:

            if phrase in text:

                return service

        return None

    # ==========================================================
    # GENERATE NORMAL CONVERSATION RESPONSE
    #
    # ONE CALL ONLY.
    # ==========================================================

    def generate_response(
        self,
        customer_message,
        temperature=0.4,
        service=None
    ):

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        messages = [

            {
                "role":
                    "system",

                "content":
                    self.get_system_prompt(
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

        # ------------------------------------------------------
        # SAFETY:
        #
        # If memory implementation does not contain the current
        # message, append it.
        #
        # Normally process_message() adds the customer message
        # before this method, so this does not duplicate it.
        # ------------------------------------------------------

        if not history_messages:

            messages.append(
                {
                    "role":
                        "user",

                    "content":
                        customer_message,
                }
            )

        print()
        print("=" * 80)
        print("ADA V12 → GROQ")
        print("=" * 80)

        print(
            "MODEL:",
            self.get_model()
        )

        print(
            "SERVICE:",
            normalized_service
        )

        print(
            "MEMORY MESSAGES:",
            len(history_messages)
        )

        print(
            "CUSTOMER MESSAGE:",
            repr(customer_message)
        )

        print(
            "GROQ REQUESTS THIS TURN:",
            1
        )

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

            print(
                "RESPONSE:",
                repr(reply)
            )

            print("=" * 80)
            print()

            return reply

        except Exception as error:

            self._log_error(
                "REAL GROQ CONVERSATION ERROR",
                error
            )

            raise

    # ==========================================================
    # PROCESS CUSTOMER MESSAGE
    #
    # THIS IS THE MAIN ADA ENTRY POINT.
    #
    # ONE CUSTOMER MESSAGE
    #         ↓
    # ONE GROQ REQUEST
    #         ↓
    # ONE ADA RESPONSE
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
        # SERVICE
        # ------------------------------------------------------

        active_service = (
            self.get_active_service()
        )

        supplied_service = (
            self.normalize_service(
                service
            )
            if service
            else None
        )

        detected_service = (
            self.find_service_in_message(
                customer_message
            )
        )

        if supplied_service:

            active_service = supplied_service

        elif detected_service:

            active_service = detected_service

        if active_service:

            self.job_state[
                "service"
            ] = active_service

        # ------------------------------------------------------
        # CONVERSATION
        # ------------------------------------------------------

        is_new_conversation = not (
            self.memory.has_conversation()
        )

        print()
        print("=" * 80)

        if is_new_conversation:

            print(
                "ADA V12 NEW CONVERSATION"
            )

        else:

            print(
                "ADA V12 CONTINUING CONVERSATION"
            )

        print("=" * 80)

        print(
            "ACTIVE SERVICE:",
            active_service
        )

        print(
            "CUSTOMER:",
            repr(customer_message)
        )

        print("=" * 80)
        print()

        # ------------------------------------------------------
        # STORE CUSTOMER MESSAGE
        # ------------------------------------------------------

        self.memory.add_customer_message(
            customer_message
        )

        # ------------------------------------------------------
        # ONE INTELLIGENCE REQUEST
        # ------------------------------------------------------

        reply = self.generate_response(
            customer_message=customer_message,
            service=active_service
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
    # NO GROQ CALL.
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
    # This is deliberately separate from normal conversation.
    #
    # It is called only when the application decides that the
    # customer is ready for document preparation.
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

        writer_prompt = f"""
Prepare the customer's requested work.

SERVICE:
{self.get_service_display_name(active_service)}

CUSTOMER CONVERSATION:
{history}

{document_context}

IMPORTANT:

Use the customer's supplied information.

Do not invent:
- names
- dates
- qualifications
- companies
- figures
- events
- experiences
- references
- facts

If the service is typing, preserve the supplied
content faithfully.

If the service is editing or rewriting, improve
the writing while preserving facts.

If the service is research assistance, produce
a complete, well-organized result based on the
customer's request.

If the service requires a professional document,
produce a polished professional document suitable
for customer review.

Return ONLY the finished document content.

Do not explain what you did.
Do not discuss internal instructions.
"""

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        print()
        print("=" * 80)
        print("ADA V12 DOCUMENT GENERATION → GROQ")
        print("=" * 80)

        print(
            "SERVICE:",
            active_service
        )

        print("=" * 80)
        print()

        try:

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),

                    messages=[

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
                                writer_prompt,
                        },

                    ],

                    temperature=0.4,
                )
            )

            draft = (
                response
                .choices[0]
                .message
                .content
            )

            if draft is None:

                raise RuntimeError(
                    "Groq returned None while "
                    "generating the document."
                )

            draft = str(
                draft
            ).strip()

            if not draft:

                raise RuntimeError(
                    "Groq returned an empty "
                    "document draft."
                )

            self.job_state[
                "draft_generated"
            ] = True

            self.job_state[
                "awaiting_review"
            ] = True

            return draft

        except Exception as error:

            self._log_error(
                "REAL GROQ DOCUMENT GENERATION ERROR",
                error
            )

            raise

    # ==========================================================
    # REVISE DOCUMENT
    #
    # ONLY CALLED WHEN CUSTOMER REQUESTS REVISION.
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

        prompt = f"""
Revise the document below according to the
customer's request.

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

Rules:

- Apply the requested changes.
- Preserve correct facts.
- Do not invent information.
- Do not remove information unless requested.
- Keep the document professional.
- Return ONLY the revised document.
"""

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        print()
        print("=" * 80)
        print("ADA V12 DOCUMENT REVISION → GROQ")
        print("=" * 80)

        print(
            "SERVICE:",
            active_service
        )

        print("=" * 80)
        print()

        try:

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),

                    messages=[

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
                                prompt,
                        },

                    ],

                    temperature=0.3,
                )
            )

            revised = (
                response
                .choices[0]
                .message
                .content
            )

            if revised is None:

                raise RuntimeError(
                    "Groq returned None while "
                    "revising the document."
                )

            revised = str(
                revised
            ).strip()

            if not revised:

                raise RuntimeError(
                    "Groq returned an empty "
                    "revised document."
                )

            self.job_state[
                "revision_requested"
            ] = True

            self.job_state[
                "revision_count"
            ] += 1

            self.job_state[
                "awaiting_review"
            ] = True

            return revised

        except Exception as error:

            self._log_error(
                "REAL GROQ DOCUMENT REVISION ERROR",
                error
            )

            raise

    # ==========================================================
    # APPROVE DOCUMENT
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
    print("ADA AI ENGINE V12 DIRECT TEST")
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
            print(
                "Ada test stopped."
            )

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
