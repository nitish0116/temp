"""Repository-level contracts for documentation and machine-readable metadata."""

import ast
import json
import re
from pathlib import Path


PROJECT = Path(__file__).parents[1]


def production_python_files() -> list[Path]:
    """Return production modules, excluding tests and generated output trees.

    Example:: the result includes ``pipeline.py`` and command modules but not this
    test file.
    """
    return sorted(
        path for path in PROJECT.rglob("*.py")
        if (
            "tests" not in path.parts
            and "outputs" not in path.parts
            and ".venv" not in path.parts
        )
    )


def test_every_production_function_has_a_meaningful_docstring():
    undocumented = []
    for path in production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                docstring = ast.get_docstring(node) or ""
                if len(docstring.strip()) < 12:
                    undocumented.append(f"{path.relative_to(PROJECT)}:{node.lineno}:{node.name}")
    assert not undocumented, "Undocumented functions:\n" + "\n".join(undocumented)


def test_schemas_configs_and_fixtures_are_valid_json():
    paths = [
        *PROJECT.glob("schemas/*.json"),
        *PROJECT.glob("config/*.json"),
        *PROJECT.glob("tests/fixtures/*.json"),
    ]
    assert paths
    for path in paths:
        json.loads(path.read_text(encoding="utf-8"))


def test_local_markdown_links_resolve():
    broken = []
    markdown_files = [PROJECT / "README.md", *PROJECT.glob("docs/*.md")]
    for path in markdown_files:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            relative_target = target.split("#", 1)[0]
            if relative_target and "://" not in relative_target:
                if not (path.parent / relative_target).exists():
                    broken.append(f"{path.relative_to(PROJECT)} -> {target}")
    assert not broken, "Broken documentation links:\n" + "\n".join(broken)
