"""
Self-service contact number change (Academic Information > Contact Number).

    save()      — checks the new number (format, not the current one, not used by
                  another account) and saves it to accounts.contactnumber. No
                  verification code: the number is only used for announcements.

Account Setup uses normalize() for its own Contact Number step.
"""
import re

from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor


# Philippine mobile number: +63 followed by 10 digits starting with 9 (e.g. +63 912 345 6789).
PH_MOBILE_RE = re.compile(r'\+63\s*9\d{2}[\s-]?\d{3}[\s-]?\d{4}')


def normalize(raw):
    """Check a typed contact number ("+63 912 345 6789", "+65 6123 4567", "09123456789")
    and return (stored form, None) or (None, user-facing error). Stored compactly, at
    most 15 characters: a PH mobile in the local form every existing record uses
    (09XXXXXXXXX), any other number as +<digits>."""
    contact = (raw or '').strip()
    if not contact:
        return None, "Please enter a contact number."
    digits = re.sub(r'\D', '', contact)
    if not re.fullmatch(r'[0-9+()\-\s]+', contact) or not (7 <= len(digits) <= 15):
        return None, "Please enter a valid contact number."
    if contact.startswith('+63') and not PH_MOBILE_RE.fullmatch(contact):
        return None, "Please enter a valid Philippine mobile number (e.g. 912 345 6789)."
    if contact.startswith('+63'):
        contact = '0' + digits[2:]
    elif contact.startswith('+'):
        contact = '+' + digits
    if len(contact) > 15:
        return None, "That contact number is too long. Please check it."
    return contact, None


class ContactChangeError(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra

    def to_json(self):
        return {'success': False, 'error': self.error, 'message': self.message, **self.extra}


def save(conn, username, new_contact):
    """Saves the new number for this account. Returns the saved (normalized) number."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""SELECT userid, NULLIF(TRIM(contactnumber), '') AS contactnumber
                       FROM accounts WHERE username = %s FOR UPDATE""", [username])
        acc = cur.fetchone()
        if not acc:
            raise ContactChangeError(404, 'not_found', 'Account not found.')
        contact, problem = normalize(new_contact)
        if problem:
            raise ContactChangeError(400, 'invalid_contact', problem)
        if acc['contactnumber'] == contact:
            raise ContactChangeError(400, 'same_contact', 'That is already your current contact number.')
        # accounts.contactnumber is unique across every account (ux_accounts_contactnumber_normalized).
        cur.execute("SELECT 1 FROM accounts WHERE BTRIM(contactnumber) = %s AND userid <> %s LIMIT 1",
                    [contact, acc['userid']])
        if cur.fetchone():
            raise ContactChangeError(409, 'contact_taken', 'That contact number is already used by another account.')
        cur.execute("UPDATE accounts SET contactnumber = %s WHERE userid = %s", [contact, acc['userid']])
        conn.commit()
        return contact
    except pg_errors.UniqueViolation:
        conn.rollback()
        raise ContactChangeError(409, 'contact_taken', 'That contact number is already used by another account.')
    except ContactChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()
