"""One config loader (#83): located once, parsed once, independent of the cwd."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SHIPPED = PROJECT / "config.yaml"


@pytest.fixture
def cfgmod(monkeypatch):
    from manimgen import config

    config.reload()
    yield config
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    config.reload()


def _use(config, monkeypatch, tmp_path, text, name="c.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv(config.ENV_VAR, str(p))
    config.reload()
    return p


def _run(code, cwd, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT)
    env.pop("MANIMGEN_CONFIG", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# -- paths do not depend on the working folder ---------------------------------


def test_output_paths_ignore_working_directory(tmp_path):
    code = "from manimgen import paths; print(paths.plan_cache()); print(paths.scenes_dir())"
    here = _run(code, PROJECT)
    there = _run(code, tmp_path)
    assert here.returncode == 0, here.stderr
    assert there.returncode == 0, there.stderr
    assert here.stdout == there.stdout
    plan = Path(there.stdout.splitlines()[0])
    assert plan.is_absolute()
    assert plan == PROJECT / "manimgen" / "output" / "plan.json"


def test_relative_output_resolves_against_config_folder(cfgmod, monkeypatch, tmp_path):
    sub = tmp_path / "proj"
    sub.mkdir()
    # A genuinely absolute path on every OS ("/abs/..." has no drive on Windows).
    absolute = tmp_path / "abs" / "scenes"
    _use(
        cfgmod,
        monkeypatch,
        sub,
        f'output:\n  plan_cache: out/plan.json\n  scenes_dir: "{absolute.as_posix()}"\n',
    )
    monkeypatch.chdir(tmp_path)
    out = cfgmod.section("output")
    assert Path(out["plan_cache"]) == sub / "out" / "plan.json"
    assert Path(out["scenes_dir"]) == absolute


# -- failures are clear errors, never silent defaults ---------------------------


def test_missing_config_is_a_clear_error(cfgmod, monkeypatch, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_VAR, str(tmp_path / "nope.yaml"))
    cfgmod.reload()
    with pytest.raises(cfgmod.ConfigError) as ei:
        cfgmod.load()
    msg = str(ei.value)
    assert "nope.yaml" in msg and "pip install -e ." in msg and "wheel" in msg


def test_malformed_yaml_names_the_file(cfgmod, monkeypatch, tmp_path):
    p = _use(cfgmod, monkeypatch, tmp_path, "this: : : not valid yaml\n  - broken")
    with pytest.raises(cfgmod.ConfigError, match="not valid YAML") as ei:
        cfgmod.load()
    assert str(p) in str(ei.value)


def test_non_mapping_top_level(cfgmod, monkeypatch, tmp_path):
    _use(cfgmod, monkeypatch, tmp_path, "- a\n- b\n")
    with pytest.raises(cfgmod.ConfigError, match="mapping"):
        cfgmod.load()


@pytest.mark.parametrize(
    "text, needle",
    [
        ("rendering:\n  fps: fast\n", "rendering.fps"),
        ("rendering:\n  fps: true\n", "rendering.fps"),
        ("llm:\n  ollama_num_ctx: lots\n", "llm.ollama_num_ctx"),
        ("tts:\n  enabled: maybe\n", "tts.enabled"),
        ("llm: just a string\n", "'llm'"),
        ("output:\n  plan_cache: 5\n", "output.plan_cache"),
        ("output:\n  logs_dir: ''\n", "output.logs_dir"),
        ("rendering:\n  render_timeout_2d: soon\n", "render_timeout_2d"),
    ],
)
def test_wrong_types_name_the_key(cfgmod, monkeypatch, tmp_path, text, needle):
    p = _use(cfgmod, monkeypatch, tmp_path, text)
    with pytest.raises(cfgmod.ConfigError) as ei:
        cfgmod.load()
    assert needle in str(ei.value) and str(p) in str(ei.value)


def test_empty_file_and_empty_blocks_use_documented_defaults(
    cfgmod, monkeypatch, tmp_path
):
    _use(cfgmod, monkeypatch, tmp_path, "llm:\ntts:\n")
    cfg = cfgmod.load()
    assert cfg["tts"]["enabled"] is True  # TTS defaults on
    assert cfg["rendering"]["max_retries"] == 3
    assert cfg["llm_provider"] == "claude_cli"
    _use(cfgmod, monkeypatch, tmp_path, "", name="empty.yaml")
    assert cfgmod.load()["tts"]["enabled"] is True


def test_utf8_config_is_read_as_utf8(cfgmod, monkeypatch, tmp_path):
    _use(cfgmod, monkeypatch, tmp_path, 'tts:\n  voice: "voix-é-日本"\n')
    assert cfgmod.section("tts")["voice"] == "voix-é-日本"


# -- one parse, honoured by all four consumers ----------------------------------


def test_file_is_parsed_once_for_all_consumers():
    code = (
        "import yaml\n"
        "calls = []\n"
        "real = yaml.safe_load\n"
        "yaml.safe_load = lambda s: (calls.append(1), real(s))[1]\n"
        "from manimgen import cli, llm, paths\n"
        "from manimgen.renderer import tts\n"
        "cli._load_config(); llm._load_llm_config(); paths.plan_cache()\n"
        "print(len(calls))\n"
    )
    res = _run(code, PROJECT)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "1"


def test_env_var_is_honoured_by_all_consumers(tmp_path):
    cfg = tmp_path / "mine.yaml"
    cfg.write_text(
        "llm_provider: ollama\n"
        "llm:\n  ollama_num_ctx: 4096\n"
        "output:\n  plan_cache: elsewhere/plan.json\n"
        "rendering:\n  fps: 24\n"
        "tts:\n  enabled: false\n  voice: v-test\n  speed: '+9%'\n",
        encoding="utf-8",
    )
    code = (
        "from manimgen import paths, llm, cli\n"
        "from manimgen.renderer import tts\n"
        "print(paths.plan_cache()); print(paths.render_fps())\n"
        "print(llm._LLM_CONFIG['ollama_num_ctx'], llm._LLM_CONFIG['llm_provider'])\n"
        "print(tts._TTS_CFG['voice'], tts._TTS_CFG['speed'])\n"
        "print(cli._tts_enabled(cli._load_config()))\n"
    )
    res = _run(code, tmp_path, {"MANIMGEN_CONFIG": str(cfg)})
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert Path(lines[0]) == tmp_path / "elsewhere" / "plan.json"
    assert lines[1:] == ["24", "4096 ollama", "v-test +9%", "False"]


def test_missing_config_stops_import_with_clear_message(tmp_path):
    res = _run(
        "import manimgen.paths",
        tmp_path,
        {"MANIMGEN_CONFIG": str(tmp_path / "gone.yaml")},
    )
    assert res.returncode != 0
    assert "gone.yaml" in res.stderr and "pip install -e ." in res.stderr


# -- no observable change for the shipped config --------------------------------

# Effective settings of the shipped config.yaml as the four old loaders produced
# them (captured on the commit before this change; output paths were relative to
# the working folder, now they are anchored at the project folder).
_OLD_RENDERING = {
    "quality": "hd",
    "resolution": "1920x1080",
    "fps": 60,
    "max_retries": 3,
    "render_timeout_2d": 240.0,
    "render_timeout_3d": 360.0,
    "render_timeout_fallback": 180.0,
}
_OLD_LLM = {
    "llm_provider": "claude_cli",
    "gemini_model": "gemini-2.5-flash",
    "anthropic_model": "claude-sonnet-5-5",
    "anthropic_max_tokens": 16000,
    "claude_cli_model": "sonnet",
    "claude_cli_path": "claude",
    "models": {},
    "ollama_model": "llama3.1",
    "ollama_base_url": "http://localhost:11434",
    "ollama_num_ctx": 32768,
}
_OLD_TTS = {
    "engine": "edge-tts",
    "voice": "en-US-AndrewMultilingualNeural",
    "enabled": True,
    "speed": "+5%",
}
_OLD_OUTPUT_RELATIVE = {
    "scenes": "manimgen/output/scenes",
    "videos": "manimgen/output/videos",
    "logs": "manimgen/output/logs",
    "audio": "manimgen/output/audio",
    "muxed": "manimgen/output/muxed",
    "exports": "manimgen/output/videos/exports",
    "plan": "manimgen/output/plan.json",
}


def test_shipped_config_effective_settings_unchanged():
    code = (
        "import json\n"
        "from manimgen import cli, llm, paths\n"
        "from manimgen.renderer import tts\n"
        "print(json.dumps({\n"
        "  'rendering': paths._RENDERING,\n"
        "  'llm': llm._load_llm_config(),\n"
        "  'tts': tts._TTS_CFG,\n"
        "  'tts_on': cli._tts_enabled(cli._load_config()),\n"
        "  'out': {k: getattr(paths, k + '_dir' if k != 'plan' else 'plan_cache')()\n"
        "          for k in ('scenes','videos','logs','audio','muxed','exports','plan')},\n"
        "}))\n"
    )
    res = _run(code, PROJECT)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["rendering"] == _OLD_RENDERING
    assert got["llm"] == _OLD_LLM
    assert {k: got["tts"][k] for k in _OLD_TTS} == _OLD_TTS
    assert not got["tts"]["proxy"]
    assert got["tts_on"] is True
    assert got["out"] == {k: str(PROJECT / v) for k, v in _OLD_OUTPUT_RELATIVE.items()}
