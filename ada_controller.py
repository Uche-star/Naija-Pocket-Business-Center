"""
ada_controller.py

Connection Controller for
Ada
Naija Pocket Business Center
"""

from assistant_intelligence import AssistantIntelligence
from ada_intelligence_extension import AdaIntelligenceExtension
from ada_intelligence_extension_extra import AdaIntelligenceExtensionExtra
from ada_workflow import AdaWorkflow
from ada_workflow_extension import AdaWorkflowExtension


class AdaController:

    def __init__(self):

        self.intelligence = AssistantIntelligence()

        self.extension = AdaIntelligenceExtension()

        self.extra_extension = AdaIntelligenceExtensionExtra()

        self.workflow = AdaWorkflow()

        self.workflow_extension = AdaWorkflowExtension()

        self.extension_services = {
            "cv_preparation",
            "cover_letter",
            "research_assistance",
            "business_proposal",
            "seminar_paper",
            "business_letter",
            "company_profile",
            "invoice",
            "quotation",
            "meeting_minutes",
            "ai_writing",
            "rewrite_document",
            "explain_topic",
            "voice_to_text",
            "image_to_text"
        }

    def process_message(self, message):

        result = self.intelligence.understand_customer(message)

        intent = result["intent"]

        if intent == "unknown":
            intent = self.extension.detect_extra_intent(message)

        if intent == "unknown":
            intent = self.extra_extension.detect_extra_intent(message)

        if intent == "unknown":
            return (
                "No wahala.\n\n"
                "I no understand that request yet.\n\n"
                "Kindly tell me what you would like me to help you with."
            )

        if intent in self.extension_services:
            return self.workflow_extension.create_workflow(intent)

        reply = self.workflow.create_workflow(intent)

        if reply is None:
            return (
                "No wahala.\n\n"
                "That service has not been configured yet."
            )

        return reply


if __name__ == "__main__":

    ada = AdaController()

    print("Ada Controller Test")

    print("=" * 40)

    while True:

        message = input("\nCustomer: ")

        if message.lower() == "exit":
            break

        print("\nAda:\n")

        print(
            ada.process_message(message)
        ) 
