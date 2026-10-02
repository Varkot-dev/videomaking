"""Pre-execution safety gate for LLM-authored ManimGL scene code (#27, #87).

A generated scene file is handed to ``manimgl <file> <Class>``, which imports
the module and then runs ``construct()``. Everything in the file therefore runs
with the full rights of the user who started the pipeline. The code comes from
an LLM (the Director, the retry fixes), and the LLM reads the topic or PDF the
user supplied, so a prompt-injected source document could steer it into
writing code that reads, deletes or sends files.

What this gate does
-------------------
It parses the source (never runs it) and walks the WHOLE syntax tree: module
body, class bodies, method bodies, nested functions, lambdas, comprehensions,
decorators, default arguments, f-strings. It rejects:

- imports of anything outside a small allowlist of pure modules
  (``manimlib``, ``numpy``, ``math``, ``random``, ``itertools`` ...), relative
  imports, star imports other than ``from manimlib import *``, and imported
  names that are themselves dangerous (``from numpy import load``);
- builtins that execute code or touch the outside world (``exec``, ``eval``,
  ``compile``, ``open``, ``__import__``, ``globals``, ``locals``, ``vars``,
  ``breakpoint``, ``input``, ``help`` ...);
- names that ``from manimlib import *`` leaks into the scene namespace
  (manimgl 1.7.2 modules define no ``__all__``, so ``os``, ``sys``,
  ``pickle``, ``tempfile``, ``urllib``, ``inspect``, ``Path``, ``Image``,
  ``manim_config`` ... are all in scope without any import);
- ``getattr`` / ``setattr`` / ``delattr`` unless the attribute is a constant
  string that is itself allowed;
- dunder attribute access other than a small safe set (``__init__``,
  ``__name__``, ``__class__``, ``__qualname__``, ``__doc__``, read only), and
  frame / code-object attributes (``gi_frame``, ``f_globals``, ``tb_frame``
  ...) that reach the module globals without a dunder;
- attributes that write files, load pickles, spawn processes or open
  sockets whatever the receiver is (``.system``, ``.save``, ``.load``,
  ``.tofile``, ``.deserialize``, ``.embed``, ``.ffmpeg_bin`` ...);
- class decorators, non-name base classes, class keywords (``metaclass=``),
  and function decorators other than ``staticmethod`` / ``classmethod`` /
  ``property``;
- bytes literals, URL and UNC-path string literals (manimlib downloads an
  http(s) path given to ``ImageMobject`` / ``SVGMobject``; a ``\\\\host\\share``
  path makes Windows open an SMB connection), TeX primitives that read or write
  files or run commands (``\\write18``, ``\\input``, ``\\openout`` ...), and
  ``str.format`` field traversal through dunders;
- a PEP 263 coding declaration other than UTF-8: a gate that parses decoded
  text ignores the cookie, but Python executes the file's bytes and honours
  it, so ``# coding: utf-7`` can hide whole statements inside a comment.

Top-level structure is also checked: only a docstring, allowed imports,
function definitions, plain assignments and exactly one class may appear at
module scope.

Every finding names the line and a short reason, so the retry prompt can tell
the model exactly what to remove.

Wiring: :func:`manimgen.validator.render_command.run_manimgl` is the single
entry point for every manimgl render (first pass, retry, fallback) and refuses
to start manimgl on a rejected file (a hard block, not a warning). The
generator also checks each new scene, and ``retry`` discards a rejected LLM fix
instead of rendering it.

What this gate does NOT do
--------------------------
It is a static denylist plus an import allowlist. **It is not a sandbox.**
Python is dynamic enough that a determined author can reach a dangerous
object through a path no static list anticipates (for example by assembling a
config key or an attribute name at runtime and passing it through an API that
looks things up by name). The rendered scene still runs with the user's full
rights. Real containment (a launcher that installs a PEP 578 audit hook
denying sockets, unexpected process spawns and writes outside the output
folder) is deferred; until it exists, only use topics and PDFs from sources
you trust. See the "Security" section of the README.

Codeguard is not a security boundary either: its banned patterns are ManimGL
API-compatibility rules (see ``tests/test_scene_ast_gate.py``).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# Root modules a scene may import. Pure computation only: no filesystem,
# process, network, introspection or code-loading modules.
_ALLOWED_IMPORT_MODULES = frozenset(
    {
        "__future__",
        "manimlib",
        "numpy",
        "math",
        "cmath",
        "random",
        "itertools",
        "functools",
        "typing",
        "colorsys",
        "dataclasses",
        "enum",
        "collections",
        "fractions",
        "decimal",
        "statistics",
        "bisect",
        "heapq",
        "copy",
        "textwrap",
    }
)

# Submodules of an allowed root that are NOT allowed (file, pickle, ctypes and
# compiler access inside numpy; manimlib internals that write files, run LaTeX
# or open windows are reached through denied names, see below).
_DENIED_SUBMODULES = frozenset(
    {
        "numpy.lib",
        "numpy.ctypeslib",
        "numpy.f2py",
        "numpy.distutils",
        "numpy.testing",
        "numpy.core",
        "numpy._core",
        "manimlib.utils.file_ops",
        "manimlib.utils.directories",
        "manimlib.utils.cache",
        "manimlib.utils.tex_file_writing",
        "manimlib.utils.shaders",
        "manimlib.utils.sounds",
        "manimlib.config",
        "manimlib.scene.scene_file_writer",
        "manimlib.scene.scene_embed",
        "manimlib.scene.interactive_scene",
        "manimlib.window",
        "manimlib.extract_scene",
        "manimlib.__main__",
    }
)

# Bare identifiers a scene may not reference in any context.
_DENIED_NAMES = frozenset(
    {
        # Builtins that execute code, read input or reach the outside world.
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "help",
        "exit",
        "quit",
        "copyright",
        "credits",
        "license",
        "memoryview",
        # Modules (imported or leaked by `from manimlib import *`).
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "ctypes",
        "importlib",
        "builtins",
        "pickle",
        "marshal",
        "shelve",
        "tempfile",
        "urllib",
        "http",
        "requests",
        "io",
        "glob",
        "inspect",
        "platform",
        "signal",
        "multiprocessing",
        "threading",
        "asyncio",
        "types",
        "gc",
        "pkg_resources",
        "pyperclip",
        "appdirs",
        "moderngl",
        "mglw",
        "gl",
        "op",
        "operator",
        "se",
        "pygments",
        "screeninfo",
        "manimlib",
        "log",
        # manimlib exports that write files, load pickles, run LaTeX, download,
        # open windows or shells, or change render settings.
        "Path",
        "Image",
        "ET",
        "Cache",
        "pyplot",
        "get_ipython",
        "manim_config",
        "get_manim_dir",
        "SceneFileWriter",
        "CheckpointManager",
        "InteractiveSceneEmbed",
        "InteractiveScene",
        "Window",
        "PygletWindow",
        "EVENT_DISPATCHER",
        "cache_on_disk",
        "clear_cache",
        "find_file",
        "guarantee_existence",
        "latex_to_svg",
        "full_tex_to_svg",
        "get_shader_code_from_file",
        "get_shader_program",
        "image_path_to_texture",
        "invert_image",
        "get_full_raster_image_path",
        "get_full_vector_image_path",
        "get_full_sound_file_path",
        "get_directories",
        "get_downloads_dir",
        "get_temp_dir",
        "get_cache_dir",
        "get_output_dir",
        # Render settings that pick the ffmpeg binary or output folder.
        "default_file_writer_config",
        "file_writer_config",
        "file_writer",
        "ffmpeg_bin",
        "output_directory",
    }
)

# getattr-family builtins: allowed only with a constant, allowed attribute name.
_ATTR_BUILTINS = frozenset({"getattr", "setattr", "delattr"})

# Dunder attributes a scene may READ. Enough for super().__init__(...) and
# type(self).__name__; not enough to walk to globals or subclasses.
_SAFE_DUNDER_ATTRS = frozenset(
    {"__init__", "__name__", "__class__", "__qualname__", "__doc__"}
)

# Attribute names rejected whatever the receiver is (static analysis cannot
# know the receiver's type, so `x = np; x.save(...)` must still fail).
_DENIED_ATTRS = frozenset(
    {
        # Frame / code objects: reach module globals without any dunder.
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_back",
        "f_code",
        "tb_frame",
        "tb_next",
        "co_code",
        "func_globals",
        "mro",
        # Process spawning and environment.
        "system",
        "popen",
        "Popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnv",
        "spawnve",
        "spawnvp",
        "posix_spawn",
        "posix_spawnp",
        "startfile",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "fork",
        "forkpty",
        "kill",
        "killpg",
        "getoutput",
        "getstatusoutput",
        "check_output",
        "check_call",
        "environ",
        "putenv",
        # Filesystem.
        "open",
        "unlink",
        "rmdir",
        "removedirs",
        "rmtree",
        "copyfile",
        "copytree",
        "copy2",
        "copymode",
        "copystat",
        "chmod",
        "chown",
        "rename",
        "renames",
        "symlink",
        "makedirs",
        "mkdir",
        "listdir",
        "scandir",
        "write_text",
        "write_bytes",
        "read_text",
        "read_bytes",
        # Network.
        "urlopen",
        "urlretrieve",
        "create_connection",
        "request",
        # Code loading and evaluation.
        "exec",
        "eval",
        "compile",
        "import_module",
        "load_module",
        "exec_module",
        "find_spec",
        "modules",
        "get_type_hints",
        "get_annotations",
        "ForwardRef",
        "_evaluate",
        "_eval_type",
        "evaluate_forward_ref",
        "attrgetter",
        "methodcaller",
        "load_lexer_from_file",
        "load_formatter_from_file",
        # rich Console (manimlib's logger handler) writes files.
        "save_html",
        "save_text",
        "save_svg",
        # Pickle and numpy file I/O (np.load(allow_pickle=True) runs code).
        "load",
        "loads",
        "dump",
        "dumps",
        "Unpickler",
        "serialize",
        "deserialize",
        "save",
        "savez",
        "savez_compressed",
        "savetxt",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "fromregex",
        "tofile",
        "memmap",
        "DataSource",
        "ctypes",
        "ctypeslib",
        "f2py",
        "distutils",
        "lib",
        "testing",
        # Modules reached as attributes (manimlib.utils.file_ops.os, ...).
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "importlib",
        "builtins",
        "pickle",
        "urllib",
        "tempfile",
        "inspect",
        # Scene hooks: embed opens an IPython shell, show spawns an image
        # viewer, checkpoint_paste execs the clipboard, the file writer picks
        # the ffmpeg binary and the output folder.
        "embed",
        "show",
        "checkpoint_paste",
        "file_writer",
        "file_writer_config",
        "default_file_writer_config",
        "ffmpeg_bin",
        "output_directory",
    }
)

# Keyword-argument names and exact dict-key strings that change where manimgl
# writes or which binary it runs.
_DENIED_CONFIG_KEYS = frozenset(
    {
        "file_writer_config",
        "default_file_writer_config",
        "file_writer",
        "ffmpeg_bin",
        "output_directory",
    }
)

_ALLOWED_DECORATORS = frozenset({"staticmethod", "classmethod", "property"})

# http(s)/ftp/file URLs anywhere in a string literal.
_URL_RE = re.compile(r"(?i)\b(?:https?|ftps?|file|smb)://")
# Windows UNC paths: \\host\share or //host/share at the start of a string.
_UNC_RE = re.compile(r"^(?:\\\\|//)[A-Za-z0-9._$-]+[\\/]")
# TeX primitives that read or write files or run shell commands.
_TEX_RE = re.compile(
    r"\\(?:write18|immediate|openout|openin|input|include|includeonly|"
    r"read|readline|directlua|catcode|csname|special|ShellEscape|"
    r"pdfshellescape)(?![A-Za-z])"
)
# str.format field traversal into dunders: "{0.__init__.__globals__}".
_FORMAT_DUNDER_RE = re.compile(r"\{[^{}]*__\w+[^{}]*\}")
# PEP 263 coding declaration (first two lines only).
_CODING_RE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
_UTF8_NAMES = frozenset({"utf-8", "utf8", "utf_8", "utf-8-sig", "utf_8_sig"})


@dataclass(frozen=True)
class GateResult:
    """Outcome of inspecting one scene module.

    Attributes:
        ok:       True when no disallowed construct was found.
        findings: Human-readable description of each rejected construct,
                  prefixed with ``line N:`` (1-based) where a line is known.
        class_count: Number of top-level ClassDef nodes seen (a valid scene
                  has exactly one).
    """

    ok: bool
    findings: list[str] = field(default_factory=list)
    class_count: int = 0


GATE_ERROR_HEADER = (
    "SceneSafetyGateError: the scene was not rendered because it uses "
    "constructs that are not allowed in generated scenes. Remove every "
    "construct listed below. Scenes may only use `from manimlib import *`, "
    "numpy, math and similar pure modules; no file, process, network, "
    "eval/exec or dunder access."
)


def format_gate_error(result: GateResult) -> str:
    """Render a rejected :class:`GateResult` as render-error text (stderr)."""
    return GATE_ERROR_HEADER + "\n" + "\n".join(f"- {f}" for f in result.findings)


def _line(node: ast.AST) -> str:
    return f"line {getattr(node, 'lineno', '?')}"


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _attr_name_problem(name: str, store: bool = False) -> str | None:
    """Why attribute ``name`` is not allowed, or None if it is."""
    if _is_dunder(name):
        if store or name not in _SAFE_DUNDER_ATTRS:
            return f"dunder attribute {name!r}"
        return None
    if name.startswith("_"):
        # Private attributes lead into library internals: random._os,
        # dataclasses._create_fn (an exec wrapper), numpy._core ...
        return f"private attribute {name!r}"
    if name in _DENIED_ATTRS:
        return f"attribute {name!r}"
    return None


def _module_allowed(module: str) -> bool:
    root = module.split(".")[0]
    if root not in _ALLOWED_IMPORT_MODULES:
        return False
    return not any(
        module == denied or module.startswith(denied + ".")
        for denied in _DENIED_SUBMODULES
    )


def _check_import(node: ast.Import | ast.ImportFrom, findings: list[str]) -> None:
    allowed = sorted(_ALLOWED_IMPORT_MODULES - {"__future__"})
    if isinstance(node, ast.ImportFrom):
        if node.level:
            findings.append(f"{_line(node)}: relative import is not allowed")
            return
        module = node.module or ""
        if not _module_allowed(module):
            findings.append(
                f"{_line(node)}: import from {module!r} is not allowed "
                f"(allowed modules: {', '.join(allowed)})"
            )
            return
        for alias in node.names:
            if alias.name == "*":
                if module not in ("manimlib", "__future__"):
                    findings.append(
                        f"{_line(node)}: star import from {module!r} is not "
                        "allowed (only `from manimlib import *`)"
                    )
                continue
            if (
                alias.name in _DENIED_NAMES
                or alias.name in _DENIED_ATTRS
                or _is_dunder(alias.name)
            ):
                findings.append(
                    f"{_line(node)}: importing {alias.name!r} from {module!r} "
                    "is not allowed"
                )
            if alias.asname and alias.asname in _DENIED_NAMES:
                findings.append(
                    f"{_line(node)}: import alias {alias.asname!r} is not allowed"
                )
        return
    for alias in node.names:
        if not _module_allowed(alias.name):
            findings.append(
                f"{_line(node)}: import of {alias.name!r} is not allowed "
                f"(allowed modules: {', '.join(allowed)})"
            )
        elif alias.asname and alias.asname in _DENIED_NAMES:
            findings.append(
                f"{_line(node)}: import alias {alias.asname!r} is not allowed"
            )


def _check_string(node: ast.Constant, findings: list[str]) -> None:
    value = node.value
    if isinstance(value, bytes):
        findings.append(f"{_line(node)}: bytes literal is not allowed")
        return
    if not isinstance(value, str):
        return
    if _URL_RE.search(value):
        findings.append(f"{_line(node)}: URL string literal is not allowed")
    if _UNC_RE.search(value):
        findings.append(f"{_line(node)}: network (UNC) path literal is not allowed")
    if _TEX_RE.search(value):
        findings.append(
            f"{_line(node)}: TeX file/shell primitive in string is not allowed"
        )
    if _FORMAT_DUNDER_RE.search(value):
        findings.append(
            f"{_line(node)}: format field that walks dunder attributes is not allowed"
        )
    if value in _DENIED_CONFIG_KEYS or (
        _is_dunder(value) and value not in _SAFE_DUNDER_ATTRS
    ):
        findings.append(f"{_line(node)}: string {value!r} is not allowed")


def _check_attr_builtin_call(node: ast.Call, findings: list[str]) -> None:
    """getattr/setattr/delattr: only with a constant, allowed attribute name."""
    func = node.func
    if not isinstance(func, ast.Name):
        return
    name_arg = node.args[1] if len(node.args) >= 2 else None
    if not (isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str)):
        findings.append(
            f"{_line(node)}: {func.id}() with a computed attribute name is not allowed"
        )
        return
    problem = _attr_name_problem(name_arg.value, store=func.id != "getattr")
    if problem:
        findings.append(f"{_line(node)}: {func.id}() of {problem} is not allowed")


def _check_decorators(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    findings: list[str],
) -> None:
    for deco in node.decorator_list:
        if isinstance(node, ast.ClassDef):
            findings.append(f"{_line(deco)}: class decorator is not allowed")
            continue
        if isinstance(deco, ast.Name) and deco.id in _ALLOWED_DECORATORS:
            continue
        # `@prop.setter` / `@prop.deleter` on a property defined in the class.
        if (
            isinstance(deco, ast.Attribute)
            and isinstance(deco.value, ast.Name)
            and deco.attr in ("setter", "getter", "deleter")
        ):
            continue
        findings.append(
            f"{_line(deco)}: decorator is not allowed "
            f"(only {', '.join(sorted(_ALLOWED_DECORATORS))})"
        )


def _check_class(node: ast.ClassDef, findings: list[str]) -> None:
    _check_decorators(node, findings)
    for base in node.bases:
        if not isinstance(base, ast.Name):
            findings.append(
                f"{_line(base)}: class base must be a plain name "
                f"(got {type(base).__name__})"
            )
    for kw in node.keywords:
        findings.append(
            f"{_line(node)}: class keyword {kw.arg or '**'!r} is not allowed"
        )


def _check_node(node: ast.AST, findings: list[str], called: set[int]) -> None:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _check_import(node, findings)
    elif isinstance(node, ast.Name):
        if node.id in _ATTR_BUILTINS and id(node) not in called:
            # `f = getattr` or `partial(getattr, x)` would dodge the
            # constant-name check below, so the bare reference is refused.
            findings.append(f"{_line(node)}: {node.id} may only be called directly")
        elif node.id in _DENIED_NAMES:
            findings.append(f"{_line(node)}: name {node.id!r} is not allowed")
        elif _is_dunder(node.id) and node.id not in ("__name__", "__doc__"):
            findings.append(f"{_line(node)}: dunder name {node.id!r} is not allowed")
    elif isinstance(node, ast.Attribute):
        problem = _attr_name_problem(
            node.attr, store=not isinstance(node.ctx, ast.Load)
        )
        if problem:
            findings.append(f"{_line(node)}: {problem} is not allowed")
    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _ATTR_BUILTINS:
            _check_attr_builtin_call(node, findings)
        for kw in node.keywords:
            if kw.arg in _DENIED_CONFIG_KEYS:
                findings.append(f"{_line(node)}: keyword {kw.arg!r} is not allowed")
    elif isinstance(node, ast.Constant):
        _check_string(node, findings)
    elif isinstance(node, ast.ClassDef):
        _check_class(node, findings)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _check_decorators(node, findings)


def _check_top_level(tree: ast.Module, findings: list[str]) -> int:
    """Module-scope shape. Returns the number of top-level classes."""
    class_count = 0
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_count += 1
        elif isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.FunctionDef,
                ast.Assign,
                ast.AnnAssign,
            ),
        ):
            continue
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # docstring or inert literal
        else:
            findings.append(
                f"{_line(node)}: disallowed top-level {type(node).__name__}"
            )
    if class_count == 0:
        findings.append("no top-level Scene class found")
    elif class_count > 1:
        findings.append(f"{class_count} top-level classes found (expected exactly 1)")
    return class_count


def _coding_problem(source: bytes) -> str | None:
    """A non-UTF-8 PEP 263 coding declaration, which Python would honour."""
    for lineno, line in enumerate(source.splitlines()[:2], start=1):
        m = _CODING_RE.match(line)
        if m:
            name = m.group(1).decode("ascii", "replace").lower().replace("_", "-")
            if name not in _UTF8_NAMES and name.replace("-", "_") not in _UTF8_NAMES:
                return f"line {lineno}: coding declaration {name!r} is not allowed (UTF-8 only)"
            return None
        if not line.strip().startswith(b"#") and line.strip():
            break
    return None


def inspect_scene_code(code: str | bytes) -> GateResult:
    """Inspect generated scene source for disallowed constructs.

    Does no I/O and never executes the code; it only parses it. A syntax error
    or a non-UTF-8 source is reported as a finding (``ok=False``) rather than
    raised, so callers in the render path never crash on malformed LLM output.

    Args:
        code: The full scene module source, as text or as the raw file bytes.

    Returns:
        A :class:`GateResult`.
    """
    raw = code.encode("utf-8") if isinstance(code, str) else code
    coding = _coding_problem(raw)
    if coding:
        return GateResult(ok=False, findings=[coding], class_count=0)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return GateResult(
            ok=False,
            findings=[f"source is not valid UTF-8 (byte offset {exc.start})"],
            class_count=0,
        )
    try:
        # Parse the bytes, exactly as Python will when it runs the file.
        tree = ast.parse(raw)
    except (SyntaxError, ValueError) as exc:
        lineno = getattr(exc, "lineno", "?")
        msg = getattr(exc, "msg", str(exc))
        return GateResult(
            ok=False,
            findings=[f"line {lineno}: SyntaxError: {msg}"],
            class_count=0,
        )

    findings: list[str] = []
    class_count = _check_top_level(tree, findings)
    nodes = list(ast.walk(tree))
    # Name nodes that are the direct callee of a Call (getattr(...) itself).
    called = {
        id(n.func)
        for n in nodes
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    for node in nodes:
        _check_node(node, findings, called)

    # Stable, de-duplicated, in source order.
    seen: set[str] = set()
    ordered: list[str] = []
    for f in sorted(findings, key=_finding_sort_key):
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return GateResult(ok=not ordered, findings=ordered, class_count=class_count)


def _finding_sort_key(finding: str) -> tuple[int, str]:
    m = re.match(r"line (\d+):", finding)
    return (int(m.group(1)) if m else 10**9, finding)


def inspect_scene_file(scene_path: str) -> GateResult:
    """Read a scene file's bytes and run :func:`inspect_scene_code` on them.

    Fails closed: a file that cannot be read is reported as a finding.
    """
    try:
        with open(scene_path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return GateResult(
            ok=False,
            findings=[f"scene file could not be read: {exc}"],
            class_count=0,
        )
    return inspect_scene_code(raw)
