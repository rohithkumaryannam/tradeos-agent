import logging

from core import db


class _DuplicateKey(Exception):
    code = 11000


class _LeaseCollection:
    def find_one_and_update(self, *args, **kwargs):
        raise _DuplicateKey("another instance owns the singleton")


class _Database:
    execution_lease = _LeaseCollection()


def test_expected_lease_contention_is_quiet(monkeypatch, caplog):
    monkeypatch.setattr(db, "_connect", lambda: _Database())
    with caplog.at_level(logging.WARNING, logger="tradeos"):
        assert db.acquire_execution_lease("standby") is False
    assert "execution lease unavailable" not in caplog.text
