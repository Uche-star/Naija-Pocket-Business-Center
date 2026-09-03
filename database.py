"""
database.py

Naija Pocket Business Center
Simple SQLite Database

PURPOSE
-------
This file stores the business records for the entire system.

This database is SERVICE-NEUTRAL and can be used for:

- CV writing
- Cover letters
- Projects
- Assignments
- Seminar papers
- Research
- Typing
- Editing
- Formatting
- Business documents
- Academic documents
- Printing-related jobs
- Other services

IMPORTANT
---------
This database does NOT store customer files.

The VPS stores only the SQLite database and application data.

Actual customer files/work should be handled by the application's
chosen file-storage system separately.

The database stores information ABOUT the work, such as:

- customer
- job
- service
- request/description
- work status
- work reference
- payment
- dates
- back-office information
"""

import sqlite3
from datetime import datetime
from pathlib import Path


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_PATH = BASE_DIR / "naija_pocket_business.db"

# Keep this name for compatibility with existing code.
DATABASE_NAME = str(DATABASE_PATH)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    """
    Open a connection to the application's SQLite database.

    The database path is absolute so that Render/API startup
    cannot accidentally create another database in a different
    working directory.
    """

    try:
        DATABASE_PATH.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        conn = sqlite3.connect(
            str(DATABASE_PATH),
            timeout=10
        )

        conn.row_factory = sqlite3.Row

        # Enforce foreign-key relationships.
        conn.execute(
            "PRAGMA foreign_keys = ON"
        )

        return conn

    except sqlite3.Error as error:

        print(
            "Database connection error:",
            error
        )

        print(
            "Database path:",
            DATABASE_PATH
        )

        return None

    except Exception as error:

        print(
            "Unexpected database connection error:",
            error
        )

        print(
            "Database path:",
            DATABASE_PATH
        )

        return None


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def initialize_database():

    conn = get_connection()

    if conn is None:
        print(
            "Database initialization failed: "
            "unable to open database connection."
        )
        return False

    try:

        cursor = conn.cursor()

        # ====================================================
        # CUSTOMERS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                customer_name TEXT,

                phone TEXT,

                email TEXT,

                customer_reference TEXT UNIQUE,

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ====================================================
        # JOBS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                customer_id INTEGER,

                customer_name TEXT NOT NULL,

                phone TEXT,

                service_type TEXT NOT NULL,

                description TEXT,

                customer_request TEXT,

                status TEXT DEFAULT 'pending',

                amount REAL DEFAULT 0,

                currency TEXT DEFAULT 'NGN',

                work_reference TEXT,

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(customer_id)
                    REFERENCES customers(id)
                    ON DELETE SET NULL
            )
            """
        )

        # ====================================================
        # WORK RECORDS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS work_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                work_title TEXT,

                work_type TEXT,

                storage_type TEXT,

                storage_reference TEXT,

                version INTEGER DEFAULT 1,

                work_status TEXT DEFAULT 'working',

                notes TEXT,

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                download_activated INTEGER DEFAULT 0,

                download_activated_at DATETIME,

                FOREIGN KEY(job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ====================================================
        # CUSTOMER FILE REFERENCES
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                file_name TEXT NOT NULL,

                file_type TEXT,

                storage_type TEXT,

                storage_reference TEXT,

                file_status TEXT DEFAULT 'received',

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ====================================================
        # PAYMENTS
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                amount REAL NOT NULL,

                currency TEXT DEFAULT 'NGN',

                payment_method TEXT,

                payment_status TEXT DEFAULT 'pending',

                payment_reference TEXT,

                payment_date DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ====================================================
        # JOB ACTIVITY
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS job_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                activity_type TEXT NOT NULL,

                description TEXT,

                created_at DATETIME
                    DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY(job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ====================================================
        # SAFE MIGRATION
        # ====================================================

        migrate_existing_database(
            cursor
        )

        # ====================================================
        # INDEXES
        # ====================================================

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_jobs_customer_id
            ON jobs(customer_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_jobs_status
            ON jobs(status)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_jobs_updated_at
            ON jobs(updated_at)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_work_records_job_id
            ON work_records(job_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_work_records_download
            ON work_records(job_id, download_activated)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_customer_files_job_id
            ON customer_files(job_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payments_job_id
            ON payments(job_id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_job_activity_job_id
            ON job_activity(job_id)
            """
        )

        # ====================================================
        # COMMIT
        # ====================================================

        conn.commit()

        print(
            "Database initialized successfully."
        )

        print(
            "Database path:",
            DATABASE_PATH
        )

        return True

    except sqlite3.Error as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Database setup error:",
            error
        )

        print(
            "Database path:",
            DATABASE_PATH
        )

        return False

    except Exception as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Unexpected database setup error:",
            error
        )

        print(
            "Database path:",
            DATABASE_PATH
        )

        return False

    finally:

        conn.close()


# ============================================================
# SAFE DATABASE MIGRATION
# ============================================================

def migrate_existing_database(cursor):
    """
    Keeps older versions of the database usable.

    Existing information is never deleted.
    Missing columns are added safely.
    """

    # --------------------------------------------------------
    # Existing jobs table
    # --------------------------------------------------------

    cursor.execute(
        "PRAGMA table_info(jobs)"
    )

    job_columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    job_columns_to_add = {

        "customer_id":
            "INTEGER",

        "customer_request":
            "TEXT",

        "currency":
            "TEXT DEFAULT 'NGN'",

        "work_reference":
            "TEXT",

        "updated_at":
            "DATETIME"
    }

    for column_name, definition in job_columns_to_add.items():

        if column_name not in job_columns:

            cursor.execute(
                f"""
                ALTER TABLE jobs
                ADD COLUMN {column_name}
                {definition}
                """
            )

            print(
                f"Database migration: "
                f"added jobs.{column_name}"
            )

    # --------------------------------------------------------
    # Existing work_records table
    # --------------------------------------------------------

    cursor.execute(
        "PRAGMA table_info(work_records)"
    )

    work_columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    work_columns_to_add = {

        "download_activated":
            "INTEGER DEFAULT 0",

        "download_activated_at":
            "DATETIME"
    }

    for column_name, definition in work_columns_to_add.items():

        if column_name not in work_columns:

            cursor.execute(
                f"""
                ALTER TABLE work_records
                ADD COLUMN {column_name}
                {definition}
                """
            )

            print(
                f"Database migration: "
                f"added work_records.{column_name}"
            )

    # --------------------------------------------------------
    # Existing payments table
    # --------------------------------------------------------

    cursor.execute(
        "PRAGMA table_info(payments)"
    )

    payment_columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    payment_columns_to_add = {

        "currency":
            "TEXT DEFAULT 'NGN'",

        "payment_reference":
            "TEXT",

        "updated_at":
            "DATETIME"
    }

    for column_name, definition in payment_columns_to_add.items():

        if column_name not in payment_columns:

            cursor.execute(
                f"""
                ALTER TABLE payments
                ADD COLUMN {column_name}
                {definition}
                """
            )

            print(
                f"Database migration: "
                f"added payments.{column_name}"
            )

    # --------------------------------------------------------
    # Existing jobs updated_at values
    # --------------------------------------------------------

    cursor.execute(
        """
        UPDATE jobs
        SET updated_at = COALESCE(
            updated_at,
            created_at,
            CURRENT_TIMESTAMP
        )
        WHERE updated_at IS NULL
        """
    )

    # --------------------------------------------------------
    # Existing payments updated_at values
    # --------------------------------------------------------

    cursor.execute(
        """
        UPDATE payments
        SET updated_at = COALESCE(
            updated_at,
            payment_date,
            CURRENT_TIMESTAMP
        )
        WHERE updated_at IS NULL
        """
    )

    # --------------------------------------------------------
    # Existing work download activation values
    # --------------------------------------------------------

    cursor.execute(
        """
        UPDATE work_records
        SET download_activated = 0
        WHERE download_activated IS NULL
        """
    )


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

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Query error:",
            error
        )

        print(
            "Query:",
            query
        )

        print(
            "Parameters:",
            params
        )

        return None

    except Exception as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Unexpected query error:",
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

        print(
            "Query:",
            query
        )

        return []

    except Exception as error:

        print(
            "Unexpected fetch error:",
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

        print(
            "Query:",
            query
        )

        return None

    except Exception as error:

        print(
            "Unexpected fetch error:",
            error
        )

        return None

    finally:

        conn.close()


# ============================================================
# CUSTOMER FUNCTIONS
# ============================================================

def create_customer(
    customer_name,
    phone=None,
    email=None,
    customer_reference=None
):

    query = """
    INSERT INTO customers (
        customer_name,
        phone,
        email,
        customer_reference
    )
    VALUES (?, ?, ?, ?)
    """

    return execute_query(
        query,
        (
            customer_name,
            phone,
            email,
            customer_reference
        )
    )


def get_customer(
    customer_id
):

    return fetch_one(
        """
        SELECT *
        FROM customers
        WHERE id = ?
        """,
        (customer_id,)
    )


def get_customer_by_reference(
    customer_reference
):

    return fetch_one(
        """
        SELECT *
        FROM customers
        WHERE customer_reference = ?
        """,
        (customer_reference,)
    )


def get_all_customers():

    return fetch_all(
        """
        SELECT *
        FROM customers
        ORDER BY updated_at DESC, id DESC
        """
    )


def update_customer(
    customer_id,
    customer_name=None,
    phone=None,
    email=None
):

    current = get_customer(
        customer_id
    )

    if current is None:
        return False

    new_name = (
        customer_name
        if customer_name is not None
        else current["customer_name"]
    )

    new_phone = (
        phone
        if phone is not None
        else current["phone"]
    )

    new_email = (
        email
        if email is not None
        else current["email"]
    )

    conn = get_connection()

    if conn is None:
        return False

    try:

        conn.execute(
            """
            UPDATE customers
            SET
                customer_name = ?,
                phone = ?,
                email = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_name,
                new_phone,
                new_email,
                customer_id
            )
        )

        conn.commit()

        return True

    except sqlite3.Error as error:

        print(
            "Customer update error:",
            error
        )

        return False

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
    status="pending",
    customer_id=None,
    customer_request=None,
    currency="NGN",
    work_reference=None
):

    query = """
    INSERT INTO jobs (
        customer_id,
        customer_name,
        phone,
        service_type,
        description,
        customer_request,
        status,
        amount,
        currency,
        work_reference,
        updated_at
    )
    VALUES (
        ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, CURRENT_TIMESTAMP
    )
    """

    job_id = execute_query(
        query,
        (
            customer_id,
            customer_name,
            phone,
            service_type,
            description,
            customer_request,
            status,
            amount,
            currency,
            work_reference
        )
    )

    if job_id is not None:

        add_job_activity(
            job_id,
            "job_created",
            "Customer job created."
        )

    return job_id


def get_job(
    job_id
):

    return fetch_one(
        """
        SELECT *
        FROM jobs
        WHERE id = ?
        """,
        (job_id,)
    )


def get_all_jobs():

    return fetch_all(
        """
        SELECT *
        FROM jobs
        ORDER BY updated_at DESC, id DESC
        """
    )


def get_jobs_by_status(
    status
):

    return fetch_all(
        """
        SELECT *
        FROM jobs
        WHERE status = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (status,)
    )


def get_jobs_by_customer(
    customer_id
):

    return fetch_all(
        """
        SELECT *
        FROM jobs
        WHERE customer_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (customer_id,)
    )


def update_job_status(
    job_id,
    status
):

    result = execute_query(
        """
        UPDATE jobs
        SET
            status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            status,
            job_id
        )
    )

    if result is not None:

        add_job_activity(
            job_id,
            "status_changed",
            f"Job status changed to {status}."
        )

    return result is not None


def update_job(
    job_id,
    customer_name=None,
    phone=None,
    service_type=None,
    description=None,
    amount=None,
    status=None,
    customer_request=None,
    currency=None,
    work_reference=None
):

    current = get_job(
        job_id
    )

    if current is None:
        return False

    values = {

        "customer_name":
            customer_name
            if customer_name is not None
            else current["customer_name"],

        "phone":
            phone
            if phone is not None
            else current["phone"],

        "service_type":
            service_type
            if service_type is not None
            else current["service_type"],

        "description":
            description
            if description is not None
            else current["description"],

        "amount":
            amount
            if amount is not None
            else current["amount"],

        "status":
            status
            if status is not None
            else current["status"],

        "customer_request":
            customer_request
            if customer_request is not None
            else current["customer_request"],

        "currency":
            currency
            if currency is not None
            else current["currency"],

        "work_reference":
            work_reference
            if work_reference is not None
            else current["work_reference"]
    }

    result = execute_query(
        """
        UPDATE jobs
        SET
            customer_name = ?,
            phone = ?,
            service_type = ?,
            description = ?,
            amount = ?,
            status = ?,
            customer_request = ?,
            currency = ?,
            work_reference = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            values["customer_name"],
            values["phone"],
            values["service_type"],
            values["description"],
            values["amount"],
            values["status"],
            values["customer_request"],
            values["currency"],
            values["work_reference"],
            job_id
        )
    )

    return result is not None


# ============================================================
# WORK RECORD FUNCTIONS
# ============================================================

def save_customer_work(
    job_id,
    work_title=None,
    work_type=None,
    storage_type=None,
    storage_reference=None,
    work_status="working",
    notes=None
):
    """
    Save a RECORD of customer work.

    This function does NOT upload or store the actual file.

    It only stores the reference to wherever the actual work
    is kept.
    """

    job = get_job(
        job_id
    )

    if job is None:
        return None

    latest = get_latest_work(
        job_id
    )

    version = 1

    if latest is not None:

        version = (
            int(latest["version"] or 0)
            + 1
        )

    work_id = execute_query(
        """
        INSERT INTO work_records (
            job_id,
            work_title,
            work_type,
            storage_type,
            storage_reference,
            version,
            work_status,
            notes,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, CURRENT_TIMESTAMP
        )
        """,
        (
            job_id,
            work_title,
            work_type,
            storage_type,
            storage_reference,
            version,
            work_status,
            notes
        )
    )

    if work_id is not None:

        execute_query(
            """
            UPDATE jobs
            SET
                work_reference = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                storage_reference,
                job_id
            )
        )

        add_job_activity(
            job_id,
            "work_saved",
            f"Customer work saved. Version {version}."
        )

    return work_id


def get_work(
    work_id
):
    """
    Get one exact work record by its ID.

    This function is required by the API.
    """

    return fetch_one(
        """
        SELECT *
        FROM work_records
        WHERE id = ?
        """,
        (work_id,)
    )


def get_work_for_job(
    job_id
):

    return fetch_all(
        """
        SELECT *
        FROM work_records
        WHERE job_id = ?
        ORDER BY version DESC, id DESC
        """,
        (job_id,)
    )


def get_latest_work(
    job_id
):

    return fetch_one(
        """
        SELECT *
        FROM work_records
        WHERE job_id = ?
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (job_id,)
    )


# ============================================================
# DOWNLOAD ACTIVATION
# ============================================================

def activate_work_download(
    work_id
):
    """
    Activates customer download for ONE exact saved work record.

    Only this saved version becomes download-authorized.

    The actual file is not copied or regenerated.
    """

    work = get_work(
        work_id
    )

    if work is None:
        return False

    conn = get_connection()

    if conn is None:
        return False

    try:

        # ----------------------------------------------------
        # Deactivate any previously activated version
        # for this job.
        # ----------------------------------------------------

        conn.execute(
            """
            UPDATE work_records
            SET
                download_activated = 0,
                download_activated_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (
                work["job_id"],
            )
        )

        # ----------------------------------------------------
        # Activate the exact selected work record.
        # ----------------------------------------------------

        conn.execute(
            """
            UPDATE work_records
            SET
                download_activated = 1,
                download_activated_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                work_id,
            )
        )

        conn.commit()

    except sqlite3.Error as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Download activation error:",
            error
        )

        return False

    finally:

        conn.close()

    add_job_activity(
        work["job_id"],
        "download_activated",
        (
            f"Customer download activated for "
            f"saved work Version {work['version']}."
        )
    )

    return True


def get_activated_work(
    job_id
):
    """
    Returns the exact saved work version that Back Office has
    activated for customer download.

    It does NOT automatically select the latest version.
    """

    return fetch_one(
        """
        SELECT *
        FROM work_records
        WHERE
            job_id = ?
            AND download_activated = 1
        ORDER BY
            version DESC,
            id DESC
        LIMIT 1
        """,
        (job_id,)
    )


def deactivate_work_download(
    job_id
):
    """
    Removes customer-download activation for a job.

    It does not delete the saved document.
    """

    conn = get_connection()

    if conn is None:
        return False

    try:

        cursor = conn.execute(
            """
            UPDATE work_records
            SET
                download_activated = 0,
                download_activated_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (job_id,)
        )

        conn.commit()

        changed = cursor.rowcount > 0

    except sqlite3.Error as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print(
            "Download deactivation error:",
            error
        )

        return False

    finally:

        conn.close()

    if changed:

        add_job_activity(
            job_id,
            "download_deactivated",
            "Customer download activation removed."
        )

    return changed


def update_work_status(
    work_id,
    work_status
):

    work = get_work(
        work_id
    )

    if work is None:
        return False

    result = execute_query(
        """
        UPDATE work_records
        SET
            work_status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            work_status,
            work_id
        )
    )

    if result is not None:

        add_job_activity(
            work["job_id"],
            "work_status_changed",
            f"Work status changed to {work_status}."
        )

    return result is not None


# ============================================================
# CUSTOMER FILE REFERENCE FUNCTIONS
# ============================================================

def save_customer_file(
    job_id,
    file_name,
    file_type=None,
    storage_type=None,
    storage_reference=None,
    file_status="received"
):

    job = get_job(
        job_id
    )

    if job is None:
        return None

    file_id = execute_query(
        """
        INSERT INTO customer_files (
            job_id,
            file_name,
            file_type,
            storage_type,
            storage_reference,
            file_status,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            CURRENT_TIMESTAMP
        )
        """,
        (
            job_id,
            file_name,
            file_type,
            storage_type,
            storage_reference,
            file_status
        )
    )

    if file_id is not None:

        execute_query(
            """
            UPDATE jobs
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,)
        )

        add_job_activity(
            job_id,
            "customer_file_received",
            f"Customer file received: {file_name}"
        )

    return file_id


def get_customer_file(
    file_id
):

    return fetch_one(
        """
        SELECT *
        FROM customer_files
        WHERE id = ?
        """,
        (file_id,)
    )


def get_customer_files(
    job_id
):

    return fetch_all(
        """
        SELECT *
        FROM customer_files
        WHERE job_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (job_id,)
    )


def update_customer_file_status(
    file_id,
    file_status
):

    result = execute_query(
        """
        UPDATE customer_files
        SET
            file_status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            file_status,
            file_id
        )
    )

    return result is not None


# ============================================================
# PAYMENT FUNCTIONS
# ============================================================

def create_payment(
    job_id,
    amount,
    payment_method=None,
    payment_status="pending",
    currency="NGN",
    payment_reference=None
):

    job = get_job(
        job_id
    )

    if job is None:
        return None

    payment_id = execute_query(
        """
        INSERT INTO payments (
            job_id,
            amount,
            currency,
            payment_method,
            payment_status,
            payment_reference,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            CURRENT_TIMESTAMP
        )
        """,
        (
            job_id,
            amount,
            currency,
            payment_method,
            payment_status,
            payment_reference
        )
    )

    if payment_id is not None:

        add_job_activity(
            job_id,
            "payment_created",
            (
                f"Payment record created for "
                f"{amount} {currency}."
            )
        )

    return payment_id


def get_payment(
    payment_id
):

    return fetch_one(
        """
        SELECT *
        FROM payments
        WHERE id = ?
        """,
        (payment_id,)
    )


def get_payments_for_job(
    job_id
):

    return fetch_all(
        """
        SELECT *
        FROM payments
        WHERE job_id = ?
        ORDER BY payment_date DESC, id DESC
        """,
        (job_id,)
    )


def get_latest_payment(
    job_id
):

    return fetch_one(
        """
        SELECT *
        FROM payments
        WHERE job_id = ?
        ORDER BY payment_date DESC, id DESC
        LIMIT 1
        """,
        (job_id,)
    )


def update_payment_status(
    payment_id,
    payment_status
):

    payment = get_payment(
        payment_id
    )

    if payment is None:
        return False

    result = execute_query(
        """
        UPDATE payments
        SET
            payment_status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            payment_status,
            payment_id
        )
    )

    if result is not None:

        add_job_activity(
            payment["job_id"],
            "payment_status_changed",
            (
                f"Payment status changed to "
                f"{payment_status}."
            )
        )

    return result is not None


# ============================================================
# JOB ACTIVITY
# ============================================================

def add_job_activity(
    job_id,
    activity_type,
    description=None
):

    job = get_job(
        job_id
    )

    if job is None:
        return None

    return execute_query(
        """
        INSERT INTO job_activity (
            job_id,
            activity_type,
            description
        )
        VALUES (?, ?, ?)
        """,
        (
            job_id,
            activity_type,
            description
        )
    )


def get_job_activity(
    job_id
):

    return fetch_all(
        """
        SELECT *
        FROM job_activity
        WHERE job_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (job_id,)
    )


# ============================================================
# BACK OFFICE
# ============================================================

def get_back_office_jobs():
    """
    Returns each job with:

    - customer information
    - latest work
    - latest customer file
    - latest payment
    """

    query = """
    SELECT

        jobs.*,

        customers.email AS customer_email,

        customers.customer_reference,

        work_records.id AS work_id,

        work_records.work_title,

        work_records.work_type,

        work_records.storage_type,

        work_records.storage_reference,

        work_records.version AS work_version,

        work_records.work_status,

        work_records.download_activated,

        work_records.download_activated_at,

        customer_files.id AS customer_file_id,

        customer_files.file_name,

        customer_files.file_type,

        customer_files.storage_type
            AS file_storage_type,

        customer_files.storage_reference
            AS file_storage_reference,

        customer_files.file_status,

        payments.id AS payment_id,

        payments.amount
            AS payment_amount,

        payments.currency
            AS payment_currency,

        payments.payment_method,

        payments.payment_status,

        payments.payment_reference,

        payments.payment_date

    FROM jobs

    LEFT JOIN customers
        ON customers.id = jobs.customer_id

    LEFT JOIN work_records
        ON work_records.id = (

            SELECT w.id

            FROM work_records w

            WHERE w.job_id = jobs.id

            ORDER BY
                w.version DESC,
                w.id DESC

            LIMIT 1
        )

    LEFT JOIN customer_files
        ON customer_files.id = (

            SELECT f.id

            FROM customer_files f

            WHERE f.job_id = jobs.id

            ORDER BY
                f.created_at DESC,
                f.id DESC

            LIMIT 1
        )

    LEFT JOIN payments
        ON payments.id = (

            SELECT p.id

            FROM payments p

            WHERE p.job_id = jobs.id

            ORDER BY
                p.payment_date DESC,
                p.id DESC

            LIMIT 1
        )

    ORDER BY
        jobs.updated_at DESC,
        jobs.id DESC
    """

    return fetch_all(
        query
    )


# ============================================================
# JOB SUMMARY
# ============================================================

def get_job_summary(
    job_id
):

    job = get_job(
        job_id
    )

    if job is None:
        return None

    return {

        "job":
            job,

        "customer":
            get_customer(
                job["customer_id"]
            )
            if job["customer_id"]
            else None,

        "work":
            get_work_for_job(
                job_id
            ),

        "activated_work":
            get_activated_work(
                job_id
            ),

        "customer_files":
            get_customer_files(
                job_id
            ),

        "payments":
            get_payments_for_job(
                job_id
            ),

        "activity":
            get_job_activity(
                job_id
            )
    }


# ============================================================
# DELETE JOB
# ============================================================

def delete_job(
    job_id
):
    """
    Deletes the database records associated with a job.

    IMPORTANT:
    This does NOT delete any actual external customer files.

    Actual file deletion should be an explicit Back Office
    operation after a proper storage system is in place.
    """

    job = get_job(
        job_id
    )

    if job is None:
        return False

    result = execute_query(
        """
        DELETE FROM jobs
        WHERE id = ?
        """,
        (job_id,)
    )

    return result is not None


# ============================================================
# DATABASE STATISTICS
# ============================================================

def get_database_statistics():

    jobs = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM jobs
        """
    )

    customers = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM customers
        """
    )

    work = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM work_records
        """
    )

    files = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM customer_files
        """
    )

    payments = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM payments
        """
    )

    return {

        "customers":
            int(customers["total"])
            if customers
            else 0,

        "jobs":
            int(jobs["total"])
            if jobs
            else 0,

        "work_records":
            int(work["total"])
            if work
            else 0,

        "customer_files":
            int(files["total"])
            if files
            else 0,

        "payments":
            int(payments["total"])
            if payments
            else 0
    }


# ============================================================
# AUTOMATIC DATABASE INITIALIZATION
# ============================================================
#
# IMPORTANT:
#
# This MUST be after migrate_existing_database() and all
# function definitions required during initialization.
#
# It means importing database.py from the API automatically
# prepares the SQLite database.
# ============================================================

DATABASE_READY = initialize_database()


# ============================================================
# DATABASE TEST
# ============================================================

if __name__ == "__main__":

    print()

    print("=" * 70)

    print(
        "NAIJA POCKET BUSINESS CENTER"
    )

    print(
        "DATABASE TEST"
    )

    print("=" * 70)

    print()

    print(
        "Database:",
        DATABASE_NAME
    )

    print()

    print(
        "Database ready:",
        DATABASE_READY
    )

    print()

    if DATABASE_READY:

        stats = get_database_statistics()

        print(
            "Customers:",
            stats["customers"]
        )

        print(
            "Jobs:",
            stats["jobs"]
        )

        print(
            "Work records:",
            stats["work_records"]
        )

        print(
            "Customer file records:",
            stats["customer_files"]
        )

        print(
            "Payments:",
            stats["payments"]
        )

        print()

        print(
            "Database:",
            "READY"
        )

        print(
            "Customer records:",
            "READY"
        )

        print(
            "Job records:",
            "READY"
        )

        print(
            "Work records:",
            "READY"
        )

        print(
            "Payment records:",
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
