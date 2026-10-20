"""
Self-service email change with a code sent to the NEW address.


Flow (keyed to the logged-in session's username — never to form input):
  1. request_code() — validates the new address (format, not the current one,
                      not used by another account), stores a hashed 6-digit
                      code plus the address it was sent to, and emails it
  2. confirm()      — the code must match AND the address must be the one it
                      was sent to; max MAX_ATTEMPTS wrong tries, single use.
                      Then accounts.email is updated.


Proving the user can read the new inbox matters because that address is
where password-change and forgot-password codes go afterwards.
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
_EMAIL_RE           = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


ENSURE_SCHEMA_SQL = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_hash       VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_new        VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_expires_at TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_sent_at    TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email_otp_attempts   SMALLINT NOT NULL DEFAULT 0",
]




class EmailChangeError(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra


    def to_json(self):
        return {'success': False, 'error': self.error, 'message': self.message, **self.extra}




def _load_account(cur, username, lock=False):
    cur.execute(f"""
        SELECT a.userid, a.employeenumber,
               NULLIF(TRIM(a.email), '')                     AS email,
               a.email_otp_hash, a.email_otp_new, a.email_otp_attempts,
               (a.email_otp_expires_at IS NOT NULL AND NOW() > a.email_otp_expires_at) AS code_expired,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (a.email_otp_sent_at + INTERVAL '{RESEND_COOLDOWN_SEC} seconds' - NOW()))))::int
                   AS resend_wait
        FROM accounts a
        WHERE a.username = %s
        {'FOR UPDATE OF a' if lock else ''}
    """, [username])
    row = cur.fetchone()
    if not row:
        raise EmailChangeError(404, 'not_found', 'Account not found.')
    return row




def _clean_new_email(cur, acc, new_email):
    new_email = (new_email or '').strip()
    if not _EMAIL_RE.fullmatch(new_email) or len(new_email) > 255:
        raise EmailChangeError(400, 'invalid_email', 'Please enter a valid email address.')
    if acc['email'] and acc['email'].lower() == new_email.lower():
        raise EmailChangeError(400, 'same_email', 'That is already your current email address.')
    # accounts.email is unique across every account (ux_accounts_email_normalized).
    cur.execute("""
        SELECT 1 FROM accounts WHERE LOWER(TRIM(email)) = LOWER(%s) AND userid <> %s
        LIMIT 1
    """, [new_email, acc['userid']])
    if cur.fetchone():
        raise EmailChangeError(409, 'email_taken', 'That email address is already used by another account.')
    return new_email




def request_code(conn, username, new_email, cfg):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        new_email = _clean_new_email(cur, acc, new_email)
        if acc['resend_wait'] and acc['resend_wait'] > 0:
            raise EmailChangeError(429, 'too_soon',
                f"Please wait {acc['resend_wait']} seconds before requesting another code.",
                retry_after=acc['resend_wait'])
        if not smtp_configured(cfg):
            raise EmailChangeError(503, 'email_unavailable',
                'The email service is not configured. Please contact the administrator.')


        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        # A new code replaces any previous one (and the address it was for).
        cur.execute(f"""
            UPDATE accounts
            SET email_otp_hash = %s, email_otp_new = %s, email_otp_attempts = 0,
                email_otp_sent_at = NOW(),
                email_otp_expires_at = NOW() + INTERVAL '{CODE_TTL_MINUTES} minutes'
            WHERE userid = %s
        """, [generate_password_hash(code), new_email, acc['userid']])
        try:
            mailer.send_email(
                cfg, new_email, 'PUP Lopez Scheduling System - Confirm your new email address',
                (f"Your verification code is: {code}\n\n"
                 f"It expires in {CODE_TTL_MINUTES} minutes and can only be used once.\n\n"
                 "If you did not ask to use this address for your account, ignore this email.\n"),
                mailer.code_email_html('Confirm your new email address',
                                       'Use this code in Profile Settings to make this your account email.',
                                       code, CODE_TTL_MINUTES,
                                       'If you did not ask to use this address for your account, ignore this email.'))
        except Exception as e:
            conn.rollback()
            print(f"[email_change] verification email failed: {type(e).__name__}")
            raise EmailChangeError(502, 'email_failed',
                'The verification code could not be sent. Check the address and try again.')
        conn.commit()
        return {'success': True, 'email_masked': mask_email(new_email),
                'expires_in_minutes': CODE_TTL_MINUTES, 'resend_after': RESEND_COOLDOWN_SEC}
    except EmailChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()




def confirm(conn, username, new_email, code):
    """Checks the code and saves the new address. Returns the saved address."""
    code = (code or '').strip()
    new_email = (new_email or '').strip()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        if not acc['email_otp_hash']:
            raise EmailChangeError(400, 'no_code', 'Please send a verification code to the new address first.')
        if acc['code_expired']:
            cur.execute("UPDATE accounts SET email_otp_hash = NULL, email_otp_expires_at = NULL WHERE userid = %s",
                        [acc['userid']])
            conn.commit()
            raise EmailChangeError(400, 'code_expired', 'This verification code has expired. Please send a new code.')
        if (acc['email_otp_new'] or '').lower() != new_email.lower():
            raise EmailChangeError(400, 'email_mismatch',
                'The code was sent to a different address. Please send a new code to this address.')
        if not (len(code) == CODE_LENGTH and code.isdigit()) or \
                not check_password_hash(acc['email_otp_hash'], code):
            attempts = acc['email_otp_attempts'] + 1
            if attempts >= MAX_ATTEMPTS:
                cur.execute("""UPDATE accounts SET email_otp_hash = NULL, email_otp_expires_at = NULL,
                               email_otp_attempts = %s WHERE userid = %s""", [attempts, acc['userid']])
                conn.commit()
                raise EmailChangeError(400, 'too_many_attempts',
                    'Too many incorrect attempts. Please send a new code.')
            cur.execute("UPDATE accounts SET email_otp_attempts = %s WHERE userid = %s",
                        [attempts, acc['userid']])
            conn.commit()
            left = MAX_ATTEMPTS - attempts
            raise EmailChangeError(400, 'invalid_code',
                f"Incorrect verification code. {left} attempt{'s' if left != 1 else ''} left.",
                attempts_left=left)


        saved = acc['email_otp_new']
        # Re-check: someone else may have taken the address since the code was sent.
        _clean_new_email(cur, acc, saved)
        cur.execute("UPDATE accounts SET email = %s WHERE userid = %s", [saved, acc['userid']])
        cur.execute("""UPDATE accounts SET email_otp_hash = NULL, email_otp_new = NULL,
                       email_otp_expires_at = NULL, email_otp_attempts = 0 WHERE userid = %s""",
                    [acc['userid']])
        conn.commit()
        return saved
    except pg_errors.UniqueViolation:
        conn.rollback()
        raise EmailChangeError(409, 'email_taken', 'That email address is already used by another account.')
    except EmailChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()
