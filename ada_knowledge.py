"""
ada_knowledge.py
Knowledge Base for Ada
Naija Pocket Business Center
"""

class AdaKnowledge:

    def __init__(self):

        self.services = {

            "document_typing": {
                "name": "General Document Typing",
                "materials": [
                    "Document or handwritten notes"
                ],
                "information": [
                    "Preferred output format"
                ],
                "outputs": [
                    "Microsoft Word",
                    "PDF"
                ]
            },

            "handwritten_typing": {
                "name": "Handwritten Typing",
                "materials": [
                    "Clear photo or scanned copy"
                ],
                "information": [
                    "Preferred output format"
                ],
                "outputs": [
                    "Microsoft Word",
                    "PDF"
                ]
            },

            "assignment_typing": {
                "name": "Assignment Typing",
                "materials": [
                    "Assignment pages"
                ],
                "information": [
                    "Course title",
                    "Deadline"
                ],
                "outputs": [
                    "Microsoft Word",
                    "PDF"
                ]
            },

            "project_typing": {
                "name": "Project Typing",
                "materials": [
                    "Project document"
                ],
                "information": [
                    "Formatting requirements",
                    "Deadline"
                ],
                "outputs": [
                    "Microsoft Word",
                    "PDF"
                ]
            },

            "cv_preparation": {
                "name": "CV Preparation",
                "materials": [
                    "Existing CV (optional)"
                ],
                "information": [
                    "Full name",
                    "Contact details",
                    "Education",
                    "Work experience",
                    "Skills"
                ],
                "outputs": [
                    "Microsoft Word",
                    "PDF"
                ]
            },

            "document_formatting": {
                "name": "Document Formatting",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Formatting instructions"
                ],
                "outputs": [
                    "Formatted document"
                ]
            },

            "grammar_correction": {
                "name": "Grammar Correction",
                "materials": [
                    "Document or text"
                ],
                "information": [
                    "Preferred English style"
                ],
                "outputs": [
                    "Corrected document"
                ]
            },

            "translation": {
                "name": "Translation",
                "materials": [
                    "Document or text"
                ],
                "information": [
                    "Source language",
                    "Target language"
                ],
                "outputs": [
                    "Translated document"
                ]
            },

            "summarization": {
                "name": "Summarization",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Short or detailed summary"
                ],
                "outputs": [
                    "Summary"
                ]
            },

            "printing": {
                "name": "Printing",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Number of copies",
                    "Paper size",
                    "Colour or black and white"
                ],
                "outputs": [
                    "Printed document"
                ]
            },

            "scanning": {
                "name": "Scanning",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Preferred output format"
                ],
                "outputs": [
                    "PDF",
                    "Image"
                ]
            },

            "pdf_conversion": {
                "name": "PDF Conversion",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Preferred output format"
                ],
                "outputs": [
                    "PDF",
                    "Microsoft Word"
                ]
            },

            "document_editing": {
                "name": "Document Editing",
                "materials": [
                    "Document"
                ],
                "information": [
                    "Required changes"
                ],
                "outputs": [
                    "Edited document"
                ]
            }
        }

    def get_service(self, service):
        return self.services.get(service)

    def list_services(self):
        return list(self.services.keys())

    def service_exists(self, service):
        return service in self.services


if __name__ == "__main__":

    knowledge = AdaKnowledge()

    for service in knowledge.list_services():
        print(service) 
