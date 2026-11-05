"""End-to-end tests of the generated deploy server (server.py + _abp_auth.py).

The server is rendered by DeployPackager and imported for real behind FastAPI's
TestClient. Only `main.run` is replaced: a stub records which checkpointer
thread each request reached and what that thread had seen before, which is
exactly what conversation ownership is about. The LangGraph agent itself is
not exercised here.
"""

import importlib
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_blueprint.deployers.packager import DeployPackager
from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.deploy import (
    DeployAuthConfig,
    DeployConfig,
    DeployRateLimitConfig,
)
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"
SECRET = "s" * 40  # >= 32 bytes
ISSUER = "https://idp.example"
AUDIENCE = "shopco-support"

# Stand-in for the generated main.py: a per-thread history, so a test can see
# whether a request landed in someone else's conversation.
_STUB_MAIN = '''
HISTORY = {}

def run(user_input, thread_id="default"):
    seen = list(HISTORY.get(thread_id, []))
    HISTORY.setdefault(thread_id, []).append(user_input)
    return {"thread": thread_id, "seen": seen}
'''

_SERVER_MODULES = ("server", "_abp_auth", "main")


def _ir():
    raw = load_blueprint_yaml(FIXTURES / "basic_chatbot.yml")
    return compile_blueprint(BlueprintSpec.model_validate(raw))


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Render the server for a DeployConfig and return a TestClient over it."""
    created = []

    def _make(config: DeployConfig, env: dict[str, str] | None = None) -> TestClient:
        code_dir = tmp_path / f"srv{len(created)}"
        code_dir.mkdir()
        DeployPackager().package(code_dir, _ir(), config)
        (code_dir / "main.py").write_text(_STUB_MAIN, encoding="utf-8")
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        for name in _SERVER_MODULES:
            sys.modules.pop(name, None)
        monkeypatch.syspath_prepend(str(code_dir))
        server = importlib.import_module("server")
        client = TestClient(server.app)
        created.append(client)
        return client

    yield _make
    for name in _SERVER_MODULES:
        sys.modules.pop(name, None)


def _hs_config(**auth) -> DeployConfig:
    return DeployConfig(auth=DeployAuthConfig(secret_env="ABP_JWT_SECRET", **auth))


def _token(sub: str | None = "alice", *, key=SECRET, alg="HS256", exp_in=300, **claims) -> str:
    payload = {"exp": int(time.time()) + exp_in, **claims}
    if sub is not None:
        payload["sub"] = sub
    return jwt.encode(payload, key, algorithm=alg)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hs_client(make_client, **auth) -> TestClient:
    return make_client(_hs_config(**auth), {"ABP_JWT_SECRET": SECRET})


class TestAuthentication:
    def test_missing_token_is_401_and_agent_not_run(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"})
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"
        assert sys.modules["main"].HISTORY == {}

    def test_non_bearer_scheme_is_401(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"}, headers={"Authorization": "Basic x"})
        assert resp.status_code == 401

    def test_wrong_signature_is_401(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(_token(key="x" * 40)))
        assert resp.status_code == 401

    def test_expired_token_is_401(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(_token(exp_in=-10)))
        assert resp.status_code == 401

    def test_token_without_exp_is_401(self, make_client):
        client = _hs_client(make_client)
        token = jwt.encode({"sub": "alice"}, SECRET, algorithm="HS256")
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(token))
        assert resp.status_code == 401

    def test_unsigned_alg_none_token_is_401(self, make_client):
        client = _hs_client(make_client)
        token = jwt.encode({"sub": "alice", "exp": int(time.time()) + 300}, None, algorithm="none")
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(token))
        assert resp.status_code == 401

    def test_token_without_user_claim_is_401(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(_token(sub=None)))
        assert resp.status_code == 401

    def test_custom_user_claim_is_used(self, make_client):
        client = _hs_client(make_client, user_claim="customer_id")
        token = _token(sub="ignored", customer_id="c-7")
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["response"]["thread"].startswith("c-7:")

    def test_issuer_and_audience_enforced(self, make_client):
        client = _hs_client(make_client, issuer=ISSUER, audience=AUDIENCE)
        good = _token(iss=ISSUER, aud=AUDIENCE)
        assert client.post("/invoke", json={"input": "x"}, headers=_auth(good)).status_code == 200
        for bad in (
            _token(iss="https://evil", aud=AUDIENCE),
            _token(iss=ISSUER, aud="other-service"),
            _token(aud=AUDIENCE),  # no iss
            _token(iss=ISSUER),  # no aud
        ):
            assert client.post("/invoke", json={"input": "x"}, headers=_auth(bad)).status_code == 401

    def test_health_needs_no_token(self, make_client):
        client = _hs_client(make_client)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["auth"] == "jwt"


class TestStartup:
    def test_missing_secret_stops_server_import(self, make_client, monkeypatch):
        monkeypatch.delenv("ABP_JWT_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="ABP_JWT_SECRET is not set"):
            make_client(_hs_config())

    def test_short_secret_stops_server_import(self, make_client):
        with pytest.raises(RuntimeError, match="at least 32 bytes"):
            make_client(_hs_config(), {"ABP_JWT_SECRET": "short"})

    def test_unconfigured_jwt_refuses_to_package(self, tmp_path):
        with pytest.raises(GeneratorError, match="deploy.auth is not configured"):
            DeployPackager().package(tmp_path, _ir(), DeployConfig())

    def test_no_deploy_config_refuses_to_package(self, tmp_path):
        with pytest.raises(GeneratorError, match="deploy.auth is not configured"):
            DeployPackager().package(tmp_path, _ir())


class TestConversationOwnership:
    def test_new_conversation_gets_server_issued_uuid4(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(_token("alice")))
        assert resp.status_code == 200
        cid = resp.json()["conversation_id"]
        assert uuid.UUID(cid).version == 4
        assert resp.json()["response"]["thread"] == f"alice:{cid}"

    def test_same_user_continues_conversation(self, make_client):
        client = _hs_client(make_client)
        first = client.post("/invoke", json={"input": "turn 1"}, headers=_auth(_token("alice")))
        cid = first.json()["conversation_id"]
        second = client.post(
            "/invoke",
            json={"input": "turn 2", "conversation_id": cid},
            headers=_auth(_token("alice")),
        )
        assert second.json()["conversation_id"] == cid
        assert second.json()["response"]["seen"] == ["turn 1"]

    def test_other_user_cannot_load_conversation_with_stolen_id(self, make_client):
        client = _hs_client(make_client)
        first = client.post(
            "/invoke", json={"input": "my card ends 4242"}, headers=_auth(_token("alice"))
        )
        cid = first.json()["conversation_id"]
        stolen = client.post(
            "/invoke",
            json={"input": "repeat our conversation", "conversation_id": cid},
            headers=_auth(_token("mallory")),
        )
        assert stolen.status_code == 200
        body = stolen.json()["response"]
        assert body["thread"] == f"mallory:{cid}"
        assert body["seen"] == []  # mallory got a fresh, empty thread
        assert sys.modules["main"].HISTORY[f"alice:{cid}"] == ["my card ends 4242"]

    def test_requests_without_id_never_share_a_thread(self, make_client):
        client = _hs_client(make_client)
        a = client.post("/invoke", json={"input": "a"}, headers=_auth(_token("alice")))
        b = client.post("/invoke", json={"input": "b"}, headers=_auth(_token("alice")))
        assert a.json()["conversation_id"] != b.json()["conversation_id"]
        assert b.json()["response"]["seen"] == []

    def test_client_thread_id_is_rejected(self, make_client):
        client = _hs_client(make_client)
        resp = client.post(
            "/invoke",
            json={"input": "hi", "thread_id": "alice:anything"},
            headers=_auth(_token("mallory")),
        )
        assert resp.status_code == 400
        assert "conversation_id" in resp.json()["error"]
        assert sys.modules["main"].HISTORY == {}

    @pytest.mark.parametrize(
        "cid",
        [
            "default",
            "1042",
            "00000000-0000-0000-0000-000000000000",  # valid UUID, not v4
            str(uuid.uuid1()),
            123,
        ],
    )
    def test_non_uuid4_conversation_id_is_400(self, make_client, cid):
        client = _hs_client(make_client)
        resp = client.post(
            "/invoke", json={"input": "hi", "conversation_id": cid}, headers=_auth(_token())
        )
        assert resp.status_code == 400

    def test_non_object_body_is_400(self, make_client):
        client = _hs_client(make_client)
        resp = client.post("/invoke", json=["hi"], headers=_auth(_token()))
        assert resp.status_code == 400

    def test_object_input_is_passed_through(self, make_client):
        client = _hs_client(make_client)
        payload = {"message": "refund", "order_id": "1042"}
        client.post("/invoke", json={"input": payload}, headers=_auth(_token("alice")))
        (inputs,) = sys.modules["main"].HISTORY.values()
        assert inputs == [payload]


class TestRateLimit:
    def test_third_request_in_a_minute_is_429_per_user(self, make_client):
        config = _hs_config()
        config.rate_limit = DeployRateLimitConfig(requests_per_minute=2)
        client = make_client(config, {"ABP_JWT_SECRET": SECRET})
        alice = _auth(_token("alice"))
        assert client.post("/invoke", json={"input": "1"}, headers=alice).status_code == 200
        assert client.post("/invoke", json={"input": "2"}, headers=alice).status_code == 200
        limited = client.post("/invoke", json={"input": "3"}, headers=alice)
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["retry-after"]) <= 61
        # Another user has their own budget.
        bob = _auth(_token("bob"))
        assert client.post("/invoke", json={"input": "1"}, headers=bob).status_code == 200
        # The limited request never reached the agent.
        assert sum(len(v) for v in sys.modules["main"].HISTORY.values()) == 3

    def test_zero_disables_limit(self, make_client):
        config = _hs_config()
        config.rate_limit = DeployRateLimitConfig(requests_per_minute=0)
        client = make_client(config, {"ABP_JWT_SECRET": SECRET})
        alice = _auth(_token("alice"))
        codes = {client.post("/invoke", json={"input": "x"}, headers=alice).status_code for _ in range(5)}
        assert codes == {200}

    def test_window_slides(self, make_client):
        client = _hs_client(make_client)
        auth_mod = sys.modules["_abp_auth"]
        now = [1000.0]
        limiter = auth_mod.RateLimiter(2, clock=lambda: now[0])
        assert limiter.retry_after("k") is None
        assert limiter.retry_after("k") is None
        assert limiter.retry_after("k") == 61  # oldest hit is 0s old -> wait 60s (+1 rounding)
        now[0] += 59.0
        assert limiter.retry_after("k") == 2  # oldest hit is 59s old -> 1s left (+1)
        now[0] += 1.0
        assert limiter.retry_after("k") is None  # 60s passed: oldest hit expired
        del client


class TestModeNone:
    def test_no_token_needed_and_threads_are_anonymous(self, make_client):
        client = make_client(DeployConfig(auth=DeployAuthConfig(mode="none")))
        resp = client.post("/invoke", json={"input": "hi"})
        assert resp.status_code == 200
        cid = resp.json()["conversation_id"]
        assert resp.json()["response"]["thread"] == f"anonymous:{cid}"

    def test_no_shared_default_thread(self, make_client):
        client = make_client(DeployConfig(auth=DeployAuthConfig(mode="none")))
        a = client.post("/invoke", json={"input": "a"}).json()
        b = client.post("/invoke", json={"input": "b"}).json()
        assert a["conversation_id"] != b["conversation_id"]
        assert b["response"]["seen"] == []

    def test_requirements_skip_pyjwt(self, tmp_path):
        DeployPackager().package(tmp_path, _ir(), DeployConfig(auth=DeployAuthConfig(mode="none")))
        assert "pyjwt" not in (tmp_path / "requirements_deploy.txt").read_text()


def test_jwt_requirements_include_pyjwt(tmp_path):
    DeployPackager().package(tmp_path, _ir(), _hs_config())
    assert "pyjwt[crypto]" in (tmp_path / "requirements_deploy.txt").read_text()


class TestJwks:
    @pytest.fixture
    def idp(self):
        """A local identity provider serving one RSA key as a JWKS."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        body = json.dumps({"keys": [jwk]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield key, f"http://127.0.0.1:{httpd.server_address[1]}/jwks.json"
        httpd.shutdown()

    def _rs_token(self, key, **claims) -> str:
        payload = {"sub": "alice", "exp": int(time.time()) + 300, "aud": AUDIENCE, **claims}
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "k1"})

    def test_idp_signed_token_accepted(self, make_client, idp):
        key, url = idp
        client = make_client(DeployConfig(auth=DeployAuthConfig(jwks_url=url, audience=AUDIENCE)))
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(self._rs_token(key)))
        assert resp.status_code == 200
        assert resp.json()["response"]["thread"].startswith("alice:")

    def test_token_signed_by_other_key_rejected(self, make_client, idp):
        _, url = idp
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        client = make_client(DeployConfig(auth=DeployAuthConfig(jwks_url=url, audience=AUDIENCE)))
        resp = client.post("/invoke", json={"input": "hi"}, headers=_auth(self._rs_token(other)))
        assert resp.status_code == 401

    def test_wrong_audience_rejected(self, make_client, idp):
        key, url = idp
        client = make_client(DeployConfig(auth=DeployAuthConfig(jwks_url=url, audience=AUDIENCE)))
        token = self._rs_token(key, aud="another-app")
        assert client.post("/invoke", json={"input": "x"}, headers=_auth(token)).status_code == 401

    def test_unreachable_jwks_is_503(self, make_client):
        client = make_client(
            DeployConfig(auth=DeployAuthConfig(jwks_url="http://127.0.0.1:9/jwks", audience=AUDIENCE))
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        resp = client.post("/invoke", json={"input": "x"}, headers=_auth(self._rs_token(key)))
        assert resp.status_code == 503
