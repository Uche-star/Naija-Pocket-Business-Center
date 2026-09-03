"""
database.py

Naija Pocket Business Center
SQLite Database Layer

PURPOSE
-------
This file stores the business records for the entire system.

The database is SERVICE-NEUTRAL and can be used for:

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
- Other document-center services

IMPORTANT
---------
The database is automatically initialized when this module is imported.

This is important because the API imports database functions instead of
running database.py directly.

The database file is stored beside this file using an absolute path so
the application does not accidentally create different database files
depending on the current working directory.
"""

import sqlite3
from datetime import datetime
from pathlib import Path


# ============================================================
# DATABASE LOCATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_PATH = BASE_DIR / "naija_pocket_business.db"

# Kept for compatibility with code that may reference DATABASE_NAME.
DATABASE_NAME = str(DATABASE_PATH)


# ============================================================
# TIME
# ============================================================

def now():
    """
    Return the current local timestamp as a string.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    """
    Open a SQLite connection.

    The database uses an absolute path and foreign-key enforcement.
    """

    try:
        # Make sure the parent directory exists.
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(
            str(DATABASE_PATH),
            timeout=10
        )

        conn.row_factory = sqlite3.Row

        # Enforce foreign-key relationships.
        conn.execute("PRAGMA foreign_keys = ON")

        return conn

    except sqlite3.Error as error:
        print("Database connection error:", error)
        print("Database path:", DATABASE_PATH)
        return None

    except Exception as error:
        print("Unexpected database connection error:", error)
        print("Database path:", DATABASE_PATH)
        return None


# ============================================================
# TABLE / COLUMN HELPERS
# ============================================================

def table_exists(conn, table_name):
    """
    Check whether a table exists.
    """

    try:
        row = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            AND name = ?
            """,
            (table_name,)
        ).fetchone()

        return row is not None

    except sqlite3.Error as error:
        print(f"Table check error for {table_name}:", error)
        return False


def column_exists(conn, table_name, column_name):
    """
    Check whether a column exists in a table.
    """

    try:
        rows = conn.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()

        return any(row["name"] == column_name for row in rows)

    except sqlite3.Error as error:
        print(
            f"Column check error for {table_name}.{column_name}:",
            error
        )
        return False


def add_column_if_missing(
    conn,
    table_name,
    column_name,
    column_definition
):
    """
    Add a column only when it does not already exist.
    """

    if not table_exists(conn, table_name):
        return

    if column_exists(conn, table_name, column_name):
        return

    try:
        conn.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_definition}
            """
        )

        print(
            f"Database migration: added "
            f"{table_name}.{column_name}"
        )

    except sqlite3.Error as error:
        print(
            f"Migration error adding "
            f"{table_name}.{column_name}:",
            error
        )
        raise


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_database():
    """
    Create the complete database structure.

    Safe to run repeatedly.

    Existing databases are preserved and missing columns are migrated.
    """

    conn = get_connection()

    if conn is None:
        print("Database initialization failed: no connection.")
        return False

    try:

        # ====================================================
        # CUSTOMERS
        # ====================================================

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                customer_name TEXT NOT NULL,

                phone TEXT,

                email TEXT,

                customer_reference TEXT UNIQUE,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL
            )
            """
        )


        # ====================================================
        # JOBS
        # ====================================================

        conn.execute(
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

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                FOREIGN KEY (customer_id)
                    REFERENCES customers(id)
                    ON DELETE SET NULL
            )
            """
        )


        # ====================================================
        # WORK RECORDS
        # ====================================================

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS work_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                work_title TEXT,

                work_type TEXT,

                storage_type TEXT,

                storage_reference TEXT,

                version INTEGER DEFAULT 1,

                work_status TEXT DEFAULT 'draft',

                notes TEXT,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                download_activated INTEGER DEFAULT 0,

                download_activated_at TEXT,

                FOREIGN KEY (job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )


        # ====================================================
        # CUSTOMER FILES
        # ====================================================

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                file_name TEXT,

                file_type TEXT,

                storage_type TEXT,

                storage_reference TEXT,

                file_status TEXT DEFAULT 'uploaded',

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                FOREIGN KEY (job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )


        # ====================================================
        # PAYMENTS
        # ====================================================

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                amount REAL NOT NULL DEFAULT 0,

                currency TEXT DEFAULT 'NGN',

                payment_method TEXT,

                payment_status TEXT DEFAULT 'pending',

                payment_reference TEXT,

                payment_date TEXT,

                updated_at TEXT NOT NULL,

                FOREIGN KEY (job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )


        # ====================================================
        # JOB ACTIVITY
        # ====================================================

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id INTEGER NOT NULL,

                activity_type TEXT,

                description TEXT,

                created_at TEXT NOT NULL,

                FOREIGN KEY (job_id)
                    REFERENCES jobs(id)
                    ON DELETE CASCADE
            )
            """
        )


        # ====================================================
        # MIGRATIONS
        # ====================================================

        # Existing jobs table migrations.

        add_column_if_missing(
            conn,
            "jobs",
            "customer_id",
            "INTEGER"
        )

        add_column_if_missing(
            conn,
            "jobs",
            "customer_request",
            "TEXT"
        )

        add_column_if_missing(
            conn,
            "jobs",
            "currency",
            "TEXT DEFAULT 'NGN'"
        )

        add_column_if_missing(
            conn,
            "jobs",
            "work_reference",
            "TEXT"
        )

        add_column_if_missing(
            conn,
            "jobs",
            "updated_at",
            "TEXT"
        )


        # Existing work_records migrations.

        add_column_if_missing(
            conn,
            "work_records",
            "download_activated",
            "INTEGER DEFAULT 0"
        )

        add_column_if_missing(
            conn,
            "work_records",
            "download_activated_at",
            "TEXT"
        )


        # Existing payments migrations.

        add_column_if_missing(
            conn,
            "payments",
            "currency",
            "TEXT DEFAULT 'NGN'"
        )

        add_column_if_missing(
            conn,
            "payments",
            "payment_reference",
            "TEXT"
        )

        add_column_if_missing(
            conn,
            "payments",
            "updated_at",
            "TEXT"
        )


        # ====================================================
        # INDEXES
        # ====================================================

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_customer_id
            ON jobs(customer_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_status
            ON jobs(status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_updated_at
            ON jobs(updated_at)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_work_records_job_id
            ON work_records(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_work_records_download
            ON work_records(download_activated)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_files_job_id
            ON customer_files(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_job_id
            ON payments(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_job_activity_job_id
            ON job_activity(job_id)
            """
        )


        # ====================================================
        # FILL NULL TIMESTAMPS IN OLD DATABASES
        # ====================================================

        current_time = now()

        if table_exists(conn, "jobs"):

            conn.execute(
                """
                UPDATE jobs
                SET updated_at = COALESCE(updated_at, created_at, ?)
                WHERE updated_at IS NULL
                """,
                (current_time,)
            )

        if table_exists(conn, "payments"):

            conn.execute(
                """
                UPDATE payments
                SET updated_at = COALESCE(updated_at, payment_date, ?)
                WHERE updated_at IS NULL
                """,
                (current_time,)
            )


        # ====================================================
        # COMMIT EVERYTHING
        # ====================================================

        conn.commit()

        print("Database initialized successfully.")
        print("Database path:", DATABASE_PATH)

        return True

    except sqlite3.Error as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print("Database initialization error:", error)
        print("Database path:", DATABASE_PATH)

        return False

    except Exception as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print("Unexpected database initialization error:", error)
        print("Database path:", DATABASE_PATH)

        return False

    finally:

        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# GENERIC QUERY EXECUTION
# ============================================================

def execute_query(
    query,
    parameters=(),
    fetch=False,
    fetchone=False,
    commit=True
):
    """
    Execute a database query safely.

    Returns:
        - fetched rows when fetch=True
        - one row when fetchone=True
        - lastrowid for write operations
        - None when an error occurs
    """

    conn = get_connection()

    if conn is None:
        return None

    try:

        cursor = conn.execute(
            query,
            parameters
        )

        if fetchone:
            result = cursor.fetchone()

            if commit:
                conn.commit()

            return result

        if fetch:
            result = cursor.fetchall()

            if commit:
                conn.commit()

            return result

        result = cursor.lastrowid

        if commit:
            conn.commit()

        return result

    except sqlite3.Error as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print("Database query error:", error)
        print("Query:", query)
        print("Parameters:", parameters)

        return None

    except Exception as error:

        try:
            conn.rollback()
        except Exception:
            pass

        print("Unexpected query error:", error)
        print("Query:", query)

        return None

    finally:

        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# CUSTOMER FUNCTIONS
# ============================================================

def create_customer(
    customer_name,
    phone=None,
    email=None,
    customer_reference=None
):
    """
    Create a customer and return the new customer ID.
    """

    timestamp = now()

    return execute_query(
        """
        INSERT INTO customers (
            customer_name,
            phone,
            email,
            customer_reference,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            customer_name,
            phone,
            email,
            customer_reference,
            timestamp,
            timestamp
        )
    )


def get_customer(customer_id):
    """
    Get a customer by ID.
    """

    return execute_query(
        """
        SELECT *
        FROM customers
        WHERE id = ?
        """,
        (customer_id,),
        fetchone=True
    )


def get_customer_by_reference(customer_reference):
    """
    Get a customer using customer reference.
    """

    return execute_query(
        """
        SELECT *
        FROM customers
        WHERE customer_reference = ?
        """,
        (customer_reference,),
        fetchone=True
    )


def update_customer(customer_id, **fields):
    """
    Update customer fields.
    """

    allowed_fields = {
        "customer_name",
        "phone",
        "email",
        "customer_reference"
    }

    updates = []
    values = []

    for field, value in fields.items():

        if field not in allowed_fields:
            continue

        updates.append(f"{field} = ?")
        values.append(value)

    if not updates:
        return False

    updates.append("updated_at = ?")
    values.append(now())

    values.append(customer_id)

    result = execute_query(
        f"""
        UPDATE customers
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        tuple(values)
    )

    return result is not None


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
    """
    Create a business job.

    Returns:
        New job ID
        None if creation fails.
    """

    timestamp = now()

    result = execute_query(
        """
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
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
            work_reference,
            timestamp,
            timestamp
        )
    )

    if result is None:
        print(
            "Failed to create job.",
            "customer_id=", customer_id,
            "customer_name=", customer_name,
            "service_type=", service_type
        )

    return result


def get_job(job_id):
    """
    Get a job by ID.
    """

    return execute_query(
        """
        SELECT *
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
        fetchone=True
    )


def get_jobs(limit=100):
    """
    Get recent jobs.
    """

    return execute_query(
        """
        SELECT *
        FROM jobs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
        fetch=True
    )


def get_customer_jobs(customer_id):
    """
    Get all jobs belonging to a customer.
    """

    return execute_query(
        """
        SELECT *
        FROM jobs
        WHERE customer_id = ?
        ORDER BY id DESC
        """,
        (customer_id,),
        fetch=True
    )


def update_job_status(job_id, status):
    """
    Update the status of a job.
    """

    result = execute_query(
        """
        UPDATE jobs
        SET status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            now(),
            job_id
        )
    )

    return result is not None


def update_job(job_id, **fields):
    """
    Update allowed job fields.
    """

    allowed_fields = {
        "customer_id",
        "customer_name",
        "phone",
        "service_type",
        "description",
        "customer_request",
        "status",
        "amount",
        "currency",
        "work_reference"
    }

    updates = []
    values = []

    for field, value in fields.items():

        if field not in allowed_fields:
            continue

        updates.append(f"{field} = ?")
        values.append(value)

    if not updates:
        return False

    updates.append("updated_at = ?")
    values.append(now())

    values.append(job_id)

    result = execute_query(
        f"""
        UPDATE jobs
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        tuple(values)
    )

    return result is not None


def delete_job(job_id):
    """
    Delete a job and related records through cascade rules.
    """

    result = execute_query(
        """
        DELETE FROM jobs
        WHERE id = ?
        """,
        (job_id,)
    )

    return result is not None


# ============================================================
# WORK RECORD FUNCTIONS
# ============================================================

def create_work_record(
    job_id,
    work_title=None,
    work_type=None,
    storage_type=None,
    storage_reference=None,
    version=1,
    work_status="draft",
    notes=None
):
    """
    Create a work record.
    """

    timestamp = now()

    return execute_query(
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
            created_at,
            updated_at,
            download_activated,
            download_activated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
        """,
        (
            job_id,
            work_title,
            work_type,
            storage_type,
            storage_reference,
            version,
            work_status,
            notes,
            timestamp,
            timestamp
        )
    )


def get_work_record(work_id):
    """
    Get a work record by ID.
    """

    return execute_query(
        """
        SELECT *
        FROM work_records
        WHERE id = ?
        """,
        (work_id,),
        fetchone=True
    )


def get_job_work_records(job_id):
    """
    Get all work records for a job.
    """

    return execute_query(
        """
        SELECT *
        FROM work_records
        WHERE job_id = ?
        ORDER BY version ASC, id ASC
        """,
        (job_id,),
        fetch=True
    )


def get_latest_work_record(job_id):
    """
    Get the latest work record for a job.
    """

    return execute_query(
        """
        SELECT *
        FROM work_records
        WHERE job_id = ?
        ORDER BY version DESC, id DESC
        LIMIT 1
        """,
        (job_id,),
        fetchone=True
    )


def update_work_status(work_id, work_status):
    """
    Update work status.
    """

    result = execute_query(
        """
        UPDATE work_records
        SET work_status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            work_status,
            now(),
            work_id
        )
    )

    return result is not None


def update_work_record(work_id, **fields):
    """
    Update allowed work record fields.
    """

    allowed_fields = {
        "work_title",
        "work_type",
        "storage_type",
        "storage_reference",
        "version",
        "work_status",
        "notes"
    }

    updates = []
    values = []

    for field, value in fields.items():

        if field not in allowed_fields:
            continue

        updates.append(f"{field} = ?")
        values.append(value)

    if not updates:
        return False

    updates.append("updated_at = ?")
    values.append(now())

    values.append(work_id)

    result = execute_query(
        f"""
        UPDATE work_records
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        tuple(values)
    )

    return result is not None


def activate_download(work_id):
    """
    Activate customer download for a work record.
    """

    timestamp = now()

    result = execute_query(
        """
        UPDATE work_records
        SET download_activated = 1,
            download_activated_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            timestamp,
            timestamp,
            work_id
        )
    )

    return result is not None


# ============================================================
# CUSTOMER FILE FUNCTIONS
# ============================================================

def add_customer_file(
    job_id,
    file_name,
    file_type=None,
    storage_type=None,
    storage_reference=None,
    file_status="uploaded"
):
    """
    Add a customer file.
    """

    timestamp = now()

    return execute_query(
        """
        INSERT INTO customer_files (
            job_id,
            file_name,
            file_type,
            storage_type,
            storage_reference,
            file_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            file_name,
            file_type,
            storage_type,
            storage_reference,
            file_status,
            timestamp,
            timestamp
        )
    )


def get_customer_file(file_id):
    """
    Get a customer file.
    """

    return execute_query(
        """
        SELECT *
        FROM customer_files
        WHERE id = ?
        """,
        (file_id,),
        fetchone=True
    )


def get_job_files(job_id):
    """
    Get all files belonging to a job.
    """

    return execute_query(
        """
        SELECT *
        FROM customer_files
        WHERE job_id = ?
        ORDER BY id ASC
        """,
        (job_id,),
        fetch=True
    )


def update_customer_file(file_id, **fields):
    """
    Update allowed customer-file fields.
    """

    allowed_fields = {
        "file_name",
        "file_type",
        "storage_type",
        "storage_reference",
        "file_status"
    }

    updates = []
    values = []

    for field, value in fields.items():

        if field not in allowed_fields:
            continue

        updates.append(f"{field} = ?")
        values.append(value)

    if not updates:
        return False

    updates.append("updated_at = ?")
    values.append(now())

    values.append(file_id)

    result = execute_query(
        f"""
        UPDATE customer_files
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        tuple(values)
    )

    return result is not None


# ============================================================
# PAYMENT FUNCTIONS
# ============================================================

def create_payment(
    job_id,
    amount,
    currency="NGN",
    payment_method=None,
    payment_status="pending",
    payment_reference=None,
    payment_date=None
):
    """
    Create a payment record.
    """

    timestamp = now()

    if payment_date is None:
        payment_date = timestamp

    return execute_query(
        """
        INSERT INTO payments (
            job_id,
            amount,
            currency,
            payment_method,
            payment_status,
            payment_reference,
            payment_date,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            amount,
            currency,
            payment_method,
            payment_status,
            payment_reference,
            payment_date,
            timestamp
        )
    )


def get_payment(payment_id):
    """
    Get a payment by ID.
    """

    return execute_query(
        """
        SELECT *
        FROM payments
        WHERE id = ?
        """,
        (payment_id,),
        fetchone=True
    )


def get_job_payments(job_id):
    """
    Get payments belonging to a job.
    """

    return execute_query(
        """
        SELECT *
        FROM payments
        WHERE job_id = ?
        ORDER BY id DESC
        """,
        (job_id,),
        fetch=True
    )


def get_latest_payment(job_id):
    """
    Get the latest payment for a job.
    """

    return execute_query(
        """
        SELECT *
        FROM payments
        WHERE job_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_id,),
        fetchone=True
    )


def update_payment_status(
    payment_id,
    payment_status,
    payment_reference=None,
    payment_method=None
):
    """
    Update payment status and related information.
    """

    updates = [
        "payment_status = ?",
        "updated_at = ?"
    ]

    values = [
        payment_status,
        now()
    ]

    if payment_reference is not None:
        updates.append("payment_reference = ?")
        values.append(payment_reference)

    if payment_method is not None:
        updates.append("payment_method = ?")
        values.append(payment_method)

    values.append(payment_id)

    result = execute_query(
        f"""
        UPDATE payments
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        tuple(values)
    )

    return result is not None


# ============================================================
# JOB ACTIVITY
# ============================================================

def add_job_activity(
    job_id,
    activity_type,
    description
):
    """
    Add an activity entry for a job.
    """

    # Verify that the job exists before inserting activity.
    job = get_job(job_id)

    if job is None:
        print(
            "Cannot add job activity: "
            f"job {job_id} does not exist."
        )
        return None

    return execute_query(
        """
        INSERT INTO job_activity (
            job_id,
            activity_type,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            job_id,
            activity_type,
            description,
            now()
        )
    )


def get_job_activity(job_id):
    """
    Get activity history for a job.
    """

    return execute_query(
        """
        SELECT *
        FROM job_activity
        WHERE job_id = ?
        ORDER BY id ASC
        """,
        (job_id,),
        fetch=True
    )


# ============================================================
# SAVE CUSTOMER WORK
# ============================================================

def save_customer_work(
    job_id,
    work_title=None,
    work_type=None,
    storage_type=None,
    storage_reference=None,
    version=1,
    work_status="completed",
    notes=None
):
    """
    Save a customer's completed work and record the activity.
    """

    work_id = create_work_record(
        job_id=job_id,
        work_title=work_title,
        work_type=work_type,
        storage_type=storage_type,
        storage_reference=storage_reference,
        version=version,
        work_status=work_status,
        notes=notes
    )

    if work_id is None:
        return None

    add_job_activity(
        job_id,
        "work_saved",
        f"Customer work saved. Work record ID: {work_id}"
    )

    return work_id


# ============================================================
# BACK-OFFICE JOBS
# ============================================================

def get_back_office_jobs(
    status=None,
    limit=100
):
    """
    Return jobs with their latest work, file and payment information.

    Used by the back-office side of the system.
    """

    if status:

        return execute_query(
            """
            SELECT
                j.*,

                wr.id AS work_id,
                wr.work_title,
                wr.work_type,
                wr.storage_type,
                wr.storage_reference,
                wr.version AS work_version,
                wr.work_status,
                wr.download_activated,
                wr.download_activated_at,

                cf.id AS file_id,
                cf.file_name,
                cf.file_type,
                cf.storage_type AS file_storage_type,
                cf.storage_reference AS file_storage_reference,
                cf.file_status,

                p.id AS payment_id,
                p.amount AS payment_amount,
                p.currency AS payment_currency,
                p.payment_method,
                p.payment_status,
                p.payment_reference,
                p.payment_date

            FROM jobs j

            LEFT JOIN work_records wr
                ON wr.id = (
                    SELECT wr2.id
                    FROM work_records wr2
                    WHERE wr2.job_id = j.id
                    ORDER BY wr2.version DESC, wr2.id DESC
                    LIMIT 1
                )

            LEFT JOIN customer_files cf
                ON cf.id = (
                    SELECT cf2.id
                    FROM customer_files cf2
                    WHERE cf2.job_id = j.id
                    ORDER BY cf2.id DESC
                    LIMIT 1
                )

            LEFT JOIN payments p
                ON p.id = (
                    SELECT p2.id
                    FROM payments p2
                    WHERE p2.job_id = j.id
                    ORDER BY p2.id DESC
                    LIMIT 1
                )

            WHERE j.status = ?

            ORDER BY j.updated_at DESC, j.id DESC

            LIMIT ?
            """,
            (
                status,
                limit
            ),
            fetch=True
        )


    return execute_query(
        """
        SELECT
            j.*,

            wr.id AS work_id,
            wr.work_title,
            wr.work_type,
            wr.storage_type,
            wr.storage_reference,
            wr.version AS work_version,
            wr.work_status,
            wr.download_activated,
            wr.download_activated_at,

            cf.id AS file_id,
            cf.file_name,
            cf.file_type,
            cf.storage_type AS file_storage_type,
            cf.storage_reference AS file_storage_reference,
            cf.file_status,

            p.id AS payment_id,
            p.amount AS payment_amount,
            p.currency AS payment_currency,
            p.payment_method,
            p.payment_status,
            p.payment_reference,
            p.payment_date

        FROM jobs j

        LEFT JOIN work_records wr
            ON wr.id = (
                SELECT wr2.id
                FROM work_records wr2
                WHERE wr2.job_id = j.id
                ORDER BY wr2.version DESC, wr2.id DESC
                LIMIT 1
            )

        LEFT JOIN customer_files cf
            ON cf.id = (
                SELECT cf2.id
                FROM customer_files cf2
                WHERE cf2.job_id = j.id
                ORDER BY cf2.id DESC
                LIMIT 1
            )

        LEFT JOIN payments p
            ON p.id = (
                SELECT p2.id
                FROM payments p2
                WHERE p2.job_id = j.id
                ORDER BY p2.id DESC
                LIMIT 1
            )

        ORDER BY j.updated_at DESC, j.id DESC

        LIMIT ?
        """,
        (limit,),
        fetch=True
    )


# ============================================================
# DATABASE STATISTICS
# ============================================================

def get_database_stats():
    """
    Return basic database statistics.
    """

    stats = {}

    tables = [
        "customers",
        "jobs",
        "work_records",
        "customer_files",
        "payments",
        "job_activity"
    ]

    conn = get_connection()

    if conn is None:
        return stats

    try:

        for table in tables:

            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()

            stats[table] = row["count"] if row else 0

        return stats

    except sqlite3.Error as error:

        print("Database statistics error:", error)
        return stats

    finally:

        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# AUTOMATIC INITIALIZATION
# ============================================================
#
# IMPORTANT:
#
# Do NOT put database initialization only inside:
#
#     if __name__ == "__main__":
#
# because ada_api.py imports this module.
#
# The initialization below runs automatically when the module
# is imported by the API.
# ============================================================

DATABASE_READY = initialize_database()


# ============================================================
# DIRECT DATABASE TEST
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("Naija Pocket Business Center - Database Test")
    print("=" * 60)

    print()
    print("Database path:")
    print(DATABASE_PATH)

    print()
    print("Database ready:")
    print(DATABASE_READY)

    print()
    print("Database statistics:")

    stats = get_database_stats()

    for table, count in stats.items():
        print(f"  {table}: {count}")

    print()
    print("=" * 60)
