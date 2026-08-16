"""
ada_prompt_manager.py

Ada Prompt Manager
Naija Pocket Business Center

Central prompt assembly point for Ada.

Responsibilities:
- Load Ada's identity
- Load Nigerian Context Intelligence
- Load service-specific prompts
- Load writing styles
- Load workflow, review and delivery rules
- Apply core business rules
- Apply BillingManager rules supplied by AdaAIEngine

AdaAIEngine should NOT import individual prompt files directly.
All prompt intelligence is assembled here.
"""

from ada_identity_prompt import ADA_IDENTITY_PROMPT
from nigerian_context_prompt import NIGERIAN_CONTEXT_PROMPT

from cv_prompt import CV_PROMPT
from cover_letter_prompt import COVER_LETTER_PROMPT
from business_documents_prompt import BUSINESS_DOCUMENTS_PROMPT
from academic_documents_prompt import ACADEMIC_DOCUMENTS_PROMPT
from document_processing_prompt import DOCUMENT_PROCESSING_PROMPT
from review_prompt import REVIEW_PROMPT
from workflow_prompt import WORKFLOW_PROMPT
from delivery_prompt import DELIVERY_PROMPT

from ada_writing_style import (
    GENERAL_STYLE,
    STUDENT_STYLE,
    BUSINESS_STYLE,
    DOCUMENT_STYLE,
    CV_STYLE,
    ADA_STYLE,
)


class AdaPromptManager:

    # ==================================================
    # INITIALIZE
    # ==================================================

    def __init__(self):

        # ----------------------------------------------
        # ADA IDENTITY
        # ----------------------------------------------

        self.identity = ADA_IDENTITY_PROMPT

        # ----------------------------------------------
        # NIGERIAN CONTEXT
        # ----------------------------------------------

        self.nigerian_context = NIGERIAN_CONTEXT_PROMPT

        # ----------------------------------------------
        # SERVICE PROMPTS
        # ----------------------------------------------

        self.prompts = {
            "cv": CV_PROMPT,
            "cover_letter": COVER_LETTER_PROMPT,
            "business": BUSINESS_DOCUMENTS_PROMPT,
            "academic": ACADEMIC_DOCUMENTS_PROMPT,
            "document_processing": DOCUMENT_PROCESSING_PROMPT,
            "review": REVIEW_PROMPT,
            "workflow": WORKFLOW_PROMPT,
            "delivery": DELIVERY_PROMPT,
        }

        # ----------------------------------------------
        # WRITING STYLES
        # ----------------------------------------------

        self.writing_styles = {

            "cv":
                GENERAL_STYLE
                + "\n\n"
                + CV_STYLE,

            "cover_letter":
                GENERAL_STYLE
                + "\n\n"
                + BUSINESS_STYLE,

            "business":
                GENERAL_STYLE
                + "\n\n"
                + BUSINESS_STYLE,

            "academic":
                GENERAL_STYLE
                + "\n\n"
                + STUDENT_STYLE,

            "document_processing":
                GENERAL_STYLE
                + "\n\n"
                + DOCUMENT_STYLE,

            "review":
                GENERAL_STYLE,

            "workflow":
                ADA_STYLE,

            "delivery":
                ADA_STYLE,
        }

    # ==================================================
    # IDENTITY
    # ==================================================

    def get_identity_prompt(self):
        return self.identity

    # ==================================================
    # NIGERIAN CONTEXT
    # ==================================================

    def get_nigerian_context_prompt(self):
        return self.nigerian_context

    # ==================================================
    # SERVICE PROMPT
    # ==================================================

    def get_service_prompt(self, service):

        if not service:
            return ""

        return self.prompts.get(
            service,
            ""
        )

    # ==================================================
    # WRITING STYLE
    # ==================================================

    def get_writing_style(self, service):

        if not service:
            return GENERAL_STYLE

        return self.writing_styles.get(
            service,
            GENERAL_STYLE
        )

    # ==================================================
    # COMPLETE PROMPT
    # ==================================================

    def build_prompt(
        self,
        service=None,
        *extra_prompts
    ):

        prompt_parts = []

        # ==================================================
        # 1. ADA IDENTITY
        # ==================================================

        if self.identity:
            prompt_parts.append(
                self.identity
            )

        # ==================================================
        # 2. NIGERIAN CONTEXT
        # ==================================================

        if self.nigerian_context:
            prompt_parts.append(
                self.nigerian_context
            )

        # ==================================================
        # 3. WRITING STYLE
        # ==================================================

        writing_style = self.get_writing_style(
            service
        )

        if writing_style:
            prompt_parts.append(
                writing_style
            )

        # ==================================================
        # 4. SERVICE-SPECIFIC INTELLIGENCE
        # ==================================================

        if service:

            service_prompt = (
                self.get_service_prompt(
                    service
                )
            )

            if service_prompt:
                prompt_parts.append(
                    service_prompt
                )

        # ==================================================
        # 5. EXTRA INSTRUCTIONS
        # ==================================================

        for prompt in extra_prompts:

            if prompt:
                prompt_parts.append(
                    str(prompt)
                )

        # ==================================================
        # 6. CORE BUSINESS RULES
        # ==================================================

        prompt_parts.append(
            """
==================================================
NAIJA POCKET BUSINESS CENTER
CORE BUSINESS RULES
==================================================

Ada provides professional digital document
services for customers.

Default delivery formats are:

• DOCX
• PDF

Do not discuss printing unless the customer
specifically asks about printing.

Never invent services that Naija Pocket Business
Center does not provide.

==================================================
CUSTOMER INFORMATION
==================================================

Use information supplied by the customer.

Do not invent:

• Names
• Addresses
• Phone numbers
• Email addresses
• Qualifications
• Employment history
• Company information
• School information
• Business information
• Statistics
• References
• Certificates
• Registration numbers
• Financial information

If essential information is missing,
ask the customer for it.

Do not make the customer repeat information
already supplied.

==================================================
NIGERIAN CONTEXT
==================================================

Nigerian Context Intelligence is an active part
of Ada's operating instructions.

Use Nigerian context naturally when relevant.

Do not force Nigerian references into documents
where they do not belong.

Formal documents should normally use clear,
professional Nigerian English unless the customer
requests another style.

==================================================
BILLING RULES
==================================================

BillingManager is the ONLY official source of
Naija Pocket Business Center service prices.

Never guess a price.

Never estimate a price.

Never create a market price.

Never change a BillingManager price.

Never increase a BillingManager price.

Never reduce a BillingManager price.

Never invent a discount.

Never invent an additional charge.

Never apologise for an official price.

If BillingManager provides a fixed price,
use the exact fixed price.

If BillingManager provides a per-page price,
use the exact per-page price.

If BillingManager requires a quotation,
tell the customer that a quotation is required.

BillingManager prices must always take priority
over Ada's general knowledge or assumptions.

==================================================
SERVICE PRICING BEHAVIOUR
==================================================

When a customer clearly asks about the price
of a supported service:

1. Identify the requested service.
2. Use the official BillingManager information.
3. Give the exact official price.
4. State whether it is fixed, per page or quotation.
5. Continue naturally with the next useful step.

Do not make the customer ask repeatedly for
the price when the official price is available.

==================================================
CUSTOMER COMMUNICATION
==================================================

Ada should be:

• Warm
• Respectful
• Friendly
• Professional
• Practical
• Clear
• Reassuring

Understand informal Nigerian customer language.

Examples include:

"I need CV."
"How much CV?"
"Abeg help me."
"I wan do project."
"How much una dey charge?"
"I want to type this."
"Help me write this."

Do not require perfect English before
understanding the customer's request.

When appropriate, Ada may respond naturally
in Nigerian Pidgin.

Do not overuse Pidgin.

Formal documents must remain professional.

==================================================
INTELLIGENT SERVICE HANDLING
==================================================

When a customer mentions a service:

• Identify what the customer wants.
• Check the relevant service intelligence.
• Check BillingManager when pricing is relevant.
• Ask only the next necessary question.
• Do not restart the conversation.
• Do not ask questions whose answers are already known.
• Do not rely only on keywords when enough context
  is available to understand the customer's request.

Ada should behave as an intelligent business
centre assistant, not as a simple keyword menu.

==================================================
DOCUMENT QUALITY
==================================================

Documents must be:

• Natural
• Professional
• Clear
• Accurate
• Practical
• Suitable for the Nigerian environment when relevant
• Suitable for editing
• Suitable for printing

Never manufacture Nigerian facts simply to make
a document appear Nigerian.

==================================================
INSTRUCTION PRIORITY
==================================================

When instructions conflict, follow this priority:

1. Customer's explicit instructions
2. Accurate customer-provided information
3. Official BillingManager information
4. Required service prompt
5. Nigerian Context Intelligence
6. General writing style

Nigerian Context Intelligence improves relevance.

It must never override actual customer information.

BillingManager remains the sole authority for
official service prices.
"""
        )

        # ==================================================
        # FINAL PROMPT
        # ==================================================

        return "\n\n".join(
            prompt_parts
        )


# ==========================================================
# TEST
# ==========================================================

if __name__ == "__main__":

    manager = AdaPromptManager()

    print("=" * 60)
    print("ADA PROMPT MANAGER TEST")
    print("=" * 60)
    print()

    prompt = manager.build_prompt(
        service="cv"
    )

    print(
        "Identity loaded:",
        bool(manager.identity)
    )

    print(
        "Nigerian context loaded:",
        bool(manager.nigerian_context)
    )

    print(
        "CV prompt loaded:",
        bool(
            manager.get_service_prompt(
                "cv"
            )
        )
    )

    print(
        "CV writing style loaded:",
        bool(
            manager.get_writing_style(
                "cv"
            )
        )
    )

    print()

    print(
        "NIGERIAN CONTEXT PRESENT:",
        "NIGERIAN CONTEXT"
        in prompt
    )

    print(
        "BILLING RULES PRESENT:",
        "BILLING RULES"
        in prompt
    )

    print()
    print(
        "ADA PROMPT MANAGER READY"
    ) 
