from nokori.utils import file_lock


def test_file_lock_is_exclusive_and_releases(tmp_path):
    path = tmp_path / "nested" / "worker.lock"

    assert file_lock.is_locked(path) is False
    with file_lock.acquire(path, label="test") as first:
        assert first is True
        assert file_lock.is_locked(path) is True
        with file_lock.acquire(path, label="test") as second:
            assert second is False
    assert file_lock.is_locked(path) is False
