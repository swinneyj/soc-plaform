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

def get_logs_dir():
    """Returns the platform Logs directory, creating it when possible."""
    return _ensure_dir(os.path.join(get_platform_root(), "Logs"))
