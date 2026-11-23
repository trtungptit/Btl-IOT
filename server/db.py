"""Lưu dữ liệu vào SQLite (file data/pond.db, không commit lên Git).

Ba bảng:
  readings      số đo cảm biến + DO ước tính (khoá lạ nằm trong cột extra, dạng JSON)
                ts = giờ SERVER lúc nhận tin; ts của ESP32 (nếu có) nằm trong extra["device_ts"]
  device_events lịch sử bật/tắt thiết bị
  alerts        lịch sử cảnh báo

Mỗi hàm tự mở và đóng kết nối nên không lo xung đột giữa các luồng.
Tham số db_path để test dùng file tạm; bình thường cứ để mặc định.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pond.db"

# Các số đo có cột riêng trong bảng readings. Số đo khác (vd water_level_cm) vào extra.
READING_COLUMNS = ("temp", "ph", "tds")


@contextmanager
def _connect(db_path):
    """Mở kết nối; xong việc thì lưu (commit) và đóng."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # cho phép lấy cột theo tên
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path=DEFAULT_DB_PATH):
    """Tạo thư mục data/ và 3 bảng nếu chưa có (chạy nhiều lần vẫn an toàn)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts INTEGER NOT NULL,
                   temp REAL, ph REAL, tds REAL, do_est REAL,
                   extra TEXT)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS device_events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts INTEGER NOT NULL,
                   device TEXT NOT NULL,
                   on_off INTEGER NOT NULL,
                   source TEXT)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS alerts (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts INTEGER NOT NULL,
                   kind TEXT NOT NULL,
                   level TEXT,
                   message TEXT)"""
        )


# ---------- Thêm bản ghi ----------
def add_reading(ts, temp, ph, tds, do_est, extra=None, db_path=DEFAULT_DB_PATH):
    """Thêm một dòng số đo. extra là dict các số đo khác (hoặc None)."""
    extra_text = json.dumps(extra, ensure_ascii=False) if extra else None
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO readings (ts, temp, ph, tds, do_est, extra) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, temp, ph, tds, do_est, extra_text),
        )


def add_device_event(ts, device, on_off, source, db_path=DEFAULT_DB_PATH):
    """Ghi một lần thiết bị đổi trạng thái. on_off: True/False."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO device_events (ts, device, on_off, source) VALUES (?, ?, ?, ?)",
            (ts, device, 1 if on_off else 0, source),
        )


def add_alert(ts, kind, level, message, db_path=DEFAULT_DB_PATH):
    """Ghi một cảnh báo."""
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO alerts (ts, kind, level, message) VALUES (?, ?, ?, ?)",
            (ts, kind, level, message),
        )


# ---------- Lấy N bản ghi mới nhất (mới nhất đứng đầu) ----------
def _latest(table, n, db_path):
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (n,))
        return [dict(row) for row in rows]


def _giai_ma_extra(rows):
    """Đổi cột extra từ chuỗi JSON về dict."""
    for row in rows:
        row["extra"] = json.loads(row["extra"]) if row["extra"] else {}
    return rows


def get_latest_readings(n=10, db_path=DEFAULT_DB_PATH):
    """N số đo mới nhất (mới nhất đứng đầu)."""
    return _giai_ma_extra(_latest("readings", n, db_path))


def get_readings_since(ts_from, db_path=DEFAULT_DB_PATH):
    """Mọi số đo có ts >= ts_from, CŨ nhất đứng đầu (đúng thứ tự để vẽ biểu đồ)."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM readings WHERE ts >= ? ORDER BY ts ASC, id ASC", (ts_from,))
        return _giai_ma_extra([dict(row) for row in rows])


def get_latest_device_events(n=10, db_path=DEFAULT_DB_PATH):
    return _latest("device_events", n, db_path)


def get_latest_alerts(n=10, db_path=DEFAULT_DB_PATH):
    return _latest("alerts", n, db_path)
