"""
ada_workflow.py
Core Workflow for Ada
Naija Pocket Business Center
"""

from ada_knowledge import AdaKnowledge


class AdaWorkflow:

    def __init__(self):
        self.knowledge = AdaKnowledge()

    def create_workflow(self, service):
        workflows = {
            "handwritten_typing": (
                "No wahala.\n\n"
                "Kindly snap the handwritten pages clearly or upload them.\n\n"
                "If you have any special instructions, kindly send them too.\n\n"
                "If not, I'll type everything neatly using professional formatting.\n\n"
                "I'll start working immediately after I receive everything."
            ),
            "document_typing": (
                "No wahala.\n\n"
                "Upload your document or snap the pages.\n\n"
                "If you have any special instructions, kindly send them too.\n\n"
                "If not, I'll prepare it using professional formatting."
            ),
            "assignment_typing": (
                "No wahala.\n\n"
                "Kindly snap or upload your assignment.\n\n"
                "If your lecturer gave any instructions, kindly send them too."
            ),
            "project_typing": (
                "No wahala.\n\n"
                "Kindly snap or upload your project.\n\n"
                "If your department has a formatting guide, kindly send it too."
            ),
            "cv_preparation": (
                "No wahala.\n\n"
                "Send your personal details, education, work experience and skills.\n\n"
                "If you already have a CV, upload it and I'll improve it."
            ),
            "document_formatting": (
                "No wahala.\n\n"
                "Upload your document and I'll format it professionally."
            ),
            "grammar_correction": (
                "No wahala.\n\n"
                "Upload or paste your document.\n\n"
                "I'll correct grammar, spelling and punctuation."
            ),
            "translation": (
                "No wahala.\n\n"
                "Upload or paste your document and tell me the language you want."
            ),
            "summarization": (
                "No wahala.\n\n"
                "Upload your document and tell me whether you want a short or detailed summary."
            ),
            "pdf_conversion": (
                "No wahala.\n\n"
                "Upload your document and I'll convert it."
            ),
            "printing": (
                "No wahala.\n\n"
                "Upload your document and tell me the number of copies you need."
            ),
            "scanning": (
                "No wahala.\n\n"
                "Upload or snap the document and tell me your preferred output format."
            ),
            "document_editing": (
                "No wahala.\n\n"
                "Upload the document and tell me the changes you want."
            )
        }

        return workflows.get(
            service,
            "No wahala.\n\nKindly send your document or instructions and I'll help you."
        )


if __name__ == "__main__":
    workflow = AdaWorkflow()
    while True:
        service = input("Service (or exit): ").strip()
        if service.lower() == "exit":
            break
        print(workflow.create_workflow(service))

 
