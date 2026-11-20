"""
Account Progress & State Cache Module
Tracks which accounts and characters have completed the daily quiz event,
and categorizes all error states (Auth error, Network error, Map location error).
"""

import os
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from core.storage_utils import atomic_write_json, prune_old_date_keys

VN_TZ = timezone(timedelta(hours=7))

class AccountCache:
    def __init__(self, cache_file: str = None):
        if not cache_file:
            storage_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")
            os.makedirs(storage_dir, exist_ok=True)
            cache_file = os.path.join(storage_dir, "account_state.json")

        self.cache_file = cache_file
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = {}
        self.load()

    def _get_today_key(self) -> str:
        return datetime.now(VN_TZ).strftime("%Y-%m-%d")

    def load(self):
        with self.lock:
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        self.data = json.load(f)
                except Exception:
                    self.data = {}
            else:
                self.data = {}

    def save(self):
        with self.lock:
            prune_old_date_keys(self.data, keep_days=14)
            try:
                atomic_write_json(self.cache_file, self.data, indent=2)
            except Exception as e:
                print(f"[Bộ nhớ đệm] [LỖI] Không thể lưu trạng thái tài khoản: {e}")

    def is_account_completed(self, username: str) -> bool:
        today = self._get_today_key()
        day_data = self.data.get(today, {})
        acc_info = day_data.get(username, {})
        return acc_info.get("status") == "COMPLETED"

    def is_character_quiz_completed(self, username: str, char_name: str) -> bool:
        """Kiểm tra xem nhân vật cụ thể đã hoàn thành 20 câu vấn đáp trong ngày hôm nay chưa."""
        today = self._get_today_key()
        day_data = self.data.get(today, {})
        acc_info = day_data.get(username, {})
        chars = acc_info.get("characters", {})
        char_entry = chars.get(char_name, {})
        return bool(char_entry.get("completed", False))

    def mark_character_quiz_completed(self, username: str, char_name: str, correct_count: int = 20, total_answered: int = 20, level: int = 0):
        """Lưu trữ ngay lập tức trạng thái hoàn thành 20 câu vấn đáp của nhân vật trong ngày hôm nay."""
        with self.lock:
            today = self._get_today_key()
            if today not in self.data:
                self.data[today] = {}
            if username not in self.data[today]:
                self.data[today][username] = {
                    "status": "IN_PROGRESS",
                    "updated_at": datetime.now(VN_TZ).strftime("%H:%M:%S"),
                    "characters": {}
                }
            if "characters" not in self.data[today][username]:
                self.data[today][username]["characters"] = {}

            self.data[today][username]["characters"][char_name] = {
                "completed": True,
                "correct": correct_count,
                "total": total_answered,
                "level": level,
                "completed_at": datetime.now(VN_TZ).strftime("%H:%M:%S")
            }
            self.save()

    def get_account_quiz_characters(self, username: str) -> Dict[str, Any]:
        """Lấy danh sách các nhân vật đã hoàn thành vấn đáp hôm nay của tài khoản."""
        today = self._get_today_key()
        day_data = self.data.get(today, {})
        acc_info = day_data.get(username, {})
        return acc_info.get("characters", {})

    def mark_account_completed(self, username: str, characters_done: int = 3, total_chars: int = 3):
        today = self._get_today_key()
        if today not in self.data:
            self.data[today] = {}
        if username not in self.data[today]:
            self.data[today][username] = {}

        self.data[today][username]["status"] = "COMPLETED"
        self.data[today][username]["characters_done"] = f"{characters_done}/{total_chars}"
        self.data[today][username]["completed_at"] = datetime.now(VN_TZ).strftime("%H:%M:%S")
        self.save()

    def mark_account_in_progress(self, username: str, characters_done: int = 1, total_chars: int = 3):
        today = self._get_today_key()
        if today not in self.data:
            self.data[today] = {}
        if username not in self.data[today]:
            self.data[today][username] = {}

        self.data[today][username]["status"] = "IN_PROGRESS"
        self.data[today][username]["characters_done"] = f"{characters_done}/{total_chars}"
        self.data[today][username]["updated_at"] = datetime.now(VN_TZ).strftime("%H:%M:%S")
        self.save()

    def mark_account_failed_auth(self, username: str, error_msg: str):
        today = self._get_today_key()
        if today not in self.data:
            self.data[today] = {}
        if username not in self.data[today]:
            self.data[today][username] = {}

        self.data[today][username]["status"] = "FAILED_AUTH"
        self.data[today][username]["reason"] = error_msg
        self.data[today][username]["updated_at"] = datetime.now(VN_TZ).strftime("%H:%M:%S")
        self.save()

    def mark_account_failed_network(self, username: str, error_msg: str):
        today = self._get_today_key()
        if today not in self.data:
            self.data[today] = {}
        if username not in self.data[today]:
            self.data[today][username] = {}

        self.data[today][username]["status"] = "FAILED_NETWORK"
        self.data[today][username]["reason"] = error_msg
        self.data[today][username]["updated_at"] = datetime.now(VN_TZ).strftime("%H:%M:%S")
        self.save()

    def mark_account_skipped_map(self, username: str, wrong_map_chars: List[Dict]):
        today = self._get_today_key()
        if today not in self.data:
            self.data[today] = {}

        self.data[today][username] = {
            "status": "SKIPPED_MAP",
            "reason": "Nhân vật chưa ở Thánh Vực",
            "characters": wrong_map_chars,
            "updated_at": datetime.now(VN_TZ).strftime("%H:%M:%S")
        }
        self.save()

    def get_summary(self) -> Dict[str, Any]:
        today = self._get_today_key()
        day_data = self.data.get(today, {})

        completed = []
        failed_auth = []
        failed_network = []
        skipped_map = []

        for user, info in day_data.items():
            st = info.get("status")
            if st == "COMPLETED":
                completed.append(user)
            elif st == "FAILED_AUTH":
                failed_auth.append((user, info.get("reason", "Sai tài khoản/mật khẩu")))
            elif st == "FAILED_NETWORK":
                failed_network.append((user, info.get("reason", "Lỗi mất kết nối")))
            elif st == "SKIPPED_MAP":
                skipped_map.append((user, info.get("characters", [])))

        return {
            "date": today,
            "completed": completed,
            "failed_auth": failed_auth,
            "failed_network": failed_network,
            "skipped_map": skipped_map
        }


# Singleton cache instance
account_cache = AccountCache()
