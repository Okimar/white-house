import os
import sys

import pytest

from tavern.errors import StorageError
from tavern.secret_store import SecretStore
from tavern.util.lock import DataDirLocked, DirLock


def test_token_is_generated_once_and_persisted(tmp_path):
    path = tmp_path / "secrets.json"
    token = SecretStore(path).access_token()
    assert len(token) >= 40
    assert SecretStore(path).access_token() == token
    if sys.platform != "win32":
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_rotate_changes_token(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    old = store.access_token()
    assert store.rotate_access_token() != old


def test_set_get_delete(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.set("api_key_openai", "sk-test")
    assert store.has("api_key_openai")
    store.delete("api_key_openai")
    assert not SecretStore(tmp_path / "secrets.json").has("api_key_openai")


def test_corrupted_file_is_reported(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(StorageError, match="corrupted"):
        SecretStore(path)


def test_lock_is_exclusive_and_reusable(tmp_path):
    path = tmp_path / ".lock"
    with DirLock(path):
        with pytest.raises(DataDirLocked, match="in use"):
            DirLock(path).acquire()
    with DirLock(path):
        pass
