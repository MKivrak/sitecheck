#!/usr/bin/env python3
"""sitecheck — a static checker for static sites.

No build step, no config, no dependencies. Point it at a folder of HTML and it
reports the mistakes a bundler would have caught for you:

  * JavaScript that does not parse (needs node on PATH; skipped otherwise)
  * imports that name something a module does not export
  * imports nothing in the file goes on to use
  * element ids referenced in script but present in no markup
  * the same id used twice on one page
  * functions that are defined and never called
  * internal links pointing at pages that do not exist
  * leftover console.log, TODO, FIXME and debugger

Usage:
    sitecheck.py [ROOT] [--quiet] [--no-node]

Exit code is 0 when clean and 1 when anything was found, so it drops into CI
without further ceremony.

MIT licensed.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

MODULE_SCRIPT = re.compile(r'<script[^>]*\btype=["\']module["\'][^>]*>(.*?)</script>', re.S)
IMPORT_BLOCK = re.compile(r"""import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]""")
EXPORT_NAMES = (
    re.compile(r"export\s+(?:async\s+)?function\s+([\w$]+)"),
    re.compile(r"export\s+const\s+([\w$]+)"),
    re.compile(r"export\s+(?:let|var)\s+([\w$]+)"),
    re.compile(r"export\s+class\s+([\w$]+)"),
)
ID_ATTR = re.compile(r'\sid=["\']([^"\']+)["\']')
ID_USE = re.compile(r"""\$\(\s*['"]#([\w-]+)['"]\s*\)|getElementById\(\s*['"]([\w-]+)['"]\s*\)|querySelector\(\s*['"]#([\w-]+)['"]\s*\)""")
FUNC_DECL = re.compile(r"\n\s*function\s+([\w$]+)\s*\(")
HREF = re.compile(r"""href=["'](/[^"'#?]*)(?:[#?][^"']*)?["']""")
LEFTOVERS = (
    (re.compile(r"\bconsole\.log\("), "console.log left in"),
    (re.compile(r"\bTODO\b|\bFIXME\b"), "TODO or FIXME"),
    (re.compile(r"\bdebugger\b"), "debugger statement"),
)


def ident(name: str) -> re.Pattern:
    """A word boundary that also works for names like `$` and `$$`."""
    return re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")


class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, where: str, kind: str, detail: str) -> None:
        self.items.append((where, kind, detail))

    @staticmethod
    def _count(n: int, noun: str) -> str:
        return f"{n} {noun}" if n == 1 else f"{n} {noun}s"

    def render(self, quiet: bool, scripts: int, pages: int) -> None:
        tally = f"{self._count(scripts, 'script')}, {self._count(pages, 'page')}"
        if not self.items:
            if not quiet:
                print(f"clean — {tally}")
            return
        groups: dict[str, list[tuple[str, str]]] = {}
        for where, kind, detail in self.items:
            groups.setdefault(kind, []).append((where, detail))
        for kind in sorted(groups):
            rows = groups[kind]
            print(f"\n{kind} ({len(rows)})")
            width = max(len(w) for w, _ in rows)
            for where, detail in rows:
                print(f"  {where:<{width}}  {detail}")
        print(f"\n{self._count(len(self.items), 'issue')} — {tally}")


def collect_scripts(root: pathlib.Path) -> tuple[dict[str, str], list[pathlib.Path]]:
    pages = sorted(p for p in root.glob("*.html"))
    scripts: dict[str, str] = {}
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for n, match in enumerate(MODULE_SCRIPT.finditer(text)):
            scripts[f"{page.name}#{n}"] = match.group(1)
    for mod in sorted(root.glob("**/*.js")):
        if "node_modules" in mod.parts:
            continue
        scripts[str(mod.relative_to(root)).replace("\\", "/")] = mod.read_text(
            encoding="utf-8", errors="replace"
        )
    return scripts, pages


def check_syntax(scripts: dict[str, str], report: Report) -> None:
    node = shutil.which("node")
    if not node:
        return
    with tempfile.TemporaryDirectory() as tmp:
        for name, source in scripts.items():
            path = pathlib.Path(tmp) / (re.sub(r"[^\w]", "_", name) + ".mjs")
            path.write_text(source, encoding="utf-8")
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            if done.returncode:
                tail = [ln for ln in done.stderr.splitlines() if ln.strip()][-1:]
                report.add(name, "syntax error", tail[0].strip() if tail else "see node --check")


def module_exports(root: pathlib.Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for mod in root.glob("**/*.js"):
        if "node_modules" in mod.parts:
            continue
        text = mod.read_text(encoding="utf-8", errors="replace")
        names: set[str] = set()
        for pattern in EXPORT_NAMES:
            names |= set(pattern.findall(text))
        rel = "/" + str(mod.relative_to(root)).replace("\\", "/")
        found[rel] = names
    return found


def check_imports(scripts: dict[str, str], exports: dict[str, set[str]], report: Report) -> None:
    for name, source in scripts.items():
        for match in IMPORT_BLOCK.finditer(source):
            module = match.group(2)
            rest = source[match.end():]
            for part in match.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                original, _, alias = part.partition(" as ")
                original = original.strip()
                local = alias.strip() or original
                known = exports.get(module)
                if known is not None and original not in known:
                    report.add(name, "import does not resolve", f"{original} is not exported by {module}")
                if not ident(local).search(rest):
                    report.add(name, "unused import", f"{local} from {module}")


def check_ids(pages: list[pathlib.Path], report: Report) -> None:
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        present = ID_ATTR.findall(text)
        unique = set(present)
        for match in ID_USE.finditer(text):
            ref = next(g for g in match.groups() if g)
            if ref not in unique:
                report.add(page.name, "id referenced but missing", f"#{ref}")
        for one in sorted(unique):
            if present.count(one) > 1:
                report.add(page.name, "duplicate id", f"#{one}")


def check_dead_functions(scripts: dict[str, str], report: Report) -> None:
    for name, source in scripts.items():
        for match in FUNC_DECL.finditer(source):
            fn = match.group(1)
            if len(ident(fn).findall(source)) <= 1:
                report.add(name, "function never called", f"{fn}()")


def check_links(root: pathlib.Path, pages: list[pathlib.Path], report: Report) -> None:
    known = {"/"} | {"/" + p.stem for p in pages}
    asset_dirs = {d.name for d in root.iterdir() if d.is_dir()}
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        for match in HREF.finditer(text):
            href = match.group(1).rstrip("/") or "/"
            head = href.lstrip("/").split("/")[0]
            if head in asset_dirs:
                continue
            if href not in known:
                report.add(page.name, "internal link goes nowhere", href)


def check_leftovers(scripts: dict[str, str], report: Report) -> None:
    for name, source in scripts.items():
        for pattern, label in LEFTOVERS:
            if pattern.search(source):
                report.add(name, "leftover", label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static checker for static sites.")
    parser.add_argument("root", nargs="?", default=".", help="folder to check (default: .)")
    parser.add_argument("--quiet", action="store_true", help="print nothing when clean")
    parser.add_argument("--no-node", action="store_true", help="skip the JavaScript syntax check")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a folder: {root}", file=sys.stderr)
        return 2

    scripts, pages = collect_scripts(root)
    if not pages and not scripts:
        print(f"nothing to check in {root}", file=sys.stderr)
        return 2

    report = Report()
    if not args.no_node:
        check_syntax(scripts, report)
    check_imports(scripts, module_exports(root), report)
    check_ids(pages, report)
    check_dead_functions(scripts, report)
    check_links(root, pages, report)
    check_leftovers(scripts, report)

    report.render(args.quiet, len(scripts), len(pages))
    return 1 if report.items else 0


if __name__ == "__main__":
    raise SystemExit(main())
