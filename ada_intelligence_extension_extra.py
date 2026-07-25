"""
ada_intelligence_extension_extra.py
Extended Intelligence for Ada
Naija Pocket Business Center
"""

class AdaIntelligenceExtensionExtra:

    def __init__(self):

        self.services = {

            "cv_preparation": [
                "cv",
                "resume",
                "curriculum vitae",
                "prepare cv",
                "prepare my cv",
                "help me prepare a cv",
                "write cv",
                "write my cv",
                "create cv",
                "make a cv",
                "update my cv",
                "edit my cv"
            ],

            "cover_letter": [
                "cover letter",
                "application letter",
                "job application",
                "employment letter"
            ],

            "research_assistance": [
                "research",
                "research work",
                "help me research",
                "find materials",
                "find references",
                "literature review",
                "gather information"
            ],

            "business_proposal": [
                "proposal",
                "business proposal",
                "write proposal",
                "prepare proposal",
                "company proposal",
                "project proposal"
            ],

            "seminar_paper": [
                "seminar",
                "seminar paper",
                "seminar presentation"
            ],

            "business_letter": [
                "business letter",
                "official letter",
                "formal letter"
            ],

            "company_profile": [
                "company profile",
                "business profile",
                "organisation profile"
            ],

            "invoice": [
                "invoice",
                "billing invoice",
                "payment invoice"
            ],

            "quotation": [
                "quotation",
                "quote",
                "price quotation"
            ],

            "meeting_minutes": [
                "minutes",
                "meeting minutes",
                "record meeting"
            ],

            "ai_writing": [
                "write for me",
                "generate content",
                "write article",
                "write essay",
                "write document"
            ],

            "rewrite_document": [
                "rewrite",
                "rephrase",
                "improve writing",
                "rewrite document"
            ],

            "explain_topic": [
                "explain",
                "teach me",
                "help me understand",
                "what is",
                "how does"
            ],

            "voice_to_text": [
                "voice",
                "voice message",
                "speech to text",
                "convert voice",
                "audio to text"
            ],

            "image_to_text": [
                "image to text",
                "extract text",
                "picture to text",
                "ocr"
            ]
        }

    def detect_extra_intent(self, message):

        text = message.lower().strip()

        for intent, keywords in self.services.items():
            for keyword in keywords:
                if keyword in text:
                    return intent

        return "unknown"


if __name__ == "__main__":

    ada = AdaIntelligenceExtensionExtra()

    while True:

        message = input("Customer: ")

        if message.lower() == "exit":
            break

        print("\nIntent:")
        print(ada.detect_extra_intent(message))