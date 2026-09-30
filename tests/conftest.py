"""Shared pytest configuration.

CORS middleware is mounted at api.main import time only when CORS_ORIGINS is
set. conftest.py is imported by pytest before any test module, so setting it
here guarantees the middleware exists regardless of test import order.
"""

import os

os.environ.setdefault("CORS_ORIGINS", "https://test.local")
