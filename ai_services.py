# ada_services.py

from jobs import create_job, get_job


# ==========================================================
# ADA IDENTITY
# ==========================================================

ADA_NAME = "Ada"
ADA_ROLE = "Your Business Center & Cyber Café Girl"


# ==========================================================
# ADA GREETING
# ==========================================================

def ada_greeting():

    return (
        "Hello 😊 I be Ada, your Business Center & Cyber Café Girl.\n\n"
        "How I fit help you today?\n\n"
        "I fit help with:\n"
        "• Typing\n"
        "• Printing\n"
        "• PDF conversion\n"
        "• CV preparation\n"
        "• Document formatting\n"
        "• Business documents\n\n"
        "Just tell me wetin you need."
    )


# ==========================================================
# CREATE CUSTOMER JOB
# ==========================================================

def receive_customer_request(
    customer_name,
    phone,
    service_type,
    description
):

    job_id = create_job(
        customer_name,
        phone,
        service_type,
        description,
        0
    )

    if job_id:

        return (
            f"Thank you {customer_name} 😊\n\n"
            f"I don receive your request.\n\n"
            f"Your job number na #{job_id}.\n\n"
            "I go prepare am and show you preview first."
        )

    return (
        "Sorry 😊\n\n"
        "I no fit create your request now. "
        "Please try again."
    )


# ==========================================================
# CHECK JOB
# ==========================================================

def check_customer_job(job_id):

    job = get_job(job_id)

    if job:

        return (
            f"Your work update:\n\n"
            f"Service: {job['service_type']}\n"
            f"Progress: {job['status']}"
        )

    return "I no find that job number."


# ==========================================================
# ADA BUSINESS REPLY
# ==========================================================

def ada_response(message):

    message = message.lower().strip()


    # Greeting

    if any(word in message for word in [
        "hello",
        "hi",
        "good morning",
        "good afternoon",
        "good evening"
    ]):

        return ada_greeting()


    # Typing

    if "typing" in message or "type" in message:

        return (
            "No wahala 😊\n\n"
            "Send the document make I prepare am.\n\n"
            "If na paper document, tap "
            "\"Snap Document\" make you take picture am.\n\n"
            "I go check am and prepare preview first."
        )


    # Printing

    if "print" in message:

        return (
            "No wahala 😊\n\n"
            "Send the document you want to print.\n\n"
            "I go prepare am for printing."
        )


    # PDF

    if "pdf" in message:

        return (
            "I fit help you convert document to PDF 😊\n\n"
            "Send the file make I prepare am."
        )


    # CV

    if "cv" in message or "resume" in message:

        return (
            "No wahala 😊\n\n"
            "I fit help you prepare professional CV.\n\n"
            "Send your old CV or tell me your details."
        )


    # Upload / Snap

    if (
        "upload" in message
        or "send file" in message
        or "snap" in message
    ):

        return (
            "You fit send the document here 😊\n\n"
            "If na paper, use Snap Document "
            "make all the pages enter."
        )


    # Price

    if (
        "price" in message
        or "cost" in message
        or "how much" in message
    ):

        return (
            "The price depend on the work 😊\n\n"
            "Send the document first make I check am "
            "and give you the correct price."
        )


    # Payment

    if "payment" in message or "pay" in message:

        return (
            "After I finish the work, I go show you "
            "preview first.\n\n"
            "Once payment don enter, your final copy go come out."
        )


    # Default

    return (
        "No wahala 😊\n\n"
        "I fit help you with:\n\n"
        "• Typing\n"
        "• Printing\n"
        "• PDF conversion\n"
        "• CV\n"
        "• Business documents\n\n"
        "Tell me wetin you need."
    )


# ==========================================================
# TEST
# ==========================================================

if __name__ == "__main__":

    print(ada_greeting())

    print()

    print(
        ada_response(
            "I need typing"
        )
    ) 
