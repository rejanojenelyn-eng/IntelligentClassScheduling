import os

# Secrets come from environment variables — never from this file.
#   • Railway: set them under the service's Variables tab.
#   • Local:   put them in a .env file next to this one (see .env.example);
#              it is loaded here when python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass


def _flag(name, default='0'):
    return os.environ.get(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


class Config:

    # Database. On Railway set DATABASE_URL to Neon's connection string
    # (postgresql://user:pass@host/db?sslmode=require); it takes precedence.
    # Locally the separate DB_* variables are used.
    DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
    DB_NAME = os.environ.get('DB_NAME', 'ASDBv11')
    DB_USER = os.environ.get('DB_USER', 'postgres')
    DB_PASS = os.environ.get('DB_PASS', '')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')

    # Signs the login session cookie. Must be a fixed value in production:
    # a random key logs everyone out (and breaks multi-step flows such as
    # Forgot Password) on every restart / redeploy.
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24)

    # Behind Railway's proxy the client IP comes from X-Forwarded-For
    # (used for rate limiting). Set TRUST_PROXY=1 on Railway only.
    TRUST_PROXY = _flag('TRUST_PROXY')

    # ── Outgoing email (verification codes) ──────────────────────────────
    # Railway blocks outbound SMTP below the Pro plan, so in production use
    # an HTTPS email API:
    #   Brevo  : BREVO_API_KEY  + EMAIL_FROM (a sender verified in Brevo)
    #   Resend : RESEND_API_KEY + EMAIL_FROM (an address on a domain verified in Resend)
    # SMTP (e.g. Gmail App Password) still works locally:
    #   SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=<address>
    #   SMTP_PASSWORD=<16-char App Password> SMTP_FROM=<address>
    # EMAIL_PROVIDER (brevo | resend | smtp) forces one; otherwise the first
    # configured of Brevo, Resend, SMTP is used.
    EMAIL_PROVIDER  = os.environ.get('EMAIL_PROVIDER', '').strip().lower()
    EMAIL_FROM      = os.environ.get('EMAIL_FROM', '').strip()
    EMAIL_FROM_NAME = os.environ.get('EMAIL_FROM_NAME', 'PUP Lopez Scheduling System').strip()
    BREVO_API_KEY   = os.environ.get('BREVO_API_KEY', '').strip()
    RESEND_API_KEY  = os.environ.get('RESEND_API_KEY', '').strip()

    SMTP_HOST     = os.environ.get('SMTP_HOST', '')
    SMTP_PORT     = int(os.environ.get('SMTP_PORT', '587') or 587)
    SMTP_USER     = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_FROM     = os.environ.get('SMTP_FROM', os.environ.get('SMTP_USER', ''))
    SMTP_USE_SSL  = os.environ.get('SMTP_USE_SSL', '0') == '1'     # port 465
    SMTP_STARTTLS = os.environ.get('SMTP_STARTTLS', '1') == '1'    # port 587
