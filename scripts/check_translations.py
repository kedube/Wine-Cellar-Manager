"""Check that every translation has the same keys as strings.json."""

import json
import sys
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parent.parent / "custom_components" / "wine_cellar_manager"


def keys(node, prefix=""):
    if not isinstance(node, dict):
        return {prefix}
    result = set()
    for key, value in node.items():
        result |= keys(value, f"{prefix}.{key}" if prefix else key)
    return result


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        print(f"::error file={path}::invalid JSON: {err}")
        sys.exit(1)


reference = keys(load(INTEGRATION / "strings.json"))
failed = False

for path in sorted((INTEGRATION / "translations").glob("*.json")):
    found = keys(load(path))
    for key in sorted(reference - found):
        print(f"::error file={path}::missing key {key}")
        failed = True
    for key in sorted(found - reference):
        print(f"::error file={path}::key {key} is not in strings.json")
        failed = True

if failed:
    sys.exit(1)
print("All translations match strings.json.")
