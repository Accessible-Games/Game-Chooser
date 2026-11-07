"""Fail if a normal build holds any tracing code.

Run: python scripts/check_no_tracing.py REPORT DIST
REPORT is Nuitka's compilation report (build/compilation-report.xml); DIST is the built folder. Prints each
problem and exits 1; otherwise prints "No tracing code in the build." and exits 0.
"""

import re
import sys
from pathlib import Path

MODULE_RE = re.compile(r'<module name="([^"]+)"')
BAD_MODULES = ("tracing", "procmon_parser")
BAD_PARTS = ("tracing", "procmon", "traces")
NO_MODULES = ("The compilation report lists no modules; its format may have changed, so the build can't be "
              "checked.")


def find_tracing(report_xml: str, dist_files: list[str]) -> list[str]:
    """Return the problems: tracing modules in the report and tracing files in the dist (relative, '/').

    A report that lists no modules at all is a problem too: it means nothing was checked."""
    problems = []
    names = MODULE_RE.findall(report_xml)
    if not names:
        problems.append(NO_MODULES)
    for name in names:
        if any(name == bad or name.startswith(bad + ".") for bad in BAD_MODULES):
            problems.append(f"module {name} is in the compilation report")
    for path in dist_files:
        parts = path.lower().replace("\\", "/").split("/")
        if any(part in BAD_PARTS for part in parts) or parts[-1].startswith("procmon"):
            problems.append(f"file {path} is in the build folder")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check_no_tracing.py REPORT DIST")
        return 2
    report, dist = Path(argv[1]), Path(argv[2])
    if not report.is_file() or not dist.is_dir():
        print(f"Cannot check: {report} or {dist} is missing.")
        return 2
    files = [p.relative_to(dist).as_posix() for p in dist.rglob("*")]
    problems = find_tracing(report.read_text(encoding="utf-8"), files)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("No tracing code in the build.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
