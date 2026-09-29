"""Railway distribution: Qwen ASR through OpenRouter is plain upstream configuration.

The built-in ``openai`` STT provider honours ``stt.openai.base_url`` / ``api_key``, so pointing it
at OpenRouter with the Qwen model needs no distribution code. This pins that contract: the model,
endpoint and ``${OPENROUTER_API_KEY}`` reference all reach the OpenAI SDK client unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from hermes_constants import get_hermes_home

QWEN_MODEL = "qwen/qwen3-asr-1.7b"
OPENROUTER_URL = "https://openrouter.ai/api/v1"


def test_qwen_on_openrouter_uses_the_builtin_openai_provider(tmp_path, monkeypatch):
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "stt:\n"
        "  enabled: true\n"
        "  provider: openai\n"
        "  openai:\n"
        f"    model: {QWEN_MODEL}\n"
        f"    base_url: {OPENROUTER_URL}\n"
        "    api_key: ${OPENROUTER_API_KEY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"ID3" + b"\0" * 256)

    seen: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            seen["request"] = kwargs
            return SimpleNamespace(text="hello from qwen")

        def close(self):
            pass

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    from tools.transcription_tools import _dispatch_stt_provider, _load_stt_config

    stt_config = _load_stt_config()
    result = _dispatch_stt_provider(str(audio), "openai", stt_config, None, "gateway")

    assert result["success"], result
    assert result["transcript"] == "hello from qwen"
    assert seen["client"]["base_url"] == OPENROUTER_URL
    assert seen["client"]["api_key"] == "test-openrouter-key"
    assert seen["request"]["model"] == QWEN_MODEL
