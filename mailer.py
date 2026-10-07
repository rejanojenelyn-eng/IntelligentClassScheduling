"""
Outgoing email for the whole system (verification codes).

Providers, chosen by Config.EMAIL_PROVIDER or else the first one configured:
  • brevo  — HTTPS API (https://api.brevo.com/v3/smtp/email), BREVO_API_KEY
  • resend — HTTPS API (https://api.resend.com/emails), RESEND_API_KEY
  • smtp   — SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
Render blocks outbound SMTP ports on free instances, so production should use
an HTTPS provider. All credentials come from environment variables (config.py).
Only the standard library is used, so there is nothing extra to install.

Nothing here logs addresses, codes or keys; failures raise MailError with a
short reason that is safe to print.
"""
import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

_TIMEOUT = 20


class MailError(Exception):
    pass


def _sender(cfg):
    return (getattr(cfg, 'EMAIL_FROM', '') or getattr(cfg, 'SMTP_FROM', '') or '').strip()


def provider(cfg):
    """'brevo' | 'resend' | 'smtp' | None (not configured)."""
    forced = (getattr(cfg, 'EMAIL_PROVIDER', '') or '').strip().lower()
    available = {
        'brevo':  bool(getattr(cfg, 'BREVO_API_KEY', '') and _sender(cfg)),
        'resend': bool(getattr(cfg, 'RESEND_API_KEY', '') and _sender(cfg)),
        'smtp':   bool(getattr(cfg, 'SMTP_HOST', '') and _sender(cfg)),
    }
    if forced:
        return forced if available.get(forced) else None
    return next((p for p in ('brevo', 'resend', 'smtp') if available[p]), None)


def configured(cfg):
    return provider(cfg) is not None


def _post_json(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'Accept': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            if resp.status >= 300:
                raise MailError(f'HTTP {resp.status}')
    except urllib.error.HTTPError as e:
        raise MailError(f'HTTP {e.code}') from None
    except urllib.error.URLError as e:
        raise MailError(f'network error: {type(e.reason).__name__}') from None


def _send_brevo(cfg, to_addr, subject, text, html):
    payload = {'sender': {'email': _sender(cfg), 'name': cfg.EMAIL_FROM_NAME or _sender(cfg)},
               'to': [{'email': to_addr}], 'subject': subject, 'textContent': text}
    if html:
        payload['htmlContent'] = html
    _post_json('https://api.brevo.com/v3/smtp/email', {'api-key': cfg.BREVO_API_KEY}, payload)


def _send_resend(cfg, to_addr, subject, text, html):
    payload = {'from': formataddr((cfg.EMAIL_FROM_NAME or '', _sender(cfg))),
               'to': [to_addr], 'subject': subject, 'text': text}
    if html:
        payload['html'] = html
    _post_json('https://api.resend.com/emails', {'Authorization': f'Bearer {cfg.RESEND_API_KEY}'}, payload)


def _send_smtp(cfg, to_addr, subject, text, html):
    msg = EmailMessage()
    msg['Subject'] = subject
    name = getattr(cfg, 'EMAIL_FROM_NAME', '')
    msg['From'] = formataddr((name, _sender(cfg))) if name else _sender(cfg)
    msg['To'] = to_addr
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    port = int(cfg.SMTP_PORT or 587)
    try:
        if cfg.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(cfg.SMTP_HOST, port, context=ssl.create_default_context(), timeout=_TIMEOUT)
        else:
            server = smtplib.SMTP(cfg.SMTP_HOST, port, timeout=_TIMEOUT)
    except OSError as e:
        raise MailError(f'SMTP connect failed: {type(e).__name__}') from None
    try:
        if not cfg.SMTP_USE_SSL and cfg.SMTP_STARTTLS:
            server.starttls(context=ssl.create_default_context())
        if cfg.SMTP_USER:
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f'SMTP error: {type(e).__name__}') from None
    finally:
        try:
            server.quit()
        except Exception:
            pass


_SENDERS = {'brevo': _send_brevo, 'resend': _send_resend, 'smtp': _send_smtp}


def send_email(cfg, to_addr, subject, text, html=None):
    p = provider(cfg)
    if not p:
        raise MailError('email is not configured')
    _SENDERS[p](cfg, to_addr, subject, text, html)


def code_email_html(title, intro, code, minutes, footer):
    """Simple branded HTML body for a verification-code email."""
    from html import escape
    return f"""<!doctype html><html><body style="margin:0;background:#f6f1f1;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0;"><tr><td align="center">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;border:1px solid #ecd6d6;">
<tr><td style="background:#7a0100;color:#fff;padding:18px 24px;font-size:16px;font-weight:bold;">PUP Lopez Scheduling System</td></tr>
<tr><td style="padding:24px;color:#333;font-size:14px;line-height:1.6;">
<p style="margin:0 0 6px;font-size:18px;font-weight:bold;color:#7a0100;">{escape(title)}</p>
<p style="margin:0 0 16px;">{escape(intro)}</p>
<p style="margin:0 0 16px;text-align:center;"><span style="display:inline-block;letter-spacing:8px;font-size:30px;font-weight:bold;color:#4b0a0a;background:#fdf0f0;border:1px dashed #d9a3a3;border-radius:10px;padding:12px 20px;">{escape(code)}</span></p>
<p style="margin:0 0 12px;">This code expires in <b>{minutes} minutes</b> and can only be used once. Never share it with anyone.</p>
<p style="margin:0;color:#888;font-size:12px;">{escape(footer)}</p>
</td></tr></table></td></tr></table></body></html>"""
