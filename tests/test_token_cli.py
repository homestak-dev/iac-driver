"""Tests for token introspection CLI."""

import base64
import hashlib
import hmac as hmac_mod
import json
import time

import pytest

from token_cli import inspect_token, main

TEST_SIGNING_KEY = "a" * 64  # 32 bytes hex = 256 bits


def _mint_token(node: str, spec: str, signing_key: str = TEST_SIGNING_KEY) -> str:
    """Mint a test provisioning token."""
    payload = {"v": 1, "n": node, "s": spec, "iat": int(time.time())}
    payload_bytes = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode()
    ).rstrip(b'=')
    sig = hmac_mod.new(
        bytes.fromhex(signing_key), payload_bytes, hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b'=')
    return f"{payload_bytes.decode()}.{sig_b64.decode()}"


class TestInspectToken:
    """Tests for inspect_token()."""

    def test_decode_valid_token(self, capsys):
        token = _mint_token("edge", "base")
        rc = inspect_token(token)
        assert rc == 0
        out = capsys.readouterr().out
        assert "node    (n): edge" in out
        assert "spec    (s): base" in out
        assert "version (v): 1" in out

    def test_verify_valid_token(self, capsys):
        token = _mint_token("edge", "base")
        rc = inspect_token(token, signing_key=TEST_SIGNING_KEY)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Signature: VALID" in out

    def test_verify_wrong_key(self, capsys):
        token = _mint_token("edge", "base")
        rc = inspect_token(token, signing_key="b" * 64)
        assert rc == 1
        out = capsys.readouterr().out
        assert "Signature: INVALID" in out

    def test_malformed_token(self, capsys):
        rc = inspect_token("not-a-token")
        assert rc == 1
        out = capsys.readouterr().out
        assert "Expected 2" in out

    def test_iat_displayed(self, capsys):
        token = _mint_token("n1", "base")
        rc = inspect_token(token)
        assert rc == 0
        out = capsys.readouterr().out
        assert "issued  (iat):" in out
        assert "T" in out  # ISO timestamp contains T


class TestTokenMain:
    """Tests for CLI entry point."""

    def test_no_action_shows_help(self, capsys):
        rc = main([])
        assert rc == 1

    def test_inspect_decodes(self, capsys):
        token = _mint_token("dev1", "pve")
        rc = main(["inspect", token])
        assert rc == 0
        out = capsys.readouterr().out
        assert "node    (n): dev1" in out
        assert "spec    (s): pve" in out
