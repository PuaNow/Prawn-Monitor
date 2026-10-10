"""Email and SMS delivery. SMTP is optional; the hub still stores the alert."""

from __future__ import annotations

import json
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable


def sms_text(alert: dict[str, Any]) -> str:
    severity = str(alert.get("severity", "warning")).upper()
    message = str(alert.get("message", "")).strip()
    text = f"Prawn Monitor {severity}: {message}"
    if len(text) > 300:
        text = text[:297] + "..."
    return text


def email_message(alert: dict[str, Any], sender: str, recipients: list[str], hub_url: str = "") -> EmailMessage:
    severity = str(alert.get("severity", "warning")).upper()
    tank = alert.get("tank_name") or alert.get("tank_id") or "tank"
    kind = str(alert.get("kind", "alert")).replace("_", " ")
    msg = EmailMessage()
    msg["Subject"] = f"[Prawn Monitor] {severity} {tank} {kind}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    lines = [
        str(alert.get("message", "")),
        "",
        f"Severity: {severity}",
        f"Tank: {tank}",
        f"Kind: {alert.get('kind', '')}",
    ]
    if alert.get("value") is not None:
        lines.append(f"Value: {alert['value']} {alert.get('unit', '')}".rstrip())
    if hub_url:
        lines.extend(["", f"Dashboard: {hub_url}"])
    lines.extend(["", "Acknowledge the alert on the dashboard after you have checked the tank."])
    msg.set_content("\n".join(lines))
    return msg


def _smtp_deliver(smtp_cfg: dict[str, Any], message: EmailMessage) -> None:
    host = smtp_cfg.get("host") or ""
    port = int(smtp_cfg.get("port") or 587)
    with smtplib.SMTP(host, port, timeout=float(smtp_cfg.get("timeout_s") or 15)) as client:
        client.ehlo()
        if smtp_cfg.get("starttls", True):
            client.starttls()
            client.ehlo()
        username = smtp_cfg.get("username") or ""
        password = smtp_cfg.get("password") or ""
        if username:
            client.login(username, password)
        client.send_message(message)


def _twilio_post(url: str, data: bytes, username: str, password: str, timeout: float) -> dict[str, Any]:
    token = f"{username}:{password}".encode()
    import base64

    auth = base64.b64encode(token).decode("ascii")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode()
        return {"status": response.status, "body": body}


def deliver_alert(
    alert: dict[str, Any],
    notify_cfg: dict[str, Any] | None,
    *,
    hub_url: str = "",
    smtp_send: Callable[[dict[str, Any], EmailMessage], None] | None = None,
    http_post: Callable[[str, bytes, str, str, float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send one alert. Missing credentials are recorded as skipped, not raised."""
    cfg = notify_cfg or {}
    result: dict[str, Any] = {"email": [], "sms": []}
    smtp_cfg = dict(cfg.get("smtp") or {})
    sender = smtp_cfg.get("from") or "prawn-monitor@localhost"
    smtp_send = smtp_send or _smtp_deliver
    http_post = http_post or _twilio_post

    email_to = [addr for addr in (smtp_cfg.get("to") or []) if addr]
    if email_to and smtp_cfg.get("host"):
        message = email_message(alert, sender, email_to, hub_url)
        try:
            smtp_send(smtp_cfg, message)
            result["email"].append({"ok": True, "to": email_to})
        except Exception as exc:  # delivery must not drop the stored alert
            result["email"].append({"ok": False, "to": email_to, "error": str(exc)})
    else:
        result["email"].append({"ok": False, "skipped": True, "reason": "smtp not configured"})

    sms_cfg = dict(cfg.get("sms") or {})
    mode = (sms_cfg.get("mode") or "email_gateway").lower()
    if mode in ("off", "none", ""):
        result["sms"].append({"ok": False, "skipped": True, "reason": "sms disabled"})
        return result

    if mode == "twilio":
        twilio = dict(sms_cfg.get("twilio") or {})
        sid = twilio.get("account_sid") or ""
        token = twilio.get("auth_token") or ""
        from_number = twilio.get("from_number") or ""
        recipients = [n for n in (twilio.get("to") or []) if n]
        if not (sid and token and from_number and recipients):
            result["sms"].append({"ok": False, "skipped": True, "reason": "twilio not configured"})
            return result
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        for number in recipients:
            payload = urllib.parse.urlencode(
                {"To": number, "From": from_number, "Body": sms_text(alert)}
            ).encode()
            try:
                http_post(url, payload, sid, token, float(twilio.get("timeout_s") or 15))
                result["sms"].append({"ok": True, "to": number, "via": "twilio"})
            except Exception as exc:
                result["sms"].append({"ok": False, "to": number, "via": "twilio", "error": str(exc)})
        return result

    gateway_to = [addr for addr in (sms_cfg.get("to") or []) if addr]
    if gateway_to and smtp_cfg.get("host"):
        message = email_message(alert, sender, gateway_to, hub_url)
        message.replace_header("Subject", "Prawn")
        message.set_content(sms_text(alert))
        try:
            smtp_send(smtp_cfg, message)
            result["sms"].append({"ok": True, "to": gateway_to, "via": "email_gateway"})
        except Exception as exc:
            result["sms"].append(
                {"ok": False, "to": gateway_to, "via": "email_gateway", "error": str(exc)}
            )
    else:
        result["sms"].append(
            {"ok": False, "skipped": True, "reason": "sms email gateway not configured"}
        )
    return result


def delivery_json(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True)
