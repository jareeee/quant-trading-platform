#!/usr/bin/env python3
"""Render the launchd plist template without shell interpolation."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TOKENS = {
    "__PROJECT_ROOT__": "project_root",
    "__HOME__": "home",
    "__EXECUTABLE__": "executable",
    "__LOG_DIR__": "log_dir",
    "__LOCK_PATH__": "lock_path",
}
TOKEN_PATTERN = re.compile(r"__[A-Z][A-Z0-9_]*__")


def render(template: Path, output: Path, values: dict[str, Path]) -> None:
    text = template.read_text(encoding="utf-8")
    found = set(TOKEN_PATTERN.findall(text))
    expected = set(TOKENS)
    if found != expected:
        unknown = sorted(found - expected)
        missing = sorted(expected - found)
        raise ValueError(f"invalid placeholders (unknown={unknown}, missing={missing})")
    for token, name in TOKENS.items():
        value = values[name]
        if not value.is_absolute():
            raise ValueError(f"{name.replace('_', '-')} must be an absolute path")
        text = text.replace(token, escape(str(value), {'"': "&quot;", "'": "&apos;"}))
    if TOKEN_PATTERN.search(text):
        raise ValueError("unresolved placeholder remains")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("template", type=Path)
    parser.add_argument("output", type=Path)
    for option in TOKENS.values():
        parser.add_argument(f"--{option.replace('_', '-')}", required=True, type=Path)
    arguments = parser.parse_args()
    values = {name: getattr(arguments, name) for name in TOKENS.values()}
    try:
        render(arguments.template, arguments.output, values)
    except (OSError, ValueError) as error:
        parser.exit(1, f"render_plist: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
