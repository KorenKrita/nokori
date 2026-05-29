from nokori.search.tokenizer import tokenize


def test_latin_words():
    assert tokenize("git push --force") == ["git", "push", "force"]


def test_drops_short_words():
    assert "a" not in tokenize("a quick fox")


def test_lowercase():
    out = tokenize("Use React Native")
    assert out == ["use", "react", "native"]


def test_cjk_bigrams():
    out = tokenize("升级版本")
    assert out == ["升级", "级版", "版本"]


def test_single_cjk_char_kept():
    out = tokenize("升")
    assert out == ["升"]


def test_mixed_text():
    out = tokenize("ORM 大版本升级")
    assert "orm" in out
    assert "大版" in out
    assert "版本" in out
    assert "本升" in out
    assert "升级" in out


def test_punctuation_separates():
    out = tokenize("git, push; force")
    assert out == ["git", "push", "force"]


def test_underscore_kept():
    out = tokenize("foo_bar")
    assert "foo_bar" in out


def test_mixed_alternation():
    out = tokenize("使用 npm 安装包")
    assert "npm" in out
    assert "使用" in out
    assert "安装" in out
    assert "装包" in out
