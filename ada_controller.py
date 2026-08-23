"""
ada_controller.py

Ada Controller V11
Naija Pocket Business Center

PURPOSE
-------
AdaController is a THIN application coordination layer.

The intelligence belongs to AdaAIEngine / Groq.

Groq is responsible for:
    - understanding the customer's request
    - maintaining conversational context
    - deciding what information is needed
    - deciding when enough information has been supplied
    - generating requested documents
    - understanding revision requests
    - revising documents
    - producing natural customer-facing responses

Python is responsible only for:
    - receiving application requests
    - passing customer messages to AdaAIEngine
    - preserving the active AdaAIEngine instance
    - storing/retrieving document context
    - exposing application state
    - forwarding document/payment/delivery operations
    - validating that real results were returned

IMPORTANT
---------
This controller does NOT:

    - conduct an interview
    - decide what question Ada should ask
    - decide whether the customer is ready
    - inspect keywords to determine readiness
    - generate fallback AI responses
    - manufacture document content
    - decide when a revision is required
    - automatically revise documents
    - pretend payment happened
    - pretend delivery happened

The controller must never compete with Groq for intelligence.

The intended customer flow is:

    SERVICE
       ↓
    CUSTOMER CONVERSATION
       ↓
    GROQ UNDERSTANDS REQUEST
       ↓
    GROQ DECIDES INFORMATION IS SUFFICIENT
       ↓
    DOCUMENT GENERATION
       ↓
    CUSTOMER REVIEW
       ↓
    REVISION ONLY IF CUSTOMER REQUESTS IT
       ↓
    APPROVAL
       ↓
    PAYMENT
       ↓
    DELIVERY / DOWNLOAD

One customer message should result in one normal
AdaAIEngine conversation request.

There are no hidden secondary intelligence requests here.
"""

import traceback

from ada_ai_engine import AdaAIEngine


class AdaController:

    # ==========================================================
    # INITIALIZATION
    # ==========================================================

    def __init__(self):

        print()
        print("=" * 72)
        print("ADA CONTROLLER V11")
        print("=" * 72)

        self.intelligence = AdaAIEngine()

        print(
            "Intelligence:",
            "AdaAIEngine"
        )

        print(
            "Groq Connected:",
            self.intelligence.is_connected()
        )

        try:
            print(
                "Groq Model:",
                self.intelligence.get_model()
            )
        except Exception:
            print(
                "Groq Model:",
                "Unavailable"
            )

        print(
            "Role:",
            "THIN APPLICATION COORDINATION"
        )

        print(
            "Python Interview Logic:",
            "DISABLED"
        )

        print(
            "Fallback AI:",
            "DISABLED"
        )

        print(
            "Keyword Readiness Logic:",
            "DISABLED"
        )

        print("=" * 72)
        print()

    # ==========================================================
    # INTERNAL VALIDATION
    # ==========================================================

    def _clean_service(self, service):

        if service is None:
            return None

        value = str(service).strip()

        if not value:
            return None

        if value.lower() in {
            "none",
            "null",
            "service not selected",
        }:
            return None

        return value

    def _validate_message(self, message):

        if message is None:
            raise ValueError(
                "AdaController received message=None."
            )

        value = str(message).strip()

        if not value:
            raise ValueError(
                "AdaController received an empty customer message."
            )

        return value

    def _require_result(
        self,
        result,
        operation
    ):

        if result is None:
            raise RuntimeError(
                f"{operation} returned None."
            )

        if isinstance(result, str):

            if not result.strip():
                raise RuntimeError(
                    f"{operation} returned an empty result."
                )

            return result.strip()

        return result

    # ==========================================================
    # MAIN ADA CONVERSATION
    #
    # IMPORTANT:
    #
    # No interview logic exists here.
    #
    # No question sequence exists here.
    #
    # No readiness detection exists here.
    #
    # The message goes directly to AdaAIEngine.
    # ==========================================================

    def process_message(
        self,
        message,
        service=None
    ):

        message = self._validate_message(message)

        service = self._clean_service(service)

        print()
        print("=" * 72)
        print("ADA CONTROLLER → ADA AI ENGINE")
        print("=" * 72)

        print(
            "Service:",
            repr(service)
        )

        print(
            "Customer Message:",
            repr(message)
        )

        print(
            "Active Service:",
            repr(
                self.intelligence.get_active_service()
            )
        )

        print(
            "Intelligence Request:",
            "1"
        )

        print("=" * 72)
        print()

        try:

            response = self.intelligence.process_message(
                customer_message=message,
                service=service
            )

        except Exception as error:

            self._log_error(
                "ADA CONVERSATION ERROR",
                error
            )

            raise

        response = self._require_result(
            response,
            "AdaAIEngine.process_message()"
        )

        print()
        print("=" * 72)
        print("ADA CONTROLLER ← ADA AI ENGINE")
        print("=" * 72)

        print(
            "Response:",
            repr(response)
        )

        print(
            "Active Service:",
            repr(
                self.intelligence.get_active_service()
            )
        )

        print("=" * 72)
        print()

        return response

    # ==========================================================
    # START NEW JOB
    #
    # This is an APPLICATION operation.
    #
    # It does not decide anything about the customer's request.
    # ==========================================================

    def start_job(self, service):

        service = self._clean_service(service)

        if not service:
            raise ValueError(
                "Cannot start a job without a service."
            )

        print()
        print("=" * 72)
        print("ADA CONTROLLER → START NEW JOB")
        print("=" * 72)

        print(
            "Service:",
            repr(service)
        )

        print("=" * 72)
        print()

        try:

            result = self.intelligence.start_job(
                service
            )

        except Exception as error:

            self._log_error(
                "START JOB ERROR",
                error
            )

            raise

        return self._require_result(
            result,
            "AdaAIEngine.start_job()"
        )

    # ==========================================================
    # ACTIVE SERVICE
    # ==========================================================

    def set_active_service(self, service):

        service = self._clean_service(service)

        if not service:
            raise ValueError(
                "Cannot set an empty active service."
            )

        return self.intelligence.set_active_service(
            service
        )

    def get_active_service(self):

        return self.intelligence.get_active_service()

    # ==========================================================
    # JOB STATE
    #
    # State is application state.
    #
    # The controller does not interpret the state to make
    # intelligence decisions.
    # ==========================================================

    def get_job_state(self):

        return self.intelligence.get_job_state()

    # ==========================================================
    # CUSTOMER HISTORY
    # ==========================================================

    def get_customer_history(self):

        return self.intelligence.get_customer_history()

    # ==========================================================
    # DOCUMENT CONTEXT
    #
    # Python may store supplied material.
    #
    # It does NOT interpret that material.
    # ==========================================================

    def set_document_context(
        self,
        extracted_text,
        file_path=None
    ):

        return self.intelligence.set_document_context(
            extracted_text,
            file_path=file_path
        )

    def get_document_context(self):

        return self.intelligence.get_document_context()

    def has_document_context(self):

        return self.intelligence.has_document_context()

    def clear_document_context(self):

        return self.intelligence.clear_document_context()

    # ==========================================================
    # DOCUMENT GENERATION
    #
    # IMPORTANT:
    #
    # This method does NOT decide whether the customer is ready.
    #
    # The application/UI should call this only when the workflow
    # has reached the document-generation stage.
    #
    # The actual document intelligence remains Groq's job through
    # AdaAIEngine.
    # ==========================================================

    def generate_document_draft(
        self,
        service=None
    ):

        service = self._clean_service(service)

        print()
        print("=" * 72)
        print("ADA CONTROLLER → DOCUMENT GENERATION")
        print("=" * 72)

        print(
            "Service:",
            repr(
                service
                or self.get_active_service()
            )
        )

        print("=" * 72)
        print()

        try:

            draft = self.intelligence.generate_document_draft(
                service=service
            )

        except Exception as error:

            self._log_error(
                "DOCUMENT GENERATION ERROR",
                error
            )

            raise

        return self._require_result(
            draft,
            "AdaAIEngine.generate_document_draft()"
        )

    # ==========================================================
    # DOCUMENT REVISION
    #
    # Revision happens ONLY because the customer requested it.
    #
    # Python does not decide whether the document needs revision.
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

        service = self._clean_service(service)

        print()
        print("=" * 72)
        print("ADA CONTROLLER → DOCUMENT REVISION")
        print("=" * 72)

        print(
            "Service:",
            repr(
                service
                or self.get_active_service()
            )
        )

        print(
            "Revision Requested:",
            True
        )

        print("=" * 72)
        print()

        try:

            revised = self.intelligence.revise_document(
                current_draft=current_draft,
                revision_request=revision_request,
                service=service
            )

        except Exception as error:

            self._log_error(
                "DOCUMENT REVISION ERROR",
                error
            )

            raise

        return self._require_result(
            revised,
            "AdaAIEngine.revise_document()"
        )

    # ==========================================================
    # APPROVAL
    #
    # This is NOT an intelligence decision.
    #
    # The customer has explicitly pressed Approve.
    # Python records that application event.
    # ==========================================================

    def approve_document(self):

        print()
        print(
            "ADA CONTROLLER → CUSTOMER APPROVAL"
        )

        try:

            return self.intelligence.approve_document()

        except Exception as error:

            self._log_error(
                "DOCUMENT APPROVAL ERROR",
                error
            )

            raise

    # ==========================================================
    # PAYMENT
    #
    # Python records the result of the actual payment system.
    #
    # It must never claim payment without confirmation from the
    # payment layer.
    # ==========================================================

    def mark_payment_received(self):

        print()
        print(
            "ADA CONTROLLER → PAYMENT CONFIRMED"
        )

        try:

            return self.intelligence.mark_payment_received()

        except Exception as error:

            self._log_error(
                "PAYMENT STATE ERROR",
                error
            )

            raise

    # ==========================================================
    # DELIVERY
    #
    # Python performs/records delivery.
    #
    # It does not generate or modify the document content here.
    # ==========================================================

    def mark_delivered(self):

        print()
        print(
            "ADA CONTROLLER → DELIVERY CONFIRMED"
        )

        try:

            return self.intelligence.mark_delivered()

        except Exception as error:

            self._log_error(
                "DELIVERY STATE ERROR",
                error
            )

            raise

    # ==========================================================
    # RESET
    #
    # Only for an explicitly new job.
    # ==========================================================

    def reset_job(self):

        print()
        print("=" * 72)
        print("ADA CONTROLLER → RESET JOB")
        print("=" * 72)
        print()

        try:

            return self.intelligence.reset_job()

        except Exception as error:

            self._log_error(
                "RESET JOB ERROR",
                error
            )

            raise

    # ==========================================================
    # LEGACY INTERVIEW COMPATIBILITY
    #
    # These methods remain ONLY so older application code does
    # not immediately break.
    #
    # They do NOT perform an interview.
    #
    # They do NOT call Groq.
    #
    # They should not be used to decide whether Ada should
    # generate a document.
    # ==========================================================

    def interview_is_complete(self):

        return self.intelligence.interview_is_complete()

    def interview_completed(self):

        return self.intelligence.interview_completed()

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


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    print()
    print("=" * 72)
    print("ADA CONTROLLER V11 DIRECT TEST")
    print("=" * 72)
    print()

    try:

        ada = AdaController()

        print(
            "Connected:",
            ada.intelligence.is_connected()
        )

        print(
            "Model:",
            ada.intelligence.get_model()
        )

        print()

        service = input(
            "Service (optional): "
        ).strip()

        if not service:
            service = None

        while True:

            message = input(
                "Customer: "
            ).strip()

            if not message:
                continue

            if message.lower() in {
                "exit",
                "quit",
                "stop",
            }:
                break

            response = ada.process_message(
                message=message,
                service=service
            )

            print()
            print("Ada:")
            print(response)
            print()

    except KeyboardInterrupt:

        print()
        print(
            "Ada Controller test stopped."
        )

    except Exception as error:

        print()
        print("=" * 72)
        print("ADA CONTROLLER DIRECT TEST ERROR")
        print("=" * 72)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 72)
        print()
