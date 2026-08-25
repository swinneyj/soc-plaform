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
    return str(pathlib.Path(__file__).parent.parent.parent.resolve())

def get_reports_dir():
    """Returns and ensures the existence of the platform Reports directory."""
    path = os.path.join(get_platform_root(), "Reports")
    os.makedirs(path, exist_ok=True)
    return path

def get_archive_dir():
    """Returns and ensures the existence of the platform Archive directory."""
    path = os.path.join(get_platform_root(), "Archive")
    os.makedirs(path, exist_ok=True)
    return path

def get_logs_dir():
    """Returns and ensures the existence of the platform Logs directory."""
    path = os.path.join(get_platform_root(), "Logs")
    os.makedirs(path, exist_ok=True)
    return path