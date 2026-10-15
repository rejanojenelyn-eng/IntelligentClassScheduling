"""
Login assistance ("Contact Admin" on the login page) for users who can't sign in.

    submit()            logged-out request: account reference (username or Employee ID),
                        concern, optional description -> a Pending row + a reference
                        number (LA-000123) + an email to the System Administrator.
    update_status()     Admin: Pending / In Progress / Resolved, remarks, resolution.
    verify_identity()   Admin records HOW the person's identity was verified. Required
                        before any recovery action below.
    clear_lock()        Admin: lift the temporary login lock now (login_security).
    reset_password()    Admin: reset to the temporary password (same as User Management).

Enumeration resistance: the response never depends on whether the account reference
matches an account. The match (userid) is stored for the Admin only. Nothing here
unlocks an account or changes a password by itself -- only an Admin action does.
"""
import re
from datetime import datetime

from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash

import mailer
import login_security


CONCERNS = ('Account Temporarily Locked', 'Forgot Password', 'Unable to Access Account', 'Other Login Concern')
STATUSES = ('Pending', 'In Progress', 'Resolved')
OPEN_STATUSES = ('Pending', 'In Progress')
VERIFY_METHODS = ('Registered email confirmed', 'Valid ID presented in person',
                  'Phone call to the contact number on file', 'Other (see notes)')

REFERENCE_MAX     = 60
DESCRIPTION_MAX   = 1000
REMARKS_MAX       = 1000
IP_MAX_PER_HOUR   = 5      # new requests from one IP address per hour
DUPLICATE_HOURS   = 24     # one open request per account reference per day

SEND_IN_BACKGROUND = True  # tests switch this off to send inline

SUCCESS_MESSAGE = ("Your support request has been submitted successfully. "
                   "Please wait for assistance from the System Administrator.")

_REFERENCE_RE = re.compile(r"[A-Za-z0-9._@\-]{1,%d}" % REFERENCE_MAX)

ENSURE_SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS login_assistance_request (
        id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        reference            VARCHAR(20)  NULL UNIQUE,
        account_reference    VARCHAR(60)  NOT NULL,
        userid               INTEGER      NULL REFERENCES accounts(userid) ON DELETE SET NULL,
        concern              VARCHAR(40)  NOT NULL,
        description          TEXT         NULL,
        status               VARCHAR(20)  NOT NULL DEFAULT 'Pending',
        admin_remarks        TEXT         NULL,
        resolution           TEXT         NULL,
        identity_method      VARCHAR(60)  NULL,
        identity_notes       TEXT         NULL,
        identity_verified_by VARCHAR(50)  NULL,
        identity_verified_at TIMESTAMP    NULL,
        actions_taken        TEXT         NULL,
        handled_by           VARCHAR(50)  NULL,
        created_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
        updated_at           TIMESTAMP    NULL,
        resolved_at          TIMESTAMP    NULL,
        request_ip           VARCHAR(64)  NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_lar_status_created ON login_assistance_request (status, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_lar_ref_created ON login_assistance_request (LOWER(account_reference), created_at)",
    "CREATE INDEX IF NOT EXISTS ix_lar_ip_created ON login_assistance_request (request_ip, created_at)",
]


class AssistanceError(Exception):
    def __init__(self, status, message, code='invalid'):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ── Login page ─────────────────────────────────────────────────────────────────

def submit(conn, cfg, account_reference, concern, description, ip):
    """Validate and save a request. Returns (reference, email job or None)."""
    ref = (account_reference or '').strip()
    concern = (concern or '').strip()
    description = (description or '').strip() or None
    if not ref:
        raise AssistanceError(400, 'Please enter your username or Employee ID.')
    if not _REFERENCE_RE.fullmatch(ref):
        raise AssistanceError(400, 'Please enter a valid username or Employee ID '
                                   f'(letters, numbers, . _ @ - only, up to {REFERENCE_MAX} characters).')
    if concern not in CONCERNS:
        raise AssistanceError(400, 'Please choose your concern from the list.')
    if description and len(description) > DESCRIPTION_MAX:
        raise AssistanceError(400, f'The description is too long (maximum {DESCRIPTION_MAX} characters).')

    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM login_assistance_request "
                    "WHERE request_ip = %s AND created_at > NOW() - INTERVAL '1 hour'", (ip or '',))
        if cur.fetchone()['n'] >= IP_MAX_PER_HOUR:
            raise AssistanceError(429, 'Too many support requests from this network. Please try again later.',
                                  'rate_limited')
        # One open request per account reference: serialize same-reference submits.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ('login_assistance:' + ref.lower(),))
        cur.execute(f"""SELECT 1 FROM login_assistance_request
                        WHERE LOWER(account_reference) = LOWER(%s) AND status = ANY(%s)
                          AND created_at > NOW() - INTERVAL '{DUPLICATE_HOURS} hours' LIMIT 1""",
                    (ref, list(OPEN_STATUSES)))
        if cur.fetchone():
            raise AssistanceError(409, 'A support request for this account was already submitted and is waiting '
                                       'for the System Administrator. Please wait for assistance.', 'duplicate')
        # Matched for the Admin only; the reply is the same either way.
        cur.execute("""SELECT userid FROM accounts
                       WHERE LOWER(username) = LOWER(%s) OR employeenumber = %s
                       ORDER BY (LOWER(username) = LOWER(%s)) DESC LIMIT 1""", (ref, ref, ref))
        match = cur.fetchone()
        cur.execute("""INSERT INTO login_assistance_request
                           (account_reference, userid, concern, description, request_ip)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id, created_at""",
                    (ref, match['userid'] if match else None, concern, description, ip or ''))
        row = cur.fetchone()
        reference = f"LA-{row['id']:06d}"
        cur.execute("UPDATE login_assistance_request SET reference = %s WHERE id = %s", (reference, row['id']))
        conn.commit()
    except AssistanceError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    submitted = datetime.now(login_security.PH_TZ)
    return reference, (lambda: send_admin_notification(cfg, reference, concern, ref, submitted))


def admin_notification(cfg, reference, concern, account_reference, submitted):
    """(to, subject, text, html) for the System Administrator. The user's description is
    left out on purpose (it is free text and could contain anything); the Admin reads it
    in the system."""
    to_addr = (getattr(cfg, 'ADMIN_NOTIFY_EMAIL', '') or getattr(cfg, 'SUPPORT_EMAIL', '') or '').strip()
    if not to_addr:
        return None
    stamp = submitted.astimezone(login_security.PH_TZ).strftime('%B %d, %Y at %I:%M %p').replace(' 0', ' ') \
        + ' (Philippine Standard Time)'
    subject = f'New login assistance request {reference}'
    paragraphs = [
        f'Reference: {reference}',
        f'Concern: {concern}',
        f'Account reference given: {account_reference}',
        f'Submitted: {stamp}',
        'Open Admin > User Management > Account Assistance to review it. Verify the person\'s identity '
        'before unlocking the account or resetting a password.',
    ]
    text = 'New login assistance request\n\n' + '\n'.join(paragraphs) + \
           '\n\nThis is an automated message from the PUP Lopez Scheduling System.\n'
    html = mailer.notice_email_html('New login assistance request', paragraphs,
                                    'This is an automated message from the PUP Lopez Scheduling System.')
    return to_addr, subject, text, html


def send_admin_notification(cfg, reference, concern, account_reference, submitted):
    """Never raises: a mail problem must not undo or hide a saved request."""
    msg = admin_notification(cfg, reference, concern, account_reference, submitted)
    if not msg:
        return False
    try:
        if not mailer.configured(cfg):
            print('[login_assistance] admin notification not sent: email is not configured', flush=True)
            return False
        mailer.send_email(cfg, *msg)
        return True
    except Exception as e:
        print(f'[login_assistance] admin notification could not be sent: {type(e).__name__}', flush=True)
        return False


def dispatch(job):
    if not job:
        return
    if SEND_IN_BACKGROUND:
        import threading
        threading.Thread(target=job, daemon=True).start()
    else:
        job()


# ── Admin ──────────────────────────────────────────────────────────────────────

def _load_for_update(cur, request_id):
    cur.execute("SELECT * FROM login_assistance_request WHERE id = %s FOR UPDATE", (request_id,))
    req = cur.fetchone()
    if not req:
        raise AssistanceError(404, 'Support request not found.')
    return req


def _log_action(cur, request_id, admin, text):
    stamp = datetime.now(login_security.PH_TZ).strftime('%b %d, %Y %I:%M %p')
    cur.execute("""UPDATE login_assistance_request
                   SET actions_taken = CONCAT_WS(E'\\n', actions_taken, %s),
                       handled_by = %s, updated_at = NOW()
                   WHERE id = %s""", (f'{stamp} — {text} ({admin})', admin, request_id))


def update_status(conn, request_id, admin, status, remarks, resolution):
    status = (status or '').strip()
    remarks = (remarks or '').strip() or None
    resolution = (resolution or '').strip() or None
    if status not in STATUSES:
        raise AssistanceError(400, 'Please choose a valid status.')
    if status == 'Resolved' and not resolution:
        raise AssistanceError(400, 'Please describe how the request was resolved.')
    for label, value in (('Remarks', remarks), ('Resolution details', resolution)):
        if value and len(value) > REMARKS_MAX:
            raise AssistanceError(400, f'{label} are too long (maximum {REMARKS_MAX} characters).')
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        req = _load_for_update(cur, request_id)
        cur.execute("""UPDATE login_assistance_request
                       SET status = %s, admin_remarks = %s, resolution = %s, handled_by = %s, updated_at = NOW(),
                           resolved_at = CASE WHEN %s = 'Resolved' THEN COALESCE(resolved_at, NOW()) END
                       WHERE id = %s""", (status, remarks, resolution, admin, status, request_id))
        if status != req['status']:
            _log_action(cur, request_id, admin, f'Status changed from {req["status"]} to {status}')
        conn.commit()
        return req['reference']
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def verify_identity(conn, request_id, admin, method, notes):
    method = (method or '').strip()
    notes = (notes or '').strip() or None
    if method not in VERIFY_METHODS:
        raise AssistanceError(400, 'Please choose how the identity was verified.')
    if method == VERIFY_METHODS[-1] and not notes:
        raise AssistanceError(400, 'Please describe how the identity was verified.')
    if notes and len(notes) > REMARKS_MAX:
        raise AssistanceError(400, f'Notes are too long (maximum {REMARKS_MAX} characters).')
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        req = _load_for_update(cur, request_id)
        if not req['userid']:
            raise AssistanceError(400, 'This request does not match any account, so there is no identity to '
                                       'verify. Contact the person to confirm their username or Employee ID.')
        cur.execute("""UPDATE login_assistance_request
                       SET identity_method = %s, identity_notes = %s, identity_verified_by = %s,
                           identity_verified_at = NOW(),
                           status = CASE WHEN status = 'Pending' THEN 'In Progress' ELSE status END
                       WHERE id = %s""", (method, notes, admin, request_id))
        _log_action(cur, request_id, admin, f'Identity verified: {method}')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _verified_account(cur, request_id):
    req = _load_for_update(cur, request_id)
    if not req['userid']:
        raise AssistanceError(400, 'This request does not match any account.')
    if not req['identity_verified_at']:
        raise AssistanceError(403, 'Verify the person\'s identity first, before any account recovery action.',
                              'not_verified')
    if req['status'] == 'Resolved':
        raise AssistanceError(400, 'This request is already resolved. Reopen it first.')
    cur.execute("SELECT userid, username, role FROM accounts WHERE userid = %s", (req['userid'],))
    acc = cur.fetchone()
    if not acc:
        raise AssistanceError(400, 'The matched account no longer exists.')
    return req, acc


def _clear_lock(cur, username):
    cur.execute("""UPDATE login_throttle SET failed_count = 0, last_failed_at = NULL,
                          locked_at = NULL, locked_until = NULL
                   WHERE username_key = %s""", (login_security.username_key(username),))


def clear_lock(conn, request_id, admin):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        req, acc = _verified_account(cur, request_id)
        _clear_lock(cur, acc['username'])
        _log_action(cur, request_id, admin, 'Temporary login lock cleared')
        conn.commit()
        return acc['username']
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def reset_to_temporary_password(cur, userid, username):
    """Temporary password PUP@<username> + required change at next login. Shared with
    User Management's Reset Password. Pending Forgot Password codes stop working."""
    cur.execute("UPDATE accounts SET passwordhash = %s, must_change_password = TRUE WHERE userid = %s",
                (generate_password_hash(f"PUP@{username}"), userid))
    cur.execute("""UPDATE password_reset_requests
                   SET invalidated_at = NOW(), code_hash = NULL, reset_token_hash = NULL
                   WHERE userid = %s AND used_at IS NULL AND invalidated_at IS NULL""", (userid,))


def reset_password(conn, request_id, admin):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        req, acc = _verified_account(cur, request_id)
        reset_to_temporary_password(cur, acc['userid'], acc['username'])
        _clear_lock(cur, acc['username'])
        _log_action(cur, request_id, admin, 'Password reset to the temporary password; login lock cleared')
        conn.commit()
        return acc['username']
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
