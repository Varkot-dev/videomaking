"""Attack corpus for the whole-tree scene safety gate (#87, audit R20).

Every payload here is an inert string. Nothing is executed: the gate only
parses source with ``ast.parse`` and these tests assert what it reports. The
payloads must contain the real primitive names to mean anything, so they are
written out plainly.

The gate is a static denylist plus an import allowlist. It stops naive and
moderately obfuscated payloads; it is NOT a sandbox (see the module docstring
of ``manimgen.validator.scene_ast_gate`` and the security notes in the README).
"""

from __future__ import annotations

import glob
import os
import textwrap

import pytest

from manimgen.validator.scene_ast_gate import (
    inspect_scene_code,
    inspect_scene_file,
)

pytestmark = pytest.mark.security

_EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "manimgen",
    "examples",
)


def _in_construct(body: str) -> str:
    """Place ``body`` inside construct(), the place the old gate never looked."""
    return (
        "from manimlib import *\n\n\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        + textwrap.indent(textwrap.dedent(body).strip("\n"), " " * 8)
        + "\n"
    )


# Payloads placed inside construct(). Each must be rejected.
CONSTRUCT_PAYLOADS = {
    # Imports inside a method body
    "import_os": "import os\nos.system('id')",
    "import_subprocess": "import subprocess\nsubprocess.run(['id'])",
    "from_subprocess": "from subprocess import run\nrun(['id'])",
    "import_socket": "import socket\nsocket.create_connection(('example.com', 80))",
    "import_requests": "import requests\nrequests.get('x')",
    "import_ctypes": "import ctypes\nctypes.CDLL('libc.so.6')",
    "import_importlib": "import importlib\nimportlib.import_module('os')",
    "import_builtins": "import builtins\nbuiltins.eval('1')",
    "import_pathlib": "import pathlib\npathlib.Path('x').write_text('y')",
    "import_typing_hints": "import typing\ntyping.get_type_hints(self.construct)",
    "import_numpy_load": "from numpy import load\nload('x.npy', allow_pickle=True)",
    "relative_import": "from . import helper",
    # Builtins that execute or reach the filesystem
    "dunder_import": "__import__('os').system('id')",
    "eval": "eval('1+1')",
    "exec": "exec('x = 1')",
    "compile": "compile('x', 'f', 'exec')",
    "open_read": "open('~/.ssh/id_rsa').read()",
    "open_write": "open('x', 'w').write('x')",
    "builtins_name": "__builtins__['eval']('1')",
    "globals_index": "globals()['os'].system('id')",
    "locals_call": "locals()",
    "vars_call": "vars(np)['save']('x', np.zeros(1))",
    "breakpoint": "breakpoint()",
    "input": "input()",
    "help_pager": "help(np)",
    "exit": "exit()",
    "fullwidth_eval": "\uff45\uff56\uff41\uff4c('1')",
    # Modules that `from manimlib import *` leaks into the scene namespace
    "os_no_import": "os.system('id')",
    "sys_modules": "sys.modules['os'].system('id')",
    "pickle_leak": "pickle.loads(x)",
    "tempfile_leak": "tempfile.mkstemp()",
    "urllib_leak": "urllib.request.urlopen(u)",
    "inspect_leak": "inspect.currentframe().f_back.f_globals",
    "platform_leak": "platform.uname()",
    "operator_attrgetter": "op.attrgetter(n)(self)",
    "pyperclip_leak": "pyperclip.paste()",
    "path_write": "Path('x').write_text('y')",
    "pil_image_save": "Image.new('RGB', (1, 1)).save('x.png')",
    "manim_config": "manim_config.file_writer.ffmpeg_bin = 'calc'",
    "get_ipython": "get_ipython()",
    "checkpoint_paste": "CheckpointManager().checkpoint_paste()",
    "clear_cache": "clear_cache()",
    "manimlib_module_walk": "manimlib.utils.file_ops.os.system('id')",
    # getattr family on computed or dangerous names
    "getattr_dynamic": "getattr(np, 'sa' + 've')('x', np.zeros(1))",
    "getattr_const_denied": "getattr(np, 'save')('x', np.zeros(1))",
    "getattr_const_dunder": "getattr(self, '__dict__')",
    "getattr_alias": "g = getattr\ng(np, 'sa' + 've')",
    "getattr_partial": "from functools import partial\npartial(getattr, np)(n)",
    "getattr_const_private": "getattr(random, '_os')",
    "setattr_dynamic": "setattr(np, n, None)",
    "delattr_dynamic": "delattr(self, n)",
    # Dunder and frame walking
    "mro_walk": "().__class__.__mro__[1].__subclasses__()",
    "bases_walk": "self.__class__.__bases__",
    "func_globals": "self.construct.__globals__['os']",
    "dunder_dict": "self.__dict__",
    "mro_method": "type(self).mro()",
    "generator_frame": "(x for x in ()).gi_frame.f_globals['os'].system('id')",
    "traceback_frame": (
        "try:\n"
        "    1 / 0\n"
        "except Exception as e:\n"
        "    e.__traceback__.tb_frame.f_globals['os']"
    ),
    "private_module_attr": "import random\nrandom._os.replace('a', 'b')",
    "dataclasses_create_fn": "import dataclasses\ndataclasses._create_fn('f', [], ['pass'])",
    "logger_console_save": "log.handlers[0].console.save_text('x.txt')",
    "pygments_load_file": "pygments.lexers.load_lexer_from_file('x.py')",
    "format_traversal": "'{0.__init__.__globals__}'.format(self)",
    # numpy and Mobject file / pickle sinks
    "np_save": "np.save('x.npy', np.zeros(3))",
    "np_load": "np.load('x.npy', allow_pickle=True)",
    "np_tofile": "np.zeros(3).tofile('x')",
    "np_ctypes": "np.zeros(3).ctypes.data",
    "alias_then_save": "s = np\ns.save('x', np.zeros(1))",
    "mobject_deserialize": "Mobject().deserialize(payload)",
    "bytes_literal": "data = b'cos\\nsystem\\n'",
    # Scene hooks that spawn processes or rewrite render settings
    "embed": "self.embed()",
    "scene_show": "self.show()",
    "ffmpeg_bin_attr": "self.file_writer.ffmpeg_bin = 'calc'",
    "file_writer_kwarg": "Scene(file_writer_config={})",
    "ffmpeg_bin_key": "cfg = {'ffmpeg_bin': 'calc'}",
    # Network / UNC fetches done by manimlib itself
    "http_image": "ImageMobject('https://example.com/a.png')",
    "unc_svg": "SVGMobject('\\\\\\\\attacker\\\\share\\\\x.svg')",
    "tex_write18": "Tex(r'\\immediate\\write18{id}')",
    "tex_input": "Tex(r'\\input{/etc/passwd}')",
    # Hiding places inside construct()
    "nested_function": "def inner():\n    import os\n    os.system('id')\ninner()",
    "lambda_default_arg": "f = lambda x=__import__('os'): x",
    "def_default_arg": "def f(x=__import__('os')):\n    pass",
    "comprehension": "[__import__('os') for _ in range(1)]",
    "walrus": "(o := __import__('os'))",
    "fstring": "t = f\"{__import__('os').system('id')}\"",
    "decorated_inner": "@__import__('os').system\ndef f():\n    pass",
}


@pytest.mark.parametrize("name", sorted(CONSTRUCT_PAYLOADS))
def test_payload_inside_construct_is_rejected(name):
    result = inspect_scene_code(_in_construct(CONSTRUCT_PAYLOADS[name]))
    assert not result.ok, f"{name} passed the gate"
    assert result.findings
    # Findings carry a line number so the retry prompt can point at the line.
    assert all(f.startswith("line ") or "class" in f for f in result.findings)


# Whole-module payloads (class body, decorators, bases, encodings).
MODULE_PAYLOADS = {
    "class_body_statement": (
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    x = __import__('os').system('id')\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "lambda_class_decorator": (
        "from manimlib import *\n"
        "@(lambda c: c)\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "call_base_class": (
        "from manimlib import *\n"
        "class S(type('X', (Scene,), {})):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "metaclass_keyword": (
        "from manimlib import *\n"
        "class S(Scene, metaclass=M):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "interactive_scene_base": (
        "from manimlib import *\n"
        "class S(InteractiveScene):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "file_writer_class_attr": (
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    default_file_writer_config = dict(ffmpeg_bin='calc')\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "method_decorator": (
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    @__import__('os').system\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    "numpy_star_import": (
        "from numpy import *\n"
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        save('x', zeros(1))\n"
    ),
    "top_level_import_os": (
        "from manimlib import *\n"
        "import os\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
    # A str parse ignores a coding cookie, but Python executes the file's
    # bytes and honours it: under UTF-7 "+AAo-" is a newline, so the comment
    # below hides a real statement from any gate that parses decoded text.
    "utf7_cookie": (
        "# coding: utf-7\n"
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        pass  #+AAo-        os.system('id')\n"
    ),
    "latin1_cookie": (
        "# -*- coding: latin-1 -*-\n"
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        pass\n"
    ),
}


@pytest.mark.parametrize("name", sorted(MODULE_PAYLOADS))
def test_module_payload_is_rejected(name):
    result = inspect_scene_code(MODULE_PAYLOADS[name])
    assert not result.ok, f"{name} passed the gate"
    assert result.findings


def test_utf7_cookie_file_is_rejected(tmp_path):
    p = tmp_path / "s.py"
    p.write_bytes(MODULE_PAYLOADS["utf7_cookie"].encode("ascii"))
    result = inspect_scene_file(str(p))
    assert not result.ok
    assert any("coding" in f for f in result.findings)


def test_non_utf8_file_is_rejected(tmp_path):
    p = tmp_path / "s.py"
    p.write_bytes(b"from manimlib import *\nx = '\xff'\n")
    assert not inspect_scene_file(str(p)).ok


def test_unreadable_file_fails_closed(tmp_path):
    result = inspect_scene_file(str(tmp_path / "missing.py"))
    assert not result.ok


def test_findings_name_the_line_and_the_reason():
    result = inspect_scene_code(_in_construct("x = 1\nos.system('id')"))
    assert not result.ok
    # construct body starts on line 6; the payload is its second line.
    assert any(f.startswith("line 7:") and "os" in f for f in result.findings), (
        result.findings
    )


# ---------------------------------------------------------------------------
# Legitimate scenes must keep passing.
# ---------------------------------------------------------------------------


def _example_files() -> list[str]:
    return sorted(glob.glob(os.path.join(_EXAMPLES_DIR, "*.py")))


def test_examples_dir_is_not_empty():
    assert len(_example_files()) >= 30


@pytest.mark.parametrize(
    "path", _example_files(), ids=lambda p: os.path.basename(p)
)
def test_every_example_scene_passes(path):
    result = inspect_scene_file(path)
    assert result.ok, f"{os.path.basename(path)}: {result.findings}"


DIRECTOR_STYLE_SCENE = '''"""Section scene."""
from __future__ import annotations

from manimlib import *
import numpy as np
import math
import random
import colorsys
from itertools import product
from functools import reduce
from collections import deque
from typing import List


PALETTE = [BLUE, TEAL, YELLOW]


def helper_curve(t):
    return np.array([np.cos(t), np.sin(t), 0])


class Section01Scene(ThreeDScene):
    """
    techniques: fade_reveal
    """

    def construct(self):
        axes = Axes(x_range=[-3, 3, 1], y_range=[-2, 2, 1])
        graph = axes.get_graph(lambda x: np.sin(x) * math.exp(-x * x / 4), color=TEAL)
        t = ValueTracker(0)
        dot = always_redraw(lambda: Dot(axes.input_to_graph_point(t.get_value(), graph)))
        boxes = VGroup(*[Square().shift(RIGHT * i) for i in range(4)])
        box_list = list(boxes)
        box_list[0], box_list[1] = box_list[1], box_list[0]
        first = boxes[0]
        sub = boxes[1:3]
        labels = {k: Text(f"x = {k:.2f}", font_size=24) for k in np.linspace(0, 1, 3)}
        pairs = [(a, b) for a, b in product(range(2), repeat=2) if a != b]
        total = reduce(lambda a, b: a + b, [1, 2, 3])
        queue: List[int] = list(deque([1, 2]))
        rgb = colorsys.hsv_to_rgb(0.5, 0.5, 0.5)
        norm = np.linalg.norm(np.array([1.0, 2.0, 3.0]))
        angle = np.arctan2(1, 2) + PI / 4
        rng = random.Random(0)
        jitter = rng.uniform(-0.1, 0.1)

        def make_label(text, color=WHITE):
            return Text(text, font_size=28, color=color)

        name = type(self).__name__
        cls_name = self.__class__.__name__
        stroke = getattr(first, "stroke_width", 2)
        has_frame = hasattr(self, "frame")
        inner = first.submobjects if hasattr(first, "submobjects") else []
        code = Text("def __init__(self): return None", font="Courier New")
        self.frame.reorient(20, 70)
        self.frame.add_updater(lambda m, dt: m.increment_theta(0.1 * dt))
        self.play(ShowCreation(graph), FadeIn(dot), run_time=1.5)
        self.play(t.animate.set_value(2), run_time=2)
        self.play(*[FadeOut(m) for m in self.mobjects])
        self.remove(dot)
        self.wait(max(0.01, 2.0 - total * 0.1))
'''


def test_director_style_scene_passes():
    result = inspect_scene_code(DIRECTOR_STYLE_SCENE)
    assert result.ok, result.findings


def test_super_init_in_scene_method_passes():
    code = _in_construct("pass") + (
        "\n    def setup(self):\n        super().setup()\n"
    )
    result = inspect_scene_code(code)
    assert result.ok, result.findings


def test_static_and_property_decorators_pass():
    code = (
        "from manimlib import *\n"
        "class S(Scene):\n"
        "    @staticmethod\n"
        "    def f(x):\n"
        "        return x\n"
        "    def construct(self):\n"
        "        self.wait(self.f(1))\n"
    )
    result = inspect_scene_code(code)
    assert result.ok, result.findings


def test_utf8_cookie_is_accepted():
    code = "# -*- coding: utf-8 -*-\n" + _in_construct("self.wait(1)")
    assert inspect_scene_code(code).ok
