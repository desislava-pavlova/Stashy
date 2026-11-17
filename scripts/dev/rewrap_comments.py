"""Rewrap comments and docstrings to fit a width (default 72 columns).

Usage: python scripts/dev/rewrap_comments.py <root> [--width 72]
[--apply] [--only <file>...] [--verbose]

Without --apply it only reports. With --apply it writes each file, but
only after two checks pass for that file:
  1. the code is unchanged (ast dump equal, docstrings compared with
     their whitespace collapsed), and
  2. every standalone comment has the same words in the same order.
A file that fails a check is left alone and reported.

What it touches: standalone comment blocks and triple-quoted docstrings,
paragraph by paragraph, and only a paragraph that has a line over the
width. What it never touches: trailing comments after code, indented or
aligned text (code samples, tables), pragma comments, raw docstrings.
"""

import ast
import io
import re
import sys
import textwrap
import tokenize
import unicodedata
from pathlib import Path

ROOT = Path(sys.argv[1])
WIDTH = int(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else 72
APPLY = "--apply" in sys.argv
VERBOSE = "--verbose" in sys.argv
ONLY = []
if "--only" in sys.argv:
    ONLY = [a for a in sys.argv[sys.argv.index("--only") + 1 :] if not a.startswith("--")]

SKIP_TOP = {"staticfiles", "stashes", ".venv", "venv.bak", ".git", ".idea", "logs", "backups", "plans"}
SKIP_ANYWHERE = {"migrations", "vendor", "node_modules", "__pycache__"}

BULLET = re.compile(r"^( *)((?:[-*•]|\d{1,2}[.)]|\([a-z0-9]\)|[a-z][.)]) +)")
PRAGMA = re.compile(r"^(?:noqa|type:|fmt:|pragma|ruff:|pylint:|-\*-|!|:|isort:)", re.I)
BANNER = re.compile(r"^-{3,}|^-{3,} .+ -{3,}$")
CODE_LIKE = re.compile(r"^(?:def |class |import |from \S+ import |return\b|path\(|[\w.]+\(.*\)$|[\w.\[\]\"']+ = )")


def width_of(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def classify(lines, base):
    """Group virtual lines (indentation included) into items.

    ("blank",) | ("pre", [lines]) | ("para", extra_indent, marker, [text
    parts], [source lines])
    """
    items = []
    previous = None  # None, "blank", "text", "bullet", "pre"
    hanging = 0
    for raw in lines:
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        relative = indent - base
        if not stripped:
            items.append(("blank",))
            previous = "blank"
            continue
        bullet = BULLET.match(raw[base:]) if relative >= 0 else None
        if bullet and previous in (None, "blank", "bullet"):
            extra, marker = len(bullet.group(1)), bullet.group(2)
            text = raw[base + extra + len(marker) :].strip()
            items.append(("para", extra, marker, [text], [raw]))
            hanging = extra + len(marker)
            previous = "bullet"
        elif previous == "bullet" and relative == hanging:
            items[-1][3].append(stripped)
            items[-1][4].append(raw)
        elif (
            relative >= 2 or stripped.startswith(">>>") or BANNER.match(stripped) or re.search(r"\S {3,}\S", stripped)
        ):
            if previous == "pre":
                items[-1][1].append(raw)
            else:
                items.append(("pre", [raw]))
            previous = "pre"
        elif previous == "text":
            items[-1][3].append(stripped)
            items[-1][4].append(raw)
        else:
            items.append(("para", 0, "", [stripped], [raw]))
            previous = "text"
    return items


def wrap(parts, first_prefix, rest_prefix, marker=""):
    wrapper = textwrap.TextWrapper(
        width=WIDTH,
        initial_indent=first_prefix + marker,
        subsequent_indent=rest_prefix + " " * len(marker),
        break_long_words=False,
        break_on_hyphens=False,
        fix_sentence_endings=False,
    )
    # A dash used as punctuation (" - ") must never start a line, where
    # it would look like a bullet: tie it to the word before it with a
    # private placeholder, put back as a space.
    text = " ".join(parts).replace(" - ", "- ")
    return [line.replace("", " ") for line in wrapper.wrap(text)]


def dash_starts_a_line(lines):
    """Whether a line after the first one in a paragraph starts with '-
    ' (prose, not a bullet)."""
    return any(line.lstrip().startswith("- ") for line in lines[1:])


def shorten_banner(line):
    """A '# ----' line over the width loses trailing dashes, never below
    three."""
    match = re.search(r"-{3,}$", line)
    if not match:
        return line
    excess = width_of(line) - WIDTH
    keep = max(3, len(match.group(0)) - excess)
    return line[: match.start()] + "-" * keep


# ---- comments ----


def comment_edits(lines):
    """Yield (start, end_exclusive, new_lines) for standalone comment
    blocks that need rewrapping."""
    source = "\n".join(lines) + "\n"
    rows = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and lines[token.start[0] - 1].lstrip().startswith("#"):
            rows.append(token.start[0] - 1)
    blocks, current, column = [], [], None
    for index in rows:
        here = len(lines[index]) - len(lines[index].lstrip())
        if current and index == current[-1] + 1 and here == column:
            current.append(index)
        else:
            if current:
                blocks.append(current)
            current, column = [index], here
    if current:
        blocks.append(current)

    for block in blocks:
        physical = [lines[i] for i in block]
        if not any(width_of(line) > WIDTH for line in physical) and not any(
            re.match(r"\s*#\s+- ", line) for line in physical[1:]
        ):
            continue
        column = len(physical[0]) - len(physical[0].lstrip())
        bodies = []
        for line in physical:
            text = line.lstrip()[1:]
            bodies.append(text[1:] if text.startswith(" ") else text)
        if any(PRAGMA.match(b.strip()) for b in bodies):
            continue
        if any(CODE_LIKE.match(b.strip()) for b in bodies):
            if VERBOSE:
                print(f"   skipped (looks like code) at line {block[0] + 1}")
            continue
        lead = " " * column + "# "
        new_lines = []
        for item in classify(bodies, 0):
            kind = item[0]
            if kind == "blank":
                new_lines.append(" " * column + "#")
            elif kind == "pre":
                for body in item[1]:
                    line = lead + body
                    new_lines.append(
                        shorten_banner(line) if width_of(line) > WIDTH and BANNER.match(body.strip()) else line
                    )
            else:
                _, extra, marker, parts, sources = item
                original = [lead + s for s in sources]
                if not marker and dash_starts_a_line(sources):
                    new_lines.extend(wrap(parts, lead + " " * extra, lead + " " * extra, marker))
                elif not any(width_of(line) > WIDTH for line in original):
                    new_lines.extend(original)
                else:
                    new_lines.extend(wrap(parts, lead + " " * extra, lead + " " * extra, marker))
        if new_lines != physical:
            yield block[0], block[-1] + 1, new_lines


# ---- docstrings ----


def docstring_nodes(tree):
    """Yield the statement that holds each docstring in the tree."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first


def docstring_edits(tree, lines):
    for docstring in docstring_nodes(tree):
        start, end = docstring.lineno - 1, docstring.end_lineno
        physical = lines[start:end]
        first = physical[0]
        base = len(first) - len(first.lstrip())
        opening = first[base:]
        if not opening.startswith('"""'):
            continue
        if not any(width_of(line) > WIDTH for line in physical) and not any(
            line.lstrip().startswith("- ") for line in physical[1:]
        ):
            continue
        last = physical[-1].rstrip()
        if not last.endswith('"""') or (len(physical) == 1 and len(last.strip()) < 6):
            continue
        # Content lines: first = text after the opening quotes; the rest
        # are physical lines.
        content = [first[base + 3 :], *physical[1:]]
        content[-1] = content[-1].rstrip()[:-3]
        closes_alone = len(content) > 1 and not content[-1].strip()
        if closes_alone:
            content.pop()
        indent = " " * base
        text_on_opening_line = bool(content[0].strip())
        virtual = ([" " * base + content[0]] if text_on_opening_line else []) + content[1:]
        items = classify(virtual, base)
        if text_on_opening_line and (not items or items[0][0] != "para"):
            continue
        new_lines = [] if text_on_opening_line else [indent + '"""']
        for position, item in enumerate(items):
            kind = item[0]
            if kind == "blank":
                new_lines.append("")
            elif kind == "pre":
                new_lines.extend(item[1])
            else:
                _, extra, marker, parts, sources = item
                is_first = text_on_opening_line and position == 0
                is_last = position == len(items) - 1 and not closes_alone
                widest = 0
                for number, source in enumerate(sources):
                    text = source if not (is_first and number == 0) else indent + '"""' + source[base:]
                    widest = max(widest, width_of(text) + (3 if is_last and number == len(sources) - 1 else 0))
                if widest <= WIDTH and (marker or not dash_starts_a_line(sources)):
                    if is_first:
                        new_lines.append(indent + '"""' + sources[0][base:])
                        new_lines.extend(sources[1:])
                    else:
                        new_lines.extend(sources)
                else:
                    first_prefix = indent + ('"""' if is_first else "") + " " * extra
                    rest_prefix = indent + " " * extra
                    new_lines.extend(wrap(parts, first_prefix, rest_prefix, marker))
        if closes_alone:
            new_lines.append(indent + '"""')
        else:
            if width_of(new_lines[-1] + '"""') > WIDTH:
                new_lines.append(indent + '"""')
            else:
                new_lines[-1] += '"""'
        if new_lines != physical:
            yield start, end, new_lines


# ---- checks ----


class _NormaliseDocstrings(ast.NodeTransformer):
    def visit_Constant(self, node):
        return node

    def _fix(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            value = node.body[0].value.value
            if isinstance(value, str):
                node.body[0].value.value = " ".join(value.split())
        return node

    # ast calls visit_<NodeName>, so these names can't be snake_case.
    visit_Module = visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _fix  # noqa: N815


def code_fingerprint(source):
    return ast.dump(_NormaliseDocstrings().visit(ast.parse(source)))


def comment_words(source):
    words = []
    lines = source.split("\n")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and lines[token.start[0] - 1].lstrip().startswith("#"):
            words.extend(w for w in token.string.lstrip("#").split() if set(w) != {"-"})
    return words


def residual(source):
    """Over-width lines left in standalone comments and docstrings."""
    lines = source.split("\n")
    flagged = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and lines[token.start[0] - 1].lstrip().startswith("#"):
            flagged.add(token.start[0])
    for docstring in docstring_nodes(ast.parse(source)):
        flagged.update(range(docstring.lineno, docstring.end_lineno + 1))
    return [n for n in sorted(flagged) if width_of(lines[n - 1]) > WIDTH]


def process(path):
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    source = raw.replace("\r\n", "\n")
    if not source.endswith("\n"):
        source += "\n"
    lines = source.split("\n")[:-1]
    tree = ast.parse(source)
    edits = list(comment_edits(lines)) + list(docstring_edits(tree, lines))
    edits.sort(key=lambda e: e[0], reverse=True)
    new = list(lines)
    for start, end, replacement in edits:
        new[start:end] = replacement
    result = "\n".join(new) + "\n"
    ok = code_fingerprint(source) == code_fingerprint(result) and comment_words(source) == comment_words(result)
    return source, result, crlf, ok, len(edits)


def main():
    totals = {"files": 0, "edits": 0, "failed": [], "before": 0, "after": 0}
    for path in sorted(ROOT.rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if parts[0] in SKIP_TOP or SKIP_ANYWHERE & set(parts):
            continue
        if ONLY and str(path.relative_to(ROOT)).replace("\\", "/") not in ONLY:
            continue
        source, result, crlf, ok, count = process(path)
        before, after = len(residual(source)), len(residual(result))
        totals["before"] += before
        totals["after"] += after
        name = str(path.relative_to(ROOT)).replace("\\", "/")
        if not ok:
            totals["failed"].append(name)
            print(f"CHECK FAILED  {name}")
            continue
        if count:
            totals["files"] += 1
            totals["edits"] += count
            print(f"{count:4} edits  over-width {before:4} -> {after:3}  {name}")
            if APPLY:
                path.write_bytes((result.replace("\n", "\r\n") if crlf else result).encode("utf-8"))
        elif VERBOSE and before:
            print(f"   0 edits  over-width {before:4} -> {after:3}  {name}")
    print(
        f"{'APPLIED' if APPLY else 'DRY RUN'}: {totals['edits']} edits in {totals['files']} files; "
        f"over-width lines {totals['before']} -> {totals['after']}; failed checks: {totals['failed']}"
    )


if __name__ == "__main__":
    main()
