"""Keeper Secrets Manager adapter tests (keeperdb_integration_tasks #3):
SDK boundary mocked (never real network), circuit-breaker behavior,
not-configured handling. The real KSM tenant is exercised only in a manual
pass — documented in the epic INDEX, not silently skipped."""
from __future__ import annotations

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from app.services import keeper_secrets_manager_backend as ksm_module
from app.services.keeper_secrets_manager_backend import KeeperSecretsManagerBackend
from app.services.secret_manager import (
    SecretManagerError,
    SecretManagerNotConfigured,
    get_secret_manager,
)
from tests.secrets.conftest import SECRET_VALUE


class _FakeRecord:
    def __init__(self, uid, fields):
        self.uid = uid
        self._fields = dict(fields)
        self.dict = {"custom": [
            {"type": "text", "label": k, "value": [v]}
            for k, v in fields.items() if k != "password"
        ]}

    def field(self, name, single=False):
        return self._fields.get(name)

    def set_standard_field_value(self, name, value):
        self._fields[name] = value

    def set_custom_field_value(self, name, value):
        self._fields[name] = value
        self.dict["custom"] = [
            {"type": "text", "label": k, "value": [v]}
            for k, v in self._fields.items() if k != "password"
        ]


class _FakeKsmClient:
    def __init__(self):
        self.records = {}
        self.counter = 0
        self.fail_with = None

    def create_secret(self, folder_uid=None, record_data=None):
        if self.fail_with:
            raise self.fail_with
        self.counter += 1
        uid = f"rec-{self.counter}"
        fields = {}
        for f in getattr(record_data, "fields", []) or []:
            values = getattr(f, "value", None)
            if values:
                fields["password"] = values[0] if isinstance(values, list) else values
        for c in getattr(record_data, "custom", []) or []:
            fields[c["label"]] = c["value"][0]
        self.records[uid] = _FakeRecord(uid, fields)
        return uid

    def get_secrets(self, uids=None):
        if self.fail_with:
            raise self.fail_with
        return [self.records[u] for u in (uids or []) if u in self.records]

    def save(self, record):
        if self.fail_with:
            raise self.fail_with
        self.records[record.uid] = record

    def delete_secret(self, record_uids=None):
        if self.fail_with:
            raise self.fail_with
        for u in record_uids or []:
            self.records.pop(u, None)


@pytest.fixture()
def keeper_backend(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRET_MANAGER_BACKEND", "keeper")
    monkeypatch.setattr(settings, "KSM_CONFIG_PATH", "/run/secrets/ksm_config.json")
    monkeypatch.setattr(ksm_module, "keeper_circuit",
                        CircuitBreaker("keeper-test", failure_threshold=3, reset_timeout=30.0))
    monkeypatch.setattr(ksm_module.time, "sleep", lambda s: None)
    fake = _FakeKsmClient()
    backend = KeeperSecretsManagerBackend()
    backend._client = fake
    return backend, fake


def test_factory_returns_keeper_backend_when_selected(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRET_MANAGER_BACKEND", "keeper")
    monkeypatch.setattr(settings, "KSM_CONFIG_PATH", "/run/secrets/ksm_config.json")
    assert isinstance(get_secret_manager(), KeeperSecretsManagerBackend)


def test_store_retrieve_roundtrip(keeper_backend):
    backend, fake = keeper_backend
    ref = backend.store(1, {"password": SECRET_VALUE, "api_key": "k-123"})
    assert ref.startswith("keeper://")
    out = backend.retrieve(ref)
    assert out["password"] == SECRET_VALUE
    assert out["api_key"] == "k-123"


def test_rotate_keeps_stable_ref(keeper_backend):
    backend, fake = keeper_backend
    ref = backend.store(1, {"password": "old"})
    new_ref = backend.rotate(ref, {"password": SECRET_VALUE})
    assert new_ref == ref  # KSM record UID stable — "rotate once, everyone re-fetches"
    assert backend.retrieve(ref)["password"] == SECRET_VALUE


def test_delete_removes_record(keeper_backend):
    backend, fake = keeper_backend
    ref = backend.store(1, {"password": SECRET_VALUE})
    backend.delete(ref)
    with pytest.raises(SecretManagerError):
        backend.retrieve(ref)


def test_unconfigured_keeper_is_a_clear_error(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SECRET_MANAGER_BACKEND", "keeper")
    monkeypatch.setattr(settings, "KSM_CONFIG_PATH", None)
    backend = KeeperSecretsManagerBackend()
    with pytest.raises(SecretManagerNotConfigured, match="KSM_CONFIG_PATH"):
        backend.retrieve("keeper://rec-1")


def test_circuit_opens_on_repeated_failures_and_fails_fast(keeper_backend):
    backend, fake = keeper_backend
    fake.fail_with = ConnectionError("keeper unreachable")
    with pytest.raises(SecretManagerError):
        backend.retrieve("keeper://rec-1")  # 3 attempts -> breaker opens
    with pytest.raises(CircuitBreakerOpen):
        backend.retrieve("keeper://rec-1")  # fails fast now

    fake.fail_with = None  # recovered, but breaker still open
    with pytest.raises(CircuitBreakerOpen):
        backend.retrieve("keeper://rec-1")


def test_missing_record_does_not_trip_breaker(keeper_backend):
    """Regression (v5 bugs2 #2): a benign "record not found" is a logical
    error, not a Keeper outage — it must not count toward the breaker
    threshold, so a few stale refs can't take down healthy credential reads."""
    from app.core.circuit_breaker import State
    from app.services import keeper_secrets_manager_backend as ksm_module

    backend, fake = keeper_backend
    for _ in range(6):  # well past the test breaker's threshold of 3
        with pytest.raises(SecretManagerError):
            backend.retrieve("keeper://ghost")
    assert ksm_module.keeper_circuit.state == State.CLOSED
    # A real record still resolves — the circuit was never opened.
    ref = backend.store(1, {"password": SECRET_VALUE})
    assert backend.retrieve(ref)["password"] == SECRET_VALUE


def test_failure_messages_never_contain_secret_values(keeper_backend):
    backend, fake = keeper_backend
    ref = backend.store(1, {"password": SECRET_VALUE})
    fake.fail_with = ConnectionError(f"boom")
    with pytest.raises(SecretManagerError) as exc:
        backend.rotate(ref, {"password": SECRET_VALUE})
    assert SECRET_VALUE not in str(exc.value)
