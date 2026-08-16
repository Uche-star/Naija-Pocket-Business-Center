"""
ada_controller.py

Connection Controller for Ada
Naija Pocket Business Center

PRIMARY INTELLIGENCE
--------------------
AdaAIEngine V8 / Groq

The controller sends the customer's message
and selected Workspace service directly to
AdaAIEngine.
"""

from ada_ai_engine import AdaAIEngine


class AdaController:

    # ==========================================================
    # INITIALIZE
    # ==========================================================
    def __init__(self):

        print()
        print("=" * 60)
        print("ADA CONTROLLER")
        print("=" * 60)

        self.intelligence = AdaAIEngine()

        print("Primary Intelligence: AdaAIEngine V8")
        print(
            "Groq Connected:",
            self.intelligence.is_connected()
        )

        print("=" * 60)
        print()

    # ==========================================================
    # PROCESS CUSTOMER MESSAGE
    # ==========================================================
    def process_message(
        self,
        message,
        service=None
    ):

        if not message:
            return (
                "Please tell me what you need "
                "help with."
            )

        message = str(message).strip()

        if not message:
            return (
                "Please tell me what you need "
                "help with."
            )

        # ------------------------------------------------------
        # SELECTED SERVICE
        # ------------------------------------------------------

        selected_service = None

        if service:

            service_text = str(service).strip()

            if (
                service_text
                and service_text.lower()
                != "service not selected"
            ):
                selected_service = service_text

        # ------------------------------------------------------
        # LOG REQUEST
        # ------------------------------------------------------

        print()
        print("=" * 60)
        print("ADA CONTROLLER REQUEST")
        print("=" * 60)
        print(
            "Selected Service:",
            selected_service
        )
        print(
            "Customer Message:",
            message
        )
        print("=" * 60)
        print()

        # ------------------------------------------------------
        # SEND DIRECTLY TO ADA AI ENGINE
        # ------------------------------------------------------

        try:

            response = self.intelligence.process_message(
                customer_message=message,
                service=selected_service
            )

        except Exception as error:

            print()
            print("=" * 60)
            print("ADA AI ENGINE ERROR")
            print("=" * 60)
            print("Error:", repr(error))
            print("=" * 60)
            print()

            return (
                "No wahala. "
                "I ran into a temporary problem "
                "while processing your request. "
                "Please try again."
            )

        # ------------------------------------------------------
        # SAFETY FALLBACK
        # ------------------------------------------------------

        if response is None:

            return (
                "No wahala. "
                "I received your request, "
                "but Ada could not generate "
                "a response yet."
            )

        response = str(response).strip()

        if not response:

            return (
                "No wahala. "
                "I received your request, "
                "but Ada could not generate "
                "a response yet."
            )

        return response

    # ==========================================================
    # ACTIVE SERVICE
    # ==========================================================
    def set_active_service(self, service):

        if not service:
            return False

        return self.intelligence.set_active_service(
            service
        )

    # ==========================================================
    # GET ACTIVE SERVICE
    # ==========================================================
    def get_active_service(self):

        return self.intelligence.get_active_service()

    # ==========================================================
    # RESET ADA JOB
    # ==========================================================
    def reset_job(self):

        self.intelligence.reset_job()

    # ==========================================================
    # JOB STATUS
    # ==========================================================
    def get_job_state(self):

        return self.intelligence.get_job_state()


# ==============================================================
# TEST
# ==============================================================
if __name__ == "__main__":

    ada = AdaController()

    print()
    print("=" * 60)
    print("ADA CONTROLLER TEST")
    print("=" * 60)

    while True:

        message = input(
            "\nCustomer: "
        ).strip()

        if message.lower() == "exit":
            break

        service = input(
            "Selected service "
            "(leave blank if none): "
        ).strip()

        if not service:
            service = None

        print()
        print("Ada:")
        print()

        print(
            ada.process_message(
                message,
                service=service
            )
        ) 
