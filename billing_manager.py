"""
billing_manager.py

Naija Pocket Business Center
Official Billing Manager

Responsibilities
----------------
• Stores the official prices for all 28 customer-facing services
• Keeps additional supported services
• Supports fixed, per-page and quotation billing
• Accepts website service names and internal service keys
• BillingManager is the ONLY source of official prices for Ada
"""


class BillingManager:

    # ==================================
    # INITIALIZE
    # ==================================
    def __init__(self):

        self.price_list = {

            # ==================================
            # 28 CUSTOMER-FACING SERVICES
            # ==================================

            "document_typing": {
                "price": 200,
                "billing": "per_page"
            },

            "document_formatting": {
                "price": 1500,
                "billing": "fixed"
            },

            "document_editing": {
                "price": 250,
                "billing": "per_page"
            },

            "grammar_correction": {
                "price": 200,
                "billing": "per_page"
            },

            "assignment_typing": {
                "price": 2500,
                "billing": "fixed"
            },

            "project_typing": {
                "price": 250,
                "billing": "per_page"
            },

            "research_assistance": {
                "price": 3500,
                "billing": "fixed"
            },

            "seminar_paper": {
                "price": 3500,
                "billing": "fixed"
            },

            "cv": {
                "price": 2500,
                "billing": "fixed"
            },

            "cover_letter": {
                "price": 1500,
                "billing": "fixed"
            },

            "business_proposal": {
                "price": 7500,
                "billing": "fixed"
            },

            "company_profile": {
                "price": 5000,
                "billing": "fixed"
            },

            "business_letters_letterhead": {
                "price": 2000,
                "billing": "fixed"
            },

            "invoices": {
                "price": 2000,
                "billing": "fixed"
            },

            "quotations": {
                "price": 2000,
                "billing": "fixed"
            },

            "meeting_minutes": {
                "price": 2500,
                "billing": "fixed"
            },

            "ai_writing_assistance": {
                "price": 2500,
                "billing": "fixed"
            },

            "document_rewriting": {
                "price": 2500,
                "billing": "fixed"
            },

            "translation": {
                "price": 0,
                "billing": "quotation"
            },

            "summarization": {
                "price": 2500,
                "billing": "fixed"
            },

            "pdf_conversion": {
                "price": 1000,
                "billing": "fixed"
            },

            "voice_to_text": {
                "price": 250,
                "billing": "per_page"
            },

            "topic_explanations": {
                "price": 2000,
                "billing": "fixed"
            },

            "printing_preparation": {
                "price": 1000,
                "billing": "fixed"
            },

            "excel_spreadsheets": {
                "price": 3000,
                "billing": "fixed"
            },

            "data_entry": {
                "price": 2000,
                "billing": "fixed"
            },

            "data_analysis": {
                "price": 5000,
                "billing": "fixed"
            },

            "presentations": {
                "price": 5000,
                "billing": "fixed"
            },

            # ==================================
            # ADDITIONAL SUPPORTED SERVICES
            # ==================================

            "cv_cover_letter": {
                "price": 3500,
                "billing": "fixed"
            },

            "term_paper": {
                "price": 3500,
                "billing": "fixed"
            },

            "research_proposal": {
                "price": 6000,
                "billing": "fixed"
            },

            "handwritten_typing": {
                "price": 250,
                "billing": "per_page"
            },

            "thesis_typing": {
                "price": 250,
                "billing": "per_page"
            },

            "dissertation_typing": {
                "price": 350,
                "billing": "per_page"
            },

            "business_plan": {
                "price": 15000,
                "billing": "fixed"
            },

            # ==================================
            # INTERNAL SERVICES
            # ==================================

            "workflow": {
                "price": 0,
                "billing": "internal"
            },

            "delivery": {
                "price": 0,
                "billing": "internal"
            },

            "conversation": {
                "price": 0,
                "billing": "internal"
            }
        }

        # ==================================
        # WEBSITE / HUMAN-READABLE ALIASES
        # ==================================

        self.service_aliases = {

            "Document Typing": "document_typing",
            "Document Formatting": "document_formatting",
            "Document Editing": "document_editing",
            "Grammar Correction": "grammar_correction",
            "Assignments": "assignment_typing",
            "Assignment": "assignment_typing",
            "Projects": "project_typing",
            "Project": "project_typing",
            "Research Assistance": "research_assistance",
            "Seminar Papers": "seminar_paper",
            "Seminar Paper": "seminar_paper",
            "CVs & Résumés": "cv",
            "CVs & Resumes": "cv",
            "CV": "cv",
            "Résumé": "cv",
            "Resume": "cv",
            "Cover Letters": "cover_letter",
            "Cover Letter": "cover_letter",
            "Business Proposals": "business_proposal",
            "Company Profiles": "company_profile",
            "Business Letters & Letterhead":
                "business_letters_letterhead",
            "Invoices": "invoices",
            "Quotations": "quotations",
            "Meeting Minutes": "meeting_minutes",
            "AI Writing Assistance": "ai_writing_assistance",
            "Document Rewriting": "document_rewriting",
            "Translation": "translation",
            "Document Summarization": "summarization",
            "PDF Conversion": "pdf_conversion",
            "Voice To Text": "voice_to_text",
            "Topic Explanations": "topic_explanations",
            "Printing Preparation": "printing_preparation",
            "Excel Spreadsheets": "excel_spreadsheets",
            "Data Entry": "data_entry",
            "Data Analysis": "data_analysis",
            "Presentations": "presentations",

            # Additional services
            "CV + Cover Letter": "cv_cover_letter",
            "Term Paper": "term_paper",
            "Research Proposal": "research_proposal",
            "Handwritten Typing": "handwritten_typing",
            "Thesis Typing": "thesis_typing",
            "Dissertation Typing": "dissertation_typing",
            "Business Plan": "business_plan",

            # Internal services
            "Workflow": "workflow",
            "Delivery": "delivery",
            "Conversation": "conversation"
        }

    # ==================================
    # NORMALIZE SERVICE
    # ==================================
    def normalize_service(self, service):

        if not service:
            return None

        if service in self.price_list:
            return service

        if service in self.service_aliases:
            return self.service_aliases[service]

        cleaned = str(service).strip().lower()

        for alias, internal_name in self.service_aliases.items():

            if alias.lower() == cleaned:
                return internal_name

        normalized = (
            cleaned
            .replace("&", "and")
            .replace("-", " ")
            .replace("_", " ")
        )

        for key in self.price_list:

            key_normalized = key.replace("_", " ")

            if key_normalized == normalized:
                return key

        return None

    # ==================================
    # SERVICE EXISTS
    # ==================================
    def has_service(self, service):

        return self.normalize_service(service) is not None

    # ==================================
    # GET SERVICE
    # ==================================
    def get_service(self, service):

        internal_service = self.normalize_service(service)

        if internal_service is None:
            return None

        return self.price_list.get(internal_service)

    # ==================================
    # GET PRICE
    # ==================================
    def get_price(self, service):

        item = self.get_service(service)

        if item is None:
            return 0

        return item["price"]

    # ==================================
    # GET BILLING TYPE
    # ==================================
    def get_billing_type(self, service):

        item = self.get_service(service)

        if item is None:
            return None

        return item["billing"]

    # ==================================
    # CHANGE PRICE
    # ==================================
    def set_price(self, service, amount):

        internal_service = self.normalize_service(service)

        if internal_service in self.price_list:

            self.price_list[
                internal_service
            ]["price"] = amount

    # ==================================
    # GENERATE BILL
    # ==================================
    def generate_bill(self, service):

        internal_service = self.normalize_service(service)

        return {
            "service": internal_service,
            "price": self.get_price(service),
            "billing": self.get_billing_type(service)
        }

    # ==================================
    # CUSTOMER BILL MESSAGE
    # ==================================
    def bill_message(self, service):

        if not self.has_service(service):

            return (
                "Sorry, pricing is currently unavailable "
                "for this service."
            )

        item = self.get_service(service)

        amount = item["price"]
        billing = item["billing"]

        internal_service = self.normalize_service(service)

        service_name = (
            internal_service
            .replace("_", " ")
            .title()
        )

        if billing == "fixed":

            return (
                f"Service: {service_name}\n\n"
                f"Price: ₦{amount:,}"
            )

        if billing == "per_page":

            return (
                f"Service: {service_name}\n\n"
                f"Price: ₦{amount:,} per page."
            )

        if billing == "quotation":

            return (
                f"Service: {service_name}\n\n"
                "This service requires a custom quotation.\n"
                "Kindly upload your document or provide "
                "more details for accurate pricing."
            )

        return (
            f"{service_name}\n\n"
            "This is an internal workflow service."
        )

    # ==================================
    # PRICE LIST
    # ==================================
    def get_price_list(self):

        return self.price_list


# ======================================
# TEST
# ======================================

if __name__ == "__main__":

    billing = BillingManager()

    print("=" * 60)
    print("NAIJA POCKET BUSINESS CENTER")
    print("BILLING MANAGER")
    print("=" * 60)
    print()

    print("28 CUSTOMER-FACING SERVICES")
    print("-" * 60)

    customer_services = [

        "Document Typing",
        "Document Formatting",
        "Document Editing",
        "Grammar Correction",
        "Assignments",
        "Projects",
        "Research Assistance",
        "Seminar Papers",
        "CVs & Résumés",
        "Cover Letters",
        "Business Proposals",
        "Company Profiles",
        "Business Letters & Letterhead",
        "Invoices",
        "Quotations",
        "Meeting Minutes",
        "AI Writing Assistance",
        "Document Rewriting",
        "Translation",
        "Document Summarization",
        "PDF Conversion",
        "Voice To Text",
        "Topic Explanations",
        "Printing Preparation",
        "Excel Spreadsheets",
        "Data Entry",
        "Data Analysis",
        "Presentations"
    ]

    for number, service in enumerate(
        customer_services,
        start=1
    ):

        item = billing.get_service(service)

        if item["billing"] == "quotation":

            print(
                f"{number:02d}. "
                f"{service}: quotation"
            )

        else:

            print(
                f"{number:02d}. "
                f"{service}: "
                f"{item['billing']} "
                f"₦{item['price']:,}"
            )

    print()
    print("ADDITIONAL SERVICES")
    print("-" * 60)

    additional_services = [

        "CV + Cover Letter",
        "Term Paper",
        "Research Proposal",
        "Handwritten Typing",
        "Thesis Typing",
        "Dissertation Typing",
        "Business Plan"
    ]

    for service in additional_services:

        item = billing.get_service(service)

        if item["billing"] == "quotation":

            print(
                f"{service}: quotation"
            )

        else:

            print(
                f"{service}: "
                f"{item['billing']} "
                f"₦{item['price']:,}"
            )

    print()
    print("BILLING MANAGER READY") 
