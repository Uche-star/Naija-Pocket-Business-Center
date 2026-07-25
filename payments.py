from database import execute_query, fetch_all, fetch_one


# ==========================================================
# CREATE PAYMENT RECORD
# ==========================================================

def create_payment(job_id, amount, payment_method):
    """
    Creates a new payment record for a job.
    """

    query = """
    INSERT INTO payments
    (
        job_id,
        amount,
        payment_method
    )
    VALUES (?, ?, ?)
    """

    return execute_query(
        query,
        (
            job_id,
            amount,
            payment_method
        )
    )



# ==========================================================
# GET ALL PAYMENTS
# ==========================================================

def get_all_payments():
    """
    Returns all payment records.
    """

    query = """
    SELECT *
    FROM payments
    ORDER BY id DESC
    """

    return fetch_all(query)



# ==========================================================
# GET PAYMENT BY ID
# ==========================================================

def get_payment(payment_id):
    """
    Finds a payment using payment ID.
    """

    query = """
    SELECT *
    FROM payments
    WHERE id = ?
    """

    return fetch_one(
        query,
        (payment_id,)
    )



# ==========================================================
# GET PAYMENTS FOR A JOB
# ==========================================================

def get_job_payments(job_id):
    """
    Returns all payments connected to a specific job.
    """

    query = """
    SELECT *
    FROM payments
    WHERE job_id = ?
    ORDER BY id DESC
    """

    return fetch_all(
        query,
        (job_id,)
    )



# ==========================================================
# UPDATE PAYMENT STATUS
# ==========================================================

def update_payment_status(payment_id, status):
    """
    Updates payment status.

    Examples:
    pending
    paid
    cancelled
    """

    query = """
    UPDATE payments
    SET payment_status = ?
    WHERE id = ?
    """

    return execute_query(
        query,
        (
            status,
            payment_id
        )
    )



# ==========================================================
# DELETE PAYMENT
# ==========================================================

def delete_payment(payment_id):
    """
    Removes a payment record.
    """

    query = """
    DELETE FROM payments
    WHERE id = ?
    """

    return execute_query(
        query,
        (payment_id,)
    )



# ==========================================================
# TEST FILE
# ==========================================================

if __name__ == "__main__":

    print("Payments module is working correctly.")