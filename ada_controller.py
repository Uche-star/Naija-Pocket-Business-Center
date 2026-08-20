"""
ada_controller.py

Connection Controller for Ada
Naija Pocket Business Center

DIAGNOSTIC VERSION
------------------
This version is intentionally designed to expose
the REAL AdaAIEngine error.

It does NOT replace exceptions with friendly
fallback messages.

If AdaAIEngine fails:
    - error type is printed
    - error message is printed
    - full traceback is printed
    - original exception is re-raised
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
        print("ADA CONTROLLER INITIALIZING")
        print("=" * 70)

        self.intelligence = AdaAIEngine()

        print(
            "Primary Intelligence:",
            "AdaAIEngine V9"
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
    # PROCESS CUSTOMER MESSAGE
    # ==========================================================

    def process_message(
        self,
        message,
        service=None
    ):

        # ------------------------------------------------------
        # VALIDATE MESSAGE
        # ------------------------------------------------------

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

        # ------------------------------------------------------
        # SELECTED SERVICE
        # ------------------------------------------------------

        selected_service = None

        if service is not None:

            service_text = str(
                service
            ).strip()

            if service_text:

                if (
                    service_text.lower()
                    != "service not selected"
                ):

                    selected_service = service_text

        # ------------------------------------------------------
        # LOG REQUEST
        # ------------------------------------------------------

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

        print("=" * 70)
        print()

        # ------------------------------------------------------
        # CALL ADA AI ENGINE
        # ------------------------------------------------------

        try:

            response = (
                self.intelligence.process_message(
                    customer_message=message,
                    service=selected_service
                )
            )

        # ------------------------------------------------------
        # REAL ERROR
        # ------------------------------------------------------

        except Exception as error:

            print()
            print("=" * 70)
            print("!!!!!!!! REAL ADA ERROR !!!!!!!!")
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
            print("!!!!!!!! END REAL ADA ERROR !!!!!!!!")
            print("=" * 70)
            print()

            # IMPORTANT:
            # Do NOT replace the real error with
            # a fake customer response.
            raise

        # ------------------------------------------------------
        # CHECK ENGINE RESPONSE
        # ------------------------------------------------------

        print()
        print("=" * 70)
        print("ADA AI ENGINE RETURNED")
        print("=" * 70)

        print(
            "Response Type:",
            type(response).__name__
        )

        print(
            "Response:",
            repr(response)
        )

        print("=" * 70)
        print()

        # ------------------------------------------------------
        # DO NOT HIDE AN EMPTY ENGINE RESPONSE
        # ------------------------------------------------------

        if response is None:

            raise RuntimeError(
                "AdaAIEngine.process_message() "
                "returned None"
            )

        response = str(
            response
        ).strip()

        if not response:

            raise RuntimeError(
                "AdaAIEngine.process_message() "
                "returned an empty response"
            )

        return response

    # ==========================================================
    # ACTIVE SERVICE
    # ==========================================================

    def set_active_service(
        self,
        service
    ):

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
    # RESET ADA JOB
    # ==========================================================

    def reset_job(self):

        return (
            self.intelligence.reset_job()
        )

    # ==========================================================
    # JOB STATUS
    # ==========================================================

    def get_job_state(self):

        return (
            self.intelligence.get_job_state()
        )


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("ADA CONTROLLER DIRECT TEST")
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

        print()
        print("=" * 70)
        print("SENDING REQUEST")
        print("=" * 70)
        print()

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
