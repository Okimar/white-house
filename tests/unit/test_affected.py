"""The `affected` tool finds the tests a change can break (docs: tavern/devtools/affected.py)."""

from __future__ import annotations

from tavern.devtools import affected


def test_a_module_is_found_by_the_imports_of_a_test():
    assert affected.imports_of("from tavern.core import retrieval\nimport tavern.services.lorebooks as lb") >= {
        "tavern.core", "tavern.core.retrieval", "tavern.services.lorebooks"}
    assert affected.module_of("tavern/core/retrieval.py") == "tavern.core.retrieval"
    assert affected.module_of("tavern/devtools/__init__.py") == "tavern.devtools"
    assert affected.module_of("web/core/tokens.js") is None


def test_a_changed_module_brings_the_tests_that_import_or_name_it():
    why = affected.affected_tests(["tavern/core/retrieval.py"])
    assert any(name.endswith("test_retrieval.py") for name in why)
    assert all(name.startswith(("tests/", "plugins/")) for name in why)


def test_a_changed_test_file_is_affected_itself():
    why = affected.affected_tests(["tests/unit/test_affected.py"])
    assert why["tests/unit/test_affected.py"] == ["changed itself"]
