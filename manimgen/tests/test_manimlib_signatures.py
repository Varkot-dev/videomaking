"""Tests for the type-aware kwarg-introspection shadow (Phase 2, design doc).

The soundness LOGIC is tested against hand-built fake class hierarchies so it runs
in CI with no manimlib / no GL. The real-manimlib path is a separately-gated smoke
test at the bottom (skipped when manimlib can't import).

The core invariant: flag a kwarg as invalid ONLY when provably invalid. Every
ambiguous case stays silent (a false positive poisons the shadow data we'd use to
decide enforcement later).
"""

from __future__ import annotations

import inspect

import pytest

from manimgen.validator import manimlib_signatures as sigs


# ── Fake hierarchies modeling the real manimlib shapes ────────────────────────

class FakeMobject:
    """Closed signature — no **kwargs. The terminal of every real chain."""
    def __init__(self, color=None, opacity=1.0, z_index=0):
        pass


class FakeVMobject(FakeMobject):
    """Names fill_color/fill_opacity explicitly AND has **kwargs (like VMobject)."""
    def __init__(self, color=None, fill_color=None, fill_opacity=None,
                 stroke_width=None, **kwargs):
        super().__init__(**kwargs)


class FakeSquare(FakeVMobject):
    """Adds side_length, forwards **kwargs up (like Square)."""
    def __init__(self, side_length=2.0, **kwargs):
        super().__init__(**kwargs)


class FakeSurface(FakeMobject):
    """Has **kwargs that forward straight to the CLOSED FakeMobject (like Surface)."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class FakeParametricSurface(FakeSurface):
    def __init__(self, uv_func=None, **kwargs):
        super().__init__(**kwargs)


class FakeLateralForwarder(FakeMobject):
    """Consumes **kwargs and forwards them LATERALLY (not via super) to a VMobject.
    Static MRO introspection can't see the real destination -> must NOT flag.
    Models BulletedList / VectorizedPoint."""
    def __init__(self, **kwargs):
        self._inner = FakeVMobject(**kwargs)
        super().__init__()


_ACCEPTS = sigs.accepted_kwargs  # (cls) -> KwargSpec


class TestAcceptedKwargs:
    def test_vmobject_names_fill_color_via_mro(self):
        spec = _ACCEPTS(FakeSquare)
        assert "fill_color" in spec.named  # named on FakeVMobject, reachable in MRO
        assert "side_length" in spec.named  # named on FakeSquare itself
        assert "color" in spec.named  # from FakeMobject

    def test_closed_mobject_has_no_var_keyword(self):
        spec = _ACCEPTS(FakeMobject)
        assert not spec.chain_is_open  # no **kwargs anywhere -> closed

    def test_surface_chain_terminates_closed(self):
        # FakeSurface has **kwargs but it forwards to the closed FakeMobject.
        spec = _ACCEPTS(FakeParametricSurface)
        assert spec.chain_terminates_closed, "Surface chain bottoms out at closed Mobject"


class TestProvablyInvalid:
    def test_fill_color_NOT_flagged_on_vmobject(self):
        # The #55 case: must be left alone.
        assert not sigs.is_provably_invalid_kwarg(FakeSquare, "fill_color")

    def test_unknown_kwarg_flagged_on_surface(self):
        # fill_color reaches the closed FakeMobject -> provably invalid.
        assert sigs.is_provably_invalid_kwarg(FakeParametricSurface, "fill_color")

    def test_hallucinated_kwarg_flagged_on_vmobject(self):
        # A kwarg in NEITHER the MRO nor the VMobject set, on a closed-terminating
        # chain, is provably invalid even on a VMobject descendant.
        assert sigs.is_provably_invalid_kwarg(FakeSquare, "flash_radius")

    def test_lateral_forwarder_never_flagged(self):
        # Even though FakeLateralForwarder's own __init__ shows only **kwargs with no
        # super-forward of them, it routes to a VMobject -> must NOT flag.
        assert not sigs.is_provably_invalid_kwarg(
            FakeLateralForwarder, "fill_color",
            lateral_exceptions={"FakeLateralForwarder"},
        )

    def test_named_kwarg_never_flagged(self):
        assert not sigs.is_provably_invalid_kwarg(FakeSquare, "side_length")
        assert not sigs.is_provably_invalid_kwarg(FakeSquare, "color")


class TestFailOpen:
    def test_unintrospectable_init_never_flags(self):
        class Weird:
            __init__ = None  # signature() will raise
        # Must not raise, must not flag.
        assert not sigs.is_provably_invalid_kwarg(Weird, "anything")

    def test_open_chain_never_flags(self):
        class OpenAll:
            def __init__(self, **kwargs):
                pass  # no super, **kwargs goes nowhere provable -> open
        assert not sigs.is_provably_invalid_kwarg(OpenAll, "whatever")


class TestShadowCheckCode:
    def test_flags_surface_fill_color_in_code(self, monkeypatch):
        # Inject our fake module as the resolution target.
        monkeypatch.setattr(sigs, "_resolve_class",
                            lambda name, aliases: {"ParametricSurface": FakeParametricSurface,
                                                   "Square": FakeSquare}.get(name))
        code = "s = ParametricSurface(uv_func=f, fill_color=BLUE)\n"
        flagged = sigs.shadow_check_kwargs(code)
        assert any(fk.kwarg == "fill_color" and fk.class_name == "ParametricSurface"
                   for fk in flagged)

    def test_does_not_flag_square_fill_color(self, monkeypatch):
        monkeypatch.setattr(sigs, "_resolve_class",
                            lambda name, aliases: {"Square": FakeSquare}.get(name))
        code = "s = Square(fill_color=BLUE, color=GREY_B)\n"
        flagged = sigs.shadow_check_kwargs(code)
        assert flagged == []

    def test_unresolvable_class_not_flagged(self, monkeypatch):
        monkeypatch.setattr(sigs, "_resolve_class", lambda name, aliases: None)
        code = "x = MyHelper(foo=1)\n"
        assert sigs.shadow_check_kwargs(code) == []

    def test_method_call_not_flagged(self, monkeypatch):
        monkeypatch.setattr(sigs, "_resolve_class", lambda name, aliases: FakeSquare)
        code = "obj.some_method(bogus=1)\n"  # attribute call, not a constructor
        assert sigs.shadow_check_kwargs(code) == []

    def test_kwargs_splat_skips_call(self, monkeypatch):
        monkeypatch.setattr(sigs, "_resolve_class", lambda name, aliases: FakeParametricSurface)
        code = "s = ParametricSurface(**opts)\n"  # can't know what's in opts
        assert sigs.shadow_check_kwargs(code) == []

    def test_syntax_error_returns_empty(self):
        assert sigs.shadow_check_kwargs("def (((") == []


@pytest.mark.skipif(
    sigs.load_manimlib_signatures() is None,
    reason="manimlib not importable (no GL) — logic covered by fake-hierarchy tests",
)
class TestRealManimlib:
    def test_square_fill_color_not_flagged(self):
        flagged = sigs.shadow_check_kwargs("s = Square(fill_color=BLUE, color=GREY_B)\n")
        assert flagged == []

    def test_parametric_surface_fill_color_flagged(self):
        flagged = sigs.shadow_check_kwargs("s = ParametricSurface(uv_func=f, fill_color=BLUE)\n")
        assert any(fk.kwarg == "fill_color" for fk in flagged)
