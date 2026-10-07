"""
Forgot Password (logged-out) for faculty accounts: emailed 6-digit code →
new password → Login.


    start()   Employee Number / Username → a reset request row; if it matches an
              active Faculty / Academic Head account whose faculty record has an
              email, a code is emailed to that address (the address on file,
              never one typed by the user).
    verify()  checks the code: expires after CODE_TTL_MINUTES, single use,
              MAX_ATTEMPTS wrong tries, then opens a VERIFIED_TTL_MINUTES window
              bound to a one-time token kept in the user's session.
    reset()   new + confirm password (system password rule), hashed, saved.
              Only accounts.passwordhash (+ password_changed_at,
              must_change_password) change — email, contact number, photo,
              profile and account_setup_complete are left exactly as they are.


Every step is keyed by the request row id held in the signed session cookie;
the account is always taken from that row, never from form input, so nobody can
reset another user's password. State lives in PostgreSQL (password_reset_requests),
not in process memory or local files, so it survives Render restarts/redeploys.


Enumeration resistance: an unknown / inactive / email-less account still gets a
request row ("decoy", userid NULL) and goes through the same screens, messages,
cooldowns and attempt limits — it can just never verify. Codes are stored only as
hashes; codes, hashes and passwords are never logged or returned.
"""
import hashlib
import hmac
import secrets


from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash


import mailer
from password_change import password_problem


CODE_LENGTH          = 6
CODE_TTL_MINUTES     = 10
VERIFIED_TTL_MINUTES = 10
RESEND_COOLDOWN_SEC  = 60
MAX_ATTEMPTS         = 5     # wrong codes per code
MAX_CODES_PER_HOUR   = 5     # per account, and per reset flow
IP_WINDOW_MINUTES    = 15
IP_MAX_REQUESTS      = 10    # new requests + resends from one IP per window
RETENTION_DAYS       = 7     # old request rows are purged after this
ALLOWED_ROLES        = ('Faculty', 'Academic Head')   # accounts with a faculty record


SEND_IN_BACKGROUND = True    # tests switch this off to send inline


GENERIC_SENT = ("If the Employee Number or Username you entered belongs to a faculty account "
                "with a registered email address, we sent a 6-digit verification code to that "
                f"email. The code expires in {CODE_TTL_MINUTES} minutes.")


ENSURE_SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS password_reset_requests (
        id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        flow_id          VARCHAR(32)  NOT NULL,
        userid           INTEGER      NULL REFERENCES accounts(userid) ON DELETE CASCADE,
        employeenumber   VARCHAR(50)  NULL,
        code_hash        VARCHAR(255) NULL,
        attempts         SMALLINT     NOT NULL DEFAULT 0,
        created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
        expires_at       TIMESTAMP    NOT NULL,
        verified_at      TIMESTAMP    NULL,
        reset_token_hash VARCHAR(64)  NULL,
        used_at          TIMESTAMP    NULL,
        invalidated_at   TIMESTAMP    NULL,
        request_ip       VARCHAR(64)  NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_prr_user_created ON password_reset_requests (userid, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_prr_ip_created   ON password_reset_requests (request_ip, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_prr_flow         ON password_reset_requests (flow_id, created_at)",
    # A code whose email could not be sent is void and doesn't count toward the hourly limit.
    "ALTER TABLE password_reset_requests ADD COLUMN IF NOT EXISTS send_failed BOOLEAN NOT NULL DEFAULT FALSE",
]




class ResetError(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra




def _sha256(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()




def _new_code():
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"




# ─────────────────────────────────────────────────────────────
#  Lookups
# ─────────────────────────────────────────────────────────────
def _find_account(cur, identifier):
    """Active Faculty / Academic Head account by username or employee number,
    with the email from its faculty record (None when missing)."""
    cur.execute("""
        SELECT a.userid, a.username, a.employeenumber,
               NULLIF(TRIM(f.email), '') AS email
        FROM accounts a
        JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.isactive AND a.role IN %s
          AND (LOWER(a.username) = LOWER(%s) OR a.employeenumber = %s)
        ORDER BY (LOWER(a.username) = LOWER(%s)) DESC
        LIMIT 1
    """, [ALLOWED_ROLES, identifier, identifier, identifier])
    return cur.fetchone()




def _account_by_id(cur, userid):
    cur.execute("""
        SELECT a.userid, a.username, a.employeenumber, NULLIF(TRIM(f.email), '') AS email
        FROM accounts a JOIN faculty f ON f.employeenumber = a.employeenumber
        WHERE a.userid = %s AND a.isactive AND a.role IN %s
    """, [userid, ALLOWED_ROLES])
    return cur.fetchone()




def _check_ip(cur, ip):
    if not ip:
        return
    cur.execute(f"""SELECT COUNT(*) AS n FROM password_reset_requests
                    WHERE request_ip = %s AND created_at > NOW() - INTERVAL '{IP_WINDOW_MINUTES} minutes'""",
                [ip])
    if cur.fetchone()['n'] >= IP_MAX_REQUESTS:
        raise ResetError(429, 'rate_limited',
                         'Too many password reset requests from this device. Please try again in a few minutes.')




def _count_last_hour(cur, column, value):
    cur.execute(f"""SELECT COUNT(*) AS n FROM password_reset_requests
                    WHERE {column} = %s AND created_at > NOW() - INTERVAL '1 hour' AND NOT send_failed""",
                [value])
    return cur.fetchone()['n']




def _insert(cur, flow_id, ip, acc=None, code_hash=None, dead=False):
    cur.execute(f"""
        INSERT INTO password_reset_requests
            (flow_id, userid, employeenumber, code_hash, expires_at, request_ip, invalidated_at)
        VALUES (%s, %s, %s, %s, NOW() + INTERVAL '{CODE_TTL_MINUTES} minutes', %s,
                CASE WHEN %s THEN NOW() END)
        RETURNING id
    """, [flow_id, acc['userid'] if acc else None, acc['employeenumber'] if acc else None,
          code_hash, (ip or '')[:64] or None, dead])
    return cur.fetchone()['id']




def _issue(cur, cfg, flow_id, ip, acc):
    """Create the next request row of a flow. Returns (row_id, mail_job|None).
    Decoys (acc is None) do the same work (incl. hashing) but send nothing."""
    code = _new_code()
    code_hash = generate_password_hash(code)          # same cost on both paths
    if _count_last_hour(cur, 'flow_id', flow_id) >= MAX_CODES_PER_HOUR:
        raise ResetError(429, 'too_many', 'Too many verification codes were requested. Please try again later.')
    if acc is None:
        return _insert(cur, flow_id, ip), None


    cur.execute("SELECT userid FROM accounts WHERE userid = %s FOR UPDATE", [acc['userid']])
    if _count_last_hour(cur, 'userid', acc['userid']) >= MAX_CODES_PER_HOUR:
        # Account-wide cap reached (several flows): answer like any request, send nothing.
        return _insert(cur, flow_id, ip, acc, dead=True), None
    # A new code replaces every earlier unused code / verification of this account.
    cur.execute("""UPDATE password_reset_requests SET invalidated_at = NOW(), code_hash = NULL,
                          reset_token_hash = NULL
                   WHERE userid = %s AND used_at IS NULL AND invalidated_at IS NULL""", [acc['userid']])
    rid = _insert(cur, flow_id, ip, acc, code_hash)
    email = acc['email']
    return rid, (lambda: _send_or_void(cfg, email, code, rid))




def _send_or_void(cfg, to_addr, code, rid):
    """Send the code; if the email can't be sent, void that code so it neither
    lingers nor counts toward the hourly limit, then re-raise for logging."""
    try:
        send_reset_email(cfg, to_addr, code)
    except Exception:
        try:
            from database import get_db_connection
            conn = get_db_connection()
            try:
                cur = conn.cursor()
                cur.execute("""UPDATE password_reset_requests
                               SET send_failed = TRUE, code_hash = NULL,
                                   invalidated_at = COALESCE(invalidated_at, NOW())
                               WHERE id = %s""", [rid])
                conn.commit(); cur.close()
            finally:
                conn.close()
        except Exception:
            pass
        raise




def send_reset_email(cfg, to_addr, code):
    text = (f"Your PUP Lopez Scheduling System password reset code is: {code}\n\n"
            f"It expires in {CODE_TTL_MINUTES} minutes and can only be used once.\n\n"
            "If you did not request a password reset, you can ignore this email — "
            "your password has not been changed.\n")
    html = mailer.code_email_html(
        'Password reset code',
        'We received a request to reset the password of your faculty account. '
        'Enter this code on the Forgot Password page:',
        code, CODE_TTL_MINUTES,
        'If you did not request a password reset, you can ignore this email — your password has not been changed.')
    mailer.send_email(cfg, to_addr, 'PUP Lopez Scheduling System - Password reset code', text, html)




def _load_row(cur, rid, lock=False):
    if not rid:
        raise ResetError(400, 'no_session', 'Your password reset session has expired. Please start again.')
    cur.execute(f"""
        SELECT r.*,
               (NOW() > r.expires_at) AS code_expired,
               GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                   (r.created_at + INTERVAL '{RESEND_COOLDOWN_SEC} seconds' - NOW()))))::int AS resend_wait,
               GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (r.expires_at - NOW()))))::int AS expires_in,
               (r.verified_at IS NOT NULL
                AND NOW() <= r.verified_at + INTERVAL '{VERIFIED_TTL_MINUTES} minutes') AS verified_valid
        FROM password_reset_requests r WHERE r.id = %s
        {'FOR UPDATE' if lock else ''}
    """, [rid])
    row = cur.fetchone()
    if not row:
        raise ResetError(400, 'no_session', 'Your password reset session has expired. Please start again.')
    return row




def _purge_old(cur):
    cur.execute(f"DELETE FROM password_reset_requests WHERE created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'")




# ─────────────────────────────────────────────────────────────
#  Steps
# ─────────────────────────────────────────────────────────────
def start(conn, cfg, identifier, ip):
    """Returns (request_row_id, mail_job|None). The caller shows GENERIC_SENT either way."""
    identifier = (identifier or '').strip()
    if not identifier:
        raise ResetError(400, 'validation', 'Please enter your Employee Number or Username.')
    if len(identifier) > 64:
        raise ResetError(400, 'validation', 'That Employee Number or Username is too long.')
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _check_ip(cur, ip)
        _purge_old(cur)
        acc = _find_account(cur, identifier)
        if acc and not acc['email']:
            acc = None                                   # nowhere to send → decoy
        if acc:
            # Asked again within the cooldown: keep the code already sent (no new email).
            cur.execute(f"""SELECT id FROM password_reset_requests
                            WHERE userid = %s AND used_at IS NULL AND invalidated_at IS NULL
                              AND code_hash IS NOT NULL AND NOW() <= expires_at
                              AND created_at > NOW() - INTERVAL '{RESEND_COOLDOWN_SEC} seconds'
                            ORDER BY id DESC LIMIT 1""", [acc['userid']])
            recent = cur.fetchone()
            if recent:
                conn.commit()
                return recent['id'], None
        rid, job = _issue(cur, cfg, secrets.token_hex(16), ip, acc)
        conn.commit()
        return rid, job
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()




def resend(conn, cfg, rid, ip):
    """New code for the same flow (after the cooldown). Returns (new_row_id, mail_job|None)."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        row = _load_row(cur, rid)
        if row['used_at']:
            raise ResetError(400, 'no_session', 'Your password reset session has expired. Please start again.')
        if row['resend_wait'] > 0:
            raise ResetError(429, 'too_soon',
                             f"Please wait {row['resend_wait']} seconds before requesting a new code.",
                             retry_after=row['resend_wait'])
        _check_ip(cur, ip)
        acc = _account_by_id(cur, row['userid']) if row['userid'] else None
        if acc and not acc['email']:
            acc = None
        new_rid, job = _issue(cur, cfg, row['flow_id'], ip, acc)
        conn.commit()
        return new_rid, job
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()




def status(conn, rid):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        row = _load_row(cur, rid)
        return {'resend_wait': row['resend_wait'], 'expires_in': row['expires_in']}
    finally:
        cur.close()




def verify(conn, rid, code):
    """Returns a one-time reset token (to keep in the session) on success."""
    code = (code or '').strip()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        row = _load_row(cur, rid, lock=True)
        if row['used_at'] or row['verified_at']:
            raise ResetError(400, 'no_session', 'This verification code was already used. Please start again.')
        if row['attempts'] >= MAX_ATTEMPTS:
            raise ResetError(400, 'too_many_attempts', 'Too many incorrect attempts. Please request a new code.')
        if row['code_expired']:
            raise ResetError(400, 'code_expired', 'This verification code has expired. Please request a new code.')


        live = row['code_hash'] and not row['invalidated_at']
        if not (live and len(code) == CODE_LENGTH and code.isdigit()
                and check_password_hash(row['code_hash'], code)):
            attempts = row['attempts'] + 1
            locked = attempts >= MAX_ATTEMPTS
            cur.execute("""UPDATE password_reset_requests
                           SET attempts = %s,
                               code_hash = CASE WHEN %s THEN NULL ELSE code_hash END,
                               invalidated_at = CASE WHEN %s THEN COALESCE(invalidated_at, NOW())
                                                     ELSE invalidated_at END
                           WHERE id = %s""", [attempts, locked, locked, row['id']])
            conn.commit()
            if locked:
                raise ResetError(400, 'too_many_attempts', 'Too many incorrect attempts. Please request a new code.')
            left = MAX_ATTEMPTS - attempts
            raise ResetError(400, 'invalid_code',
                             f"Incorrect verification code. {left} attempt{'s' if left != 1 else ''} left.",
                             attempts_left=left)


        token = secrets.token_urlsafe(32)
        # Correct: the code is consumed now (single use) and a short reset window opens.
        cur.execute("""UPDATE password_reset_requests
                       SET code_hash = NULL, verified_at = NOW(), reset_token_hash = %s
                       WHERE id = %s""", [_sha256(token), row['id']])
        conn.commit()
        return token
    except ResetError:
        conn.rollback()
        raise
    finally:
        cur.close()




def is_verified(conn, rid, token):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        row = _load_row(cur, rid)
        return bool(row['userid'] and row['verified_valid'] and not row['used_at']
                    and not row['invalidated_at'] and row['reset_token_hash'] and token
                    and hmac.compare_digest(row['reset_token_hash'], _sha256(token)))
    except ResetError:
        return False
    finally:
        cur.close()




def reset(conn, rid, token, new_pw, confirm_pw):
    """Sets the new password. Returns the account's username."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        row = _load_row(cur, rid, lock=True)
        if not (row['userid'] and row['verified_valid'] and not row['used_at'] and not row['invalidated_at']
                and row['reset_token_hash'] and token
                and hmac.compare_digest(row['reset_token_hash'], _sha256(token))):
            raise ResetError(403, 'not_verified',
                             'Your verification has expired. Please request a new verification code.')
        cur.execute("""SELECT userid, username, passwordhash FROM accounts
                       WHERE userid = %s AND isactive AND role IN %s FOR UPDATE""",
                    [row['userid'], ALLOWED_ROLES])
        acc = cur.fetchone()
        if not acc:
            raise ResetError(403, 'not_verified', 'This account can no longer be reset. Please contact the administrator.')


        new_pw, confirm_pw = new_pw or '', confirm_pw or ''
        if not new_pw or not confirm_pw:
            raise ResetError(400, 'validation', 'Please fill in both password fields.')
        if new_pw != confirm_pw:
            raise ResetError(400, 'validation', 'New Password and Confirm New Password do not match.')
        problem = password_problem(new_pw, acc['username'])
        if problem:
            raise ResetError(400, 'validation', problem)
        if check_password_hash(acc['passwordhash'], new_pw):
            raise ResetError(400, 'validation', 'New password must be different from your current password.')


        # Password only. account_setup_complete, email, contact number, photo and
        # profile are deliberately untouched; a pending "must change password" is
        # satisfied by choosing a new password here.
        cur.execute("""UPDATE accounts
                       SET passwordhash = %s, password_changed_at = NOW(), must_change_password = FALSE,
                           pw_otp_verified_at = NULL
                       WHERE userid = %s""", [generate_password_hash(new_pw), acc['userid']])
        cur.execute("""UPDATE password_reset_requests SET used_at = NOW(), reset_token_hash = NULL
                       WHERE id = %s""", [row['id']])
        cur.execute("""UPDATE password_reset_requests
                       SET invalidated_at = NOW(), code_hash = NULL, reset_token_hash = NULL
                       WHERE userid = %s AND id <> %s AND used_at IS NULL AND invalidated_at IS NULL""",
                    [acc['userid'], row['id']])
        conn.commit()
        return acc['username']
    except ResetError:
        conn.rollback()
        raise
    finally:
        cur.close()