"""Redaction battery — the upgrade canary for detect-secrets.

Pins a `redact_payload` contract over realistic secret shapes across the
gitleaks rule families. detect-secrets is pinned (==1.5.0) because the
integration reaches into plugin internals (denylist regexes, entropy limits);
if the library is upgraded and these tests fail, re-verify the plugin-attribute
access patterns before shipping the bump.
"""

from __future__ import annotations

import json

from nanobot.tracing.redaction import redact_payload

_AWS = "AKIAIOSFODNN7EXAMPLE"
_GITHUB = "ghp_A1bC2dE3fG4hI5jK6lM7nO8pQ9rS0tU3vW4x"
_SLACK = "xoxb-123456789012-abcdefABCDEF"
_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
_OPENAI = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7b1H8fK3mX9uQw2rT5y8uI1oP4aS6dF7gH8jK9lM0nB3vC5x\n"
    "-----END RSA PRIVATE KEY-----"
)
_BASIC = "dXNlcm5hbWU6cGFzc3dvcmQxMjM0NTY3ODk="


class TestSecretShapesMasked:
    def test_aws_access_key(self) -> None:
        assert _AWS not in redact_payload(f"aws key: {_AWS} for s3")

    def test_github_pat(self) -> None:
        assert _GITHUB not in redact_payload(f"call api.github.com with {_GITHUB} token")

    def test_slack_token(self) -> None:
        assert _SLACK not in redact_payload(f"slack bot token {_SLACK} here")

    def test_jwt(self) -> None:
        assert _JWT not in redact_payload(f"Authorization: Bearer {_JWT}")

    def test_openai_key(self) -> None:
        assert _OPENAI not in redact_payload(f"openai api uses {_OPENAI} ok")

    def test_basic_auth_b64(self) -> None:
        assert _BASIC not in redact_payload(f"auth: Basic {_BASIC} end")

    def test_keyword_assignments(self) -> None:
        masked = redact_payload('password=hunter2supersecret and "api_key": "supersecret12345"')
        assert "hunter2supersecret" not in masked
        assert "supersecret12345" not in masked

    def test_pem_block_multiline(self) -> None:
        masked = redact_payload(f"upload this key:\n{_PEM}\nthanks")
        assert "MIIEpAIBAAKCAQEA7b1H8fK3mX9uQw2rT5y8uI1oP4aS6dF7gH8jK9lM0nB3vC5x" not in masked
        assert "BEGIN RSA PRIVATE KEY" not in masked

    def test_pem_block_embedded_in_json_single_line(self) -> None:
        payload = json.dumps({"role": "user", "content": f"sign with {_PEM} please"})
        masked = redact_payload(payload)
        assert "MIIEpAIBAAKCAQEA7b1H8fK3mX9uQw2rT5y8uI1oP4aS6dF7gH8jK9lM0nB3vC5x" not in masked

    def test_escaped_json_quotes_do_not_hide_secrets(self) -> None:
        # Tool-call arguments serialize nested JSON with escaped quotes.
        nested = json.dumps({"api_key": "supersecretvalue12345"})
        payload = json.dumps({"role": "user", "tool_input": nested})
        masked = redact_payload(payload)
        assert "supersecretvalue12345" not in masked

    def test_llm_payload_shape(self) -> None:
        payload = json.dumps(
            {
                "model": "gemma4:31b-cloud",
                "messages": [
                    {"role": "system", "content": "you are helpful"},
                    {"role": "user", "content": f"creds: {_AWS} / {_GITHUB} / {_SLACK} / {_JWT}"},
                ],
            }
        )
        masked = redact_payload(payload)
        for secret in (_AWS, _GITHUB, _SLACK, _JWT):
            assert secret not in masked, secret
        assert "you are helpful" in masked
        assert "gemma4:31b-cloud" in masked


class TestNoFalsePositivesOnNormalText:
    def test_prose_survives(self) -> None:
        text = "Summarize the quarterly report and file an issue about the scheduler poll interval."
        assert redact_payload(text) == text

    def test_normal_json_survives(self) -> None:
        payload = json.dumps({"messages": [{"role": "user", "content": "what is the weather in Hanoi"}]})
        assert redact_payload(payload) == payload


class TestClipping:
    def test_clip_marker(self) -> None:
        masked = redact_payload("y" * 10_000)
        assert len(masked) < 4_200
        assert "[clipped" in masked

    def test_clip_respects_custom_limit(self) -> None:
        masked = redact_payload("z" * 1_000, limit=100)
        assert masked.endswith("[clipped 900 chars]")
