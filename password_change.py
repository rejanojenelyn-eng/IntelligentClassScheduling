"""
Self-service password change with an emailed verification code.


Flow (all steps keyed to the logged-in session's username — never to form input):
  1. eligibility()     — at most one successful change per calendar month
  2. request_code()    — 6-digit code, stored only as a hash, emailed to the
                         account's address, valid CODE_TTL_MINUTES
  3. verify_code()     — max MAX_ATTEMPTS wrong tries; a correct code is
                         consumed immediately (single use) and marks the
                         session as verified for VERIFIED_TTL_MINUTES
  4. change_password() — current/new/confirm validation, then the hash and
                         password_changed_at are updated in one transaction


Codes, hashes and passwords are never returned to the client or logged.
All times come from the database clock (NOW()) so app/DB clock skew can't
shorten or extend a window.
"""
import hmac
import secrets


from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash


import mailer


CODE_LENGTH          = 6
CODE_TTL_MINUTES     = 10
VERIFIED_TTL_MINUTES = 10     # time allowed between verifying the code and saving
RESEND_COOLDOWN_SEC  = 60
MAX_ATTEMPTS         = 5      # wrong codes, and separately wrong current passwords
MIN_PASSWORD_LENGTH  = 8      # shared with the first-login setup wizard (password_problem)


# Passwords seen so often in breach lists that browsers warn about them on sight.
_TOO_COMMON = {'password', 'password1', 'password123', 'passw0rd', '12345678', '123456789',
               '1234567890', 'qwerty123', 'qwertyuiop', 'abc12345', 'abcd1234', 'iloveyou1',
               'admin123', 'welcome1', 'letmein1', '11111111', '00000000', 'pup12345'}




def password_problem(new_pw, username=None):
    """The one password rule for the whole system (first-login setup and
    Profile Settings). Returns a user-facing message, or None if acceptable.
    Weak / common passwords are what make browsers show their
    'found in a data breach' warning, so they're refused here."""
    pw = new_pw or ''
    if len(pw) < MIN_PASSWORD_LENGTH:
        return f'New password must be at least {MIN_PASSWORD_LENGTH} characters.'
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        return 'New password must contain both letters and numbers.'
    if pw.lower() in _TOO_COMMON or len(set(pw)) <= 2:
        return 'That password is too common. Please choose a less predictable one.'
    if username and username.lower() in pw.lower():
        return 'New password must not contain your employee number or username.'
    return None




class PasswordChangeError(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra


    def to_json(self):
        return {'success': False, 'error': self.error, 'message': self.message, **self.extra}




# ─────────────────────────────────────────────────────────────
#  Schema (idempotent) — also shipped as migrations/2026-09-28_password_change_otp.sql
# ─────────────────────────────────────────────────────────────
ENSURE_SCHEMA_SQL = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP NULL",
    # Contact email for standalone accounts that have no faculty record (the
    # system Admin). Faculty-linked accounts keep using faculty.email.
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email              VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_hash        VARCHAR(255) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_expires_at  TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_sent_at     TIMESTAMP NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_attempts    SMALLINT NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS pw_otp_verified_at TIMESTAMP NULL",
]




# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────
def mask_email(email):
    local, _, domain = (email or '').partition('@')
    if not domain:
        return ''
    shown = local[:2] if len(local) > 2 else local[:1]
    return f"{shown}{'*' * max(len(local) - len(shown), 3)}@{domain}"




def _fmt_date(d):
    return d.strftime('%B %d, %Y').replace(' 0', ' ')




def _load_account(cur, username, lock=False):
    """The account row plus its eligibility, computed by the DB. The email is
    the linked faculty record's (faculty.email) for Faculty / Academic Head
    accounts, and the account's own accounts.email for standalone accounts
    with no faculty record (the system Admin)."""
    cur.execute(f"""
        SELECT a.userid, a.username, a.passwordhash, a.password_changed_at,
               a.pw_otp_hash, a.pw_otp_attempts,
               a.pw_otp_expires_at, a.pw_otp_sent_at, a.pw_otp_verified_at,
               NULLIF(TRIM(CASE WHEN a.employeenumber IS NULL THEN a.email
                                ELSE f.email END), '')        AS email,
               a.password_changed_at + INTERVAL '1 month'   AS next_allowed_at,
               (a.password_changed_at IS NULL
                OR NOW() >= a.password_changed_at + INTERVAL '1 month') AS eligible,
               (a.pw_otp_expires_at IS NOT NULL AND NOW() > a.pw_otp_expires_at) AS code_expired,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (a.pw_otp_sent_at + INTERVAL '{RESEND_COOLDOWN_SEC} seconds' - NOW()))))::int
                   AS resend_wait,
               (a.pw_otp_verified_at IS NOT NULL
                AND NOW() <= a.pw_otp_verified_at + INTERVAL '{VERIFIED_TTL_MINUTES} minutes')
                   AS verified_valid
        FROM accounts a
        LEFT JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.username = %s
        {'FOR UPDATE OF a' if lock else ''}
    """, [username])
    row = cur.fetchone()
    if not row:
        raise PasswordChangeError(404, 'not_found', 'Account not found.')
    return row




def _require_eligible(acc):
    if not acc['eligible']:
        when = _fmt_date(acc['next_allowed_at'])
        raise PasswordChangeError(
            403, 'not_eligible',
            f"You can only change your password once per month. "
            f"You may change your password again on {when}.",
            next_allowed_date=when)




# ─────────────────────────────────────────────────────────────
#  Email
# ─────────────────────────────────────────────────────────────
def smtp_configured(cfg):
    """Whether any email provider (Brevo / Resend API, or SMTP) is configured."""
    return mailer.configured(cfg)




def send_code_email(cfg, to_addr, code):
    text = (f"Your verification code is: {code}\n\n"
            f"It expires in {CODE_TTL_MINUTES} minutes and can only be used once.\n\n"
            "If you did not try to change your password, ignore this email and "
            "consider changing your password soon.\n")
    mailer.send_email(cfg, to_addr, 'PUP Lopez Scheduling System - Password change verification code', text,
                      mailer.code_email_html('Password change verification code',
                                             'Use this code to confirm your password change in Profile Settings.',
                                             code, CODE_TTL_MINUTES,
                                             'If you did not try to change your password, ignore this email '
                                             'and consider changing your password soon.'))




# ─────────────────────────────────────────────────────────────
#  Steps
# ─────────────────────────────────────────────────────────────
def eligibility(conn, username):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username)
        out = {'success': True, 'eligible': bool(acc['eligible']),
               'has_email': bool(acc['email']), 'email_masked': mask_email(acc['email'])}
        if not acc['eligible']:
            out['next_allowed_date'] = _fmt_date(acc['next_allowed_at'])
            out['message'] = (f"You can only change your password once per month. "
                              f"You may change your password again on {out['next_allowed_date']}.")
        if acc['password_changed_at']:
            out['last_changed'] = _fmt_date(acc['password_changed_at'])
        return out
    finally:
        cur.close()




def request_code(conn, username, cfg):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        _require_eligible(acc)
        if not acc['email']:
            raise PasswordChangeError(400, 'no_email',
                'Your account has no email address. Add one under Email Address first.')
        if acc['resend_wait'] and acc['resend_wait'] > 0:
            raise PasswordChangeError(429, 'too_soon',
                f"Please wait {acc['resend_wait']} seconds before requesting another code.",
                retry_after=acc['resend_wait'])
        if not smtp_configured(cfg):
            raise PasswordChangeError(503, 'email_unavailable',
                'The email service is not configured. Please contact the administrator.')


        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        # A new code replaces any previous code or verification.
        cur.execute(f"""
            UPDATE accounts
            SET pw_otp_hash = %s, pw_otp_attempts = 0, pw_otp_verified_at = NULL,
                pw_otp_sent_at = NOW(),
                pw_otp_expires_at = NOW() + INTERVAL '{CODE_TTL_MINUTES} minutes'
            WHERE userid = %s
        """, [generate_password_hash(code), acc['userid']])
        try:
            send_code_email(cfg, acc['email'], code)
        except Exception as e:
            conn.rollback()
            print(f"[password_change] verification email failed: {type(e).__name__}")
            raise PasswordChangeError(502, 'email_failed',
                'The verification code could not be sent. Please try again later.')
        conn.commit()
        return {'success': True, 'email_masked': mask_email(acc['email']),
                'expires_in_minutes': CODE_TTL_MINUTES, 'resend_after': RESEND_COOLDOWN_SEC}
    except PasswordChangeError:
        conn.rollback()
        raise
    finally:
        cur.close()




def verify_code(conn, username, code):
    """Returns the verification timestamp (to be bound to the session)."""
    code = (code or '').strip()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        _require_eligible(acc)
        if not acc['pw_otp_hash']:
            raise PasswordChangeError(400, 'no_code',
                'No active verification code. Please request a new code.')
        if acc['code_expired']:
            cur.execute("UPDATE accounts SET pw_otp_hash = NULL, pw_otp_expires_at = NULL WHERE userid = %s",
                        [acc['userid']])
            conn.commit()
            raise PasswordChangeError(400, 'code_expired',
                'This verification code has expired. Please request a new code.')
        if not (len(code) == CODE_LENGTH and code.isdigit()) or \
                not check_password_hash(acc['pw_otp_hash'], code):
            attempts = acc['pw_otp_attempts'] + 1
            if attempts >= MAX_ATTEMPTS:
                cur.execute("""UPDATE accounts SET pw_otp_hash = NULL, pw_otp_expires_at = NULL,
                               pw_otp_attempts = %s WHERE userid = %s""", [attempts, acc['userid']])
                conn.commit()
                raise PasswordChangeError(400, 'too_many_attempts',
                    'Too many incorrect attempts. Please request a new code.')
            cur.execute("UPDATE accounts SET pw_otp_attempts = %s WHERE userid = %s",
                        [attempts, acc['userid']])
            conn.commit()
            left = MAX_ATTEMPTS - attempts
            raise PasswordChangeError(400, 'invalid_code',
                f"Incorrect verification code. {left} attempt{'s' if left != 1 else ''} left.",
                attempts_left=left)


        # Correct: consume the code (single use) and open the verified window.
        cur.execute("""
            UPDATE accounts SET pw_otp_hash = NULL, pw_otp_expires_at = NULL,
                                pw_otp_attempts = 0, pw_otp_verified_at = NOW()
            WHERE userid = %s RETURNING pw_otp_verified_at
        """, [acc['userid']])
        verified_at = cur.fetchone()['pw_otp_verified_at']
        conn.commit()
        return verified_at.isoformat()
    except PasswordChangeError:
        conn.rollback()   # releases the row lock; attempt counters were already committed
        raise
    finally:
        cur.close()




def change_password(conn, username, session_verified_at, current_pw, new_pw, confirm_pw):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        acc = _load_account(cur, username, lock=True)
        _require_eligible(acc)
        # The verification must exist, be fresh, and belong to THIS session.
        if not (acc['verified_valid'] and session_verified_at and
                hmac.compare_digest(acc['pw_otp_verified_at'].isoformat(), str(session_verified_at))):
            raise PasswordChangeError(403, 'not_verified',
                'Your verification has expired. Please request a new verification code.')


        current_pw, new_pw, confirm_pw = current_pw or '', new_pw or '', confirm_pw or ''
        if not current_pw or not new_pw or not confirm_pw:
            raise PasswordChangeError(400, 'validation_error', 'Please fill in all password fields.')
        if not check_password_hash(acc['passwordhash'], current_pw):
            attempts = acc['pw_otp_attempts'] + 1
            if attempts >= MAX_ATTEMPTS:
                cur.execute("""UPDATE accounts SET pw_otp_verified_at = NULL, pw_otp_attempts = 0
                               WHERE userid = %s""", [acc['userid']])
                conn.commit()
                raise PasswordChangeError(403, 'not_verified',
                    'Too many incorrect current-password attempts. Please request a new verification code.')
            cur.execute("UPDATE accounts SET pw_otp_attempts = %s WHERE userid = %s",
                        [attempts, acc['userid']])
            conn.commit()
            raise PasswordChangeError(400, 'wrong_current', 'Current password is incorrect.')
        if new_pw != confirm_pw:
            raise PasswordChangeError(400, 'validation_error', 'New Password and Confirm New Password do not match.')
        problem = password_problem(new_pw, username)
        if problem:
            raise PasswordChangeError(400, 'validation_error', problem)
        if check_password_hash(acc['passwordhash'], new_pw):
            raise PasswordChangeError(400, 'validation_error',
                'New password must be different from your current password.')


        cur.execute("""
            UPDATE accounts
            SET passwordhash = %s, password_changed_at = NOW(),
                pw_otp_hash = NULL, pw_otp_expires_at = NULL, pw_otp_attempts = 0,
                pw_otp_verified_at = NULL
            WHERE userid = %s AND username = %s
            RETURNING password_changed_at + INTERVAL '1 month' AS next_allowed_at
        """, [generate_password_hash(new_pw), acc['userid'], username])
        nxt = cur.fetchone()['next_allowed_at']
        conn.commit()
        return {'success': True, 'next_allowed_date': _fmt_date(nxt)}
    except PasswordChangeError:
        conn.rollback()   # releases the row lock; attempt counters were already committed
        raise
    finally:
        cur.close()