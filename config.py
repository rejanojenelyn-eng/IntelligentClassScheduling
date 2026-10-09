import os


def _load_dotenv(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')):
    """Local development: read KEY=VALUE lines from .env (gitignored) into the
    environment. Variables already set (e.g. on Render) are never overridden."""
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


class Config:
    
    DB_NAME = "ASDBv11"
    DB_USER = "postgres"
    DB_PASS = "050105"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    
    SECRET_KEY = os.urandom(24)

    # Behind Render's proxy set TRUST_PROXY=1 so rate limiting uses the real client IP.
    TRUST_PROXY = os.environ.get('TRUST_PROXY', '0') == '1'


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
    SMTP_USE_SSL  = os.environ.get('SMTP_USE_SSL', '0') == '1'    
    SMTP_STARTTLS = os.environ.get('SMTP_STARTTLS', '1') == '1'   
