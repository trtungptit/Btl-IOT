"""Kiểm tra db.py bằng file DB tạm (không đụng data/pond.db).
Chạy từ thư mục gốc: python -m pytest server/tests"""

import pytest

from server import db


@pytest.fixture
def db_path(tmp_path):
    """Mỗi test có một file DB tạm riêng, đã tạo bảng."""
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def test_init_db_chay_hai_lan_khong_loi(db_path):
    db.init_db(db_path)  # tạo lại lần 2 vẫn ổn


def test_them_va_lay_reading(db_path):
    db.add_reading(100, 28.4, 7.2, 310, 6.5, db_path=db_path)
    row = db.get_latest_readings(1, db_path)[0]
    assert (row["ts"], row["temp"], row["ph"], row["tds"], row["do_est"]) == (100, 28.4, 7.2, 310, 6.5)
    assert row["extra"] == {}


def test_extra_luu_duoi_dang_json(db_path):
    db.add_reading(100, 28, 7, 300, 6, extra={"water_level_cm": 22.5}, db_path=db_path)
    assert db.get_latest_readings(1, db_path)[0]["extra"] == {"water_level_cm": 22.5}


def test_lay_n_ban_ghi_moi_nhat_moi_nhat_dung_dau(db_path):
    for ts in (1, 2, 3, 4):
        db.add_reading(ts, 28, 7, 300, 6, db_path=db_path)
    rows = db.get_latest_readings(2, db_path)
    assert [r["ts"] for r in rows] == [4, 3]


def test_device_event_va_alert(db_path):
    db.add_device_event(10, "aerator", True, "esp32", db_path=db_path)
    db.add_alert(11, "temp_high", "warning", "Nhiệt độ cao", db_path=db_path)
    event = db.get_latest_device_events(1, db_path)[0]
    alert = db.get_latest_alerts(1, db_path)[0]
    assert (event["device"], event["on_off"], event["source"]) == ("aerator", 1, "esp32")
    assert (alert["kind"], alert["level"], alert["message"]) == ("temp_high", "warning", "Nhiệt độ cao")
