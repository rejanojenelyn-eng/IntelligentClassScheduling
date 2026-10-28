"""
Self-service contact number change, confirmed with a code sent to the account's
email address (the system can't text a code to the new number).

Flow (keyed to the logged-in session's username — never to form input):
  1. request_code() — validates the new number (format, not the current one,
                      not used by another account), stores a hashed 6-digit
                      code plus the number it is for, and emails the code
  2. confirm()      — the code must match AND the number must be the one it
                      was sent for; max MAX_ATTEMPTS wrong tries, single use.
                      Then accounts.contactnumber is updated.

Account Setup uses normalize() for its own Contact Number step.
Codes and hashes are never returned to the client or logged.
"""
import re
import secrets

from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash

import mailer
from password_change import mask_email, smtp_configured


CODE_LENGTH         = 6
CODE_TTL_MINUTES    = 10
RESEND_COOLDOWN_SEC = 60
MAX_ATTEMPTS        = 5
# Philippine mobile number: +63 followed by 10 digits starting with 9 (e.g. +63 912 345 6789).
PH_MOBILE_RE        = re.compile(r'\+63\s*9\d{2}[\s-]?\d{3}[\s-]?\d{4}')


ENSURE_SCHEMA_SQL = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_hash       VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_new        VARCHAR(20) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_expires_at TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_sent_at    TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_attempts   SMALLINT NOT NULL DEFAULT 0",
]


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


def _load_account(cur, username, lock=False):
    cur.execute(f"""
        SELECT a.userid,
               NULLIF(TRIM(a.email), '')         AS email,
               NULLIF(TRIM(a.contactnumber), '') AS contactnumber,
               a.contact_otp_hash, a.contact_otp_new, a.contact_otp_attempts,
               (a.contact_otp_expires_at IS NOT NULL AND NOW() > a.contact_otp_expires_at) AS code_expired,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (a.contact_otp_sent_at + INTERVAL '{RESEND_COOLDOWN_SEC} seconds' - NOW()))))::int
                   AS resend_wait
        FROM accounts a
        WHERE a.username = %s
        {'FOR UPDATE OF a' if lock else ''}
    """, [username])
    row = cur.fetchone()
    if not row:
        raise ContactChangeError(404, 'not_found', 'Account not found.')
    return row


def _clean_new_contact(cur, acc, raw):
    contact, problem = normalize(raw)
    if problem:
        raise ContactChangeError(400, 'invalid_contact', problem)
    if acc['contactnumber'] and acc['contactnumber'] == contact:
        raise ContactChangeError(400, 'same_contact', 'That is already your current contact number.')
    # accounts.contactnumber is unique across every account (ux_accounts_contactnumber_normalized).
    cur.execute("SELECT 1 FROM accounts WHERE BTRIM(contactnumber) = %s AND userid <> %s LIMIT 1",
                [contact, acc['userid']])
    if cur.fetchone():
        raise ContactChangeError(409, 'contact_taken', 'That contact number is already used by another account.')
    return contact


def request_code(conn, username, new_contact, cfg):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        if not acc['email']:
            raise ContactChangeError(400, 'no_email',
                'The code is sent to your email, but your account has no email address yet. Add one first.')
        contact = _clean_new_contact(cur, acc, new_contact)
        if acc['resend_wait'] and acc['resend_wait'] > 0:
            raise ContactChangeError(429, 'too_soon',
                f"Please wait {acc['resend_wait']} seconds before requesting another code.",
                retry_after=acc['resend_wait'])
        if not smtp_configured(cfg):
            raise ContactChangeError(503, 'email_unavailable',
                'The email service is not configured. Please contact the administrator.')

        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        # A new code replaces any previous one (and the number it was for).
        cur.execute(f"""
            UPDATE accounts
            SET contact_otp_hash = %s, contact_otp_new = %s, contact_otp_attempts = 0,
                contact_otp_sent_at = NOW(),
                contact_otp_expires_at = NOW() + INTERVAL '{CODE_TTL_MINUTES} minutes'
            WHERE userid = %s
        """, [generate_password_hash(code), contact, acc['userid']])
        try:
            mailer.send_email(
                cfg, acc['email'], 'PUP Lopez Scheduling System - Confirm your new contact number',
                (f"Your verification code is: {code}\n\n"
                 f"It expires in {CODE_TTL_MINUTES} minutes and can only be used once.\n\n"
                 "If you did not ask to change your contact number, ignore this email.\n"),
                mailer.code_email_html('Confirm your new contact number',
                                       'Use this code in Academic Information to change your contact number.',
                                       code, CODE_TTL_MINUTES,
                                       'If you did not ask to change your contact number, ignore this email.'))
        except Exception as e:
            conn.rollback()
            print(f"[contact_change] verification email failed: {type(e).__name__}")
            raise ContactChangeError(502, 'email_failed',
                'The verification code could not be sent. Please try again.')
        conn.commit()
        return {'success': True, 'email_masked': mask_email(acc['email']), 'contact': contact,
                'expires_in_minutes': CODE_TTL_MINUTES, 'resend_after': RESEND_COOLDOWN_SEC}
    except ContactChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()


def confirm(conn, username, new_contact, code):
    """Checks the code and saves the new number. Returns the saved number."""
    code = (code or '').strip()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        if not acc['contact_otp_hash']:
            raise ContactChangeError(400, 'no_code', 'Please send a verification code first.')
        if acc['code_expired']:
            cur.execute("UPDATE accounts SET contact_otp_hash = NULL, contact_otp_expires_at = NULL WHERE userid = %s",
                        [acc['userid']])
            conn.commit()
            raise ContactChangeError(400, 'code_expired', 'This verification code has expired. Please send a new code.')
        contact, _problem = normalize(new_contact)
        if contact != acc['contact_otp_new']:
            raise ContactChangeError(400, 'contact_mismatch',
                'The code was sent for a different number. Please send a new code for this number.')
        if not (len(code) == CODE_LENGTH and code.isdigit()) or \
                not check_password_hash(acc['contact_otp_hash'], code):
            attempts = acc['contact_otp_attempts'] + 1
            if attempts >= MAX_ATTEMPTS:
                cur.execute("""UPDATE accounts SET contact_otp_hash = NULL, contact_otp_expires_at = NULL,
                               contact_otp_attempts = %s WHERE userid = %s""", [attempts, acc['userid']])
                conn.commit()
                raise ContactChangeError(400, 'too_many_attempts',
                    'Too many incorrect attempts. Please send a new code.')
            cur.execute("UPDATE accounts SET contact_otp_attempts = %s WHERE userid = %s",
                        [attempts, acc['userid']])
            conn.commit()
            left = MAX_ATTEMPTS - attempts
            raise ContactChangeError(400, 'invalid_code',
                f"Incorrect verification code. {left} attempt{'s' if left != 1 else ''} left.",
                attempts_left=left)

        # Re-check: someone else may have taken the number since the code was sent.
        _clean_new_contact(cur, acc, contact)
        cur.execute("""UPDATE accounts SET contactnumber = %s, contact_otp_hash = NULL, contact_otp_new = NULL,
                       contact_otp_expires_at = NULL, contact_otp_attempts = 0 WHERE userid = %s""",
                    [contact, acc['userid']])
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
