"""
Tests for the spec-based scene generator pipeline.
All LLM calls are mocked — zero API cost.
"""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock


# ── _validate_spec ────────────────────────────────────────────────────────────

class TestValidateSpec:

    def _call_with_schema_errors(self, spec, schema_errors):
        """Run _validate_spec with spec_schema.validate stubbed to return schema_errors."""
        import manimgen.templates.spec_schema as schema_mod
        with patch.object(schema_mod, "validate", return_value=schema_errors):
            from manimgen.generator.scene_generator import _validate_spec
            _validate_spec(spec)

    def test_raises_when_template_key_missing(self):
        spec = {
            "mode": "2d",
            "title": "Test",
            "duration_seconds": 10.0,
            "beats": [{"type": "title_only", "duration": 2.0}],
        }
        with pytest.raises(Exception, match="template"):
            self._call_with_schema_errors(spec, ["'template' key is required"])

    def test_raises_for_unknown_template(self):
        spec = {
            "mode": "2d",
            "template": "nonexistent_template",
            "title": "Test",
            "duration_seconds": 10.0,
            "beats": [{"type": "title_only", "duration": 2.0}],
        }
        with pytest.raises(Exception, match="nonexistent_template"):
            self._call_with_schema_errors(spec, ["Unknown template 'nonexistent_template'"])

    def test_raises_when_beats_empty(self):
        spec = {
            "mode": "2d",
            "template": "text",
            "title": "Test",
            "duration_seconds": 10.0,
            "beats": [],
        }
        with pytest.raises(Exception, match="beats"):
            self._call_with_schema_errors(spec, ["'beats' must not be empty"])


# ── validate_spec_safety ──────────────────────────────────────────────────────

class TestValidateSpecSafety:

    def _call(self, spec):
        from manimgen.validator.codeguard import validate_spec_safety
        return validate_spec_safety(spec)

    def test_exec_in_expr_str_is_flagged(self):
        spec = {
            "mode": "2d",
            "template": "function",
            "title": "T",
            "duration_seconds": 10.0,
            "beats": [
                {"type": "curve_appear", "expr_str": "exec('rm -rf /')", "duration": 2.0}
            ],
        }
        errors = self._call(spec)
        assert any("exec" in e for e in errors)

    def test_duration_over_120_is_flagged(self):
        spec = {
            "mode": "2d",
            "template": "text",
            "title": "T",
            "duration_seconds": 200.0,
            "beats": [],
        }
        errors = self._call(spec)
        assert any("duration_seconds" in e for e in errors)

    def test_duration_under_2_is_flagged(self):
        spec = {
            "mode": "2d",
            "template": "text",
            "title": "T",
            "duration_seconds": 1.0,
            "beats": [],
        }
        errors = self._call(spec)
        assert any("duration_seconds" in e for e in errors)

    def test_safe_spec_returns_no_errors(self):
        spec = {
            "mode": "2d",
            "template": "function",
            "title": "Parabola",
            "duration_seconds": 12.0,
            "beats": [
                {"type": "curve_appear", "expr_str": "x**2", "duration": 3.0}
            ],
        }
        errors = self._call(spec)
        assert errors == []

    def test_3d_template_with_2d_mode_is_flagged(self):
        spec = {
            "mode": "2d",
            "template": "surface_3d",
            "title": "T",
            "duration_seconds": 10.0,
            "beats": [],
        }
        errors = self._call(spec)
        assert any("surface_3d" in e or "3d" in e for e in errors)

    def test_node_count_over_50_is_flagged(self):
        nodes = [[i * 0.01, 0.0] for i in range(51)]
        spec = {
            "mode": "2d",
            "template": "graph_theory",
            "title": "T",
            "duration_seconds": 10.0,
            "beats": [
                {"type": "graph_appear", "nodes": nodes, "edges": [], "duration": 3.0}
            ],
        }
        errors = self._call(spec)
        assert any("node count" in e or "50" in e for e in errors)


# ── _is_3d_scene ──────────────────────────────────────────────────────────────

class TestIs3dScene:

    def test_returns_true_for_threedscene(self):
        from manimgen.validator.runner import _is_3d_scene
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("from manimlib import *\nclass MyScene(ThreeDScene):\n    pass\n")
            path = f.name
        try:
            assert _is_3d_scene(path) is True
        finally:
            os.unlink(path)

    def test_returns_false_for_plain_scene(self):
        from manimgen.validator.runner import _is_3d_scene
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("from manimlib import *\nclass MyScene(Scene):\n    pass\n")
            path = f.name
        try:
            assert _is_3d_scene(path) is False
        finally:
            os.unlink(path)


# ── generate_scenes mock integration ─────────────────────────────────────────

class TestGenerateScenesCallsRenderSpec:

    def test_render_spec_called_with_valid_spec(self, tmp_path):
        section = {
            "id": "section_01",
            "title": "Binary Search",
            "visual_description": "A sorted array with a pointer moving",
            "key_objects": ["array", "pointer"],
        }
        fake_spec = {
            "mode": "2d",
            "template": "text",
            "title": "Binary Search",
            "duration_seconds": 8.0,
            "beats": [
                {"type": "title_only", "title": "Binary Search", "duration": 2.5}
            ],
        }
        fake_code = "from manimlib import *\nclass Section01Scene(Scene): pass"
        mock_render_spec = MagicMock(return_value=fake_code)

        from manimgen.generator import scene_generator

        with patch.object(scene_generator, "_generate_spec", return_value=fake_spec), \
             patch.object(scene_generator, "_validate_spec"), \
             patch.object(scene_generator.paths, "scenes_dir", return_value=str(tmp_path)), \
             patch.object(scene_generator, "render_spec", mock_render_spec):
            code, class_name, scene_path = scene_generator.generate_scenes(
                section, cue_index=None, total_cues=None, duration_seconds=8.0
            )

        mock_render_spec.assert_called_once()
        assert class_name == "Section01Scene"


class TestSpecRetryTriggered:

    def test_retry_spec_called_on_validation_failure(self, tmp_path):
        """SpecValidationError on first spec → retry_spec() called → second spec used."""
        from manimgen.templates.spec_schema import SpecValidationError
        from manimgen.generator import scene_generator

        section = {
            "id": "section_01",
            "title": "Binary Search",
            "visual_description": "desc",
            "key_objects": [],
        }
        bad_spec = {"mode": "2d", "template": "bad", "title": "T", "duration_seconds": 8.0, "beats": []}
        good_spec = {
            "mode": "2d", "template": "text", "title": "T", "duration_seconds": 8.0,
            "beats": [{"type": "title_only", "title": "T", "duration": 2.0}],
        }
        fake_code = "from manimlib import *\nclass Section01Scene(Scene): pass"
        mock_render_spec = MagicMock(return_value=fake_code)
        mock_retry_spec = MagicMock(return_value=good_spec)

        validate_calls = [0]
        def fake_validate(spec):
            validate_calls[0] += 1
            if spec is bad_spec:
                raise SpecValidationError("Unknown template 'bad'")

        mock_dispatch = MagicMock()
        mock_dispatch.render_spec = mock_render_spec

        with patch.object(scene_generator, "_generate_spec", return_value=bad_spec), \
             patch.object(scene_generator, "_validate_spec", side_effect=fake_validate), \
             patch.object(scene_generator, "retry_spec", mock_retry_spec), \
             patch.object(scene_generator.paths, "scenes_dir", return_value=str(tmp_path)), \
             patch.object(scene_generator, "render_spec", mock_render_spec):
            code, class_name, scene_path = scene_generator.generate_scenes(
                section, cue_index=None, total_cues=None, duration_seconds=8.0
            )

        mock_retry_spec.assert_called_once()
        assert validate_calls[0] == 2  # once for bad_spec, once for good_spec
        mock_render_spec.assert_called_once()

    def test_raises_after_max_retries_exhausted(self, tmp_path):
        """If every spec fails validation, SpecValidationError is re-raised after MAX_SPEC_RETRIES."""
        from manimgen.templates.spec_schema import SpecValidationError
        from manimgen.validator.retry import MAX_SPEC_RETRIES
        from manimgen.generator import scene_generator

        section = {
            "id": "section_01",
            "title": "Test",
            "visual_description": "desc",
            "key_objects": [],
        }
        always_bad = {"mode": "2d", "template": "bad", "title": "T", "duration_seconds": 8.0, "beats": []}

        with patch.object(scene_generator, "_generate_spec", return_value=always_bad), \
             patch.object(scene_generator, "_validate_spec", side_effect=SpecValidationError("bad template")), \
             patch.object(scene_generator, "retry_spec", return_value=always_bad), \
             patch.object(scene_generator.paths, "scenes_dir", return_value=str(tmp_path)):
            with pytest.raises(SpecValidationError):
                scene_generator.generate_scenes(
                    section, cue_index=None, total_cues=None, duration_seconds=8.0
                )
