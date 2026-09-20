"""Behavior tests for the single-provider OpenRouter Qwen ASR route."""

from unittest.mock import MagicMock, patch

import httpx


def _qwen_config():
    return {
        "provider": "openai",
        "openai": {
            "model": "qwen/qwen3-asr-1.7b",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "test-openrouter-key",
        },
        "cloud_trim_silence": False,
    }


def test_qwen_uses_openai_multipart_once_and_redacts_provider_failure(
    tmp_path, caplog
):
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS" + b"audio")
    client = MagicMock()
    client.audio.transcriptions.create.side_effect = RuntimeError(
        "provider response included secret-like details"
    )

    from tools.transcription_tools import _transcribe_openai

    with patch("tools.transcription_tools._HAS_OPENAI", True), patch(
        "openai.OpenAI", return_value=client
    ) as openai_cls:
        result = _transcribe_openai(
            str(audio),
            "qwen/qwen3-asr-1.7b",
            api_key="test-openrouter-key",
            base_url="https://openrouter.ai/api/v1",
        )

    assert result == {
        "success": False,
        "transcript": "",
        "error": "OpenRouter Qwen transcription failed",
    }
    client.audio.transcriptions.create.assert_called_once()
    openai_cls.assert_called_once_with(
        api_key="test-openrouter-key",
        base_url="https://openrouter.ai/api/v1",
        timeout=30,
        max_retries=0,
    )
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "qwen/qwen3-asr-1.7b"
    assert kwargs["file"].name.endswith("/voice.ogg")
    assert "secret-like details" not in caplog.text


def test_qwen_wire_contract_uses_exact_url_multipart_fields_and_json_text(tmp_path):
    from openai import OpenAI as RealOpenAI
    from tools.transcription_tools import _transcribe_openai

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS-audio")
    seen = {}

    def handle(request):
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": "wire transcript"})

    def build_client(**kwargs):
        seen["constructor"] = kwargs
        return RealOpenAI(
            api_key=kwargs["api_key"],
            base_url=kwargs["base_url"],
            timeout=kwargs["timeout"],
            max_retries=kwargs["max_retries"],
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )

    with patch("tools.transcription_tools._HAS_OPENAI", True), patch(
        "openai.OpenAI", side_effect=build_client
    ):
        result = _transcribe_openai(
            str(audio),
            "qwen/qwen3-asr-1.7b",
            api_key="wire-test-key",
            base_url="https://openrouter.ai/api/v1",
        )

    assert result == {
        "success": True,
        "transcript": "wire transcript",
        "provider": "openai",
    }
    assert seen["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert seen["content_type"].startswith("multipart/form-data; boundary=")
    assert seen["authorization"] == "Bearer wire-test-key"
    assert seen["constructor"]["max_retries"] == 0
    body = seen["body"]
    assert b'name="file"; filename="voice.ogg"' in body
    assert b'name="model"' in body
    assert b"qwen/qwen3-asr-1.7b" in body
    assert b'name="response_format"' in body
    assert b"json" in body


def test_qwen_validation_failure_is_always_marked_no_fallback(tmp_path):
    from tools.transcription_tools import transcribe_audio

    missing = tmp_path / "missing.ogg"
    with patch(
        "tools.transcription_tools._load_stt_config", return_value=_qwen_config()
    ):
        result = transcribe_audio(str(missing), source="gateway")

    assert result["success"] is False
    assert result["no_fallback"] is True


def test_qwen_preprocessing_failure_is_always_marked_no_fallback(tmp_path):
    from tools.transcription_tools import transcribe_audio

    audio = tmp_path / "voice.silk"
    audio.write_bytes(b"silk-audio")
    prep_error = {
        "success": False,
        "transcript": "",
        "error": "decoder unavailable",
    }
    with patch(
        "tools.transcription_tools._load_stt_config", return_value=_qwen_config()
    ), patch(
        "tools.transcription_tools._prepare_audio_for_transcription",
        return_value=(None, None, prep_error),
    ):
        result = transcribe_audio(str(audio), source="gateway")

    assert result == {**prep_error, "no_fallback": True}


def test_qwen_rejects_upload_over_decimal_25_mb_without_large_fixture(tmp_path):
    from tools.transcription_tools import (
        _apply_qwen_single_provider_policy,
        _validate_qwen_openrouter_upload_size,
    )

    audio = tmp_path / "small.ogg"
    audio.write_bytes(b"OggS-audio")
    with patch("pathlib.Path.stat") as stat:
        stat.return_value.st_size = 25_000_001
        result = _validate_qwen_openrouter_upload_size(
            str(audio), _qwen_config()
        )

    result = _apply_qwen_single_provider_policy(result, _qwen_config())
    assert result["success"] is False
    assert result["no_fallback"] is True
    assert "25000000" in result["error"].replace(" ", "")


def test_qwen_accepts_exact_decimal_25_mb_boundary(tmp_path):
    from tools.transcription_tools import _validate_qwen_openrouter_upload_size

    audio = tmp_path / "small.ogg"
    audio.write_bytes(b"OggS-audio")
    with patch("pathlib.Path.stat") as stat:
        stat.return_value.st_size = 25_000_000
        result = _validate_qwen_openrouter_upload_size(
            str(audio), _qwen_config()
        )

    assert result is None
