import pytest
from pydantic import BaseModel

from smb_agent_kit.config import ConfigError, load_model, require_fictional
from smb_agent_kit.delivery import DryRunSender, ResendSender, SenderUnavailable, make_sender
from smb_agent_kit.llm import (
    CachedLLM,
    OllamaLLM,
    ProviderUnavailable,
    ScriptedLLM,
    TransientProviderError,
    make_provider,
    parse_json_reply,
    structured_output,
)


class Reply(BaseModel):
    service: str
    hours: int


SCHEMA = {"type": "object"}


# --- llm ----------------------------------------------------------------------------------


def test_scripted_structured_output_is_deterministic():
    text = '{"service": "cleaning", "hours": 3}'
    first = structured_output(ScriptedLLM([text]), "sys", "hi", SCHEMA, Reply)
    second = structured_output(ScriptedLLM([text]), "sys", "hi", SCHEMA, Reply)
    assert first[0] == second[0] == Reply(service="cleaning", hours=3)
    assert first[2] is None and second[2] is None


def test_structured_output_reports_an_invalid_reply_and_a_failed_call():
    parsed, call, failure = structured_output(ScriptedLLM(["not json"]), "s", "p", SCHEMA, Reply)
    assert parsed is None and call is not None and failure == "invalid_reply"
    parsed, call, failure = structured_output(ScriptedLLM([]), "s", "p", SCHEMA, Reply)
    assert parsed is None and call is None and failure == "ProviderUnavailable"


def test_parse_json_reply_accepts_a_fenced_block():
    assert parse_json_reply('```json\n{"service": "x", "hours": 1}\n```', Reply).hours == 1


def test_cached_provider_answers_a_repeat_without_a_second_call():
    inner = ScriptedLLM(["one"])
    cached = CachedLLM(inner)
    assert cached.complete("s", "p").text == cached.complete("s", "p").text == "one"
    assert len(inner.calls) == 1


def test_factory_never_guesses_a_model_and_anthropic_needs_a_key(monkeypatch):
    with pytest.raises(ProviderUnavailable):
        make_provider("ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailable):
        make_provider("anthropic", model="owner-supplied-model-id")
    with pytest.raises(ValueError):
        make_provider("other", model="m")


def _requests(monkeypatch, outcomes):
    calls = []

    def fake(self, path, payload=None):
        calls.append(payload)
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(OllamaLLM, "_request", fake)
    return calls


OK = {"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2}


def test_ollama_pins_sampling_and_retries_once_on_a_transient_error(monkeypatch):
    calls = _requests(monkeypatch, [TransientProviderError("500"), OK])
    provider = OllamaLLM("some-model")
    result = provider.complete("sys", "hi")
    assert result.retries == 1 and provider.retries == 1 and len(calls) == 2
    assert calls[0]["options"] == {"temperature": 0.0, "seed": 42}


def test_ollama_does_not_retry_other_failures(monkeypatch):
    calls = _requests(monkeypatch, [ProviderUnavailable("400")])
    with pytest.raises(ProviderUnavailable):
        OllamaLLM("some-model").complete("sys", "hi")
    assert len(calls) == 1


def test_ollama_warm_up_is_not_counted_and_a_failure_is_only_noted(monkeypatch):
    _requests(monkeypatch, [TransientProviderError("500"), TransientProviderError("500")])
    provider = OllamaLLM("some-model")
    provider.warm_up()
    assert provider.settings["warm_up"].startswith("failed") and provider.retries == 0


# --- delivery -----------------------------------------------------------------------------


def test_dry_run_sender_writes_the_outbox_with_the_project_header(tmp_path):
    ref = DryRunSender(tmp_path, key_header="X-Project-Key").send(
        key="k-v1", to="a@example.com", sender="b@example.com", subject="S", body="B"
    )
    text = (tmp_path / "k-v1.eml").read_text("utf-8")
    assert (
        ref.endswith("k-v1.eml") and "X-Project-Key: k-v1" in text and "To: a@example.com" in text
    )


def test_resend_needs_a_key_and_dry_run_needs_an_outbox(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(SenderUnavailable):
        ResendSender()
    with pytest.raises(ValueError):
        make_sender("dry-run")


# --- config -------------------------------------------------------------------------------


class Profile(BaseModel):
    name: str
    fictional: bool


@pytest.mark.parametrize(
    "text", ["name: Acme\n", "name: Acme\nfictional: false\n", "name: Acme\nfictional: yes-ish\n"]
)
def test_config_loader_rejects_a_business_config_without_fictional_true(tmp_path, text):
    path = tmp_path / "business.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_model(path, Profile, fictional=True)


def test_config_loader_accepts_fictional_true(tmp_path):
    path = tmp_path / "business.yaml"
    path.write_text("name: Acme\nfictional: true\n", encoding="utf-8")
    assert load_model(path, Profile, fictional=True).name == "Acme"
    assert require_fictional({"fictional": True}, "x") == {"fictional": True}
