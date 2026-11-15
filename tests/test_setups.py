import json

import pytest

from acc_engineer.setups import SetupManager


@pytest.fixture
def manager(tmp_path):
    (tmp_path / "source.json").write_text(json.dumps({"fuel": 20, "tyres": [4, 5, 6, 7]}))
    profile = {"fixture": {"validated": True, "parameters": {
        "fuel": {"path": "/fuel", "min": 1, "max": 100, "unit": "litres", "integer": True},
        "pressure": {"path": "/tyres/0", "min": 20, "max": 30, "unit": "psi", "scale": 10, "offset": -200, "integer": True},
    }}}
    return SetupManager(tmp_path, profile)


def test_preserves_original_backs_up_and_verifies_new_file(manager):
    original = (manager.root / "source.json").read_bytes()
    result = manager.create("source.json", "race.json", {"fuel": 35, "pressure": 26.5}, "fixture")
    assert (manager.root / "source.json").read_bytes() == original
    assert (manager.root / result["backup"]).read_bytes() == original
    assert manager.inspect("race.json")["data"] == {"fuel": 35, "tyres": [65, 5, 6, 7]}
    assert result["active_in_game"] is False


@pytest.mark.parametrize("name", ["../escape.json", "source.txt", "."])
def test_rejects_path_escape(manager, name):
    with pytest.raises(ValueError):
        manager.create("source.json", name, {"fuel": 35}, "fixture")


@pytest.mark.parametrize("changes", [{"fuel": 101}, {"wing": 4}, {"fuel": True}, {"fuel": float("inf")}, {"fuel": 1.5}])
def test_rejects_unvalidated_values_before_writing(manager, changes):
    with pytest.raises(ValueError):
        manager.create("source.json", "race.json", changes, "fixture")
    assert not (manager.root / "race.json").exists()
    assert not (manager.root / ".backups").exists()


def test_does_not_overwrite_existing_file(manager):
    with pytest.raises(ValueError):
        manager.create("source.json", "source.json", {"fuel": 35}, "fixture")


def test_no_implicit_acc_profile(manager):
    with pytest.raises(ValueError, match="explicitly validated"):
        manager.create("source.json", "race.json", {"fuel": 35})
    manager.profiles["fixture"]["validated"] = False
    with pytest.raises(ValueError, match="marked validated"):
        manager.create("source.json", "race.json", {"fuel": 35}, "fixture")


def test_absent_profile_path_is_rejected(manager):
    manager.profiles["fixture"]["parameters"]["fuel"]["path"] = "/missing/value"
    with pytest.raises(ValueError, match="absent"):
        manager.create("source.json", "race.json", {"fuel": 35}, "fixture")
