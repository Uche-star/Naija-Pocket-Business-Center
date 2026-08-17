"""
ada_reasoner.py

Ada Simple Reasoner
Naija Pocket Business Center

Responsibilities
----------------
• Receive customer messages.
• Send messages to AdaAIEngine.
• Preserve the selected service.
• Return Ada's response.
• Use the current Groq-powered Ada AI Engine.
"""

from ada_ai_engine import AdaAIEngine


class AdaReasoner:

    # ==========================================
    # INITIALIZE
    # ==========================================

    def __init__(self):
        self.ai = AdaAIEngine()

    # ==========================================
    # CONNECTION STATUS
    # ==========================================

    def is_connected(self):
        return self.ai.is_connected()

    # ==========================================
    # PROCESS CUSTOMER MESSAGE
    # ==========================================

    def process(
        self,
        message,
        service=None
    ):
        """
        Send the customer's message to
        AdaAIEngine.

        The AI Engine handles:
        • Conversation memory
        • Service detection
        • Billing
        • Document context
        • Ada's prompts
        • Groq communication
        """

        if message is None:
            return "Please tell me what you need."

        message = str(message).strip()

        if not message:
            return "Please tell me what you need."

        if not self.ai.is_connected():
            return (
                "Sorry 😊\n\n"
                "Ada is temporarily unable to connect "
                "to the service. Please try again shortly."
            )

        try:
            return self.ai.process_message(
                message,
                service=service
            )

        except Exception as error:
            print(
                "Ada Reasoner Error:",
                repr(error)
            )

            return (
                "Sorry 😊\n\n"
                "I encountered a temporary problem while "
                "processing your request. Please try again."
            )

    # ==========================================
    # START NEW JOB
    # ==========================================

    def start_job(self, service):
        """
        Start a new Ada customer job.
        """

        try:
            self.ai.start_job(service)
            return True

        except Exception as error:
            print(
                "Ada Reasoner Start Job Error:",
                repr(error)
            )
            return False

    # ==========================================
    # DOCUMENT CONTEXT
    # ==========================================

    def set_document_context(
        self,
        extracted_text,
        file_path=None
    ):
        """
        Give Ada the text extracted from a
        customer document.
        """

        try:
            return self.ai.set_document_context(
                extracted_text,
                file_path=file_path
            )

        except Exception as error:
            print(
                "Ada Reasoner Document Error:",
                repr(error)
            )
            return False

    # ==========================================
    # JOB STATUS
    # ==========================================

    def get_job_state(self):
        try:
            return self.ai.get_job_state()

        except Exception as error:
            print(
                "Ada Reasoner Job State Error:",
                repr(error)
            )
            return {}


# ==========================================
# TEST
# ==========================================

if __name__ == "__main__":

    reasoner = AdaReasoner()

    print("=" * 60)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA REASONER")
    print("=" * 60)
    print()

    print(
        "Groq Connected:",
        reasoner.is_connected()
    )

    print()
    print("Ada Reasoner Ready")
    print()

    while True:

        try:
            message = input("Customer: ")

        except KeyboardInterrupt:
            print()
            break

        except EOFError:
            print()
            break

        if message.strip().lower() == "exit":
            break

        reply = reasoner.process(message)

        print()
        print("Ada:")
        print(reply)
        print() 
