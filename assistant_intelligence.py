"""
assistant_intelligence.py
Shared Intelligence for Ada and Nora
Naija Pocket Business Center
"""

class AssistantIntelligence:

    def __init__(self):
        self.intents = {
            "assignment_typing": ["assignment", "type my assignment"],
            "project_typing": ["project", "final year project", "research project", "thesis", "dissertation"],
            "cv_preparation": ["cv", "resume"],
            "resignation_letter": ["resignation", "resign"],
            "printing": ["print", "printing"],
            "scanning": ["scan", "scanning"],
            "pdf_conversion": ["pdf", "convert"],
            "translation": ["translate", "translation"],
            "grammar_correction": ["grammar", "correct", "proofread"],
            "summarization": ["summary", "summarize"]
        }

        self.job_requirements = {
            "project_typing": {
                "materials": ["project document"],
                "information": ["formatting requirements", "deadline"]
            },
            "assignment_typing": {
                "materials": ["assignment content"],
                "information": ["course title", "deadline"]
            },
            "cv_preparation": {
                "materials": [],
                "information": [
                    "full name",
                    "contact details",
                    "education background",
                    "work experience",
                    "skills"
                ]
            }
        }

    def detect_intent(self, message):
        text = message.lower()
        for intent, keywords in self.intents.items():
            if any(keyword in text for keyword in keywords):
                return intent
        return "unknown"

    def detect_provided_information(self, message):
        text = message.lower()
        provided = []
        if any(day in text for day in [
            "monday","tuesday","wednesday","thursday",
            "friday","saturday","sunday","deadline"
        ]):
            provided.append("deadline")
        if "format" in text:
            provided.append("formatting requirements")
        return provided

    def check_missing(self, intent, message):
        if intent not in self.job_requirements:
            return []
        provided = self.detect_provided_information(message)
        missing = []
        job = self.job_requirements[intent]
        missing.extend(job["materials"])
        for item in job["information"]:
            if item not in provided:
                missing.append(item)
        return missing

    def generate_response(self, person, intent, missing):
        if person != "Ada":
            return "I understand your request. Please provide the remaining information."

        if intent == "project_typing":
            reply = "No wahala. I fit help you type your project.\n\n"
            if "deadline" not in missing:
                reply += "I don note your deadline.\n\n"
            reply += "Please upload or snap the project pages. "
            if "formatting requirements" in missing:
                reply += "If your department get formatting requirements, tell me too."
            return reply

        responses = {
            "assignment_typing": "No wahala. I fit type your assignment.\n\nPlease upload the assignment content and tell me the course title.",
            "cv_preparation": "No wahala. I fit prepare your CV.\n\nSend your personal details, education, experience and skills.",
            "printing": "Upload the document and tell me how many copies you want.",
            "scanning": "Snap or upload the document and I go help you process am.",
            "pdf_conversion": "Upload the file and I go convert am to PDF.",
            "grammar_correction": "Upload or paste the text. I go correct the grammar.",
            "translation": "Upload or paste the text and tell me the language you want.",
            "summarization": "Upload or paste the document and I go summarize am."
        }
        return responses.get(intent,
            "Welcome to Naija Pocket Business Center.\n\nI fit help with typing, CVs, projects, printing, scanning, PDFs, grammar, translation and more.")

    def understand_customer(self, message, person="Ada"):
        intent = self.detect_intent(message)
        missing = self.check_missing(intent, message)
        return {
            "intent": intent,
            "missing": missing,
            "response": self.generate_response(person, intent, missing)
        }

if __name__ == "__main__":
    assistant = AssistantIntelligence()
    while True:
        msg = input("Customer: ")
        if msg.lower() == "exit":
            break
        print(assistant.understand_customer(msg)["response"])

 
