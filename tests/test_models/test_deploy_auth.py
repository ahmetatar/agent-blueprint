"""deploy.auth / deploy.rate_limit schema rules."""

import pytest
from pydantic import ValidationError

from agent_blueprint.models.deploy import DeployAuthConfig, DeployConfig, DeployRateLimitConfig


class TestDefaults:
    def test_auth_defaults_to_jwt_and_is_not_configured(self):
        # Secure by default: a deploy section without auth still demands a JWT,
        # and nothing can verify one yet, so packaging must refuse.
        auth = DeployConfig().auth
        assert auth.mode == "jwt"
        assert auth.is_configured is False

    def test_rate_limit_defaults_to_60_per_minute(self):
        assert DeployConfig().rate_limit.requests_per_minute == 60

    def test_secret_env_defaults_to_hs256(self):
        assert DeployAuthConfig(secret_env="S").resolved_algorithms == ["HS256"]

    def test_jwks_defaults_to_rs256(self):
        auth = DeployAuthConfig(jwks_url="https://idp/jwks", audience="api")
        assert auth.resolved_algorithms == ["RS256"]

    def test_mode_none_counts_as_configured(self):
        assert DeployAuthConfig(mode="none").is_configured is True


class TestValidation:
    def test_jwks_and_secret_together_rejected(self):
        with pytest.raises(ValidationError, match="either jwks_url or secret_env"):
            DeployAuthConfig(jwks_url="https://idp/jwks", secret_env="S", audience="api")

    def test_jwks_without_audience_rejected(self):
        with pytest.raises(ValidationError, match="audience is required"):
            DeployAuthConfig(jwks_url="https://idp/jwks")

    def test_secret_without_audience_allowed(self):
        assert DeployAuthConfig(secret_env="S").audience is None

    def test_hmac_algorithm_with_jwks_rejected(self):
        # Algorithm confusion: HS256 verified with a public key as the secret.
        with pytest.raises(ValidationError, match="cannot be used with jwks_url"):
            DeployAuthConfig(jwks_url="https://idp/jwks", audience="api", algorithms=["HS256"])

    def test_asymmetric_algorithm_with_secret_rejected(self):
        with pytest.raises(ValidationError, match="cannot be used with secret_env"):
            DeployAuthConfig(secret_env="S", algorithms=["RS256"])

    def test_alg_none_rejected(self):
        with pytest.raises(ValidationError, match="cannot be used"):
            DeployAuthConfig(secret_env="S", algorithms=["none"])

    def test_empty_algorithms_rejected(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            DeployAuthConfig(secret_env="S", algorithms=[])

    def test_mode_none_with_jwt_settings_rejected(self):
        with pytest.raises(ValidationError, match="secret_env"):
            DeployAuthConfig(mode="none", secret_env="S")

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValidationError):
            DeployAuthConfig(mode="basic")  # type: ignore[arg-type]

    def test_empty_user_claim_rejected(self):
        with pytest.raises(ValidationError, match="user_claim"):
            DeployAuthConfig(secret_env="S", user_claim="")

    def test_negative_rate_limit_rejected(self):
        with pytest.raises(ValidationError):
            DeployRateLimitConfig(requests_per_minute=-1)
