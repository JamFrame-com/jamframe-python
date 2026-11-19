"""Download the live API spec into spec/openapi.yaml, for tests/test_spec.py.

Usage: python scripts/update_spec.py [url]
"""

import sys
import urllib.request
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://app.jamframe.com/openapi.yaml"
TARGET = Path(__file__).resolve().parents[1] / "spec" / "openapi.yaml"

with urllib.request.urlopen(URL, timeout=30) as response:
    text = response.read().decode("utf-8")
if not text.startswith("openapi:"):
    sys.exit(f"{URL} is not an OpenAPI document")
TARGET.write_text(text, encoding="utf-8")
print(f"spec/openapi.yaml updated from {URL} ({len(text)} characters)")
