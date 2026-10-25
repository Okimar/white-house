import logging

from tavern.logs import setup_logging


def test_setup_is_idempotent_and_keeps_foreign_handlers(tmp_path):
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        setup_logging("info", tmp_path / "logs" / "t.log")
        setup_logging("debug", tmp_path / "logs" / "t.log")
        owned = [h for h in root.handlers if getattr(h, "_tavern_handler", False)]
        assert len(owned) == 2  # console + file, not duplicated
        assert foreign in root.handlers
        assert root.level == logging.DEBUG
        logging.getLogger("tavern.test").info("hello file")
        assert "hello file" in (tmp_path / "logs" / "t.log").read_text(encoding="utf-8")
    finally:
        root.removeHandler(foreign)
        for h in [h for h in root.handlers if getattr(h, "_tavern_handler", False)]:
            root.removeHandler(h)
            h.close()
