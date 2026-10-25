"""
Contact number change (Profile Settings) verified by a code sent to the account's
REGISTERED email address. No SMS: the code always goes by email. (Account Setup's
Step 4 registers the number without a code; it is saved with the rest of the setup.)

Flow (keyed to the logged-in session's username — never to form input):
  1. request_code() — the new number (already format-checked by the caller) must
                      differ from the saved one and not belong to another account;
                      stores a hashed 6-digit code bound to this account and to
                      that number, and emails it to accounts.email
  2. confirm()      — the code must match AND the number must be the one it was
                      sent for; max MAX_ATTEMPTS wrong tries, single use, expires
                      after CODE_TTL_MINUTES. Only then is accounts.contactnumber
                      updated, and a notice is mailed to the registered address.

The contact_otp_* columns are used for this action only, so a password or email
code can never be replayed here (and vice versa). Sends are limited by a resend
cooldown plus MAX_SENDS_PER_WINDOW per SEND_WINDOW_MINUTES.
Entering a code mailed to accounts.email also proves the user can read that inbox,
so a successful change records that address as verified (email_verified_addr), and
the number as the user's own (contact_verified -- a number the Admin typed in is not).
Codes and hashes are never returned to the client or logged.
"""
import secrets

from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash

import email_change
import mailer
from password_change import mask_email, smtp_configured


CODE_LENGTH          = 6
CODE_TTL_MINUTES     = 5
RESEND_COOLDOWN_SEC  = 60
MAX_ATTEMPTS         = 5
MAX_SENDS_PER_WINDOW = 5
SEND_WINDOW_MINUTES  = 60


ENSURE_SCHEMA_SQL = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_hash         VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_new          VARCHAR(20)  NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_expires_at   TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_sent_at      TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_attempts     SMALLINT NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_window_start TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_otp_window_sends SMALLINT NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS contact_verified         VARCHAR(20)  NULL",
]


def is_verified(contact, verified_contact):
    """The saved number counts as verified only while it equals the number the user verified."""
    contact = (contact or '').strip()
    return bool(contact) and contact == (verified_contact or '').strip()


class ContactChangeError(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra

    def to_json(self):
        return {'success': False, 'error': self.error, 'message': self.message, **self.extra}


_NO_EMAIL_MSG = ("A verification code is sent to your registered email, but your account has no email "
                 "address yet. Add one under Email first. If you can't, ask the Administrator to update "
                 "your contact number for you.")


def _load_account(cur, username, lock=False):
    cur.execute(f"""
        SELECT a.userid, a.employeenumber,
               NULLIF(TRIM(a.email), '')          AS email,
               a.email_verified_addr,
               NULLIF(TRIM(a.contactnumber), '')  AS contactnumber,
               a.contact_verified,
               a.contact_otp_hash, a.contact_otp_new, a.contact_otp_attempts,
               (a.contact_otp_expires_at IS NOT NULL AND NOW() > a.contact_otp_expires_at) AS code_expired,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (a.contact_otp_sent_at + INTERVAL '{RESEND_COOLDOWN_SEC} seconds' - NOW()))))::int
                   AS resend_wait,
               (a.contact_otp_window_start IS NULL
                OR NOW() >= a.contact_otp_window_start + INTERVAL '{SEND_WINDOW_MINUTES} minutes')
                   AS window_over,
               a.contact_otp_window_sends,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (a.contact_otp_window_start + INTERVAL '{SEND_WINDOW_MINUTES} minutes' - NOW()))))::int
                   AS window_wait
        FROM accounts a
        WHERE a.username = %s
        {'FOR UPDATE OF a' if lock else ''}
    """, [username])
    row = cur.fetchone()
    if not row:
        raise ContactChangeError(404, 'not_found', 'Account not found.')
    return row


def _check_new_contact(cur, acc, contact):
    if not contact:
        raise ContactChangeError(400, 'invalid_contact', 'Please enter a contact number.')
    # The number already on file may still be verified if the user never proved it
    # (e.g. one the Admin entered when creating the account).
    if acc['contactnumber'] == contact and is_verified(acc['contactnumber'], acc['contact_verified']):
        raise ContactChangeError(400, 'same_contact', 'That is already your contact number.')
    # accounts.contactnumber is unique across every account (ux_accounts_contactnumber_normalized).
    cur.execute("SELECT 1 FROM accounts WHERE BTRIM(contactnumber) = %s AND userid <> %s LIMIT 1",
                [contact, acc['userid']])
    if cur.fetchone():
        raise ContactChangeError(409, 'contact_taken', 'That contact number is already used by another account.')


def _email_ok(acc):
    """Raises unless a code may be sent to the account's email right now."""
    if not acc['email']:
        raise ContactChangeError(400, 'no_email', _NO_EMAIL_MSG)


def email_is_verified(acc):
    return email_change.is_verified(acc['email'], acc['email_verified_addr'])


def status(conn, username):
    """What the contact form needs before a code is sent."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username)
        out = {'success': True, 'has_email': bool(acc['email']), 'email_masked': mask_email(acc['email']),
               'email_verified': email_is_verified(acc), 'can_send': True,
               'code_length': CODE_LENGTH, 'expires_in_minutes': CODE_TTL_MINUTES,
               'resend_after': RESEND_COOLDOWN_SEC}
        try:
            _email_ok(acc)
        except ContactChangeError as ce:
            out.update(can_send=False, error=ce.error, message=ce.message)
        return out
    finally:
        cur.close()


def request_code(conn, username, contact, cfg):
    """contact is the caller's normalized number (app._normalize_contact)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        _email_ok(acc)
        _check_new_contact(cur, acc, contact)
        if acc['resend_wait'] and acc['resend_wait'] > 0:
            raise ContactChangeError(429, 'too_soon',
                f"Please wait {acc['resend_wait']} seconds before requesting another code.",
                retry_after=acc['resend_wait'])
        if not acc['window_over'] and acc['contact_otp_window_sends'] >= MAX_SENDS_PER_WINDOW:
            minutes = max(1, -(-acc['window_wait'] // 60))
            raise ContactChangeError(429, 'rate_limited',
                f"Too many verification codes were requested. Please try again in {minutes} "
                f"minute{'s' if minutes != 1 else ''}.", retry_after=acc['window_wait'])
        if not smtp_configured(cfg):
            raise ContactChangeError(503, 'email_unavailable',
                'The email service is not configured. Please contact the administrator.')

        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        # A new code replaces any previous one (and the number it was for).
        cur.execute(f"""
            UPDATE accounts
            SET contact_otp_hash = %s, contact_otp_new = %s, contact_otp_attempts = 0,
                contact_otp_sent_at = NOW(),
                contact_otp_expires_at = NOW() + INTERVAL '{CODE_TTL_MINUTES} minutes',
                contact_otp_window_start = CASE WHEN %s THEN NOW() ELSE contact_otp_window_start END,
                contact_otp_window_sends = CASE WHEN %s THEN 1 ELSE contact_otp_window_sends + 1 END
            WHERE userid = %s
        """, [generate_password_hash(code), contact, acc['window_over'], acc['window_over'], acc['userid']])
        try:
            mailer.send_email(
                cfg, acc['email'], 'PUP Lopez Scheduling System - Confirm your contact number change',
                (f"Your verification code is: {code}\n\n"
                 f"Enter it in Profile Settings to change your contact number. It expires in "
                 f"{CODE_TTL_MINUTES} minutes and can only be used once.\n\n"
                 "If you did not ask to change your contact number, ignore this email and "
                 "consider changing your password.\n"),
                mailer.code_email_html('Confirm your contact number change',
                                       'Use this code in Profile Settings to change your contact number.',
                                       code, CODE_TTL_MINUTES,
                                       'If you did not ask to change your contact number, ignore this email '
                                       'and consider changing your password.'))
        except Exception as e:
            conn.rollback()
            print(f"[contact_change] verification email failed: {type(e).__name__}")
            raise ContactChangeError(502, 'email_failed',
                'The verification code could not be sent. Please try again later.')
        conn.commit()
        return {'success': True, 'email_masked': mask_email(acc['email']),
                'expires_in_minutes': CODE_TTL_MINUTES, 'resend_after': RESEND_COOLDOWN_SEC}
    except ContactChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()


def confirm(conn, username, contact, code):
    """Checks the code and saves the number. Returns (saved number, registered email)."""
    code = (code or '').strip()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        _email_ok(acc)
        if not acc['contact_otp_hash']:
            raise ContactChangeError(400, 'no_code', 'Please send a verification code first.')
        if acc['code_expired']:
            cur.execute("UPDATE accounts SET contact_otp_hash = NULL, contact_otp_expires_at = NULL "
                        "WHERE userid = %s", [acc['userid']])
            conn.commit()
            raise ContactChangeError(400, 'code_expired', 'This verification code has expired. Please send a new code.')
        if (acc['contact_otp_new'] or '') != (contact or ''):
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

        saved = acc['contact_otp_new']
        # Re-check: someone else may have taken the number since the code was sent.
        _check_new_contact(cur, acc, saved)
        cur.execute("""
            UPDATE accounts
            SET contactnumber = %s, contact_verified = %s, email_verified_addr = email,
                contact_otp_hash = NULL, contact_otp_new = NULL, contact_otp_expires_at = NULL,
                contact_otp_attempts = 0
            WHERE userid = %s
        """, [saved, saved, acc['userid']])
        conn.commit()
        return saved, acc['email']
    except pg_errors.UniqueViolation:
        conn.rollback()
        raise ContactChangeError(409, 'contact_taken', 'That contact number is already used by another account.')
    except ContactChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()


def send_changed_notice(cfg, email, contact):
    """Best effort: tell the registered address the number changed. Never raises."""
    if not email:
        return
    shown = ('*' * max(len(contact) - 4, 0)) + contact[-4:]
    try:
        mailer.send_email(
            cfg, email, 'PUP Lopez Scheduling System - Your contact number was changed',
            (f"The contact number on your account was changed to {shown}.\n\n"
             "If you did not make this change, change your password and contact the Administrator.\n"),
            mailer.notice_email_html('Your contact number was changed',
                                     [f"The contact number on your account was changed to {shown}."],
                                     'If you did not make this change, change your password and '
                                     'contact the Administrator.'))
    except Exception as e:
        print(f"[contact_change] change notice failed: {type(e).__name__}")
