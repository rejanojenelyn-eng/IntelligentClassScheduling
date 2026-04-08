# init_db.py

import os
from werkzeug.security import generate_password_hash
from database import get_db_connection # Assuming your db connection function is in a file named database.py

def initialize_admin_user():
    """Checks for and creates the initial admin user if one doesn't exist."""
    
    conn = get_db_connection()
    cur = conn.cursor()

    # Check if the 'admin' user already exists
    cur.execute("SELECT Username FROM Accounts WHERE Username = 'admin'")
    if cur.fetchone():
        print("--- Admin user already exists. No action taken. ---")
        return

    # If the admin user does not exist, create one.
    print("--- Admin user not found. Creating initial admin account... ---")
    
    # IMPORTANT: The default password is 'changeme'.
    # This generates the secure hash for the password.
    admin_password = 'changeme'
    hashed_password = generate_password_hash(admin_password)

    try:
        cur.execute(
            """
            INSERT INTO Accounts (Username, PasswordHash, Role, IsActive) 
            VALUES (%s, %s, 'Admin', TRUE)
            """,
            ('admin', hashed_password)
        )
        conn.commit()
        print("--- Admin user 'admin' with password 'changeme' created successfully! ---")
        print("--- IMPORTANT: Log in as admin and change this password immediately. ---")
    except Exception as e:
        conn.rollback()
        print(f"--- FAILED to create admin user: {e} ---")
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    # This block runs when you execute 'python init_db.py'
    initialize_admin_user()