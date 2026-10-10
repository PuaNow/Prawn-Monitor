from prawn_monitor.notify import deliver_alert, sms_text


def test_sms_stays_short():
    text = sms_text({"severity": "critical", "message": "x" * 400})
    assert text.startswith("Prawn Monitor CRITICAL:")
    assert len(text) <= 300


def test_email_and_gateway_use_the_injected_smtp():
    sent = []

    def fake_smtp(cfg, message):
        sent.append((cfg["host"], message["To"], message["Subject"], message.get_content()))

    alert = {
        "severity": "critical",
        "kind": "dissolved_oxygen",
        "tank_name": "Nursery",
        "message": "Nursery dissolved oxygen 3.60 mg/L is under the critical limit of 4 mg/L.",
        "value": 3.6,
        "unit": "mg/L",
    }
    result = deliver_alert(
        alert,
        {
            "smtp": {"host": "smtp.local", "port": 587, "from": "prawn@local", "to": ["farm@example.com"]},
            "sms": {"mode": "email_gateway", "to": ["15555550123@vtext.com"]},
        },
        hub_url="http://hub.local",
        smtp_send=fake_smtp,
    )
    assert result["email"][0]["ok"] is True
    assert result["sms"][0]["via"] == "email_gateway"
    assert sent[0][1] == "farm@example.com"
    assert "CRITICAL" in sent[0][2]
    assert "http://hub.local" in sent[0][3]
    assert sent[1][1] == "15555550123@vtext.com"
    assert sent[1][2] == "Prawn"


def test_missing_smtp_is_skipped_and_twilio_posts_when_configured():
    skipped = deliver_alert(
        {"severity": "warning", "message": "check the tank", "kind": "ph", "tank_name": "Nursery"},
        {"smtp": {"host": "", "to": []}, "sms": {"mode": "off"}},
    )
    assert skipped["email"][0]["skipped"] is True
    assert skipped["sms"][0]["skipped"] is True

    posts = []

    def fake_http(url, data, username, password, timeout):
        posts.append((url, data.decode(), username, password))
        return {"status": 201}

    result = deliver_alert(
        {"severity": "warning", "message": "Nursery pH 7.10 is outside 7.5-8.3.", "kind": "ph", "tank_name": "Nursery"},
        {
            "smtp": {"host": "", "to": []},
            "sms": {
                "mode": "twilio",
                "twilio": {
                    "account_sid": "AC123",
                    "auth_token": "secret",
                    "from_number": "+15555550100",
                    "to": ["+15555550123"],
                },
            },
        },
        http_post=fake_http,
    )
    assert result["sms"][0]["ok"] is True
    assert "AC123" in posts[0][0]
    assert "To=%2B15555550123" in posts[0][1]
    assert posts[0][2] == "AC123"
    assert posts[0][3] == "secret"
