import os
import pathlib


class Colors:
    """ANSI color codes used by Commander and tools.

    Kept minimal but compatible with existing usage in commander.py
    (CYAN, BOLD, HEADER, GREEN, BLUE, WARNING, FAIL, ENDC).
    """

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    BOLD = "\033[1m"
    ENDC = "\033[0m"


def clear_screen():
    """Clear the terminal screen in a cross-platform way."""
    try:
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")
    except Exception:
        # Never fail if clearing the screen is not possible.
        pass


def get_platform_root():
    """Returns the absolute root directory of the application workspace."""
    # First checks if running inside a Docker container context, fallback to host path
    if os.path.exists("/app"):
        return "/app"
    # Resolve the module path before walking parents.  Several catalog tools
    # import this module through a relative ``Tools/..`` entry; walking parents
    # first leaves the ``..`` segment in the chain and can incorrectly return
    # ``Tools/<tool>`` instead of the platform root on Windows and POSIX hosts.
    return str(pathlib.Path(__file__).resolve().parents[2])

def _ensure_dir(path):
    """Create a directory if possible, tolerating read-only filesystems.

    Serverless platforms (e.g. Vercel) mount the deployment as read-only,
    so os.makedirs raises OSError even for directories included in the
    bundle. Callers only need a usable path: if creation fails but the
    directory already exists, return it; if it genuinely cannot exist,
    return it anyway so listing endpoints can degrade to an empty result
    instead of a 500. Writes will fail naturally at write time.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path

def get_reports_dir():
    """Returns the platform Reports directory, creating it when possible."""
    return _ensure_dir(os.path.join(get_platform_root(), "Reports"))

def get_archive_dir():
    """Returns the platform Archive directory, creating it when possible."""
    return _ensure_dir(os.path.join(get_platform_root(), "Archive"))

_TOOL_TARGET_EXTS = (".py", ".ps1")


def validated_tool_target(path, allowed_exts=_TOOL_TARGET_EXTS):
    """Resolve a helper-script/file target for subprocess execution (shell=False).

    The target must be a real file inside the platform tree with an allowed
    extension (default: .py/.ps1 tools). Anything else — absolute escapes,
    missing files, disallowed extensions — raises ValueError instead of
    executing.
    """
    root = os.path.abspath(get_platform_root())
    abs_path = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
    if os.path.commonpath([abs_path, root]) != root:
        raise ValueError("tool target escapes the platform tree: %r" % (path,))
    if not os.path.isfile(abs_path):
        raise ValueError("tool target is not a file: %r" % (path,))
    if os.path.splitext(abs_path)[1].lower() not in tuple(allowed_exts):
        raise ValueError("tool target has a disallowed extension: %r" % (path,))
    return abs_path


def validated_arg_list(args):
    """Coerce subprocess args to a list of non-empty NUL-free strings.

    With shell=False the shell cannot interpret metacharacters, but strict
    typing here keeps a programming error (None, bytes, empty argv entries)
    from becoming a confusing Popen failure — or worse, an argument
    boundary surprise in a downstream tool's parser.
    """
    items = list(args or [])
    for item in items:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError("invalid subprocess argument: %r" % (item,))
    return items


def get_logs_dir():
    """Returns the platform Logs directory, creating it when possible."""
    return _ensure_dir(os.path.join(get_platform_root(), "Logs"))
