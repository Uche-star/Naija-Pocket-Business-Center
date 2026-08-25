"""
database.py

Naija Pocket Business Center
Simple SQLite Database

Stores:
- Customer jobs
- Customer work/documents
- Payments

The database records the information about a job.
The actual customer files are stored on disk by Python.

This system is service-neutral and can be used for:
- CVs
- Cover letters
- Projects
- Assignments
- Seminar papers
- Academic documents
- Business documents
- Typing
- Editing
- Formatting
- Other supported services
"""

import os
import sqlite3
from datetime import datetime


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DATABASE_NAME = "naija_pocket_business.db"

BASE_WORK_DIRECTORY = "customer_work"


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    try:

        conn = sqlite3.connect(
            DATABASE_NAME
        )

        conn.row_factory = sqlite3.Row

        # Make SQLite enforce foreign-key relationships.
        conn.execute(
            "PRAGMA foreign_keys = ON"
        )

        return conn

    except sqlite3.Error as error:

        print(
            "Database connection error:",
            error
        )

        return None


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def initialize_database():

    conn = get_connection()

    if conn is None:
        return False

    try:

        cursor = conn.cursor()

        # ====================================================
        # JOBS
        # ====================================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT,
            service_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            amount REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # ====================================================
        # PAYMENTS
        # ====================================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT,
            payment_status TEXT DEFAULT 'pending',
            payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id)
                REFERENCES jobs(id)
                ON DELETE CASCADE
        )
        """)

        # ====================================================
        # DOCUMENTS / CUSTOMER WORK
        # ====================================================

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT,
            document_status TEXT DEFAULT 'working',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id)
                REFERENCES jobs(id)
                ON DELETE CASCADE
        )
        """)

        # ====================================================
        # SAFE MIGRATION FOR EXISTING DATABASES
        # ====================================================

        # Existing databases created before updated_at was
        # introduced may not have that column.
        #
        # SQLite does not allow IF NOT EXISTS on ADD COLUMN,
        # so check first.

        cursor.execute(
            "PRAGMA table_info(jobs)"
        )

        existing_columns = {
            row["name"]
            for row in cursor.fetchall()
        }

        if "updated_at" not in existing_columns:

            cursor.execute("""
            ALTER TABLE jobs
            ADD COLUMN updated_at
            DATETIME
            """)

            cursor.execute("""
            UPDATE jobs
            SET updated_at = created_at
            WHERE updated_at IS NULL
            """)

        # ====================================================
        # WORK DIRECTORY
        # ====================================================

        os.makedirs(
            BASE_WORK_DIRECTORY,
            exist_ok=True
        )

        conn.commit()

        return True

    except sqlite3.Error as error:

        print(
            "Database setup error:",
            error
        )

        return False

    finally:

        conn.close()


# ============================================================
# GENERAL QUERY HELPERS
# ============================================================

def execute_query(
    query,
    params=()
):

    conn = get_connection()

    if conn is None:
        return None

    try:

        cursor = conn.cursor()

        cursor.execute(
            query,
            params
        )

        conn.commit()

        return cursor.lastrowid

    except sqlite3.Error as error:

        print(
            "Query error:",
            error
        )

        return None

    finally:

        conn.close()


def fetch_all(
    query,
    params=()
):

    conn = get_connection()

    if conn is None:
        return []

    try:

        cursor = conn.cursor()

        cursor.execute(
            query,
            params
        )

        return cursor.fetchall()

    except sqlite3.Error as error:

        print(
            "Fetch error:",
            error
        )

        return []

    finally:

        conn.close()


def fetch_one(
    query,
    params=()
):

    conn = get_connection()

    if conn is None:
        return None

    try:

        cursor = conn.cursor()

        cursor.execute(
            query,
            params
        )

        return cursor.fetchone()

    except sqlite3.Error as error:

        print(
            "Fetch error:",
            error
        )

        return None

    finally:

        conn.close()


# ============================================================
# JOB FUNCTIONS
# ============================================================

def create_job(
    customer_name,
    service_type,
    phone=None,
    description=None,
    amount=0,
    status="pending"
):

    query = """
    INSERT INTO jobs (
        customer_name,
        phone,
        service_type,
        description,
        status,
        amount,
        updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """

    return execute_query(
        query,
        (
            customer_name,
            phone,
            service_type,
            description,
            status,
            amount
        )
    )


def get_job(
    job_id
):

    query = """
    SELECT *
    FROM jobs
    WHERE id = ?
    """

    return fetch_one(
        query,
        (job_id,)
    )


def get_all_jobs():

    query = """
    SELECT *
    FROM jobs
    ORDER BY updated_at DESC, id DESC
    """

    return fetch_all(
        query
    )


def get_jobs_by_status(
    status
):

    query = """
    SELECT *
    FROM jobs
    WHERE status = ?
    ORDER BY updated_at DESC, id DESC
    """

    return fetch_all(
        query,
        (status,)
    )


def update_job_status(
    job_id,
    status
):

    query = """
    UPDATE jobs
    SET
        status = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """

    result = execute_query(
        query,
        (
            status,
            job_id
        )
    )

    return result is not None


def update_job(
    job_id,
    customer_name=None,
    phone=None,
    service_type=None,
    description=None,
    amount=None,
    status=None
):

    current = get_job(
        job_id
    )

    if current is None:
        return False

    new_customer_name = (
        customer_name
        if customer_name is not None
        else current["customer_name"]
    )

    new_phone = (
        phone
        if phone is not None
        else current["phone"]
    )

    new_service_type = (
        service_type
        if service_type is not None
        else current["service_type"]
    )

    new_description = (
        description
        if description is not None
        else current["description"]
    )

    new_amount = (
        amount
        if amount is not None
        else current["amount"]
    )

    new_status = (
        status
        if status is not None
        else current["status"]
    )

    query = """
    UPDATE jobs
    SET
        customer_name = ?,
        phone = ?,
        service_type = ?,
        description = ?,
        amount = ?,
        status = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """

    result = execute_query(
        query,
        (
            new_customer_name,
            new_phone,
            new_service_type,
            new_description,
            new_amount,
            new_status,
            job_id
        )
    )

    return result is not None


# ============================================================
# CUSTOMER WORK DIRECTORY
# ============================================================

def get_job_directory(
    job_id
):

    directory = os.path.join(
        BASE_WORK_DIRECTORY,
        f"JOB-{job_id:06d}"
    )

    os.makedirs(
        directory,
        exist_ok=True
    )

    return directory


def get_work_file_path(
    job_id,
    file_name
):

    directory = get_job_directory(
        job_id
    )

    safe_name = os.path.basename(
        str(file_name)
    )

    return os.path.join(
        directory,
        safe_name
    )


# ============================================================
# DOCUMENT / CUSTOMER WORK FUNCTIONS
# ============================================================

def save_document_record(
    job_id,
    file_name,
    file_path,
    file_type=None,
    document_status="working"
):

    # Make sure the job exists.

    job = get_job(
        job_id
    )

    if job is None:
        return None

    query = """
    INSERT INTO documents (
        job_id,
        file_name,
        file_path,
        file_type,
        document_status,
        updated_at
    )
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """

    document_id = execute_query(
        query,
        (
            job_id,
            os.path.basename(
                str(file_name)
            ),
            str(file_path),
            file_type,
            document_status
        )
    )

    if document_id is not None:

        # Saving a document also means the job
        # has been updated.

        execute_query(
            """
            UPDATE jobs
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,)
        )

    return document_id


def get_document(
    document_id
):

    query = """
    SELECT *
    FROM documents
    WHERE id = ?
    """

    return fetch_one(
        query,
        (document_id,)
    )


def get_documents_for_job(
    job_id
):

    query = """
    SELECT *
    FROM documents
    WHERE job_id = ?
    ORDER BY updated_at DESC, id DESC
    """

    return fetch_all(
        query,
        (job_id,)
    )


def get_latest_document(
    job_id
):

    query = """
    SELECT *
    FROM documents
    WHERE job_id = ?
    ORDER BY updated_at DESC, id DESC
    LIMIT 1
    """

    return fetch_one(
        query,
        (job_id,)
    )


def update_document(
    document_id,
    file_name=None,
    file_path=None,
    file_type=None,
    document_status=None
):

    current = get_document(
        document_id
    )

    if current is None:
        return False

    new_file_name = (
        file_name
        if file_name is not None
        else current["file_name"]
    )

    new_file_path = (
        file_path
        if file_path is not None
        else current["file_path"]
    )

    new_file_type = (
        file_type
        if file_type is not None
        else current["file_type"]
    )

    new_document_status = (
        document_status
        if document_status is not None
        else current["document_status"]
    )

    query = """
    UPDATE documents
    SET
        file_name = ?,
        file_path = ?,
        file_type = ?,
        document_status = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """

    result = execute_query(
        query,
        (
            os.path.basename(
                str(new_file_name)
            ),
            str(new_file_path),
            new_file_type,
            new_document_status,
            document_id
        )
    )

    if result is not None:

        execute_query(
            """
            UPDATE jobs
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current["job_id"],)
        )

    return result is not None


def update_document_status(
    document_id,
    status
):

    query = """
    UPDATE documents
    SET
        document_status = ?,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """

    result = execute_query(
        query,
        (
            status,
            document_id
        )
    )

    if result is not None:

        document = get_document(
            document_id
        )

        if document:

            execute_query(
                """
                UPDATE jobs
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (document["job_id"],)
            )

    return result is not None


# ============================================================
# SAVE CUSTOMER WORK
# ============================================================

def save_customer_work(
    job_id,
    source_file_path,
    file_name=None,
    file_type=None,
    document_status="working"
):

    """
    Register a customer's saved work.

    The actual file should already have been created
    or copied into the job directory by the application.

    This function records the saved file in SQLite.
    """

    if not source_file_path:
        return None

    if not os.path.exists(
        source_file_path
    ):

        print(
            "Customer work file does not exist:",
            source_file_path
        )

        return None

    if not file_name:

        file_name = os.path.basename(
            source_file_path
        )

    return save_document_record(
        job_id=job_id,
        file_name=file_name,
        file_path=source_file_path,
        file_type=file_type,
        document_status=document_status
    )


# ============================================================
# GET CUSTOMER WORK
# ============================================================

def get_customer_work(
    job_id
):

    """
    Return the most recently saved work for a job.
    """

    document = get_latest_document(
        job_id
    )

    if document is None:
        return None

    return document


# ============================================================
# PAYMENTS
# ============================================================

def create_payment(
    job_id,
    amount,
    payment_method=None,
    payment_status="pending"
):

    query = """
    INSERT INTO payments (
        job_id,
        amount,
        payment_method,
        payment_status
    )
    VALUES (?, ?, ?, ?)
    """

    return execute_query(
        query,
        (
            job_id,
            amount,
            payment_method,
            payment_status
        )
    )


def get_payment(
    payment_id
):

    query = """
    SELECT *
    FROM payments
    WHERE id = ?
    """

    return fetch_one(
        query,
        (payment_id,)
    )


def get_payments_for_job(
    job_id
):

    query = """
    SELECT *
    FROM payments
    WHERE job_id = ?
    ORDER BY payment_date DESC, id DESC
    """

    return fetch_all(
        query,
        (job_id,)
    )


def get_latest_payment(
    job_id
):

    query = """
    SELECT *
    FROM payments
    WHERE job_id = ?
    ORDER BY payment_date DESC, id DESC
    LIMIT 1
    """

    return fetch_one(
        query,
        (job_id,)
    )


def update_payment_status(
    payment_id,
    payment_status
):

    query = """
    UPDATE payments
    SET payment_status = ?
    WHERE id = ?
    """

    result = execute_query(
        query,
        (
            payment_status,
            payment_id
        )
    )

    if result is not None:

        payment = get_payment(
            payment_id
        )

        if payment:

            execute_query(
                """
                UPDATE jobs
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (payment["job_id"],)
            )

    return result is not None


# ============================================================
# JOB + DOCUMENT + PAYMENT SUMMARY
# ============================================================

def get_job_summary(
    job_id
):

    query = """
    SELECT
        jobs.*,

        documents.id AS document_id,
        documents.file_name,
        documents.file_path,
        documents.file_type,
        documents.document_status,
        documents.updated_at AS document_updated_at,

        payments.id AS payment_id,
        payments.amount AS payment_amount,
        payments.payment_method,
        payments.payment_status,
        payments.payment_date

    FROM jobs

    LEFT JOIN documents
        ON documents.id = (
            SELECT d.id
            FROM documents d
            WHERE d.job_id = jobs.id
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT 1
        )

    LEFT JOIN payments
        ON payments.id = (
            SELECT p.id
            FROM payments p
            WHERE p.job_id = jobs.id
            ORDER BY p.payment_date DESC, p.id DESC
            LIMIT 1
        )

    WHERE jobs.id = ?
    """

    return fetch_one(
        query,
        (job_id,)
    )


# ============================================================
# BACK OFFICE HELPERS
# ============================================================

def get_back_office_jobs():

    """
    Return jobs together with their latest document
    and latest payment.

    This is intentionally simple and is suitable for
    the future Back Office.
    """

    query = """
    SELECT
        jobs.*,

        documents.id AS document_id,
        documents.file_name,
        documents.file_path,
        documents.file_type,
        documents.document_status,
        documents.updated_at AS document_updated_at,

        payments.id AS payment_id,
        payments.amount AS payment_amount,
        payments.payment_method,
        payments.payment_status,
        payments.payment_date

    FROM jobs

    LEFT JOIN documents
        ON documents.id = (
            SELECT d.id
            FROM documents d
            WHERE d.job_id = jobs.id
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT 1
        )

    LEFT JOIN payments
        ON payments.id = (
            SELECT p.id
            FROM payments p
            WHERE p.job_id = jobs.id
            ORDER BY p.payment_date DESC, p.id DESC
            LIMIT 1
        )

    ORDER BY jobs.updated_at DESC, jobs.id DESC
    """

    return fetch_all(
        query
    )


# ============================================================
# DELETE JOB
# ============================================================

def delete_job(
    job_id
):

    """
    Delete the database records belonging to a job.

    The actual files on disk are NOT automatically deleted.
    This prevents accidental loss of customer work.

    File deletion should only happen through an explicit
    Back Office operation later.
    """

    query = """
    DELETE FROM jobs
    WHERE id = ?
    """

    result = execute_query(
        query,
        (job_id,)
    )

    return result is not None


# ============================================================
# DATABASE TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER")
    print("DATABASE TEST")
    print("=" * 70)
    print()

    if initialize_database():

        print(
            "Database initialized successfully."
        )

        print(
            "Database:",
            DATABASE_NAME
        )

        print(
            "Customer work directory:",
            BASE_WORK_DIRECTORY
        )

        print()

        jobs = get_all_jobs()

        print(
            "Existing jobs:",
            len(jobs)
        )

        documents = fetch_all(
            "SELECT * FROM documents"
        )

        print(
            "Existing documents:",
            len(documents)
        )

        payments = fetch_all(
            "SELECT * FROM payments"
        )

        print(
            "Existing payments:",
            len(payments)
        )

        print()

        print(
            "Database:",
            "READY"
        )

        print(
            "Job storage:",
            "READY"
        )

        print(
            "Customer work storage:",
            "READY"
        )

        print(
            "Payment storage:",
            "READY"
        )

        print(
            "Back Office data:",
            "READY"
        )

    else:

        print(
            "Database initialization failed."
        )

    print()
    print("=" * 70)
