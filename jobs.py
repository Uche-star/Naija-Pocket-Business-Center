# jobs.py

from database import execute_query, fetch_all, fetch_one


# ==========================================================
# CREATE NEW CUSTOMER JOB
# ==========================================================

def create_job(
    customer_name,
    phone,
    service_type,
    description,
    amount=0
):
    """
    Creates a new customer work request.

    New jobs start as:
    received
    """

    query = """
    INSERT INTO jobs
    (
        customer_name,
        phone,
        service_type,
        description,
        status,
        amount
    )
    VALUES (?, ?, ?, ?, ?, ?)
    """

    return execute_query(
        query,
        (
            customer_name,
            phone,
            service_type,
            description,
            "received",
            amount
        )
    )


# ==========================================================
# GET ALL JOBS
# ==========================================================

def get_all_jobs():
    """
    Returns all customer jobs.
    """

    query = """
    SELECT *
    FROM jobs
    ORDER BY id DESC
    """

    return fetch_all(query)


# ==========================================================
# GET SINGLE JOB
# ==========================================================

def get_job(job_id):
    """
    Finds a customer job by ID.
    """

    query = """
    SELECT *
    FROM jobs
    WHERE id = ?
    """

    return fetch_one(
        query,
        (job_id,)
    )


# ==========================================================
# UPDATE JOB STATUS
# ==========================================================

def update_job_status(job_id, status):
    """
    Updates job progress.

    Examples:
    received
    working
    preview_ready
    payment_pending
    completed
    """

    query = """
    UPDATE jobs
    SET status = ?
    WHERE id = ?
    """

    return execute_query(
        query,
        (
            status,
            job_id
        )
    )


# ==========================================================
# UPDATE JOB AMOUNT
# ==========================================================

def update_job_amount(job_id, amount):
    """
    Sets the final price after reviewing the work.
    """

    query = """
    UPDATE jobs
    SET amount = ?
    WHERE id = ?
    """

    return execute_query(
        query,
        (
            amount,
            job_id
        )
    )


# ==========================================================
# MARK PREVIEW READY
# ==========================================================

def mark_preview_ready(job_id):
    """
    Means Ada has completed the work
    and a watermarked preview can be shown.
    """

    return update_job_status(
        job_id,
        "preview_ready"
    )


# ==========================================================
# CONFIRM PAYMENT
# ==========================================================

def confirm_payment(job_id):
    """
    After payment, the final copy can be released.
    """

    return update_job_status(
        job_id,
        "completed"
    )


# ==========================================================
# DELETE JOB
# ==========================================================

def delete_job(job_id):
    """
    Removes a job.
    """

    query = """
    DELETE FROM jobs
    WHERE id = ?
    """

    return execute_query(
        query,
        (job_id,)
    )


# ==========================================================
# DISPLAY JOB INFORMATION
# ==========================================================

def format_job(job):
    """
    Converts a job record into a customer-friendly message.
    """

    if not job:
        return "I no find that job number."

    return (
        f"Job Number: #{job['id']}\n\n"
        f"Service: {job['service_type']}\n"
        f"Customer: {job['customer_name']}\n\n"
        f"Progress: {job['status']}"
    )


# ==========================================================
# TEST
# ==========================================================

if __name__ == "__main__":

    print("Jobs module is working correctly.") 
