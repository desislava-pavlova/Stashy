"""List history notes inside comments and docstrings (not code or test
data).

Usage: python scripts/dev/find_history_notes.py <project root> [--count]
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(sys.argv[1])
COUNT_ONLY = "--count" in sys.argv

# What counts as a history note. D1-D10 are deliberately not here:
# CLAUDE.md uses them as the names of settled rules.
PATTERNS = [
    r"\b[Pp]hase\s+\d",
    r"\b[Pp]lan\s+0?\d",
    r"\bplan\s+0?\d",
    r"\bS\d{1,3}\b",
    r"\bR\d{1,2}\b",
    r"\bQ\d\b",
    r"\bF\d{1,2}\b",
    r"\b[AL]\d\b",
    r"\b\d{1,2}/\d{1,2}/20\d\d\b",
    r"\b20\d\d-\d\d-\d\d\b",
    r"follow-up",
    r"the user's (own )?(call|choice)",
    r"\buser's call\b",
    r"Security review",
    r"(?<![\w.])\d\.\d{1,2}(/\d\.\d{1,2})?(?=\s*\(F\d|:|\s+\(plan)",
    r"\bplan 0?\d, \d",
]
HISTORY = re.compile("|".join(PATTERNS))

SKIP_TOP = {"staticfiles", "stashes", ".venv", "venv.bak", ".git", ".idea", "logs", "backups"}
SKIP_ANYWHERE = {"migrations", "vendor", "node_modules", "__pycache__"}


def python_texts(path):
    """Yield (line number, text) of every comment line and docstring
    line."""
    source = path.read_bytes().decode("utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                for number in range(body[0].lineno, body[0].end_lineno + 1):
                    doc_lines.add(number)
    for number in sorted(doc_lines):
        yield number, lines[number - 1]
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string


def other_texts(path):
    """Comments in JS, CSS and HTML files, found by simple patterns."""
    suffix = path.suffix
    text = path.read_bytes().decode("utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if (
            (suffix == ".js" and ("//" in line or "/*" in line or line.strip().startswith("*")))
            or (suffix == ".css" and ("/*" in line or line.strip().startswith("*") or "*/" in line))
            or (suffix == ".html" and ("{#" in line or "<!--" in line))
        ):
            yield number, line


def main():
    found = {}
    for path in sorted(ROOT.rglob("*")):
        parts = path.relative_to(ROOT).parts
        if not path.is_file() or parts[0] in SKIP_TOP or SKIP_ANYWHERE & set(parts):
            continue
        if path.suffix == ".py":
            texts = python_texts(path)
        elif path.suffix in {".js", ".css", ".html"}:
            texts = other_texts(path)
        else:
            continue
        for number, text in texts:
            if HISTORY.search(text):
                found.setdefault(str(path.relative_to(ROOT)), []).append((number, text.rstrip()))
    total = 0
    for name, hits in found.items():
        total += len(hits)
        if COUNT_ONLY:
            print(f"{len(hits):4} {name}")
        else:
            for number, text in hits:
                print(f"{name}:{number}: {text.strip()}")
    print(f"TOTAL {total} lines in {len(found)} files")


if __name__ == "__main__":
    main()
