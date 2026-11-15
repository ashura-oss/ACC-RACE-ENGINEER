import pytest

from acc_engineer.memory import DriverMemory
from acc_engineer.storage import Store


def test_search_retrieves_relevant_cross_session_excerpt_only(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.remember_conversation(
        "At Spa I struggle with rear traction on exit of La Source.",
        "We can review throttle pickup after the session.", "spa-12")
    memory.remember_conversation(
        "At Monza I prefer a stable rear under braking into turn one.",
        "We'll watch the brake trace.", "monza-8")

    matches = memory.search("rear traction La Source", limit=5)
    assert len(matches) == 1
    assert matches[0]["kind"] == "conversation"
    assert matches[0]["session_id"] == "spa-12"
    assert "rear traction" in matches[0]["excerpt"]
    assert "unverified" in matches[0]["source"]
    assert memory.search("rear braking Monza", limit=1)[0]["session_id"] == "monza-8"
    store.close()


def test_index_survives_reopen_and_backfills_existing_explicit_records(tmp_path):
    store = Store(tmp_path)
    store.add("driver_profile", {"driver_name": "Ari", "encouragement": "brief",
                                  "profanity": False, "driving_preferences": ["late braking"]})
    store.add("driver_session_summary", {"session_id": "silverstone-4", "verified_final": True,
                                           "track": "Silverstone", "car": "GT3", "laps": 21, "position": 3})
    store.add("driver_session_summary", {"session_id": "bad", "verified_final": False,
                                           "track": "Secret Track"})
    store.close()

    reopened = Store(tmp_path)
    memory = DriverMemory(reopened)
    assert memory.search("late braking Ari")[0]["kind"] == "profile"
    session = memory.search("Silverstone 21 laps", limit=1)
    assert session[0]["kind"] == "session"
    assert session[0]["session_id"] == "silverstone-4"
    assert memory.search("Secret Track") == []
    reopened.close()


def test_conversation_index_is_idempotent_and_secrets_are_redacted(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    exchange = ("Remember my brake pedal feels long at Zolder.",
                "We can compare it with the verified session notes. API key=sk-or-v1-supersecretcredentialvalue")
    assert memory.remember_conversation(*exchange, session_id="zolder-2") is True
    assert memory.remember_conversation(*exchange, session_id="zolder-2") is False
    found = memory.search("Zolder brake pedal", limit=1)
    assert len(found) == 1
    assert "unverified assistant content" in found[0]["excerpt"]
    with store.lock:
        indexed = store.db.execute("SELECT content FROM driver_memory_documents").fetchall()
    assert "supersecretcredentialvalue" not in repr(indexed)
    assert "[redacted]" in repr(indexed)
    store.close()


def test_manifest_contains_only_counts_and_search_bounds_are_enforced(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.remember_conversation("I prefer early braking.", "Copy.", "s1")
    manifest = memory.manifest()
    assert manifest["record_count"] == 1
    assert manifest["kinds"] == {"conversation": 1}
    assert "excerpt" not in repr(manifest)
    for query, limit in (("x" * 201, 5), ("brakes", 0), ("brakes", 9), ("brakes", True)):
        with pytest.raises(ValueError):
            memory.search(query, limit)
    store.close()


def test_fts_operators_are_literal_tokens_and_fallback_like_escapes_wildcards(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.remember_conversation("The word OR is written literally with 100% grip.", "Copy.", "s1")
    # FTS operators are reduced to ordinary tokens before parameterized lookup.
    results = memory.search('OR NEAR("grip") ; DROP TABLE driver_memory_documents')
    assert all(row["session_id"] == "s1" for row in results)
    with store.lock:
        store.db.execute("SELECT COUNT(*) FROM driver_memory_documents").fetchone()
    # Exercise the no-FTS fallback path even on a host with SQLite FTS5.
    memory._fts_available = False
    assert memory.search("100% grip", limit=1)[0]["session_id"] == "s1"
    assert memory.search("100x grip") == []
    store.close()


def test_profile_and_verified_session_are_searchable_immediately(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.update({"driver_name": "Ari", "driving_preferences": ["late braking"]})
    assert memory.search("Ari braking")[0]["kind"] == "profile"

    memory.observe("I prefer smooth inputs")
    assert memory.search("smooth inputs")[0]["kind"] == "profile"

    assert memory.record_session({"session_id": "spa-live-1", "verified_final": True,
                                  "track": "Spa", "car": "GT3", "laps": 14, "position": 2})
    session = memory.search("Spa 14 laps", limit=1)
    assert session[0]["kind"] == "session"
    assert session[0]["session_id"] == "spa-live-1"
    store.close()


def test_conversation_sources_share_stable_id_across_restarts(tmp_path):
    store = Store(tmp_path)
    exchange = {"driver": "The brake pedal feels long at Zolder.",
                "engineer": "We'll compare the verified brake data.", "session_id": "zolder-3"}
    store.add("conversation", exchange)
    memory = DriverMemory(store)
    assert memory.remember_conversation(exchange["driver"], exchange["engineer"], exchange["session_id"]) is False
    assert memory.manifest()["record_count"] == 1
    store.close()

    for _ in range(2):
        store = Store(tmp_path)
        memory = DriverMemory(store)
        assert memory.manifest()["record_count"] == 1
        matches = memory.search("Zolder brake pedal", limit=1)
        assert matches[0]["session_id"] == "zolder-3"
        store.close()


def test_updated_preferences_replace_old_profile_in_retrieval(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.update({"driver_name": "Ari", "profanity": True})
    memory.update({"profanity": False})
    matches = memory.search("profanity", limit=8)
    assert len(matches) == 1
    assert "off" in matches[0]["excerpt"]
    assert "allowed" not in matches[0]["excerpt"]
    store.close()


def test_natural_query_recalls_relevant_note_without_generic_radio_checks(tmp_path):
    store = Store(tmp_path)
    memory = DriverMemory(store)
    memory.remember_conversation("I want a gritty radio voice with brisk delivery.", "Preference recorded.", "preferences")
    memory.remember_conversation("Radio check.", "Reading you.", "radio-check")
    matches = memory.search("preferred radio voice delivery")
    assert matches and matches[0]["session_id"] == "preferences"
    assert not any(row["session_id"] == "radio-check" for row in matches)
    store.close()

