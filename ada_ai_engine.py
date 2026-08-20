"""
ada_ai_engine.py
Ada AI Engine V10
Naija Pocket Business Center

PRIMARY INTELLIGENCE:
    Groq

MODEL:
    Read from GROQ_MODEL environment variable.

    IMPORTANT:
    Groq permanently shut down:
        llama-3.1-8b-instant
        llama-3.3-70b-versatile

    If either deprecated model is found in the environment,
    this engine automatically uses:

        openai/gpt-oss-20b

API KEY:
    Read from GROQ_API_KEY environment variable.

IMPORTANT:
    This file DOES NOT import ada_ai_config.py.
    The Groq API key must NEVER be stored in GitHub.

ERROR HANDLING:
    Real Groq errors are deliberately NOT hidden.

    Every Groq request prints:
        MODEL
        ERROR TYPE
        REAL GROQ ERROR
        FULL TRACEBACK

    The original exception is then re-raised so that
    FastAPI / AdaController can expose the actual failure.
"""

import os
import traceback

from groq import Groq

from ada_prompt_manager import AdaPromptManager
from ada_conversation_memory import AdaConversationMemory
from billing_manager import BillingManager


class AdaAIEngine:

    # ==================================================
    # CURRENT GROQ DEFAULT MODEL
    # ==================================================

    DEFAULT_MODEL = "openai/gpt-oss-20b"

    # Groq models that have already been shut down.
    # If one is still present in Render environment variables,
    # do NOT use it.
    DEPRECATED_MODELS = {
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
    }

    # ==================================================
    # INITIALIZE
    # ==================================================

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
            "delivered": False
        }

        self.connect()

    # ==================================================
    # GROQ API KEY
    # ==================================================

    def get_api_key(self):

        possible_names = [
            "GROQ_API_KEY",
            "GROQ_KEY",
            "API_KEY"
        ]

        for name in possible_names:

            value = os.getenv(name)

            if value:

                value = str(value).strip()

                if value:
                    return value

        return None

    # ==================================================
    # GROQ MODEL
    # ==================================================

    def get_model(self):

        configured_model = os.getenv(
            "GROQ_MODEL"
        )

        if configured_model:

            configured_model = str(
                configured_model
            ).strip()

            if configured_model:

                # ------------------------------------------
                # IMPORTANT:
                # Never use the old shut-down Groq models.
                # ------------------------------------------

                if configured_model in self.DEPRECATED_MODELS:

                    print()
                    print("=" * 60)
                    print("DEPRECATED GROQ MODEL DETECTED")
                    print("=" * 60)
                    print(
                        "Configured Model:",
                        configured_model
                    )
                    print(
                        "Replacement Model:",
                        self.DEFAULT_MODEL
                    )
                    print(
                        "The deprecated model will NOT be used."
                    )
                    print("=" * 60)
                    print()

                    return self.DEFAULT_MODEL

                return configured_model

        return self.DEFAULT_MODEL

    # ==================================================
    # CONNECT TO GROQ
    # ==================================================

    def connect(self):

        try:

            api_key = self.get_api_key()

            if not api_key:

                print()
                print("=" * 60)
                print("GROQ CONNECTION ERROR")
                print("=" * 60)
                print(
                    "GROQ_API_KEY environment variable "
                    "was not found."
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
            print("GROQ CONNECTION")
            print("=" * 60)
            print("Groq Connected: True")
            print(
                "Groq Model:",
                self.get_model()
            )
            print("=" * 60)
            print()

            return True

        except Exception as error:

            self.client = None
            self.connected = False

            self._log_groq_error(
                "GROQ CONNECTION ERROR",
                error
            )

            return False

    # ==================================================
    # CENTRAL GROQ ERROR LOGGER
    # ==================================================

    def _log_groq_error(
        self,
        title,
        error
    ):

        print()
        print("=" * 70)
        print(title)
        print("=" * 70)

        print(
            "MODEL:",
            self.get_model()
        )

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

    # ==================================================
    # CONNECTION STATUS
    # ==================================================

    def is_connected(self):
        return self.connected

    # ==================================================
    # DOCUMENT CONTEXT
    # ==================================================

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

    # ==================================================
    # SERVICE NORMALIZATION
    # ==================================================

    def normalize_service(self, service):

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

    # ==================================================
    # SERVICE EXISTS
    # ==================================================

    def service_exists(self, service):

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

    # ==================================================
    # SERVICE PRICE
    # ==================================================

    def get_service_price(self, service):

        normalized = (
            self.normalize_service(
                service
            )
        )

        return self.billing.get_price(
            normalized
        )

    # ==================================================
    # BILLING RULES
    # ==================================================

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
            "Official Price List\n\n"
            + "\n".join(services)
        )

    # ==================================================
    # SERVICE DISPLAY NAME
    # ==================================================

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

    # ==================================================
    # SYSTEM PROMPT
    # ==================================================

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

        document_context = ""

        if self.has_document_context():

            document_context = (
                "\n\n"
                "==================================================\n"
                "ACTIVE DOCUMENT CONTEXT\n"
                "==================================================\n"
                "The customer has already supplied "
                "a document and its readable text "
                "has already been extracted.\n\n"
                "IMPORTANT:\n"
                "Do NOT ask the customer to provide "
                "the document text again.\n"
                "Do NOT pretend the document has not "
                "been received.\n"
                "Treat the extracted text below as "
                "customer-provided document content.\n"
                "Use it when the customer asks you "
                "to type, rewrite, format, edit, "
                "summarize, convert or otherwise "
                "work on the document.\n"
                "Do not invent missing words or facts.\n\n"
                "EXTRACTED DOCUMENT TEXT\n"
                "------------------------\n"
                + self.active_document_text
                + "\n"
                "------------------------\n"
            )

        return (
            prompt
            + document_context
            + "\n\n"
            + self.get_billing_rules()
        )

    # ==================================================
    # CONVERT MEMORY TO GROQ MESSAGES
    # ==================================================

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

    # ==================================================
    # RESET JOB
    # ==================================================

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
            "delivered": False
        }

    # ==================================================
    # START NEW JOB
    # ==================================================

    def start_job(self, service):

        self.reset_job()

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        self.job_state[
            "service"
        ] = normalized_service

    # ==================================================
    # JOB STATUS
    # ==================================================

    def get_job_state(self):

        return self.job_state

    # ==================================================
    # SET ACTIVE SERVICE
    # ==================================================

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

    # ==================================================
    # GET ACTIVE SERVICE
    # ==================================================

    def get_active_service(self):

        return self.job_state.get(
            "service"
        )

    # ==================================================
    # PRICE REQUEST DETECTION
    # ==================================================

    def detect_price_request(
        self,
        customer_message
    ):

        if not customer_message:
            return False

        text = (
            str(customer_message)
            .strip()
            .lower()
        )

        price_words = (
            "price",
            "pricing",
            "cost",
            "charge",
            "charges",
            "how much",
            "how much is",
            "how much for",
            "what is the price",
            "what's the price",
            "what is your price",
            "what do you charge",
            "how much una",
            "how much una dey charge",
            "how much do you charge",
            "fee",
            "fees"
        )

        return any(
            word in text
            for word in price_words
        )

    # ==================================================
    # FIND SERVICE IN MESSAGE
    # ==================================================

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
            "presentations": "presentations"
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

    # ==================================================
    # PRICE RESPONSE
    # ==================================================

    def generate_price_response(
        self,
        service
    ):

        normalized_service = (
            self.normalize_service(
                service
            )
        )

        if not normalized_service:

            return (
                "I couldn't match that service "
                "to our current price list.\n\n"
                "Please tell me the exact service "
                "you need."
            )

        if not self.billing.has_service(
            normalized_service
        ):

            return (
                "Sorry, pricing is currently "
                "unavailable for that service."
            )

        try:

            info = (
                self.billing.get_service(
                    normalized_service
                )
            )

        except Exception as error:

            print(
                "Billing Service Error:",
                repr(error)
            )

            raise

        service_name = (
            self.get_service_display_name(
                normalized_service
            )
        )

        billing_type = info["billing"]
        amount = info["price"]

        if billing_type == "fixed":

            price_information = (
                f"Service: {service_name}\n"
                f"Official Price: ₦{amount:,}\n"
                f"Billing Type: Fixed"
            )

        elif billing_type == "per_page":

            price_information = (
                f"Service: {service_name}\n"
                f"Official Price: ₦{amount:,} per page\n"
                f"Billing Type: Per Page"
            )

        elif billing_type == "quotation":

            price_information = (
                f"Service: {service_name}\n"
                "Billing Type: Quotation Required"
            )

        else:

            price_information = (
                f"Service: {service_name}\n"
                "Billing Type: Internal"
            )

        prompt = f"""
You are Ada, the friendly Nigerian Business
Center Agent for Naija Pocket Business Center.

The customer has asked about the price of a service.

Use ONLY this official BillingManager information:

{price_information}

Rules:

- Never change the official price.
- Never invent another amount.
- Never estimate.
- Never add a charge.
- Never remove a charge.
- Never invent a discount.
- Speak naturally.
- Be friendly and professional.
- Use clear Nigerian English.
- A small amount of natural Nigerian warmth
  is acceptable.
- Do not overuse Pidgin.

If fixed price:
State the exact price.

If per page:
State the exact price per page and explain
that the final amount depends on the number
of pages.

If quotation:
Explain that the document/details need to
be reviewed before quotation.

After giving the price, naturally tell the
customer what they can do next.

Return ONLY the customer-facing response.
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
                            "content": prompt
                        }
                    ],
                    temperature=0.4
                )
            )

            reply = (
                response
                .choices[0]
                .message
                .content
            )

            if reply:
                return reply.strip()

            raise RuntimeError(
                "Groq returned an empty price response."
            )

        except Exception as error:

            self._log_groq_error(
                "REAL GROQ PRICE ERROR",
                error
            )

            raise

    # ==================================================
    # SEND MESSAGE TO GROQ
    # ==================================================

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

        current_message_exists = False

        if history_messages:

            last_message = (
                history_messages[-1]
            )

            if (
                last_message.get("role")
                == "user"
                and
                last_message.get("content")
                == customer_message
            ):

                current_message_exists = True

        if not current_message_exists:

            messages.append(
                {
                    "role": "user",
                    "content": customer_message
                }
            )

        try:

            print()
            print("=" * 60)
            print("ADA → GROQ REQUEST")
            print("=" * 60)
            print(
                "MODEL:",
                self.get_model()
            )
            print(
                "SERVICE:",
                normalized_service
            )
            print(
                "CUSTOMER MESSAGE:",
                customer_message
            )
            print("=" * 60)
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

            if reply:

                return reply.strip()

            raise RuntimeError(
                "Groq returned an empty response."
            )

        except Exception as error:

            self._log_groq_error(
                "REAL GROQ ERROR",
                error
            )

            # --------------------------------------------------
            # CRITICAL:
            # DO NOT HIDE THE REAL ERROR.
            # --------------------------------------------------

            raise

    # ==================================================
    # INTERVIEW CHECK
    # ==================================================

    def interview_is_complete(self):

        history = (
            self.memory.get_conversation()
        )

        document_context = ""

        if self.has_document_context():

            document_context = (
                "\n\n"
                "The customer has already supplied "
                "a document and its text has already "
                "been extracted. Do not treat the "
                "document text as missing."
            )

        prompt = f"""
You are monitoring Ada's interview.

Conversation:
{history}

{document_context}

Determine whether Ada now has enough
information to prepare the customer's
requested document.

If the customer has already supplied
document text and clearly instructed Ada
what to do with it, that may be enough.

Reply with ONLY one word:

YES

or

NO
"""

        if not self.connected:

            return False

        try:

            response = (
                self.client
                .chat
                .completions
                .create(
                    model=self.get_model(),
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=0
                )
            )

            answer = (
                response
                .choices[0]
                .message
                .content
                .strip()
                .upper()
            )

            return answer.startswith("YES")

        except Exception as error:

            self._log_groq_error(
                "REAL GROQ INTERVIEW CHECK ERROR",
                error
            )

            raise

    # ==================================================
    # START CONVERSATION
    # ==================================================

    def start_conversation(
        self,
        customer_message,
        service=None
    ):

        detected_service = (
            self.find_service_in_message(
                customer_message
            )
        )

        normalized_service = (
            self.normalize_service(service)
            if service
            else None
        )

        if not normalized_service:

            normalized_service = (
                detected_service
            )

        self.start_job(
            normalized_service
        )

        self.memory.add_customer_message(
            customer_message
        )

        if (
            self.detect_price_request(
                customer_message
            )
            and detected_service
        ):

            reply = (
                self.generate_price_response(
                    detected_service
                )
            )

        else:

            reply = (
                self.generate_response(
                    customer_message,
                    service=normalized_service
                )
            )

        self.memory.add_ada_message(
            reply
        )

        return reply

    # ==================================================
    # CONTINUE CONVERSATION
    # ==================================================

    def continue_conversation(
        self,
        customer_reply,
        service=None
    ):

        self.memory.add_customer_message(
            customer_reply
        )

        active_service = (
            self.job_state.get("service")
        )

        if (
            not active_service
            and service
        ):

            active_service = (
                self.normalize_service(
                    service
                )
            )

            self.job_state[
                "service"
            ] = active_service

        detected_service = (
            self.find_service_in_message(
                customer_reply
            )
        )

        if detected_service:

            active_service = detected_service

            self.job_state[
                "service"
            ] = active_service

        if self.detect_price_request(
            customer_reply
        ):

            price_service = (
                detected_service
                or active_service
            )

            if price_service:

                reply = (
                    self.generate_price_response(
                        price_service
                    )
                )

            else:

                reply = (
                    self.generate_response(
                        customer_reply,
                        service=active_service
                    )
                )

        else:

            reply = (
                self.generate_response(
                    customer_reply,
                    service=active_service
                )
            )

        self.memory.add_ada_message(
            reply
        )

        if self.interview_is_complete():

            self.job_state[
                "interview_complete"
            ] = True

        return reply

    # ==================================================
    # PROCESS MESSAGE
    # ==================================================

    def process_message(
        self,
        customer_message,
        service=None
    ):

        if not customer_message:

            return (
                "Please tell me what you need "
                "help with."
            )

        customer_message = (
            str(customer_message)
            .strip()
        )

        if not customer_message:

            return (
                "Please tell me what you need "
                "help with."
            )

        if not self.memory.messages:

            return self.start_conversation(
                customer_message,
                service=service
            )

        return self.continue_conversation(
            customer_message,
            service=service
        )

    # ==================================================
    # MARK INTERVIEW COMPLETE
    # ==================================================

    def interview_completed(self):

        self.job_state[
            "interview_complete"
        ] = True

    # ==================================================
    # CUSTOMER HISTORY
    # ==================================================

    def get_customer_history(self):

        return (
            self.memory.get_conversation()
        )

    # ==================================================
    # DOCUMENT WRITER
    # ==================================================

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

            document_context = (
                "\n\n"
                "DOCUMENT ALREADY SUPPLIED BY CUSTOMER\n"
                "======================================\n"
                "Use the extracted text below as the "
                "source document to be typed/prepared.\n\n"
                + self.active_document_text
                + "\n"
                "======================================\n"
            )

        writer_prompt = f"""
You are Ada's Senior Nigerian Professional
Document Writer.

Prepare the customer's requested document.

SERVICE:
{active_service}

CUSTOMER INTERVIEW:
{history}

{document_context}

Rules:

- Use ONLY information supplied by customer.
- If a document is supplied, use its text.
- Do NOT ask for text that is already available.
- Preserve customer meaning.
- Never invent information.
- Never remove customer facts.
- Use professional Nigerian English where
  appropriate.
- Use proper headings where necessary.
- If the request is typing, reproduce readable
  text faithfully.
- Do not summarize when asked to type.
- Do not rewrite when asked to type.
- Follow the selected service prompt.

Return ONLY the finished document.
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

            if draft:

                self.job_state[
                    "draft_generated"
                ] = True

                self.job_state[
                    "awaiting_review"
                ] = True

                return draft.strip()

            raise RuntimeError(
                "Groq returned an empty document draft."
            )

        except Exception as error:

            self._log_groq_error(
                "REAL GROQ DOCUMENT DRAFT ERROR",
                error
            )

            raise

    # ==================================================
    # REVISE DOCUMENT
    # ==================================================

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

{current_draft}

CUSTOMER REVISION REQUEST:

{revision_request}

Instructions:

- Apply the customer's requested changes.
- Preserve all correct information.
- Do not invent facts.
- Do not remove information unless the
  customer specifically requests it.
- Maintain professional formatting.
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

            if revised:

                self.job_state[
                    "revision_requested"
                ] = True

                self.job_state[
                    "revision_count"
                ] += 1

                self.job_state[
                    "awaiting_review"
                ] = True

                return revised.strip()

            raise RuntimeError(
                "Groq returned an empty revised document."
            )

        except Exception as error:

            self._log_groq_error(
                "REAL GROQ DOCUMENT REVISION ERROR",
                error
            )

            raise

    # ==================================================
    # APPROVE DOCUMENT
    # ==================================================

    def approve_document(self):

        self.job_state[
            "approved"
        ] = True

        self.job_state[
            "awaiting_review"
        ] = False

        return True

    # ==================================================
    # MARK PAYMENT RECEIVED
    # ==================================================

    def mark_payment_received(self):

        self.job_state[
            "payment_received"
        ] = True

        return True

    # ==================================================
    # MARK DELIVERED
    # ==================================================

    def mark_delivered(self):

        self.job_state[
            "delivered"
        ] = True

        return True


# ==============================================================
# DIRECT ENGINE TEST
# ==============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("ADA AI ENGINE DIRECT TEST")
    print("=" * 70)

    ada = AdaAIEngine()

    print(
        "Connected:",
        ada.is_connected()
    )

    print(
        "Model:",
        ada.get_model()
    )

    print("=" * 70)
    print()

    if not ada.is_connected():

        print(
            "TEST STOPPED:"
        )

        print(
            "Groq is not connected."
        )

        print(
            "Check GROQ_API_KEY."
        )

    else:

        try:

            result = ada.process_message(
                customer_message=(
                    "Hello Ada, please introduce yourself."
                ),
                service="CV"
            )

            print()
            print("=" * 70)
            print("ADA RESPONSE")
            print("=" * 70)
            print(result)
            print("=" * 70)
            print()

        except Exception as error:

            print()
            print("=" * 70)
            print("DIRECT TEST REAL ERROR")
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
            print()
