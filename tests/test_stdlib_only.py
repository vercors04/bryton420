"""The package must import nothing outside the standard library.

That is what lets the same code run under Termux on a phone or in a browser
through Pyodide, and it is easy to lose by reaching for a convenient package.
"""

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "bryton_route"


def test_package_imports_only_the_standard_library():
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            offenders += [
                f"{path.name}: {name}"
                for name in names
                if name.split(".")[0] not in sys.stdlib_module_names
            ]
    assert not offenders
