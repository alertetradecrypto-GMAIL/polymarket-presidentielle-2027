import pytest

import x_alerts
import x_daily
import x_post
import x_top5


@pytest.fixture(autouse=True)
def x_files_in_tmp(tmp_path, monkeypatch):
    """Aucun test n'écrit l'état X ni out/ dans le dépôt ; jamais de publication réelle."""
    monkeypatch.setattr(x_alerts, "PENDING_FILE", tmp_path / "out" / "x_alert.json")
    monkeypatch.setattr(x_alerts, "X_ALERTS_STATE", tmp_path / "state" / "x_alerts.json")
    monkeypatch.setattr(x_daily, "X_DAILY_STATE", tmp_path / "state" / "x_daily.json")
    monkeypatch.setattr(x_daily, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(x_top5, "X_TOP5_STATE", tmp_path / "state" / "x_top5.json")
    monkeypatch.setattr(x_top5, "PENDING_FILE", tmp_path / "out" / "x_top5.json")
    monkeypatch.setattr(x_post, "POSTS_FILE", tmp_path / "state" / "x_posts.json")
    monkeypatch.setenv("DRY_RUN", "1")
