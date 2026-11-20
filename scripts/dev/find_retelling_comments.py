"""Flag standalone comments that look like they retell the code below
them.

A comment is flagged when it is short and most of its content words also
appear in the next few lines of code. Only a pointer for a human to
read.

Usage: python scripts/dev/find_retelling_comments.py <root> [--tests]
"""

import io
import re
import sys
import tokenize
from pathlib import Path


ROOT = Path(sys.argv[1])
INCLUDE_TESTS = "--tests" in sys.argv
SKIP_TOP = {
    "staticfiles",
    "stashes",
    ".venv",
    "venv.bak",
    ".git",
    ".idea",
    "logs",
    "backups",
    "plans",
}
STOP = {
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "was",
    "be",
    "it",
    "its",
    "this",
    "that",
    "with",
    "as",
    "at",
    "by",
    "from",
    "not",
    "no",
    "if",
    "so",
    "but",
    "then",
    "than",
    "into",
    "only",
    "any",
    "each",
    "one",
    "all",
    "more",
    "less",
    "per",
    "via",
    "a's",
}


def words(text):
    return {w for w in re.findall(r"[a-z_]{3,}", text.lower()) if w not in STOP}


def main():
    flagged = 0
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if (
            parts[0] in SKIP_TOP
            or "migrations" in parts
            or ("tests" in parts and not INCLUDE_TESTS)
        ):
            continue
        if path.name.startswith("test") and not INCLUDE_TESTS:
            continue
        source = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        lines = source.split("\n")
        blocks, current = [], []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and lines[token.start[0] - 1].lstrip().startswith(
                "#"
            ):
                row = token.start[0] - 1
                if current and row == current[-1] + 1:
                    current.append(row)
                else:
                    if current:
                        blocks.append(current)
                    current = [row]
        if current:
            blocks.append(current)
        for block in blocks:
            comment = " ".join(lines[i].lstrip()[1:].strip() for i in block)
            comment_words = words(comment)
            if not 2 <= len(comment_words) <= 14:
                continue
            following = " ".join(lines[block[-1] + 1 : block[-1] + 4])
            code_words = words(following.replace("_", " ")) | words(following)
            overlap = len(comment_words & code_words) / len(comment_words)
            if overlap >= 0.6:
                flagged += 1
                print(f"{path.relative_to(ROOT)}:{block[0] + 1}: [{overlap:.0%}] {comment}")
                print(f"        -> {lines[block[-1] + 1].strip()[:90]}")
    print(f"{flagged} flagged")


if __name__ == "__main__":
    main()
