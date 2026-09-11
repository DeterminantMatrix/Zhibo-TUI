from pathlib import Path


USERSCRIPT = Path(__file__).parent.parent / "userscripts" / "fs1-auth-export.user.js"


def test_fs1_userscript_has_strict_runtime_domain_gate_and_versioned_handoff():
    text = USERSCRIPT.read_text(encoding="utf-8")

    assert "(?:fszb|fs)\\d+\\.com" in text
    assert "@include      /^https:" in text
    assert "@match        https://fs*.com/*" not in text
    assert 'const FORMAT = "zhibo.fs1-auth"' in text
    assert 'const VERSION = 2' in text
    assert 'const API_PATH = "/v1/room"' in text
    assert 'const REQUEST_EVENT = "zhibo-fs1-auth-request"' in text
    assert 'const RESPONSE_EVENT = "zhibo-fs1-auth-response"' in text
    assert 'request_snapshot' in text
    assert 'captured_requests' in text
    assert 'match_id' in text
    assert 'device2' in text
    assert 'PLAY_PATH = "/v230/play/url"' in text
    assert "window.setTimeout" in text


def test_fs1_userscript_does_not_send_credentials_to_a_network_receiver():
    text = USERSCRIPT.read_text(encoding="utf-8")

    assert "GM_xmlhttpRequest" not in text
    assert "127.0.0.1" not in text
    assert "localhost" not in text
    assert "GM_setClipboard" in text
