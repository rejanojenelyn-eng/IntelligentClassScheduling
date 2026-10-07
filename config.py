import os

class Config:
    
    DB_NAME = "ASDBv11"
    DB_USER = "postgres"
    DB_PASS = "050105"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    
    SECRET_KEY = os.urandom(24)


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
