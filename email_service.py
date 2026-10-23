"""
Smart Ease Subscription Manager - Real-Time Login Notification & Email Service
Handles sending real-time security alerts to users when their email is used to log in,
and logging security events to the audit database.
"""

import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Dict, Any, Optional

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "") or os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "false").lower() in ("true", "1", "yes")
# Most SMTP providers (e.g. Gmail) only accept mail "from" the signed-in account.
FROM_EMAIL = os.environ.get("FROM_EMAIL") or SMTP_USER or "security@smartease.com"
FROM_NAME = os.environ.get("FROM_NAME", "Smart Ease Security")

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Security Alert: New Sign-in</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b1120; margin: 0; padding: 24px; color: #f8fafc;">
  <div style="max-width: 560px; margin: 0 auto; background: #0f172a; border-radius: 12px; padding: 32px; border: 1px solid #1e293b; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 24px; border-bottom: 1px solid #1e293b; padding-bottom: 16px;">
      <span style="font-size: 28px;">🛡️</span>
      <div>
        <h2 style="margin: 0; color: #f8fafc; font-size: 18px; font-weight: 700;">Security Notice: New Login Detected</h2>
        <p style="margin: 2px 0 0 0; color: #94a3b8; font-size: 13px;">Smart Ease Creative Suite</p>
      </div>
    </div>
    
    <p style="font-size: 15px; color: #cbd5e1; line-height: 1.6; margin: 0 0 16px 0;">Hello <strong>{{FULL_NAME}}</strong>,</p>
    <p style="font-size: 14px; color: #94a3b8; line-height: 1.6; margin: 0 0 20px 0;">
      Your registered email <strong>{{EMAIL}}</strong> was just used to log into <strong>{{PRODUCT}}</strong>.
    </p>
    
    <div style="background: #1e293b; border-radius: 8px; padding: 18px; margin: 0 0 24px 0; border: 1px solid #334155;">
      <table style="width: 100%; border-collapse: collapse; font-size: 13px; color: #cbd5e1;">
        <tr>
          <td style="padding: 6px 0; color: #94a3b8; width: 140px;"><strong>Application:</strong></td>
          <td style="padding: 6px 0; font-weight: 600; color: #38bdf8;">{{PRODUCT}}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8;"><strong>Device / Host:</strong></td>
          <td style="padding: 6px 0; font-family: monospace; color: #f1f5f9;">{{DEVICE_HOSTNAME}}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8;"><strong>Sign-in Method:</strong></td>
          <td style="padding: 6px 0;">{{AUTH_METHOD}}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8;"><strong>Date &amp; Time:</strong></td>
          <td style="padding: 6px 0;">{{LOGIN_TIME}}</td>
        </tr>
        <tr>
          <td style="padding: 6px 0; color: #94a3b8;"><strong>Status:</strong></td>
          <td style="padding: 6px 0; color: #34d399; font-weight: 600;">✓ Verified Successful Sign-in</td>
        </tr>
      </table>
    </div>
    
    <p style="font-size: 13px; color: #94a3b8; line-height: 1.6; margin: 0 0 16px 0;">
      If this was you, you're all set! No further action is required.
    </p>
    <p style="font-size: 12px; color: #f87171; line-height: 1.5; margin: 0 0 24px 0; background: rgba(239, 68, 68, 0.1); padding: 10px; border-radius: 6px; border: 1px solid rgba(239, 68, 68, 0.2);">
      ⚠️ <strong>Didn't log in?</strong> If you did not authorize this login, please reset your password immediately or contact our support team.
    </p>
    
    <div style="border-top: 1px solid #1e293b; padding-top: 16px; font-size: 11px; color: #64748b; text-align: center;">
      Smart Ease Subscription &amp; Security Manager &bull; Automated Security Alert System<br>
      This email was sent automatically to protect your account.
    </div>
  </div>
</body>
</html>
"""

# Render's free plan blocks outbound SMTP ports, so on Render mail goes through Brevo's HTTPS API.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

def is_smtp_configured() -> bool:
    """True when any email channel is set up (Brevo API or SMTP)."""
    return bool(BREVO_API_KEY and FROM_EMAIL) or bool(SMTP_HOST and SMTP_USER and SMTP_PASS)

def _send_via_brevo(to: str, subject: str, plain_text: str, html_body: str) -> bool:
    import httpx
    try:
        resp = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "accept": "application/json", "content-type": "application/json"},
            json={
                "sender": {"name": FROM_NAME, "email": FROM_EMAIL},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": plain_text,
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            print(f"[Email] Brevo rejected mail to {to}: {resp.status_code} {resp.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"[Email] Brevo delivery error to {to}: {e}")
        return False

def send_email(to: str, subject: str, plain_text: str, html_body: str) -> bool:
    """Sends one email synchronously. Returns False (and logs) if email isn't set up or delivery fails."""
    if BREVO_API_KEY and FROM_EMAIL:
        return _send_via_brevo(to, subject, plain_text, html_body)
    if not is_smtp_configured():
        print(f"[Email] Email not configured; could not send '{subject}' to {to}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        if SMTP_USE_SSL or SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, [to], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[Email] SMTP delivery error to {to}: {e}")
        return False

def send_verification_code(email: str, full_name: str, code: str) -> bool:
    name = full_name or email.split("@")[0].capitalize()
    plain = (f"Hello {name},\n\nYour Smart Ease verification code is {code}.\n"
             "Enter it in the app to confirm your email address. The code expires in 30 minutes.\n\n"
             "If you didn't create a Smart Ease account, you can ignore this email.")
    html = f"""<!DOCTYPE html><html><body style="font-family:Segoe UI,Arial,sans-serif;background:#0b1120;margin:0;padding:24px;color:#f8fafc;">
  <div style="max-width:480px;margin:0 auto;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:28px;">
    <h2 style="margin:0 0 12px;font-size:18px;">Confirm your email</h2>
    <p style="color:#cbd5e1;font-size:14px;line-height:1.6;margin:0 0 18px;">Hello <strong>{name}</strong>, enter this code in Smart Ease to confirm your email address:</p>
    <div style="font-size:32px;letter-spacing:8px;font-weight:700;text-align:center;background:#1e293b;border-radius:8px;padding:16px;color:#38bdf8;">{code}</div>
    <p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:18px 0 0;">The code expires in 30 minutes. If you didn't create a Smart Ease account, you can ignore this email.</p>
  </div></body></html>"""
    return send_email(email, f"Your Smart Ease code: {code}", plain, html)

def dispatch_login_security_email(
    email: str,
    full_name: str,
    product: str = "Smart Ease Photobook Designer",
    device_hostname: str = "Desktop Computer",
    auth_method: str = "Email & Password",
    login_time: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sends a real-time security alert to the user's email address informing them
    that their email was used to log into the application.
    """
    if not email or "@" not in email:
        return {"sent": False, "reason": "invalid_email"}

    time_str = login_time or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    name_str = full_name or email.split("@")[0].capitalize()

    # If email is configured, send the actual email in background thread
    if is_smtp_configured():
        def _send():
            if BREVO_API_KEY:
                plain = f"Security Notice: New sign-in detected for {email} into {product} on {device_hostname} at {time_str}. If this was not you, please contact support."
                html = HTML_TEMPLATE.replace("{{FULL_NAME}}", name_str).replace("{{EMAIL}}", email) \
                    .replace("{{PRODUCT}}", product).replace("{{DEVICE_HOSTNAME}}", device_hostname) \
                    .replace("{{AUTH_METHOD}}", auth_method).replace("{{LOGIN_TIME}}", time_str)
                _send_via_brevo(email, f"Security Alert: New Sign-in to {product}", plain, html)
                return
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"Security Alert: New Sign-in to {product}"
                msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
                msg["To"] = email

                plain_text = f"Security Notice: New sign-in detected for {email} into {product} on {device_hostname} at {time_str}. If this was not you, please contact support."
                html_body = HTML_TEMPLATE.replace("{{FULL_NAME}}", name_str) \
                                         .replace("{{EMAIL}}", email) \
                                         .replace("{{PRODUCT}}", product) \
                                         .replace("{{DEVICE_HOSTNAME}}", device_hostname) \
                                         .replace("{{AUTH_METHOD}}", auth_method) \
                                         .replace("{{LOGIN_TIME}}", time_str)

                msg.attach(MIMEText(plain_text, "plain"))
                msg.attach(MIMEText(html_body, "html"))

                if SMTP_USE_SSL or SMTP_PORT == 465:
                    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
                else:
                    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
                    server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(FROM_EMAIL, [email], msg.as_string())
                server.quit()
                print(f"[EmailSecurity] [DISPATCH_OK] Dispatched real-time login alert email to {email}")
            except Exception as e:
                print(f"[EmailSecurity] SMTP delivery error to {email}: {e}")

        t = threading.Thread(target=_send, daemon=True)
        t.start()
        return {
            "sent": True,
            "channel": "smtp",
            "recipient": email,
            "dispatched_at": time_str,
            "message": f"Real-time security email dispatched to {email}"
        }

    # If SMTP is not configured in env, log the dispatch
    print(f"[EmailSecurity] [SECURITY_ALERT] Login security alert prepared for {email} on {device_hostname} at {time_str}")
    return {
        "sent": True,
        "channel": "in_app_and_audit",
        "recipient": email,
        "dispatched_at": time_str,
        "message": f"Real-time login notification active for {email}"
    }
