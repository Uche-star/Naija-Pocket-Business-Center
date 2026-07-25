"""
ada_intelligence_extension.py
Extra Intelligence Layer for Ada
Naija Pocket Business Centre
"""

class AdaIntelligenceExtension:

    def __init__(self):

        self.services = {

            "general_document_help": [
                "paper work",
                "document",
                "file",
                "i need help",
                "help me",
                "don't know what to do",
                "what can i do",
                "what should i do"
            ],

            "handwritten_typing": [
                "handwritten",
                "handwriting",
                "notes on paper",
                "snap my note",
                "picture of document",
                "photo of document"
            ],

            "assignment_typing": [
                "assignment",
                "school work",
                "homework",
                "course work"
            ],

            "project_typing": [
                "project",
                "final year project",
                "research work",
                "thesis",
                "dissertation"
            ],

            "document_typing": [
                "type",
                "typing",
                "make it a document",
                "convert to word"
            ],

            "document_formatting": [
                "arrange",
                "format",
                "make it neat",
                "organize",
                "layout"
            ],

            "grammar_correction": [
                "grammar",
                "correct",
                "proofread",
                "check my English"
            ],

            "translation": [
                "translate",
                "translation",
                "change language"
            ],

            "summarization": [
                "summary",
                "summarize",
                "shorten"
            ],

            "printing": [
                "print",
                "printing",
                "hard copy",
                "copies"
            ],

            "scanning": [
                "scan",
                "scanning"
            ],

            "pdf_conversion": [
                "pdf",
                "convert file",
                "change format"
            ],

            "document_editing": [
                "edit",
                "modify",
                "update document"
            ]
        }


    def detect_extra_intent(self, message):

        text = message.lower().strip()

        for intent, keywords in self.services.items():

            for keyword in keywords:

                if keyword in text:
                    return intent

        return "unknown"


    def get_response(self, intent):

        responses = {

            "general_document_help":
            "No wahala.\n\n"
            "Send the document or snap the pages for me.\n\n"
            "I will check it and help you know what to do.\n\n"
            "I fit help with typing, arranging, correcting, "
            "converting to PDF, printing, or preparing the document properly.",


            "handwritten_typing":
            "No wahala.\n\n"
            "Snap and send the handwritten document. "
            "I go help convert am into a clean typed document.",


            "assignment_typing":
            "No wahala.\n\n"
            "Send your assignment and I go help type and prepare am.",


            "project_typing":
            "No wahala.\n\n"
            "Send your project pages and I go help prepare the document.",


            "document_typing":
            "No wahala.\n\n"
            "Send the document and tell me what you want done.",


            "document_formatting":
            "No wahala.\n\n"
            "Send the document and I go help arrange and format am properly.",


            "grammar_correction":
            "Send the document or text. I go help correct the grammar.",


            "translation":
            "Send the document and tell me the language you want.",


            "summarization":
            "Send the document and I go help create a summary.",


            "printing":
            "Send the document and tell me the number of copies you need.",


            "scanning":
            "Send the document and I go guide you through the scanning process.",


            "pdf_conversion":
            "Send the file and tell me the format you need.",


            "document_editing":
            "Send the document and tell me the changes you want."
        }


        return responses.get(
            intent,
            "I no understand that request yet."
        )


if __name__ == "__main__":

    ada = AdaIntelligenceExtension()

    while True:

        message = input("Customer: ")

        if message.lower() == "exit":
            break

        intent = ada.detect_extra_intent(message)

        print("\nIntent:", intent)

        print("\nAda:")

        print(
            ada.get_response(intent)
        ) 
