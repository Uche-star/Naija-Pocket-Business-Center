import sqlite3


DATABASE_NAME = "naija_pocket_business.db"


def get_connection():
    try:
        conn = sqlite3.connect(DATABASE_NAME)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as error:
        print("Database connection error:", error)
        return None


def initialize_database():

    conn = get_connection()

    if conn is None:
        return False

    try:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            phone TEXT,
            service_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            amount REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)


        cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT,
            payment_status TEXT DEFAULT 'pending',
            payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """)


        conn.commit()

        return True

    except sqlite3.Error as error:
        print("Database setup error:", error)
        return False

    finally:
        conn.close()



def execute_query(query, params=()):

    conn = get_connection()

    if conn is None:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.lastrowid

    except sqlite3.Error as error:
        print("Query error:", error)
        return None

    finally:
        conn.close()



def fetch_all(query, params=()):

    conn = get_connection()

    if conn is None:
        return []

    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    except sqlite3.Error as error:
        print("Fetch error:", error)
        return []

    finally:
        conn.close()



def fetch_one(query, params=()):

    conn = get_connection()

    if conn is None:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchone()

    except sqlite3.Error as error:
        print("Fetch error:", error)
        return None

    finally:
        conn.close()



if __name__ == "__main__":

    if initialize_database():
        print("Naija Pocket Business Center database initialized successfully.")
    else:
        print("Database initialization failed.")