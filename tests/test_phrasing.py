"""One counting rule for the whole product: nothing ever prints "1 file(s)"."""

from __future__ import annotations

from gitsquid.phrasing import plural


def test_one_is_singular():
    assert plural(1, "file") == "1 file"


def test_none_and_many_are_plural():
    assert plural(0, "file") == "0 files"
    assert plural(3, "file") == "3 files"


def test_a_noun_phrase_pluralises_on_its_last_word():
    assert plural(2, "sample change") == "2 sample changes"


def test_an_irregular_form_can_be_given():
    assert plural(2, "entry", many="entries") == "2 entries"


def test_one_file_conflicts_and_several_conflict():
    from gitsquid.phrasing import conflicts

    assert conflicts(1) == "1 file still conflicts"
    assert conflicts(3) == "3 files still conflict"
