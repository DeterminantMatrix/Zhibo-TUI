import logging
import sys
from logging.handlers import RotatingFileHandler

from zhibo import app_logging
from zhibo.app_logging import (
    _RedactingFilter,
    _RedactingFormatter,
    is_sensitive_field,
    redact_sensitive_mapping,
    redact_sensitive_text,
    redact_url,
)


def test_sensitive_field_matching_covers_headers_json_and_device_identifiers():
    assert is_sensitive_field("Authorization")
    assert is_sensitive_field("access_token")
    assert is_sensitive_field("X-API-Key")
    assert is_sensitive_field("dun_imei")
    assert is_sensitive_field("sessionId")
    assert is_sensitive_field("auth")
    assert is_sensitive_field("request_auth")
    assert is_sensitive_field("sign")
    assert is_sensitive_field("signing_key")
    assert is_sensitive_field("X-Authorization")
    assert is_sensitive_field("stream_key")
    assert not is_sensitive_field("content_type")


def test_redact_url_hides_userinfo_query_and_fragment_secrets():
    original = (
        "https://demo-user:demo-password@example.test/live?"
        "access_token=demo-token&safe=1&signature=demo-signature#sig=fragment-signature&tab=chat"
    )

    redacted = redact_url(original)

    for secret in ("demo-user", "demo-password", "demo-token", "demo-signature", "fragment-signature"):
        assert secret not in redacted
    assert "safe=1" in redacted
    assert "tab=chat" in redacted


def test_redact_url_hides_auth_and_sign_aliases():
    original = "https://example.test/live?auth=auth-secret&sign=sign-secret&safe=1#signed=fragment-secret"

    redacted = redact_url(original)

    for secret in ("auth-secret", "sign-secret", "fragment-secret"):
        assert secret not in redacted
    assert "safe=1" in redacted


def test_redact_sensitive_text_handles_headers_json_and_embedded_urls():
    text = (
        "authorization: Bearer demo-bearer token=demo-token\n"
        '{"cookie": "SID=demo-cookie", "imei": "demo-device", "safe": "visible"}\n'
        "https://example.test/live?sig=demo-signature&safe=1"
    )

    redacted = redact_sensitive_text(text)

    for secret in ("demo-bearer", "demo-token", "demo-cookie", "demo-device", "demo-signature"):
        assert secret not in redacted
    assert '"safe": "visible"' in redacted
    assert "safe=1" in redacted


def test_redact_sensitive_text_hides_all_cookie_header_values():
    redacted = redact_sensitive_text("Cookie: SID=demo-session; theme=demo-theme\nstatus=200")

    assert "demo-session" not in redacted
    assert "demo-theme" not in redacted
    assert "Cookie: ***" in redacted
    assert "status=200" in redacted


def test_redact_sensitive_text_hides_bearer_values_and_nonleading_cookie_headers():
    text = "request token: Bearer demo-token\nresponse headers -> Cookie: SID=demo-session; arbitrary=demo-value"

    redacted = redact_sensitive_text(text)

    for secret in ("demo-token", "demo-session", "demo-value"):
        assert secret not in redacted


def test_redact_sensitive_text_hides_a_bare_bearer_value_without_a_field_name():
    redacted = redact_sensitive_text("plugin returned Bearer bare-bearer-secret")

    assert "bare-bearer-secret" not in redacted


def test_redacting_formatter_hides_exception_tracebacks():
    formatter = _RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("token: Bearer traceback-token")
    except RuntimeError:
        record = logging.LogRecord(
            "zhibo.test",
            logging.ERROR,
            __file__,
            1,
            "poll failure",
            (),
            sys.exc_info(),
        )

    rendered = formatter.format(record)

    assert "traceback-token" not in rendered
    assert "traceback-token" not in (record.exc_text or "")
    assert "RuntimeError" in rendered


def test_setup_logging_upgrades_an_existing_handler_to_redact_tracebacks(tmp_path, monkeypatch):
    logger = logging.getLogger("zhibo")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers = []
    existing = RotatingFileHandler(tmp_path / "zhibo.log", encoding="utf-8")
    existing.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(existing)
    monkeypatch.setattr(app_logging, "LOG_FILE", tmp_path / "zhibo.log")
    monkeypatch.setattr(app_logging, "_CONFIGURED", False)

    try:
        app_logging.setup_logging()
        assert isinstance(existing.formatter, _RedactingFormatter)
        assert any(isinstance(filter_, _RedactingFilter) for filter_ in existing.filters)
        try:
            raise RuntimeError("token: Bearer existing-handler-token")
        except RuntimeError:
            logger.exception("poll failure")
        existing.flush()
        assert "existing-handler-token" not in (tmp_path / "zhibo.log").read_text(encoding="utf-8")
    finally:
        logger.removeHandler(existing)
        existing.close()
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_redact_sensitive_mapping_is_recursive_and_does_not_mutate_input():
    payload = {
        "token": "demo-token",
        "headers": {"Authorization": "Bearer demo-bearer", "Accept": "application/json"},
        "url": "https://example.test/?api_key=demo-key&safe=1",
        "items": [{"password": "demo-password"}],
    }

    redacted = redact_sensitive_mapping(payload)

    assert payload["token"] == "demo-token"
    assert payload["headers"]["Authorization"] == "Bearer demo-bearer"
    assert redacted["token"] == "***"
    assert redacted["headers"]["Authorization"] == "***"
    assert redacted["headers"]["Accept"] == "application/json"
    assert "demo-key" not in redacted["url"]
    assert "safe=1" in redacted["url"]
    assert redacted["items"][0]["password"] == "***"
