"""Tests for the Speech-to-Text category in `hermes tools` (tools_config).

Covers the STT provider picker rows, config writes (stt.provider /
use_gateway), the model picker catalog, config-only checklist exclusion,
and the faster_whisper post-setup readiness hook.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hermes_cli.tools_config import (  # noqa: E402
    _CONFIG_ONLY_TOOLSETS,
    CONFIGURABLE_TOOLSETS,
    STT_MODEL_CATALOG,
    TOOL_CATEGORIES,
    _checklist_toolset_keys,
    _configure_stt_model,
    _is_provider_active,
    _write_provider_config,
    apply_provider_selection,
)


def _stt_cat():
    return TOOL_CATEGORIES["stt"]


def _stt_provider_named(name):
    return next(p for p in _stt_cat()["providers"] if p["name"] == name)


class TestSttCategory:
    def test_stt_category_exists(self):
        cat = _stt_cat()
        assert cat["name"] == "Speech-to-Text"
        assert len(cat["providers"]) >= 5




    def test_managed_row_shares_tts_coverage_category(self):
        from hermes_cli.nous_subscription import MANAGED_FEATURE_COVERAGE_CATEGORY

        managed = [p for p in _stt_cat()["providers"] if p.get("managed_nous_feature")]
        assert managed, "expected a Nous Subscription row"
        for p in managed:
            assert p["managed_nous_feature"] == "stt"
        assert MANAGED_FEATURE_COVERAGE_CATEGORY["stt"] == "openai-audio"


class TestConfigWrites:
    def test_write_provider_config_sets_stt_provider(self):
        config = {}
        prov = _stt_provider_named("Groq")
        _write_provider_config(prov, config, managed_feature=None)
        assert config["stt"]["provider"] == "groq"
        assert config["stt"]["use_gateway"] is False


    def test_apply_provider_selection_stt(self):
        config = {}
        with patch(
            "hermes_cli.tools_config.get_nous_subscription_features"
        ) as feats:
            feats.return_value = MagicMock(
                nous_auth_present=False, account_info=None
            )
            apply_provider_selection("stt", "OpenAI", config)
        assert config["stt"]["provider"] == "openai"


class TestActiveDetection:
    def test_active_matches_config(self):
        config = {"stt": {"provider": "groq"}}
        assert _is_provider_active(_stt_provider_named("Groq"), config)
        assert not _is_provider_active(_stt_provider_named("OpenAI"), config)

    def test_unset_provider_defaults_to_local(self):
        assert _is_provider_active(_stt_provider_named("Local Whisper"), {})


class TestModelPicker:

    def test_catalog_matches_runtime_model_sets(self):
        from tools.transcription_tools import GROQ_MODELS, OPENAI_MODELS

        assert set(STT_MODEL_CATALOG["openai"]) == OPENAI_MODELS
        assert set(STT_MODEL_CATALOG["groq"]) == GROQ_MODELS




    def test_configure_stt_model_defaults_to_current(self):
        config = {"stt": {"openai": {"model": "gpt-transcribe"}}}
        with patch(
            "hermes_cli.tools_config._prompt_choice", return_value=0
        ) as pc:
            _configure_stt_model("openai", config)
        # default index should point at the currently configured model
        args = pc.call_args[0]
        assert args[2] == STT_MODEL_CATALOG["openai"].index("gpt-transcribe")

    def test_qwen_selection_sets_openrouter_transport_without_touching_tts(self):
        config = {
            "stt": {"openai": {"model": "whisper-1"}},
            "tts": {
                "provider": "elevenlabs",
                "elevenlabs": {
                    "voice_id": "voice-id",
                    "model_id": "eleven_multilingual_v2",
                },
            },
        }
        qwen_index = STT_MODEL_CATALOG["openai"].index("qwen/qwen3-asr-1.7b")

        with patch("hermes_cli.tools_config._prompt_choice", return_value=qwen_index):
            _configure_stt_model("openai", config)

        assert config["stt"]["openai"] == {
            "model": "qwen/qwen3-asr-1.7b",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "${OPENROUTER_API_KEY}",
        }
        assert config["tts"] == {
            "provider": "elevenlabs",
            "elevenlabs": {
                "voice_id": "voice-id",
                "model_id": "eleven_multilingual_v2",
            },
        }

    def test_switching_from_qwen_removes_only_qwen_transport(self):
        config = {
            "stt": {
                "openai": {
                    "model": "qwen/qwen3-asr-1.7b",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "${OPENROUTER_API_KEY}",
                    "language": "en",
                }
            }
        }
        native_index = STT_MODEL_CATALOG["openai"].index("gpt-transcribe")

        with patch("hermes_cli.tools_config._prompt_choice", return_value=native_index):
            _configure_stt_model("openai", config)

        assert config["stt"]["openai"] == {
            "model": "gpt-transcribe",
            "language": "en",
        }

    def test_native_selection_preserves_custom_openai_compatible_transport(self):
        config = {
            "stt": {
                "openai": {
                    "model": "whisper-1",
                    "base_url": "https://stt.example.test/v1",
                    "api_key": "${CUSTOM_STT_KEY}",
                }
            }
        }
        native_index = STT_MODEL_CATALOG["openai"].index("gpt-transcribe")

        with patch("hermes_cli.tools_config._prompt_choice", return_value=native_index):
            _configure_stt_model("openai", config)

        assert config["stt"]["openai"] == {
            "model": "gpt-transcribe",
            "base_url": "https://stt.example.test/v1",
            "api_key": "${CUSTOM_STT_KEY}",
        }

    def test_qwen_key_reference_survives_runtime_expansion_and_unrelated_save(
        self, tmp_path, monkeypatch
    ):
        from hermes_cli.config import load_config, save_config

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "runtime-openrouter-secret")
        (tmp_path / "config.yaml").write_text(
            "stt:\n  provider: openai\n  openai:\n    model: whisper-1\n",
            encoding="utf-8",
        )
        config = load_config()
        qwen_index = STT_MODEL_CATALOG["openai"].index("qwen/qwen3-asr-1.7b")

        with patch("hermes_cli.tools_config._prompt_choice", return_value=qwen_index):
            _configure_stt_model("openai", config)
        save_config(config)

        saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert "api_key: ${OPENROUTER_API_KEY}" in saved
        assert "runtime-openrouter-secret" not in saved

        expanded = load_config()
        assert expanded["stt"]["openai"]["api_key"] == "runtime-openrouter-secret"
        expanded["display"]["compact"] = True
        save_config(expanded)

        saved_again = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert "api_key: ${OPENROUTER_API_KEY}" in saved_again
        assert "runtime-openrouter-secret" not in saved_again


class TestConfigOnlyExclusion:
    def test_stt_is_config_only(self):
        assert "stt" in _CONFIG_ONLY_TOOLSETS

    def test_stt_excluded_from_checklist_universe(self):
        assert "stt" not in _checklist_toolset_keys("cli")
        # sanity: tts (a real toolset) stays in
        assert "tts" in _checklist_toolset_keys("cli")


class TestPostSetup:
    def test_faster_whisper_in_post_setup_ready(self):
        from hermes_cli.tools_config import _POST_SETUP_READY

        assert "faster_whisper" in _POST_SETUP_READY
