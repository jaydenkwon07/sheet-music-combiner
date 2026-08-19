import os
import time

import pytest

from web.backend.sessions import SessionStore


def test_create_and_dirs(tmp_path):
    store = SessionStore(tmp_path, ttl_seconds=3600)
    sid = store.create()
    assert store.in_dir(sid).is_dir()
    assert store.out_dir(sid).is_dir()


def test_save_upload_nfc(tmp_path):
    store = SessionStore(tmp_path, ttl_seconds=3600)
    sid = store.create()
    store.save_upload(sid, "Song_1.png", b"data")
    assert (store.in_dir(sid) / "Song_1.png").read_bytes() == b"data"


def test_unknown_session_raises(tmp_path):
    store = SessionStore(tmp_path, ttl_seconds=3600)
    with pytest.raises(KeyError):
        store.in_dir("nope")


def test_clear_results(tmp_path):
    store = SessionStore(tmp_path, ttl_seconds=3600)
    sid = store.create()
    (store.out_dir(sid) / "old.png").write_bytes(b"x")
    store.clear_results(sid)
    assert list(store.out_dir(sid).iterdir()) == []


def test_sweep_removes_expired(tmp_path):
    store = SessionStore(tmp_path, ttl_seconds=1)
    sid = store.create()
    old = time.time() - 10
    os.utime(tmp_path / sid, (old, old))
    store.sweep()
    with pytest.raises(KeyError):
        store.in_dir(sid)
