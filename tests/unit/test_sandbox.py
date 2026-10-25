from tavern.context import open_context
from tavern.sandbox import make_sandbox, remove_sandbox


def test_sandbox_copies_data_and_isolates_writes(data_paths):
    ctx = open_context(data_paths)
    ctx.db.execute("INSERT INTO settings (key, value) VALUES ('theme', '\"dark\"')")
    ctx.close()
    (data_paths.backups / "big.sqlite3").write_bytes(b"x")

    sandbox = make_sandbox(data_paths)
    try:
        assert sandbox.root != data_paths.root
        assert not (sandbox.backups / "big.sqlite3").exists()
        sctx = open_context(sandbox, sandbox=True)
        assert sctx.db.fetchone("SELECT value FROM settings WHERE key='theme'")[0] == '"dark"'
        sctx.db.execute("DELETE FROM settings")
        sctx.close()

        real = open_context(data_paths)
        assert real.db.fetchone("SELECT count(*) FROM settings")[0] == 1
        real.close()
    finally:
        remove_sandbox(sandbox)
    assert not sandbox.root.exists()


def test_sandbox_of_missing_data_dir_starts_empty(tmp_path):
    from tavern.paths import DataPaths

    sandbox = make_sandbox(DataPaths(tmp_path / "nothing-here"))
    try:
        assert sandbox.root.exists()
        assert not sandbox.db.exists()
    finally:
        remove_sandbox(sandbox)
