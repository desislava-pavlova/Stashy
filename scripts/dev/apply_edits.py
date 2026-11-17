"""Apply exact-text edits to files, keeping each file's own line
endings.

Usage: python scripts/dev/apply_edits.py <edits module>

The module defines EDITS = [(path, old, new), ...] or (path, old, new,
count). Every `old` must occur exactly `count` times (default 1) in its
file, or nothing in that file is written and the script exits with an
error. Paths are relative to the project root (the current directory).
"""

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path


def load_edits(module_path):
    """A module file's EDITS, as {path: [(old, new, count)]}."""
    spec = importlib.util.spec_from_file_location("edits", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    by_file = defaultdict(list)
    for path, old, new, *rest in module.EDITS:
        by_file[path].append((old, new, rest[0] if rest else 1))
    return by_file


def apply_to_text(text, edits):
    """The text with every edit applied, or the first mistake as a
    message.

    Returns (new text, None), or (None, message) when an `old` does not
    occur exactly as often as its count says.
    """
    for old, new, count in edits:
        found = text.count(old)
        if found != count:
            return None, f"expected {count}, found {found}: {old[:70]!r}"
        text = text.replace(old, new)
    return text, None


def apply_to_file(path, edits):
    """Edit one file, keeping its line endings. True when it was
    written."""
    text = Path(path).read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    new_text, mistake = apply_to_text(text.replace("\r\n", "\n"), edits)
    if mistake:
        print(f"FAIL {path}: {mistake}")
        return False
    if crlf:
        new_text = new_text.replace("\n", "\r\n")
    Path(path).write_bytes(new_text.encode("utf-8"))
    print(f"ok   {path}: {len(edits)} edits")
    return True


def main():
    results = [apply_to_file(path, edits) for path, edits in load_edits(sys.argv[1]).items()]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
