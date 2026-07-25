"""
ada_workflow_extension.py
Extended Workflow for Ada
Naija Pocket Business Center
"""

class AdaWorkflowExtension:

    def create_workflow(self, service):
        workflows = {
            "cv_preparation": "No wahala.\n\nSend your existing CV or your personal details, education, work experience and skills. I'll prepare a professional CV.",
            "cover_letter": "No wahala.\n\nSend the job title, company name and your CV if available. I'll prepare a professional cover letter.",
            "research_assistance": "No wahala.\n\nSend your research topic and any instructions. I'll begin immediately.",
            "business_proposal": "No wahala.\n\nTell me about your business and the proposal you need.",
            "seminar_paper": "No wahala.\n\nSend your seminar topic and any guidelines.",
            "business_letter": "No wahala.\n\nTell me who the letter is for and what you want to communicate.",
            "company_profile": "No wahala.\n\nSend your company information.",
            "invoice": "No wahala.\n\nSend your business name, customer details and items.",
            "quotation": "No wahala.\n\nSend the products or services and their prices.",
            "meeting_minutes": "No wahala.\n\nUpload your meeting notes or recording.",
            "ai_writing": "No wahala.\n\nTell me exactly what you want me to write.",
            "rewrite_document": "No wahala.\n\nUpload or paste your document.",
            "explain_topic": "No wahala.\n\nTell me the topic.",
            "image_to_text": "No wahala.\n\nUpload the image.",
            "voice_to_text": "No wahala.\n\nUpload your voice recording."
        }
        return workflows.get(service,"No wahala.\n\nTell me more about what you need.")

 
