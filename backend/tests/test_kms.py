"""U1 - KeyManagementService: LocalDevKMS signing and the fail-closed factory."""

from __future__ import annotations

import types

import pytest

import app.security.kms as kms_mod
from app.security.kms import LocalDevKMS, get_kms


def test_localdevkms_signs_and_verifies(tmp_path):
    kms = LocalDevKMS(tmp_path / "keys")
    signature = kms.sign(b"hello")
    assert kms.verify(b"hello", signature)
    assert not kms.verify(b"hell0", signature)
    flipped = bytearray(signature)
    flipped[0] ^= 0x01
    assert not kms.verify(b"hello", bytes(flipped))


def test_a_different_key_id_does_not_verify(tmp_path):
    a = LocalDevKMS(tmp_path / "keys", key_id="a")
    b = LocalDevKMS(tmp_path / "keys", key_id="b")
    signature = a.sign(b"hello")
    assert a.verify(b"hello", signature)
    assert not b.verify(b"hello", signature, key_id="b")


def test_key_rotation_old_signature_still_verifies(tmp_path):
    a = LocalDevKMS(tmp_path / "keys", key_id="a")
    signature = a.sign(b"payload")
    b = LocalDevKMS(tmp_path / "keys", key_id="b")  # active key moves on
    assert b.key_id == "b"
    assert b.verify(b"payload", signature, key_id="a")
    assert not b.verify(b"payload", signature)  # default is the new active key


def test_get_kms_returns_local_in_development(tmp_path, monkeypatch):
    fake = types.SimpleNamespace(
        AUDIT_KMS_BACKEND="local", ENVIRONMENT="development", SESSION_BASE_PATH=str(tmp_path)
    )
    monkeypatch.setattr(kms_mod, "get_settings", lambda: fake)
    assert isinstance(get_kms(), LocalDevKMS)


@pytest.mark.parametrize("backend", ["local", ""])
def test_get_kms_fails_closed_in_production(tmp_path, monkeypatch, backend):
    fake = types.SimpleNamespace(
        AUDIT_KMS_BACKEND=backend, ENVIRONMENT="production", SESSION_BASE_PATH=str(tmp_path)
    )
    monkeypatch.setattr(kms_mod, "get_settings", lambda: fake)
    with pytest.raises(RuntimeError, match="production"):
        get_kms()
