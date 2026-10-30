import api_service
import json
import threading
import urllib.error
import urllib.request


def test_jwt_round_trip_and_claims(monkeypatch):
    monkeypatch.setenv("EPFO_JWT_SECRET", "test-secret-long-enough")
    token, ttl = api_service.create_access_token("operator")
    claims = api_service.decode_access_token(token)
    assert ttl >= 60
    assert claims is not None
    assert claims["sub"] == "operator"
    assert "operator" in claims["roles"]


def test_jwt_tampering_is_rejected(monkeypatch):
    monkeypatch.setenv("EPFO_JWT_SECRET", "test-secret-long-enough")
    token, _ = api_service.create_access_token("operator")
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}x.{signature}"
    assert api_service.decode_access_token(tampered) is None


def test_structured_error_contract():
    assert api_service.error_response("VALIDATION_ERROR", "Invalid row.", details=["name"]) == {
        "error": {"code": "VALIDATION_ERROR", "message": "Invalid row.", "details": ["name"]}
    }


def test_http_login_issues_jwt_cookie_and_protects_api(monkeypatch):
    monkeypatch.setenv("EPFO_UI_USER", "admin-test")
    monkeypatch.setenv("EPFO_UI_PASSWORD", "password-test")
    monkeypatch.setenv("EPFO_JWT_SECRET", "test-secret-long-enough")
    server = api_service.LocalHTTPServer(("127.0.0.1", 0), api_service.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        body = json.dumps({"username": "admin-test", "password": "password-test"}).encode()
        request = urllib.request.Request(f"{base}/api/v1/auth/login", data=body, headers={"Content-Type": "application/json"})
        response = opener.open(request)
        payload = json.loads(response.read())
        assert payload["token_type"] == "Bearer"
        companies = json.loads(opener.open(f"{base}/api/v1/companies").read())
        assert companies["companies"]
        try:
            urllib.request.urlopen(f"{base}/api/v1/companies")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("Unauthenticated API request should be rejected")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
