"""Every product-specific string of the SDK, in one place.

A rename means editing this file, the
``name`` in ``pyproject.toml`` and the package directory (``src/jamframe``);
nothing else in the code spells the brand.
"""

NAME = "JamFrame"
DEFAULT_BASE_URL = "https://api.jamframe.com"

# Environment variables read when the client is built without arguments.
API_KEY_ENV = "JAMFRAME_API_KEY"
BASE_URL_ENV = "JAMFRAME_BASE_URL"

# The live smoke test (tests/test_live_smoke.py) runs only when this is set.
SMOKE_API_KEY_ENV = "JAMFRAME_SMOKE_API_KEY"
SMOKE_BASE_URL_ENV = "JAMFRAME_SMOKE_BASE_URL"

USER_AGENT_PREFIX = "jamframe-python"
