from nokori.utils.ids import new_uuid, short_id_for, MIN_SHORT_LEN


def test_new_uuid_unique():
    assert new_uuid() != new_uuid()


def test_short_id_default_length():
    full = "abcdef12-3456-7890-abcd-ef1234567890"
    sid = short_id_for(full, taken=set())
    assert len(sid) == MIN_SHORT_LEN
    assert sid == full.replace("-", "")[:MIN_SHORT_LEN]


def test_short_id_grows_on_collision():
    full = "abcdef12-3456-7890-abcd-ef1234567890"
    taken = {"abcdef"}
    sid = short_id_for(full, taken=taken)
    assert sid != "abcdef"
    assert sid.startswith("abcdef")
