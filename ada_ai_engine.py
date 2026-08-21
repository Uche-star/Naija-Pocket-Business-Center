"""
ada_ai_engine.py
Ada AI Engine V11
Naija Pocket Business Center

PURPOSE
-------
This version deliberately simplifies Ada's intelligence flow.

Groq is the primary intelligence.

Groq handles:
    - understanding the customer's request
    - asking questions
    - understanding answers
    - continuing the conversation
    - deciding when enough information has been supplied
    - responding naturally

Python handles:
    - conversation memory
    - selected service
    - document context
    - job state
    - document generation
    - revision
    - approval
    - payment state
    - delivery state

IMPORTANT
---------
There is NO separate interview_is_complete() Groq call
after every customer message.

This is intentional.

One customer message should produce ONE intelligence request.

The previous architecture could do:

    Customer message
          ↓
    Groq response
          ↓
    second Groq request
          ↓
    interview_is_complete()

That second request was unnecessary and could break the
conversation flow.

Groq is allowed to conduct the conversation naturally.

MODEL
-----
Read from GROQ_MODEL.

Deprecated models are automatically replaced with:

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
    # DEFAULT MODEL
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

        configured_model = os.getenv("GROQ_MODEL")

        if configured_model:

            configured_model = str(
                configured_model
            ).strip()

            if configured_model:

                if configured_model in self.DEPRECATED_MODELS:

                    print()
                    print("=" * 60)
                    print("DEPRECATED GROQ MODEL")
                    print("=" * 60)
                    print(
                        "Configured:",
                        configured_model
                    )
                    print(
                        "Using:",
                        self.DEFAULT_MODEL
                    )
                    print("=" * 60)
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
                print("=" * 60)
                print("GROQ CONNECTION ERROR")
                print("=" * 60)
                print(
                    "GROQ_API_KEY was not found."
                )
                print("=" * 60)
                print()

                self.connected = False

                return False

            self.client = Groq(
                api_key=api_key
            )

            self.connected = True

            print()
            print("=" * 60)
            print("ADA AI ENGINE V11")
            print("=" * 60)
            print(
                "Groq Connected:",
                True
            )
            print(
                "Groq Model:",
                self.get_model()
            )
            print(
                "Conversation Mode:",
                "DIRECT GROQ MULTI-TURN"
            )
            print(
                "Interview Check:",
                "DISABLED"
            )
            print("=" * 60)
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
        print("=" * 70)
        print(title)
        print("=" * 70)

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
        print("-" * 70)

        traceback.print_exc()

        print("=" * 70)
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

        if not extracted_text:
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

            return service

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
            "These prices come ONLY from "
            "BillingManager.\n"
            "Never invent prices.\n"
            "Never estimate prices.\n"
            "Never change prices.\n"
            "Never offer discounts unless instructed.\n\n"
            "OFFICIAL PRICE LIST\n\n"
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
            return str(service)

        return (
            internal_service
            .replace("_", " ")
            .title()
        )

    # ==========================================================
    # SYSTEM PROMPT
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

        prompt = (
            self.prompt_manager.build_prompt(
                service=normalized_service
            )
        )

        workflow_instructions = f"""

==================================================
ADA WORKFLOW INSTRUCTION
==================================================

You are Ada, the customer-facing Business Center
assistant for Naija Pocket Business Center.

SELECTED SERVICE:
{self.get_service_display_name(normalized_service)
 if normalized_service else "Not yet selected"}

YOUR MAIN RESPONSIBILITY
------------------------
Work naturally with the customer from the beginning
of the request until enough information is available
for the requested work.

Do NOT force the customer through an artificial
question sequence.

Do NOT ask unnecessary questions.

Do NOT repeat a question that the customer has
already answered.

Use the conversation history to understand what the
customer has already told you.

If the customer's request is already clear enough,
move forward instead of asking another unnecessary
question.

If important information is missing, ask for it
naturally.

If the customer answers a question, use that answer
immediately.

If the customer changes or clarifies the request,
adapt to the new information.

IMPORTANT:
You are allowed to have a normal multi-turn
conversation.

You do NOT need a separate interview-complete check.

When enough information has been gathered, tell the
customer that you have enough information and that
the work can proceed to preparation/review.

Do not claim that a document has been created unless
the document-generation operation has actually created
one.

Do not claim that payment has been received unless
the application has confirmed payment.

Do not claim that a document has been delivered unless
the application has confirmed delivery.

Never expose internal Python, FastAPI, Groq,
controller, memory, API, or workflow details.

==================================================
CONVERSATION PRIORITY
==================================================

1. Understand the customer's actual request.
2. Remember previous answers.
3. Ask only necessary questions.
4. Do not restart the interview.
5. Do not forget earlier answers.
6. Do not fabricate information.
7. Move toward completing the selected service.
"""

        document_context = ""

        if self.has_document_context():

            document_context = f"""

==================================================
ACTIVE DOCUMENT CONTEXT
==================================================

The customer has already supplied document content.

Do NOT ask the customer to provide the document
text again.

Do NOT pretend that the document is missing.

Use the supplied text when appropriate.

EXTRACTED DOCUMENT TEXT
------------------------

{self.active_document_text}

------------------------
"""

        return (
            prompt
            + workflow_instructions
            + document_context
            + "\n\n"
            + self.get_billing_rules()
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
                            "content": content
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
                            "content": content
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

    # ==========================================================
    # START JOB
    # ==========================================================

    def start_job(
        self,
        service
    ):

        self.reset_job()

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        self.job_state[
            "service"
        ] = normalized_service

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

        if normalized_service:

            self.job_state[
                "service"
            ] = normalized_service

            return True

        return False

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

        phrase_map = {

            "cv": "cv",
            "resume": "cv",
            "résumé": "cv",
            "cover letter": "cover_letter",
            "assignment": "assignment_typing",
            "project": "project_typing",
            "seminar": "seminar_paper",
            "business proposal": "business_proposal",
            "company profile": "company_profile",
            "invoice": "invoices",
            "quotation": "quotations",
            "meeting minutes": "meeting_minutes",
            "typing": "document_typing",
            "formatting": "document_formatting",
            "editing": "document_editing",
            "grammar": "grammar_correction",
            "translation": "translation",
            "summarize": "summarization",
            "summarisation": "summarization",
            "pdf": "pdf_conversion",
            "voice to text": "voice_to_text",
            "excel": "excel_spreadsheets",
            "data entry": "data_entry",
            "data analysis": "data_analysis",
            "presentation": "presentations",
            "presentations": "presentations",
            "research": "research_assistance",
            "research assistance": "research_assistance",
            "topic explanation": "topic_explanations",
            "topic explanations": "topic_explanations",
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
                "role": "system",
                "content": self.get_system_prompt(
                    normalized_service
                )
            }
        ]

        history_messages = (
            self.build_history_messages()
        )

        messages.extend(
            history_messages
        )

        # ------------------------------------------------------
        # IMPORTANT
        #
        # The current customer message is normally already
        # stored in memory before this function is called.
        #
        # Therefore we do NOT automatically append it again.
        #
        # This prevents:
        #
        # Customer answer
        # Customer answer
        #
        # appearing twice in the Groq conversation.
        # ------------------------------------------------------

        if not history_messages:

            messages.append(
                {
                    "role": "user",
                    "content": customer_message
                }
            )

        try:

            print()
            print("=" * 70)
            print("ADA → GROQ")
            print("=" * 70)

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
                "GROQ REQUEST COUNT THIS TURN:",
                1
            )

            print("=" * 70)
            print()

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),
                    messages=messages,
                    temperature=temperature
                )
            )

            reply = (
                response
                .choices[0]
                .message
                .content
            )

            if not reply:

                raise RuntimeError(
                    "Groq returned an empty response."
                )

            reply = str(
                reply
            ).strip()

            if not reply:

                raise RuntimeError(
                    "Groq returned an empty response."
                )

            print()
            print("=" * 70)
            print("GROQ RESPONSE RECEIVED")
            print("=" * 70)

            print(
                "Response:",
                repr(reply)
            )

            print("=" * 70)
            print()

            return reply

        except Exception as error:

            self._log_error(
                "REAL GROQ CONVERSATION ERROR",
                error
            )

            raise

    # ==========================================================
    # PROCESS MESSAGE
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
        # DETERMINE SERVICE
        # ------------------------------------------------------

        active_service = (
            self.get_active_service()
        )

        supplied_service = (
            self.normalize_service(service)
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

        elif not active_service and detected_service:

            active_service = detected_service

        elif detected_service:

            active_service = detected_service

        if active_service:

            self.job_state[
                "service"
            ] = active_service

        # ------------------------------------------------------
        # FIRST MESSAGE
        # ------------------------------------------------------

        if not self.memory.has_conversation():

            print()
            print("=" * 70)
            print("ADA NEW CONVERSATION")
            print("=" * 70)

            print(
                "Service:",
                active_service
            )

            print(
                "Customer:",
                customer_message
            )

            print("=" * 70)
            print()

            self.memory.add_customer_message(
                customer_message
            )

            reply = self.generate_response(
                customer_message=customer_message,
                service=active_service
            )

            self.memory.add_ada_message(
                reply
            )

            return reply

        # ------------------------------------------------------
        # CONTINUING MESSAGE
        # ------------------------------------------------------

        print()
        print("=" * 70)
        print("ADA CONTINUING CONVERSATION")
        print("=" * 70)

        print(
            "Active Service:",
            active_service
        )

        print(
            "Customer:",
            customer_message
        )

        print("=" * 70)
        print()

        self.memory.add_customer_message(
            customer_message
        )

        reply = self.generate_response(
            customer_message=customer_message,
            service=active_service
        )

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
    # ==========================================================
    #
    # IMPORTANT:
    # No extra Groq request.
    #
    # This method remains only for compatibility with
    # older controller code.
    #
    # The application should not use this to trigger
    # another intelligence request.
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

    # ==========================================================
    # DOCUMENT DRAFT
    # ==========================================================

    def generate_document_draft(
        self,
        service=None
    ):

        active_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        history = (
            self.get_customer_history()
        )

        document_context = ""

        if self.has_document_context():

            document_context = f"""

DOCUMENT ALREADY SUPPLIED BY CUSTOMER
=====================================

{self.active_document_text}

=====================================
"""

        writer_prompt = f"""
You are Ada's Senior Nigerian Professional
Document Writer.

Prepare the customer's requested work.

SERVICE:
{active_service}

CUSTOMER CONVERSATION:
{history}

{document_context}

Rules:

- Use information supplied by the customer.
- Do not invent facts.
- Do not invent names, dates, qualifications,
  figures or events.
- Preserve the customer's intended meaning.
- Follow the selected service.
- If the customer requested an explanation,
  produce a complete, clear explanation.
- If the customer requested typing,
  reproduce the supplied content faithfully.
- If the customer requested rewriting,
  improve the writing without changing facts.
- If the customer requested research assistance,
  organize the requested research clearly.
- Return ONLY the finished document content.
"""

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        try:

            print()
            print("=" * 70)
            print("ADA DOCUMENT GENERATION → GROQ")
            print("=" * 70)
            print(
                "SERVICE:",
                active_service
            )
            print("=" * 70)
            print()

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),
                    messages=[
                        {
                            "role": "system",
                            "content": self.get_system_prompt(
                                active_service
                            )
                        },
                        {
                            "role": "user",
                            "content": writer_prompt
                        }
                    ],
                    temperature=0.4
                )
            )

            draft = (
                response
                .choices[0]
                .message
                .content
            )

            if not draft:

                raise RuntimeError(
                    "Groq returned an empty document draft."
                )

            draft = str(
                draft
            ).strip()

            if not draft:

                raise RuntimeError(
                    "Groq returned an empty document draft."
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
    # ==========================================================

    def revise_document(
        self,
        current_draft,
        revision_request,
        service=None
    ):

        active_service = (
            self.normalize_service(service)
            or self.get_active_service()
        )

        prompt = f"""
You are Ada, the professional document
assistant for Naija Pocket Business Center.

SERVICE:
{active_service}

CURRENT DOCUMENT:
-----------------
{current_draft}
-----------------

CUSTOMER REVISION REQUEST:
--------------------------
{revision_request}
--------------------------

Apply the customer's requested changes.

Rules:

- Preserve correct information.
- Do not invent facts.
- Do not remove information unless requested.
- Keep the document professional.
- Return ONLY the revised document.
"""

        if not self.connected:

            raise RuntimeError(
                "Groq is not connected. "
                "Check GROQ_API_KEY."
            )

        try:

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),
                    messages=[
                        {
                            "role": "system",
                            "content": self.get_system_prompt(
                                active_service
                            )
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=0.3
                )
            )

            revised = (
                response
                .choices[0]
                .message
                .content
            )

            if not revised:

                raise RuntimeError(
                    "Groq returned an empty revised document."
                )

            revised = str(
                revised
            ).strip()

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
    # APPROVE
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
    print("=" * 70)
    print("ADA AI ENGINE V11 DIRECT TEST")
    print("=" * 70)
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
                    customer_message=customer_message,
                    service="Research Assistance"
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
            print("=" * 70)
            print("DIRECT TEST ERROR")
            print("=" * 70)

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR:",
                repr(error)
            )

            traceback.print_exc()

            print("=" * 70)
