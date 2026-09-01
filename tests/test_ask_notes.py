"""ask_notes: retrieval (shared with search_notes) + optional LLM synthesis.
The synthesis half is mocked here (no real GROQ_API_KEY/MISTRAL_API_KEY
assumed available in a build/CI environment) - _retrieve and _build_llm_chain
are real, only the actual litellm network call is faked."""

from __future__ import annotations

from pathlib import Path

import pytest

import local_notes_search as lns
from tests.conftest import requires_model


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_db_path: Path):
    monkeypatch.setattr(lns, "DB_PATH", tmp_db_path)


@pytest.fixture(autouse=True)
def _no_llm_keys_by_default(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)


def test_build_llm_chain_is_empty_with_no_keys():
    assert lns._build_llm_chain() == []


def test_build_llm_chain_includes_only_configured_providers(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    chain = lns._build_llm_chain()
    assert len(chain) == 1
    assert chain[0]["model"] == "groq/openai/gpt-oss-120b"
    assert chain[0]["api_key"] == "fake-groq-key"


def test_build_llm_chain_orders_groq_before_mistral(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("MISTRAL_API_KEY", "m")
    chain = lns._build_llm_chain()
    assert [c["model"] for c in chain] == ["groq/openai/gpt-oss-120b", "mistral/mistral-small-latest"]


@pytest.mark.asyncio
async def test_synthesize_answer_returns_none_with_no_configured_provider():
    result = await lns._synthesize_answer("question", [("f.md", 1, 2, "text", 0.1)])
    assert result is None


@pytest.mark.asyncio
async def test_synthesize_answer_returns_none_when_every_provider_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    async def _boom(**kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("litellm.acompletion", _boom)
    result = await lns._synthesize_answer("question", [("f.md", 1, 2, "text", 0.1)])
    assert result is None


@pytest.mark.asyncio
async def test_synthesize_answer_returns_model_content_on_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _FakeMessage:
        content = "the answer, grounded in context"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    captured = {}

    async def _fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

    result = await lns._synthesize_answer("what is the answer?", [("f.md", 1, 2, "some context text", 0.1)])

    assert result == "the answer, grounded in context"
    assert captured["model"] == "groq/openai/gpt-oss-120b"
    assert "some context text" in captured["messages"][1]["content"]
    assert "what is the answer?" in captured["messages"][1]["content"]


@pytest.mark.asyncio
async def test_synthesize_answer_returns_none_on_empty_choices_without_crashing(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _FakeResponse:
        choices = []  # e.g. a moderation-filtered or tool-call-only completion

    async def _fake_acompletion(**kwargs):
        return _FakeResponse()

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

    result = await lns._synthesize_answer("question", [("f.md", 1, 2, "text", 0.1)])
    assert result is None  # must not raise IndexError


@pytest.mark.asyncio
async def test_ask_notes_rejects_empty_question():
    result = await lns.ask_notes("   ")
    assert "boş sorgu" in result.lower()


@requires_model
@pytest.mark.asyncio
async def test_ask_notes_with_no_index_returns_friendly_message():
    result = await lns.ask_notes("anything")
    assert "Sonuç bulunamadı" in result


@requires_model
@pytest.mark.asyncio
async def test_ask_notes_without_llm_key_degrades_to_raw_results(tmp_notes_dir: Path):
    (tmp_notes_dir / "recipe.md").write_text("Boil pasta for 9 minutes, then drain.")
    await lns.index_directory(str(tmp_notes_dir))

    result = await lns.ask_notes("how long to boil pasta")

    assert "GROQ_API_KEY" in result
    assert "recipe.md" in result


@requires_model
@pytest.mark.asyncio
async def test_ask_notes_with_key_but_failed_provider_says_provider_failed_not_unconfigured(tmp_notes_dir: Path, monkeypatch):
    # A key IS configured but the call fails/returns nothing usable - the
    # message must not falsely claim no key is configured (regression test
    # for the code-review finding: these are different situations and were
    # previously conflated into one misleading message).
    (tmp_notes_dir / "recipe.md").write_text("Boil pasta for 9 minutes, then drain.")
    await lns.index_directory(str(tmp_notes_dir))

    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    async def _boom(**kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("litellm.acompletion", _boom)

    result = await lns.ask_notes("how long to boil pasta")

    assert "GROQ_API_KEY ya da MISTRAL_API_KEY yapılandırılmamış" not in result
    assert "geçerli bir yanıt alınamadı" in result
    assert "recipe.md" in result


@requires_model
@pytest.mark.asyncio
async def test_ask_notes_with_llm_returns_synthesized_answer_and_sources(tmp_notes_dir: Path, monkeypatch):
    (tmp_notes_dir / "recipe.md").write_text("Boil pasta for 9 minutes, then drain.")
    await lns.index_directory(str(tmp_notes_dir))

    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _FakeMessage:
        content = "Boil pasta for 9 minutes."

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    async def _fake_acompletion(**kwargs):
        return _FakeResponse()

    monkeypatch.setattr("litellm.acompletion", _fake_acompletion)

    result = await lns.ask_notes("how long to boil pasta")

    assert "Boil pasta for 9 minutes." in result
    assert "Kaynaklar:" in result
    assert "recipe.md" in result
