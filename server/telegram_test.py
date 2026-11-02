# server/telegram_test.py
# Gửi một tin thử tới điện thoại để kiểm tra token và chat id.
# Chỉ dùng thư viện có sẵn của Python, chưa cần cài gì.
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def doc_env(duong_dan):
    """Đọc file .env, trả về dict {tên: giá trị}."""
    ket_qua = {}
    # utf-8-sig để bỏ ký tự lạ ở đầu file nếu editor thêm vào
    for dong in Path(duong_dan).read_text(encoding="utf-8-sig").splitlines():
        dong = dong.strip()
        if not dong or dong.startswith("#") or "=" not in dong:
            continue
        ten, gia_tri = dong.split("=", 1)
        ket_qua[ten.strip()] = gia_tri.strip()
    return ket_qua


# .env nằm ở thư mục gốc dự án, cha của thư mục server
env = doc_env(Path(__file__).resolve().parent.parent / ".env")
token = env["TELEGRAM_BOT_TOKEN"]
chat_id = env["TELEGRAM_ALLOWED_CHAT_IDS"].split(",")[0].strip()

url = f"https://api.telegram.org/bot{token}/sendMessage"
du_lieu = urllib.parse.urlencode(
    {"chat_id": chat_id, "text": "Xin chào từ hệ thống ao cá rô phi!"}
).encode()

try:
    with urllib.request.urlopen(url, data=du_lieu, timeout=10) as phan_hoi:
        ket_qua = json.loads(phan_hoi.read().decode())
    print("Đã gửi, ok =", ket_qua.get("ok"))
except urllib.error.HTTPError as loi:
    # 401 = sai token, 400 = sai chat id hoặc bạn chưa nhắn tin cho bot
    print("Lỗi từ Telegram, mã", loi.code)
except Exception as loi:
    print("Lỗi mạng hoặc file .env:", type(loi).__name__)