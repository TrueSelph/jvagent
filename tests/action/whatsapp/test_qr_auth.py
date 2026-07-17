"""WhatsApp QR / connection page must be admin-gated (once-over S1)."""

from jvagent.action.whatsapp import endpoints as wa_endpoints


def test_whatsapp_qr_requires_admin():
    cfg = wa_endpoints.whatsapp_connection_qr._jvspatial_endpoint_config
    assert cfg["auth_required"] is True
    assert "admin" in cfg["roles"]


def test_whatsapp_connection_page_requires_admin():
    cfg = wa_endpoints.whatsapp_connection_page._jvspatial_endpoint_config
    assert cfg["auth_required"] is True
    assert "admin" in cfg["roles"]


def test_email_send_requires_admin():
    from jvagent.action.email_action import endpoints as email_endpoints

    cfg = email_endpoints.email_send._jvspatial_endpoint_config
    assert cfg["auth_required"] is True
    assert "admin" in cfg["roles"]
