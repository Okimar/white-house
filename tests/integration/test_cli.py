"""CLI commands that do not start a server. `serve` is covered by test_smoke.py."""

import pytest

from tavern.cli import _split_global, main


@pytest.fixture
def run(data_paths, capsys):
    def invoke(*args: str) -> tuple[int, str]:
        code = main(["--data-dir", str(data_paths.root), *args])
        return code, capsys.readouterr().out

    return invoke


def test_check_creates_and_verifies_data_dir(run, data_paths):
    code, out = run("check")
    assert code == 0
    assert "integrity: ok" in out
    assert data_paths.db.exists()


def test_backup_create_list_restore(run):
    run("check")
    code, name = run("backup", "create", "--reason", "test")
    assert code == 0 and name.strip().endswith("-manual-test.zip")
    code, listing = run("backup", "list")
    assert name.strip() in listing
    code, out = run("backup", "restore", name.strip())
    assert code == 0 and "Previous state saved as" in out


def test_backup_restore_unknown_name_fails_cleanly(run, caplog):
    run("check")
    code, _ = run("backup", "restore", "../../etc/passwd")
    assert code == 1
    assert "Нет такой копии" in caplog.text


def test_doctor_reports_and_fixes_leftovers(run, data_paths):
    run("check")
    (data_paths.root / ".backup-tmp" / "stale").mkdir(parents=True)
    code, out = run("doctor")
    assert code == 0 and "Остатки прерванных операций" in out and "--fix" in out
    code, out = run("doctor", "--fix")
    assert code == 0 and not (data_paths.root / ".backup-tmp" / "stale").exists()


def test_token_is_stable_until_rotated(run):
    _, first = run("token")
    _, second = run("token")
    assert first == second
    _, rotated = run("token", "--rotate")
    assert rotated.splitlines()[0] != first.strip()


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--data-dir", "x", "--port", "1"], (["--data-dir", "x"], ["--port", "1"])),
        (["--data-dir=x", "serve"], (["--data-dir=x"], ["serve"])),
        ([], ([], [])),
    ],
)
def test_split_global(argv, expected):
    assert _split_global(argv) == expected
