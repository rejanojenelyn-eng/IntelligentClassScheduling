"""
Login protection: failed-attempt limit, temporary lockout, per-IP rate limit and
the security-alert email. Used by app.login for every role (Faculty, Academic Head,
Admin).

    attempt()  one password login attempt, decided inside a row lock:
                 - while the username is locked, the password is not even checked;
                 - a correct password resets the counter;
                 - each wrong one counts; the MAX_FAILED_ATTEMPTS-th locks password
                   login for LOCKOUT_MINUTES (the account itself is never disabled);
                 - once the lock has expired the counter starts again from zero.

State lives in PostgreSQL, so refreshing the page, another browser or another device
cannot bypass it, and the row lock (SELECT ... FOR UPDATE) makes simultaneous
attempts count one by one.

Enumeration resistance: the counter is keyed by a SHA-256 of the lower-cased
username typed, whether or not that account exists, so an unknown username gets
exactly the same warnings and lockout as a real one (and nothing typed into the
username box -- possibly a password -- is stored in plain text). Only a real account
with an email on file receives the alert email.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash, generate_password_hash

import mailer


MAX_FAILED_ATTEMPTS   = 5
LOCKOUT_MINUTES       = 5
WARN_FROM_ATTEMPT     = 3      # this failed attempt onwards shows "N attempts remaining"
FAILURE_WINDOW_HOURS  = 24     # failures older than this no longer count as consecutive
IP_WINDOW_MINUTES     = 10
IP_MAX_FAILURES       = 30     # wrong passwords from one IP (any usernames) per window

PH_TZ = timezone(timedelta(hours=8))   # Philippine Standard Time (UTC+8, no DST)

SEND_IN_BACKGROUND = True              # tests switch this off to send inline

LOCKED_MESSAGE = ("Too many incorrect login attempts. Sign-in with this username is paused for "
                  f"{LOCKOUT_MINUTES} minutes. If you have forgotten your password, please use Forgot "
                  "Password or contact the System Administrator for assistance.")
IP_LIMIT_MESSAGE = ("Too many login attempts from this network. Please wait a few minutes and try again. "
                    "If you have forgotten your password, please use Forgot Password.")
INVALID_MESSAGE = "Invalid username or password."

# Compared against when the username does not exist, so the response takes as long
# as a real password check.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16), method='pbkdf2:sha256')

ENSURE_SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS login_throttle (
        username_key    CHAR(64)  PRIMARY KEY,         -- sha256 of the lower-cased username typed
        failed_count    SMALLINT  NOT NULL DEFAULT 0,  -- consecutive wrong passwords
        last_failed_at  TIMESTAMP NULL,
        locked_at       TIMESTAMP NULL,
        locked_until    TIMESTAMP NULL
    )""",
    """CREATE TABLE IF NOT EXISTS login_ip_failures (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ip         VARCHAR(64) NOT NULL,
        failed_at  TIMESTAMP   NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_login_ip_failures_ip_at ON login_ip_failures (ip, failed_at)",
]


def username_key(username):
    return hashlib.sha256((username or '').strip().lower().encode('utf-8')).hexdigest()


def password_matches(user, password):
    """Correct password for this account row (None = unknown username)."""
    if not user:
        check_password_hash(_DUMMY_HASH, password or '')
        return False
    return check_password_hash(str(user.get('passwordhash') or '').strip(), password or '')


def _purge(cur):
    cur.execute("DELETE FROM login_ip_failures WHERE failed_at < NOW() - INTERVAL '1 day'")
    cur.execute(f"""DELETE FROM login_throttle
                    WHERE (locked_until IS NULL OR locked_until < NOW())
                      AND (last_failed_at IS NULL OR last_failed_at < NOW() - INTERVAL '{FAILURE_WINDOW_HOURS} hours')""")


def attempt(conn, username, password, ip):
    """One login attempt. Returns a dict:
        {'result': 'ok', 'user': row}
        {'result': 'invalid', 'failed': n, 'remaining': k}
        {'result': 'locked', 'seconds_left': s, 'new_lock': bool, 'user': row or None}
        {'result': 'ip_limited'}
    The caller sends the alert email only when new_lock is True (once per lockout)."""
    key = username_key(username)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM login_ip_failures "
                    "WHERE ip = %s AND failed_at > NOW() - make_interval(mins => %s)",
                    (ip or '', IP_WINDOW_MINUTES))
        if cur.fetchone()['n'] >= IP_MAX_FAILURES:
            conn.commit()
            return {'result': 'ip_limited'}

        cur.execute("INSERT INTO login_throttle (username_key) VALUES (%s) ON CONFLICT DO NOTHING", (key,))
        cur.execute(f"""
            SELECT failed_count, locked_until,
                   GREATEST(0, CEIL(EXTRACT(EPOCH FROM (locked_until - NOW()))))::int AS lock_left,
                   (last_failed_at IS NOT NULL
                    AND last_failed_at < NOW() - INTERVAL '{FAILURE_WINDOW_HOURS} hours') AS stale
            FROM login_throttle WHERE username_key = %s FOR UPDATE
        """, (key,))
        row = cur.fetchone()
        if row['locked_until'] is not None and row['lock_left'] > 0:
            conn.commit()           # locked: the password is not checked at all
            return {'result': 'locked', 'seconds_left': row['lock_left'], 'new_lock': False, 'user': None}
        # An expired lock or old failures: start counting from zero again.
        failed = 0 if (row['locked_until'] is not None or row['stale']) else row['failed_count']

        cur.execute("""
            SELECT a.*, NULLIF(TRIM(a.email), '') AS notify_email
            FROM accounts a
            WHERE a.username = %s
        """, (username,))
        user = cur.fetchone()

        if password_matches(user, password):
            cur.execute("""UPDATE login_throttle SET failed_count = 0, last_failed_at = NULL,
                                  locked_at = NULL, locked_until = NULL
                           WHERE username_key = %s""", (key,))
            conn.commit()
            return {'result': 'ok', 'user': user}

        failed += 1
        cur.execute("INSERT INTO login_ip_failures (ip) VALUES (%s)", (ip or '',))
        if failed >= MAX_FAILED_ATTEMPTS:
            cur.execute("""UPDATE login_throttle
                           SET failed_count = %s, last_failed_at = NOW(), locked_at = NOW(),
                               locked_until = NOW() + make_interval(mins => %s)
                           WHERE username_key = %s""", (failed, LOCKOUT_MINUTES, key))
            _purge(cur)
            conn.commit()
            return {'result': 'locked', 'seconds_left': LOCKOUT_MINUTES * 60, 'new_lock': True, 'user': user}
        cur.execute("""UPDATE login_throttle
                       SET failed_count = %s, last_failed_at = NOW(), locked_at = NULL, locked_until = NULL
                       WHERE username_key = %s""", (failed, key))
        if secrets.randbelow(50) == 0:
            _purge(cur)
        conn.commit()
        return {'result': 'invalid', 'failed': failed, 'remaining': MAX_FAILED_ATTEMPTS - failed}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def invalid_message(failed, remaining):
    """The text shown after a wrong password that did not lock the account."""
    if failed >= WARN_FROM_ATTEMPT:
        return (f"{INVALID_MESSAGE} Warning: {remaining} attempt{'s' if remaining != 1 else ''} remaining "
                f"before sign-in with this username is paused for {LOCKOUT_MINUTES} minutes. "
                "Check that the username is your current Employee ID.")
    return INVALID_MESSAGE


def locked_message(seconds_left, new_lock):
    if new_lock:
        return LOCKED_MESSAGE
    minutes = max(1, -(-int(seconds_left) // 60))
    return ("Sign-in with this username is paused because of too many incorrect login attempts. "
            f"Please try again in about {minutes} minute{'s' if minutes != 1 else ''}. "
            "If you have forgotten your password, please use Forgot Password or contact the "
            "System Administrator for assistance.")


PRIVILEGED_ROLES = ('Admin', 'Academic Head')   # their lockouts also go to SECURITY_ALERT_EMAIL


def describe_device(user_agent):
    """'Chrome on Windows' style summary of a User-Agent ('' when unknown)."""
    ua = user_agent or ''
    if not ua:
        return ''
    browser = next((name for pat, name in (
        ('Edg/', 'Microsoft Edge'), ('OPR/', 'Opera'), ('SamsungBrowser', 'Samsung Internet'),
        ('FBAN', 'Facebook app'), ('FB_IAB', 'Facebook/Messenger app'), ('Firefox/', 'Firefox'),
        ('CriOS', 'Chrome'), ('Chrome/', 'Chrome'), ('Safari/', 'Safari')) if pat in ua), 'a web browser')
    system = next((name for pat, name in (
        ('Windows', 'Windows'), ('Android', 'Android'), ('iPhone', 'iPhone'), ('iPad', 'iPad'),
        ('Mac OS X', 'macOS'), ('CrOS', 'ChromeOS'), ('Linux', 'Linux')) if pat in ua), '')
    return f'{browser} on {system}' if system else browser


def lockout_email(cfg, user, when=None, ip=None, device=None, to_addr=None, for_owner=True):
    """(to, subject, text, html) for the security alert, or None when there is no address.
    for_owner=False words it for the security inbox (SECURITY_ALERT_EMAIL) instead of the
    account's owner. Never includes passwords, codes or anything typed at login."""
    to_addr = to_addr or (user or {}).get('notify_email')
    if not to_addr:
        return None
    when = (when or datetime.now(PH_TZ)).astimezone(PH_TZ)
    stamp = when.strftime('%B %d, %Y at %I:%M %p').replace(' 0', ' ') + ' (Philippine Standard Time)'
    uname = (user or {}).get('username')
    role = (user or {}).get('role') or 'user'
    source = ' and '.join(x for x in (f'IP address {ip}' if ip else '', device or '') if x)
    from_line = f' The attempts came from {source}.' if source else ''
    if for_owner:
        subject = 'Security alert: someone tried to sign in to your PUP Lopez Scheduling System account'
        title = 'Someone tried to sign in to your account'
        paragraphs = [
            f"On {stamp}, there were {MAX_FAILED_ATTEMPTS} consecutive incorrect password attempts to sign in "
            f"to your account (username: {uname}).{from_line}",
            f"To protect your account, password login was temporarily locked for {LOCKOUT_MINUTES} minutes. "
            "You can sign in again after that. Your account and your data were not changed.",
            "If these attempts were not made by you, please change your password as soon as you can "
            "(Forgot Password on the login page, or Profile > Password after you sign in) and let the "
            "System Administrator know.",
            "If it was you, no action is needed.",
        ]
    else:
        subject = f'Security alert: repeated sign-in attempts on the {role} account "{uname}"'
        title = f'Repeated sign-in attempts on the {role} account'
        paragraphs = [
            f"On {stamp}, there were {MAX_FAILED_ATTEMPTS} consecutive incorrect password attempts to sign in "
            f"to the {role} account \"{uname}\".{from_line}",
            f"Password login for this account was temporarily locked for {LOCKOUT_MINUTES} minutes. "
            "The account and its data were not changed.",
            "If the account's owner did not make these attempts, ask them to change their password and "
            "review Settings > Activity Logs (Security) for other attempts.",
        ]
    text = 'Security alert\n\n' + '\n\n'.join(paragraphs) + \
           '\n\nThis is an automated message. Please do not reply to this email.\n'
    html = mailer.notice_email_html(title, paragraphs,
                                    'This is an automated message. Please do not reply to this email.')
    return to_addr, subject, text, html


def lockout_messages(cfg, user, when=None, ip=None, device=None):
    """Every alert this lockout sends: to the account's own email and, for an Admin /
    Academic Head account, to the security inbox (SECURITY_ALERT_EMAIL) too."""
    msgs = []
    own = lockout_email(cfg, user, when, ip, device)
    if own:
        msgs.append(own)
    inbox = (getattr(cfg, 'SECURITY_ALERT_EMAIL', '') or '').strip()
    if inbox and (user or {}).get('role') in PRIVILEGED_ROLES \
            and inbox.lower() != str((user or {}).get('notify_email') or '').strip().lower():
        msgs.append(lockout_email(cfg, user, when, ip, device, to_addr=inbox, for_owner=False))
    return msgs


def send_lockout_email(cfg, user, when=None, ip=None, device=None):
    """Send the alert(s); a failure is logged (without the address) and never raised, so a
    mail problem can't undo or block the lockout."""
    try:
        msgs = lockout_messages(cfg, user, when, ip, device)
        if not msgs:
            return False
        if not mailer.configured(cfg):
            print('[login_security] lockout alert not sent: email is not configured', flush=True)
            return False
        for msg in msgs:
            mailer.send_email(cfg, *msg)
        return True
    except Exception as e:
        print(f'[login_security] lockout alert could not be sent: {type(e).__name__}', flush=True)
        return False


def dispatch_lockout_email(cfg, user, ip=None, user_agent=None):
    when = datetime.now(PH_TZ)
    device = describe_device(user_agent)
    if SEND_IN_BACKGROUND:
        import threading
        threading.Thread(target=send_lockout_email, args=(cfg, user, when, ip, device), daemon=True).start()
    else:
        send_lockout_email(cfg, user, when, ip, device)
