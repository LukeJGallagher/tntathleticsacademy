"""Fail if app code imports a third-party package that isn't in requirements.txt.
(Things installed on a dev laptop hide this; Streamlit Cloud only installs requirements.txt.)
Run:  python tests/requirements_check.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = {p.stem for p in ROOT.glob("*.py")} | {"views"}
# import name -> pip package name, where they differ
PIP_NAME = {"argon2": "argon2-cffi", "psycopg2": "psycopg2-binary", "yaml": "pyyaml", "PIL": "pillow"}
# libraries pandas loads by name (engine="openpyxl", read_excel) rather than via an import statement
IMPLICIT = {"openpyxl"}

reqs = {re.split(r"[<>=\s#\[]", line.strip(), maxsplit=1)[0].lower()
        for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip() and not line.startswith("#")}

used = set(IMPLICIT)
for f in [*ROOT.glob("*.py"), *(ROOT / "views").glob("*.py")]:
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            if top not in LOCAL and top not in sys.stdlib_module_names and top != "__future__":
                used.add(top)

missing = sorted(PIP_NAME.get(m, m) for m in used if PIP_NAME.get(m, m).lower() not in reqs)
print("third-party imports:", ", ".join(sorted(used)))
if missing:
    print("MISSING from requirements.txt:", ", ".join(missing))
    sys.exit(1)
print("requirements.txt covers every import")
