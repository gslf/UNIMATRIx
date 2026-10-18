"""Credential persistence must be private from creation through atomic replacement."""

import pytest

from unimatrix.persistence import json_files

from .file_permissions import assert_private_file


def test_private_json_is_protected_before_writing_and_after_replacement(tmp_path, monkeypatch):
    target = tmp_path / "credentials with spaces and 'quotes'" / "secret.json"
    original_dump = json_files.json.dump
    checked = []

    def inspect_before_write(data, stream, **kwargs):
        temporary, = target.parent.glob(".write-*.tmp")
        assert_private_file(temporary)
        checked.append(temporary)
        return original_dump(data, stream, **kwargs)

    monkeypatch.setattr(json_files.json, "dump", inspect_before_write)
    for value in ("first-key", "replacement-key"):
        json_files.write_json(target, dict(api_key=value), private=True)
        assert_private_file(target)
        assert json_files.read_json(target) == dict(api_key=value)
    assert len(checked) == 2
    assert not list(target.parent.glob(".write-*.tmp"))


def test_failed_private_json_write_preserves_original_and_cleans_up(tmp_path):
    target = tmp_path / "secret.json"
    json_files.write_json(target, dict(api_key="original-key"), private=True)
    with pytest.raises(ValueError):
        json_files.write_json(target, dict(api_key="replacement", invalid=float("nan")), private=True)
    assert json_files.read_json(target) == dict(api_key="original-key")
    assert_private_file(target)
    assert not list(tmp_path.glob(".write-*.tmp"))
