"""
ada_controller.py

Ada Controller V10
Naija Pocket Business Center

ROLE
----
AdaController is the coordination layer between the
customer-facing API/workspace and AdaAIEngine V10.

AdaAIEngine remains responsible for:
    - Groq intelligence
    - conversation memory
    - interview progression
    - service handling
    - document drafting
    - document revision
    - job state

AdaController is responsible for:
    - validating incoming requests
    - passing the correct service to AdaAIEngine
    - exposing the job/workflow operations
    - preserving real errors
    - preventing silent/empty responses

IMPORTANT
---------
This controller does NOT create its own conversation memory.

This controller does NOT generate fallback AI responses.

This controller does NOT replace real exceptions
with friendly fake messages.

All real AdaAIEngine errors are allowed to surface.
"""

import traceback

from ada_ai_engine import AdaAIEngine


class AdaController:

    # ==========================================================
    # INITIALIZE
    # ==========================================================

    def __init__(self):

        print()
        print("=" * 70)
        print("ADA CONTROLLER V10 INITIALIZING")
        print("=" * 70)

        self.intelligence = AdaAIEngine()

        print(
            "Primary Intelligence:",
            "AdaAIEngine V10"
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
                "Unable to read model"
            )

        print("=" * 70)
        print()

    # ==========================================================
    # NORMALIZE SELECTED SERVICE
    # ==========================================================

    def _clean_service(self, service):

        if service is None:
            return None

        service_text = str(
            service
        ).strip()

        if not service_text:
            return None

        if (
            service_text.lower()
            in {
                "service not selected",
                "none",
                "null"
            }
        ):
            return None

        return service_text

    # ==========================================================
    # VALIDATE CUSTOMER MESSAGE
    # ==========================================================

    def _validate_message(self, message):

        if message is None:

            raise ValueError(
                "AdaController received message=None"
            )

        message = str(
            message
        ).strip()

        if not message:

            raise ValueError(
                "AdaController received an empty message"
            )

        return message

    # ==========================================================
    # PROCESS CUSTOMER MESSAGE
    #
    # THIS IS THE MAIN CONVERSATION ENTRY POINT.
    #
    # The same AdaAIEngine instance is deliberately reused.
    # Therefore AdaAIEngine's conversation memory survives
    # from question 1 → question 2 → question 3 → completion.
    # ==========================================================

    def process_message(
        self,
        message,
        service=None
    ):

        message = self._validate_message(
            message
        )

        selected_service = (
            self._clean_service(service)
        )

        print()
        print("=" * 70)
        print("ADA CONTROLLER REQUEST")
        print("=" * 70)

        print(
            "Selected Service:",
            repr(selected_service)
        )

        print(
            "Customer Message:",
            repr(message)
        )

        print(
            "Active Service Before:",
            repr(
                self.intelligence.get_active_service()
            )
        )

        print("=" * 70)
        print()

        # ------------------------------------------------------
        # IMPORTANT:
        #
        # DO NOT call reset_job() here.
        #
        # Every customer message after the first one must
        # continue the existing conversation.
        #
        # AdaAIEngine.process_message() decides whether this
        # is the first message or a continuation.
        # ------------------------------------------------------

        try:

            response = (
                self.intelligence.process_message(
                    customer_message=message,
                    service=selected_service
                )
            )

        except Exception as error:

            print()
            print("=" * 70)
            print("!!!!!!!! REAL ADA V10 ERROR !!!!!!!!")
            print("=" * 70)

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR MESSAGE:",
                str(error)
            )

            print()
            print("ERROR REPRESENTATION:")
            print(
                repr(error)
            )

            print()
            print("FULL TRACEBACK:")
            print()

            traceback.print_exc()

            print()
            print("=" * 70)
            print("!!!!!!!! END REAL ADA V10 ERROR !!!!!!!!")
            print("=" * 70)
            print()

            # NEVER hide the real intelligence error.
            raise

        # ------------------------------------------------------
        # RESPONSE VALIDATION
        # ------------------------------------------------------

        print()
        print("=" * 70)
        print("ADA AI ENGINE V10 RETURNED")
        print("=" * 70)

        print(
            "Response Type:",
            type(response).__name__
        )

        print(
            "Response:",
            repr(response)
        )

        print(
            "Active Service After:",
            repr(
                self.intelligence.get_active_service()
            )
        )

        print(
            "Job State:",
            self.intelligence.get_job_state()
        )

        print("=" * 70)
        print()

        if response is None:

            raise RuntimeError(
                "AdaAIEngine V10.process_message() "
                "returned None"
            )

        response = str(
            response
        ).strip()

        if not response:

            raise RuntimeError(
                "AdaAIEngine V10.process_message() "
                "returned an empty response"
            )

        return response

    # ==========================================================
    # START NEW JOB
    #
    # Used when a NEW service/job is intentionally selected.
    # ==========================================================

    def start_job(self, service):

        service = self._clean_service(
            service
        )

        if not service:

            raise ValueError(
                "Cannot start an Ada job without a service"
            )

        print()
        print("=" * 70)
        print("ADA CONTROLLER STARTING NEW JOB")
        print("=" * 70)
        print(
            "Service:",
            repr(service)
        )
        print("=" * 70)
        print()

        return (
            self.intelligence.start_job(
                service
            )
        )

    # ==========================================================
    # SET ACTIVE SERVICE
    # ==========================================================

    def set_active_service(
        self,
        service
    ):

        service = self._clean_service(
            service
        )

        if not service:

            raise ValueError(
                "Cannot set an empty active service"
            )

        return (
            self.intelligence.set_active_service(
                service
            )
        )

    # ==========================================================
    # GET ACTIVE SERVICE
    # ==========================================================

    def get_active_service(self):

        return (
            self.intelligence.get_active_service()
        )

    # ==========================================================
    # GET JOB STATE
    # ==========================================================

    def get_job_state(self):

        return (
            self.intelligence.get_job_state()
        )

    # ==========================================================
    # GET CUSTOMER HISTORY
    # ==========================================================

    def get_customer_history(self):

        return (
            self.intelligence.get_customer_history()
        )

    # ==========================================================
    # INTERVIEW STATUS
    # ==========================================================

    def interview_is_complete(self):

        return (
            self.intelligence.interview_is_complete()
        )

    # ==========================================================
    # MARK INTERVIEW COMPLETE
    # ==========================================================

    def interview_completed(self):

        return (
            self.intelligence.interview_completed()
        )

    # ==========================================================
    # DOCUMENT CONTEXT
    # ==========================================================

    def set_document_context(
        self,
        extracted_text,
        file_path=None
    ):

        return (
            self.intelligence.set_document_context(
                extracted_text,
                file_path=file_path
            )
        )

    def get_document_context(self):

        return (
            self.intelligence.get_document_context()
        )

    def has_document_context(self):

        return (
            self.intelligence.has_document_context()
        )

    def clear_document_context(self):

        return (
            self.intelligence.clear_document_context()
        )

    # ==========================================================
    # GENERATE DOCUMENT DRAFT
    #
    # This moves the workflow from:
    #
    # INTERVIEW
    #     ↓
    # DRAFT
    #     ↓
    # REVIEW
    # ==========================================================

    def generate_document_draft(
        self,
        service=None
    ):

        selected_service = (
            self._clean_service(service)
        )

        print()
        print("=" * 70)
        print("ADA CONTROLLER → GENERATE DOCUMENT DRAFT")
        print("=" * 70)

        print(
            "Service:",
            repr(
                selected_service
                or self.get_active_service()
            )
        )

        print("=" * 70)
        print()

        try:

            draft = (
                self.intelligence.generate_document_draft(
                    service=selected_service
                )
            )

        except Exception as error:

            print()
            print("=" * 70)
            print("!!!!!!!! DOCUMENT DRAFT ERROR !!!!!!!!")
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

            raise

        if draft is None:

            raise RuntimeError(
                "AdaAIEngine returned None while "
                "generating document draft"
            )

        draft = str(
            draft
        ).strip()

        if not draft:

            raise RuntimeError(
                "AdaAIEngine returned an empty "
                "document draft"
            )

        return draft

    # ==========================================================
    # REVISE DOCUMENT
    #
    # REVIEW
    #   ↓
    # REVISION
    #   ↓
    # REVIEW AGAIN
    # ==========================================================

    def revise_document(
        self,
        current_draft,
        revision_request,
        service=None
    ):

        if current_draft is None:

            raise ValueError(
                "Cannot revise a document that is None"
            )

        current_draft = str(
            current_draft
        ).strip()

        if not current_draft:

            raise ValueError(
                "Cannot revise an empty document"
            )

        if revision_request is None:

            raise ValueError(
                "Revision request cannot be None"
            )

        revision_request = str(
            revision_request
        ).strip()

        if not revision_request:

            raise ValueError(
                "Revision request cannot be empty"
            )

        selected_service = (
            self._clean_service(service)
        )

        return (
            self.intelligence.revise_document(
                current_draft=current_draft,
                revision_request=revision_request,
                service=selected_service
            )
        )

    # ==========================================================
    # APPROVE DOCUMENT
    #
    # REVIEW
    #   ↓
    # APPROVED
    #   ↓
    # PAYMENT
    # ==========================================================

    def approve_document(self):

        return (
            self.intelligence.approve_document()
        )

    # ==========================================================
    # PAYMENT RECEIVED
    #
    # PAYMENT
    #   ↓
    # PAYMENT CONFIRMED
    #   ↓
    # DELIVERY
    # ==========================================================

    def mark_payment_received(self):

        return (
            self.intelligence.mark_payment_received()
        )

    # ==========================================================
    # MARK DELIVERED
    #
    # DELIVERY
    #   ↓
    # COMPLETED
    # ==========================================================

    def mark_delivered(self):

        return (
            self.intelligence.mark_delivered()
        )

    # ==========================================================
    # RESET ADA JOB
    #
    # IMPORTANT:
    # This must ONLY happen when starting a genuinely
    # new job/conversation.
    #
    # It must NEVER happen automatically for every
    # customer message.
    # ==========================================================

    def reset_job(self):

        print()
        print("=" * 70)
        print("ADA CONTROLLER RESETTING JOB")
        print("=" * 70)
        print()

        return (
            self.intelligence.reset_job()
        )


# ==============================================================
# DIRECT CONTROLLER TEST
# ==============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("ADA CONTROLLER V10 DIRECT TEST")
    print("=" * 70)
    print()

    try:

        ada = AdaController()

        message = input(
            "Customer message: "
        ).strip()

        service = input(
            "Service (optional): "
        ).strip()

        if not service:
            service = None

        response = ada.process_message(
            message=message,
            service=service
        )

        print()
        print("=" * 70)
        print("ADA RESPONSE")
        print("=" * 70)
        print(response)
        print("=" * 70)
        print()

    except Exception as error:

        print()
        print("=" * 70)
        print("!!!!!!!! DIRECT TEST REAL ERROR !!!!!!!!")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        print()
        print("FULL TRACEBACK:")
        print()

        traceback.print_exc()

        print()
        print("=" * 70)
        print()
