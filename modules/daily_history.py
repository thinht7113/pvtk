"""
PVTK Daily Task History Manager (Lottery & Quiz).
Thread-safe persistent tracker to ensure characters never repeat Lottery or Quiz on the same day.
Data is persisted to disk in:
- storage/lottery_history.json
- storage/quiz_history.json
Also checks legacy storage/lottery_results.json for seamless backward compatibility.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Set, List

from core.config import settings
from core.storage_utils import atomic_write_json, prune_old_date_keys

VN_TZ = timezone(timedelta(hours=7))
_HISTORY_LOCK = threading.Lock()


class DailyHistoryManager:
    _lottery_cache_date: str = ""
    _lottery_cache: Dict[str, Dict[str, Any]] = {}

    _quiz_cache_date: str = ""
    _quiz_cache: Dict[str, Dict[str, Any]] = {}

    _login_lottery_cache_date: str = ""
    _login_lottery_cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _get_lottery_path(cls) -> str:
        os.makedirs(settings.storage_dir, exist_ok=True)
        return os.path.join(settings.storage_dir, "lottery_history.json")

    @classmethod
    def _get_quiz_path(cls) -> str:
        os.makedirs(settings.storage_dir, exist_ok=True)
        return os.path.join(settings.storage_dir, "quiz_history.json")

    @classmethod
    def _make_key(cls, username: str, char_name: str) -> str:
        u = (username or "").strip().lower()
        c = (char_name or "").strip().lower()
        if u and c:
            return f"{u}_{c}"
        return c or u

    # =========================================================================
    # LOTTERY HISTORY
    # =========================================================================

    @classmethod
    def _ensure_lottery_cache(cls, today_str: str):
        if cls._lottery_cache_date == today_str:
            return

        cls._lottery_cache_date = today_str
        cls._lottery_cache = {}

        # 1. Load from storage/lottery_history.json
        file_path = cls._get_lottery_path()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                    today_data = all_data.get(today_str, {})
                    if isinstance(today_data, dict):
                        for k, v in today_data.items():
                            cls._lottery_cache[k.lower()] = v
            except Exception:
                pass

        # 2. Backward compatibility: Load from storage/lottery_results.json
        legacy_path = os.path.join(settings.storage_dir, "lottery_results.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    leg_data = json.load(f)
                    today_list = leg_data.get(today_str, [])
                    if isinstance(today_list, list):
                        for acc_item in today_list:
                            u = acc_item.get("username", "")
                            for cr in acc_item.get("characters_results", []):
                                c_name = cr.get("name", "")
                                if not c_name:
                                    continue
                                k = cls._make_key(u, c_name)
                                if k not in cls._lottery_cache:
                                    cls._lottery_cache[k] = {
                                        "username": u,
                                        "char_name": c_name,
                                        "level": cr.get("level", 0),
                                        "best_number": cr.get("best_number", 0),
                                        "rolls_done": cr.get("rolls_done", 0),
                                        "hit_target": cr.get("hit_target", False),
                                        "hit_max_1000": cr.get("hit_max_1000", False),
                                        "status": cr.get("status", "COMPLETED"),
                                        "message": cr.get("message", ""),
                                        "time": "legacy"
                                    }
            except Exception:
                pass

    @classmethod
    def is_lottery_done_today(cls, username: str, char_name: str) -> bool:
        """Checks if a character has already completed lottery today."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_lottery_cache(today_str)
            k1 = cls._make_key(username, char_name)
            k2 = (char_name or "").strip().lower()
            return k1 in cls._lottery_cache or k2 in cls._lottery_cache

    @classmethod
    def get_lottery_record(cls, username: str, char_name: str) -> Optional[Dict[str, Any]]:
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_lottery_cache(today_str)
            k1 = cls._make_key(username, char_name)
            if k1 in cls._lottery_cache:
                return cls._lottery_cache[k1]
            k2 = (char_name or "").strip().lower()
            return cls._lottery_cache.get(k2)

    @classmethod
    def save_lottery_done(
        cls,
        username: str,
        char_name: str,
        level: int = 0,
        best_number: int = 0,
        rolls_done: int = 0,
        hit_target: bool = False,
        hit_max_1000: bool = False,
        status: str = "COMPLETED",
        message: str = ""
    ):
        """Persists completed lottery result to memory cache and storage/lottery_history.json."""
        now = datetime.now(VN_TZ)
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        key = cls._make_key(username, char_name)

        record = {
            "username": username,
            "char_name": char_name,
            "level": level,
            "best_number": best_number,
            "rolls_done": rolls_done,
            "hit_target": hit_target,
            "hit_max_1000": hit_max_1000,
            "status": status,
            "message": message,
            "time": time_str
        }

        with _HISTORY_LOCK:
            cls._ensure_lottery_cache(today_str)
            cls._lottery_cache[key] = record
            char_key_only = (char_name or "").strip().lower()
            if char_key_only:
                cls._lottery_cache[char_key_only] = record

            # Persist to disk
            file_path = cls._get_lottery_path()
            all_data = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        all_data = json.load(f)
                except Exception:
                    all_data = {}

            if today_str not in all_data:
                all_data[today_str] = {}

            all_data[today_str][key] = record

            prune_old_date_keys(all_data, keep_days=14)
            try:
                atomic_write_json(file_path, all_data, indent=2)
            except Exception:
                pass

    @classmethod
    def get_all_lottery_dates(cls) -> List[str]:
        """Trả về danh sách tất cả các ngày có lưu lịch sử rút thưởng theo thứ tự mới nhất -> cũ nhất."""
        file_path = cls._get_lottery_path()
        if not os.path.exists(file_path):
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_data = json.load(f)
                dates = [d for d, v in all_data.items() if isinstance(v, dict) and len(v) > 0]
                dates.sort(reverse=True)
                return dates
        except Exception:
            return []

    @classmethod
    def get_lottery_summary(cls, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Returns statistical summary of all characters completed on target_date (default today)."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        if not target_date:
            target_date = today_str

        with _HISTORY_LOCK:
            if target_date == today_str:
                cls._ensure_lottery_cache(today_str)
                records_source = cls._lottery_cache.values()
            else:
                file_path = cls._get_lottery_path()
                day_data = {}
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            all_data = json.load(f)
                            day_data = all_data.get(target_date, {})
                    except Exception:
                        day_data = {}
                records_source = day_data.values() if isinstance(day_data, dict) else []

            # Filter unique by char_name to avoid counting duplicate key formats
            seen_chars = set()
            records = []
            for r in records_source:
                if not isinstance(r, dict):
                    continue
                c = r.get("char_name", "")
                if c and c not in seen_chars:
                    seen_chars.add(c)
                    records.append(r)

            total_chars = len(records)
            lucky_321_records = [r for r in records if r.get("hit_target") or r.get("best_number") == 321]
            hit_target = len(lucky_321_records)
            hit_max = sum(1 for r in records if r.get("hit_max_1000") or r.get("best_number", 0) >= 1000)
            best_all = max((r.get("best_number", 0) for r in records), default=0)

            top_records = []
            if best_all > 0:
                top_records = [r for r in records if r.get("best_number", 0) == best_all]

            return {
                "date": target_date,
                "total_chars": total_chars,
                "hit_target": hit_target,
                "hit_max": hit_max,
                "best_all": best_all,
                "top_records": top_records,
                "lucky_321_records": lucky_321_records,
                "records": records
            }

    @classmethod
    def get_today_lottery_summary(cls) -> Dict[str, Any]:
        """Returns statistical summary of all characters completed today."""
        return cls.get_lottery_summary()

    @classmethod
    def get_recent_lottery_overview(cls, limit: int = 7) -> List[Dict[str, Any]]:
        """Trả về thống kê tóm tắt các ngày rút thưởng gần nhất (mặc định 7 ngày)."""
        dates = cls.get_all_lottery_dates()
        overview = []
        for d_str in dates[:limit]:
            s = cls.get_lottery_summary(target_date=d_str)
            try:
                display_date = datetime.strptime(d_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                display_date = d_str
            overview.append({
                "date": d_str,
                "display_date": display_date,
                "total_chars": s.get("total_chars", 0),
                "best_all": s.get("best_all", 0),
                "hit_target": s.get("hit_target", 0),
                "hit_max": s.get("hit_max", 0)
            })
        return overview

    @classmethod
    def format_lottery_summary_message(
        cls,
        title: Optional[str] = None,
        elapsed: Optional[float] = None,
        target_date: Optional[str] = None
    ) -> str:
        """Tạo báo cáo chi tiết kèm danh sách nhân vật/tài khoản có điểm cao nhất và trúng số 321."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        if not target_date:
            target_date = today_str

        try:
            display_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            display_date = target_date

        if not title:
            title = "🎲 <b>BÁO CÁO KẾT QUẢ RÚT THƯỞNG MAY MẮN (08:01)</b>"

        summary = cls.get_lottery_summary(target_date=target_date)
        total_chars = summary.get("total_chars", 0)
        hit_max = summary.get("hit_max", 0)
        best_all = summary.get("best_all", 0)
        top_records = summary.get("top_records", [])
        lucky_321 = summary.get("lucky_321_records", [])

        if total_chars == 0:
            return (
                f"{title}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 <b>Ngày rút thưởng:</b> {display_date}\n"
                f"⚠️ <i>Không có dữ liệu rút thưởng số may mắn trong ngày này.</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )

        lines = [
            title,
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 <b>Ngày rút thưởng:</b> {display_date}",
            f"👥 <b>Nhân vật tham gia (Lv30+):</b> {total_chars}",
            f"🏆 <b>Điểm số cao nhất:</b> {best_all}",
            f"🎯 <b>Trúng số may mắn (321):</b> {len(lucky_321)}",
            f"🌟 <b>Đạt điểm tối đa (1000):</b> {hit_max}",
        ]
        if elapsed is not None:
            lines.append(f"⏱ <b>Thời gian quét:</b> {elapsed} giây")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        # 1. Nhân vật có số cao nhất
        lines.append("👑 <b>NHÂN VẬT CÓ ĐIỂM SỐ CAO NHẤT:</b>")
        if top_records:
            for r in top_records[:10]:
                u = r.get("username") or "N/A"
                c = r.get("char_name") or "N/A"
                score = r.get("best_number", 0)
                rolls = r.get("rolls_done", 0)
                lines.append(f"  • Nhân vật: <b>{c}</b> | Acc: <code>{u}</code> | Điểm: <b>{score}</b> ({rolls} lần rút)")
            if len(top_records) > 10:
                lines.append(f"  <i>... và {len(top_records) - 10} nhân vật khác cùng đạt {best_all} điểm.</i>")
        else:
            lines.append("  <i>(Chưa có dữ liệu điểm số)</i>")

        lines.append("")
        # 2. Nhân vật trúng số may mắn 321
        lines.append("🎯 <b>NHÂN VẬT TRÚNG SỐ MAY MẮN 321:</b>")
        if lucky_321:
            for r in lucky_321:
                u = r.get("username") or "N/A"
                c = r.get("char_name") or "N/A"
                rolls = r.get("rolls_done", 0)
                lines.append(f"  • 🎉 Nhân vật: <b>{c}</b> | Acc: <code>{u}</code> | Trúng số: <b>321</b> ({rolls} lần rút)")
        else:
            lines.append(f"  <i>(Ngày {display_date} chưa có nhân vật nào quay trúng số 321)</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💾 <i>Lịch sử đã được lưu vào storage/lottery_history.json tránh lặp lại!</i>")
        return "\n".join(lines)

    # =========================================================================
    # QUIZ HISTORY
    # =========================================================================

    @classmethod
    def _ensure_quiz_cache(cls, today_str: str):
        if cls._quiz_cache_date == today_str:
            return

        cls._quiz_cache_date = today_str
        cls._quiz_cache = {}

        file_path = cls._get_quiz_path()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                    today_data = all_data.get(today_str, {})
                    if isinstance(today_data, dict):
                        for k, v in today_data.items():
                            cls._quiz_cache[k.lower()] = v
            except Exception:
                pass

    @classmethod
    def is_quiz_done_today(cls, username: str, char_name: str) -> bool:
        """Checks if a character has already completed quiz today (verified is_done or questions >= 20)."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_quiz_cache(today_str)
            k1 = cls._make_key(username, char_name)
            k2 = (char_name or "").strip().lower()
            rec = cls._quiz_cache.get(k1)
            if not rec and k2:
                rec = cls._quiz_cache.get(k2)
            if not rec:
                return False
            return bool(rec.get("is_done", False) or rec.get("questions", 0) >= 20)

    @classmethod
    def save_quiz_done(
        cls,
        username: str,
        char_name: str,
        level: int = 0,
        questions: int = 0,
        correct: int = 0,
        is_done: bool = True,
        message: str = ""
    ):
        """Persists completed quiz result to memory cache and storage/quiz_history.json."""
        # Only mark as completed today if the quiz was actually finished
        if not is_done and questions < 20:
            return

        now = datetime.now(VN_TZ)
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        key = cls._make_key(username, char_name)

        record = {
            "username": username,
            "char_name": char_name,
            "level": level,
            "questions": questions,
            "correct": correct,
            "is_done": is_done,
            "message": message,
            "time": time_str
        }

        with _HISTORY_LOCK:
            cls._ensure_quiz_cache(today_str)
            cls._quiz_cache[key] = record
            char_key_only = (char_name or "").strip().lower()
            if char_key_only:
                cls._quiz_cache[char_key_only] = record

            file_path = cls._get_quiz_path()
            all_data = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        all_data = json.load(f)
                except Exception:
                    all_data = {}

            if today_str not in all_data:
                all_data[today_str] = {}

            all_data[today_str][key] = record

            prune_old_date_keys(all_data, keep_days=14)
            try:
                atomic_write_json(file_path, all_data, indent=2)
            except Exception:
                pass

    @classmethod
    def get_today_quiz_summary(cls) -> Dict[str, Any]:
        """Returns statistical summary of all characters completed quiz today."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_quiz_cache(today_str)
            seen_chars = set()
            records = []
            for r in cls._quiz_cache.values():
                c = r.get("char_name", "")
                if c and c not in seen_chars:
                    seen_chars.add(c)
                    records.append(r)

            total_chars = len(records)
            total_correct = sum(r.get("correct", 0) for r in records)
            total_done = sum(1 for r in records if r.get("is_done") or r.get("questions", 0) >= 20)

            return {
                "total_chars": total_chars,
                "total_correct": total_correct,
                "total_done": total_done,
                "records": records
            }

    # =========================================================================
    # ONLINE REWARD HISTORY
    # =========================================================================

    _online_reward_cache_date: str = ""
    _online_reward_cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _get_online_reward_path(cls) -> str:
        os.makedirs(settings.storage_dir, exist_ok=True)
        return os.path.join(settings.storage_dir, "online_reward_history.json")

    @classmethod
    def _ensure_online_reward_cache(cls, today_str: str):
        if cls._online_reward_cache_date == today_str:
            return

        cls._online_reward_cache_date = today_str
        cls._online_reward_cache = {}

        file_path = cls._get_online_reward_path()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                    today_data = all_data.get(today_str, {})
                    if isinstance(today_data, dict):
                        for k, v in today_data.items():
                            cls._online_reward_cache[k.lower()] = v
            except Exception:
                pass

    @classmethod
    def is_online_reward_done_today(cls, username: str, char_name: str) -> bool:
        """Kiểm tra xem một nhân vật đã nhận hết quà online hôm nay chưa."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_online_reward_cache(today_str)
            k1 = cls._make_key(username, char_name)
            k2 = (char_name or "").strip().lower()
            return k1 in cls._online_reward_cache or k2 in cls._online_reward_cache

    @classmethod
    def is_account_online_reward_done_today(cls, username: str, char_names: List[str]) -> bool:
        """Kiểm tra xem toàn bộ các nhân vật trên tài khoản đã nhận hết quà online hôm nay chưa."""
        if not char_names:
            return False
        return all(cls.is_online_reward_done_today(username, c) for c in char_names)

    @classmethod
    def save_online_reward_done(
        cls,
        username: str,
        char_name: str,
        milestones: int = 0
    ):
        """Lưu lại trạng thái nhân vật đã nhận đủ quà online hôm nay vào file storage/online_reward_history.json."""
        now = datetime.now(VN_TZ)
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        key = cls._make_key(username, char_name)

        record = {
            "username": username,
            "char_name": char_name,
            "milestones": milestones,
            "completed": True,
            "time": time_str
        }

        with _HISTORY_LOCK:
            cls._ensure_online_reward_cache(today_str)
            cls._online_reward_cache[key] = record
            char_key_only = (char_name or "").strip().lower()
            if char_key_only:
                cls._online_reward_cache[char_key_only] = record

            file_path = cls._get_online_reward_path()
            all_data = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        all_data = json.load(f)
                except Exception:
                    all_data = {}

            if today_str not in all_data:
                all_data[today_str] = {}

            all_data[today_str][key] = record

            prune_old_date_keys(all_data, keep_days=14)
            try:
                atomic_write_json(file_path, all_data, indent=2)
            except Exception:
                pass

    @classmethod
    def save_account_fully_done_today(cls, username: str):
        """Lưu lại trạng thái tài khoản đã hoàn thành 100% tất cả nhân vật trong ngày hôm nay."""
        now = datetime.now(VN_TZ)
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        key = f"__account_fully_done__:{username.strip().lower()}"

        record = {
            "username": username,
            "account_completed": True,
            "time": time_str
        }

        with _HISTORY_LOCK:
            cls._ensure_online_reward_cache(today_str)
            cls._online_reward_cache[key] = record

            file_path = cls._get_online_reward_path()
            all_data = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        all_data = json.load(f)
                except Exception:
                    all_data = {}

            if today_str not in all_data:
                all_data[today_str] = {}

            all_data[today_str][key] = record

            prune_old_date_keys(all_data, keep_days=14)
            try:
                atomic_write_json(file_path, all_data, indent=2)
            except Exception:
                pass

    @classmethod
    def is_account_fully_done_today(cls, username: str) -> bool:
        """Kiểm tra xem tài khoản đã được đánh dấu hoàn thành 100% tất cả nhân vật hôm nay chưa."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_online_reward_cache(today_str)
            key = f"__account_fully_done__:{username.strip().lower()}"
            return key in cls._online_reward_cache

    @classmethod
    def get_today_online_reward_summary(cls) -> Dict[str, Any]:
        """Thống kê chi tiết số lượng nhân vật và tài khoản đã hoàn thành quà online hôm nay."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_online_reward_cache(today_str)
            seen_chars = set()
            records = []
            explicit_fully_done = set()
            acc_char_map: Dict[str, Set[str]] = {}

            for k, r in cls._online_reward_cache.items():
                if r.get("account_completed"):
                    u = r.get("username", "").strip().lower()
                    if u:
                        explicit_fully_done.add(u)
                    continue
                c = r.get("char_name", "")
                u = r.get("username", "").strip().lower()
                if c and c not in seen_chars:
                    seen_chars.add(c)
                    records.append(r)
                if u and c:
                    if u not in acc_char_map:
                        acc_char_map[u] = set()
                    acc_char_map[u].add(c)

            # Tài khoản hoàn thành 100%: có cờ explicit_fully_done HOẶC đã xong >= 3 nhân vật
            fully_done_accs = set(explicit_fully_done)
            for u, c_set in acc_char_map.items():
                if len(c_set) >= 3:
                    fully_done_accs.add(u)

            # Tài khoản đang treo xoay tua (đã xong 1-2 NV nhưng chưa đủ 3 NV)
            partial_accs = set(u for u, c_set in acc_char_map.items() if len(c_set) < 3 and u not in fully_done_accs)

            return {
                "total_chars_done": len(records),
                "total_accs_done": len(fully_done_accs), # Số acc đã xong 100% tất cả nhân vật (đang OFFLINE)
                "total_accs_fully_done": len(fully_done_accs),
                "total_accs_partial": len(partial_accs), # Số acc đang online nhận tiếp NV 2-3
                "total_accs_started": len(acc_char_map),
                "records": records
            }

    # =========================================================================
    # LOGIN LOTTERY HISTORY (RÚT THƯỞNG LOGIN / ĐĂNG NHẬP)
    # =========================================================================

    @classmethod
    def _get_login_lottery_path(cls) -> str:
        os.makedirs(settings.storage_dir, exist_ok=True)
        return os.path.join(settings.storage_dir, "login_lottery_history.json")

    @classmethod
    def _ensure_login_lottery_cache(cls, today_str: str):
        if cls._login_lottery_cache_date == today_str:
            return

        cls._login_lottery_cache_date = today_str
        cls._login_lottery_cache = {}

        file_path = cls._get_login_lottery_path()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                    today_data = all_data.get(today_str, {})
                    if isinstance(today_data, dict):
                        for k, v in today_data.items():
                            cls._login_lottery_cache[k.lower()] = v
            except Exception:
                pass

    @classmethod
    def is_login_lottery_done_today(cls, username: str, char_name: str) -> bool:
        """Kiểm tra xem nhân vật đã rút hoặc đã hết lượt rút thưởng login hôm nay chưa."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_login_lottery_cache(today_str)
            k = cls._make_key(username, char_name)
            if k in cls._login_lottery_cache:
                return True
            char_key = (char_name or "").strip().lower()
            if char_key and char_key in cls._login_lottery_cache:
                return True
            return False

    @classmethod
    def save_login_lottery_done(
        cls,
        username: str,
        char_name: str,
        level: int = 0,
        focus_index: int = -1,
        item_name: str = "",
        status: str = "SUCCESS",
        message: str = ""
    ):
        """Lưu lại kết quả rút thưởng login cho nhân vật hôm nay."""
        now = datetime.now(VN_TZ)
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        key = cls._make_key(username, char_name)

        record = {
            "username": username,
            "char_name": char_name,
            "level": level,
            "focus_index": focus_index,
            "item_name": item_name,
            "status": status,
            "message": message,
            "time": time_str
        }

        with _HISTORY_LOCK:
            cls._ensure_login_lottery_cache(today_str)
            cls._login_lottery_cache[key] = record
            char_key = (char_name or "").strip().lower()
            if char_key:
                cls._login_lottery_cache[char_key] = record

            file_path = cls._get_login_lottery_path()
            all_data = {}
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        all_data = json.load(f)
                except Exception:
                    all_data = {}

            if today_str not in all_data:
                all_data[today_str] = {}

            all_data[today_str][key] = record

            prune_old_date_keys(all_data, keep_days=14)
            try:
                atomic_write_json(file_path, all_data, indent=2)
            except Exception:
                pass

    @classmethod
    def get_today_login_lottery_summary(cls) -> Dict[str, Any]:
        """Thống kê chi tiết kết quả rút thưởng login hôm nay."""
        today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        with _HISTORY_LOCK:
            cls._ensure_login_lottery_cache(today_str)
            seen_chars = set()
            items_won = []
            total_success = 0
            total_already_drawn = 0

            for k, r in cls._login_lottery_cache.items():
                c = r.get("char_name", "")
                if c and c in seen_chars:
                    continue
                if c:
                    seen_chars.add(c)
                status = r.get("status", "")
                if status == "SUCCESS":
                    total_success += 1
                    if r.get("item_name"):
                        items_won.append(r)
                elif status == "ALREADY_DRAWN":
                    total_already_drawn += 1

            return {
                "total_scanned": len(seen_chars),
                "total_success": total_success,
                "total_already_drawn": total_already_drawn,
                "items_won": items_won
            }

    EXCLUDED_LOGIN_KEYWORDS = [
        "dược thảo", "ngày", "giờ", "đá", "vật liệu", "đồng", "kim điệp", "kim diệp"
    ]

    @classmethod
    def is_excluded_login_item(cls, item_name: str) -> bool:
        """Kiểm tra xem tên vật phẩm có thuộc danh sách bị lọc bỏ (vật phẩm thông thường/rác) hay không."""
        if not item_name:
            return True
        name_lower = item_name.strip().lower()
        for kw in cls.EXCLUDED_LOGIN_KEYWORDS:
            if kw in name_lower:
                return True
        return False

    @classmethod
    def format_grouped_login_items(
        cls,
        items_won: List[Dict[str, Any]],
        max_chars_per_item: int = 8,
        bullet: str = "  • ",
        strip_html: bool = False
    ) -> List[str]:
        """
        Gộp các vật phẩm rút thưởng login giống nhau lại theo item_name:
        Ví dụ:
          • Hộp Sách Tu Chân (x5): char1 (acc1), char2 (acc2), char3 (acc3)...
          • 🐱 Trứng Mèo Đen (x1): charA (accA) 🔥
        """
        valuable_items = [
            it for it in items_won
            if not cls.is_excluded_login_item(it.get("item_name", ""))
        ]
        if not valuable_items:
            return []

        # Nhóm theo item_name (chuẩn hóa khoảng trắng)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for it in valuable_items:
            iname = (it.get("item_name") or "Vật phẩm").strip()
            if iname not in grouped:
                grouped[iname] = []
            grouped[iname].append(it)

        # Sắp xếp: Trứng mèo đen ưu tiên đầu tiên, sau đó theo số lượng trúng giảm dần
        def sort_key(kv):
            iname = kv[0].lower()
            is_cat = 1 if ("mèo đen" in iname or "meo den" in iname) else 0
            return (is_cat, len(kv[1]))

        sorted_groups = sorted(grouped.items(), key=sort_key, reverse=True)

        lines = []
        for iname, winners in sorted_groups:
            count = len(winners)
            is_cat = "mèo đen" in iname.lower() or "meo den" in iname.lower()
            pfx = "🐱 " if is_cat else ""
            sfx = " 🔥" if is_cat else ""

            shown_winners = winners[:max_chars_per_item]
            if strip_html:
                winner_strs = [
                    f"{w.get('char_name', 'N/A')} ({w.get('username', 'N/A')})"
                    for w in shown_winners
                ]
                remain = count - len(shown_winners)
                if remain > 0:
                    winner_strs.append(f"+{remain} NV khác")
                winners_text = ", ".join(winner_strs)
                lines.append(f"{bullet}{pfx}{iname} (x{count}): {winners_text}{sfx}")
            else:
                winner_strs = [
                    f"{w.get('char_name', 'N/A')} (<code>{w.get('username', 'N/A')}</code>)"
                    for w in shown_winners
                ]
                remain = count - len(shown_winners)
                if remain > 0:
                    winner_strs.append(f"<i>+{remain} NV khác</i>")
                winners_text = ", ".join(winner_strs)
                lines.append(f"{bullet}{pfx}<b>{iname} (x{count}):</b> {winners_text}{sfx}")

        return lines

    @classmethod
    def format_login_lottery_summary_message(
        cls,
        title: str = "🎁 <b>BÁO CÁO KẾT QUẢ RÚT THƯỞNG LOGIN (07:01)</b>",
        elapsed: Optional[float] = None
    ) -> str:
        """Tạo báo cáo chi tiết gửi Telegram với các vật phẩm giống nhau được gộp gọn gàng."""
        summary = cls.get_today_login_lottery_summary()
        total_scanned = summary.get("total_scanned", 0)
        total_success = summary.get("total_success", 0)
        total_already = summary.get("total_already_drawn", 0)
        items_won = summary.get("items_won", [])

        valuable_items = [
            it for it in items_won
            if not cls.is_excluded_login_item(it.get("item_name", ""))
        ]
        filtered_count = len(items_won) - len(valuable_items)

        lines = [
            title,
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🧙‍♂️ <b>Nhân vật tham gia (Lv &gt;= 35):</b> {total_scanned} NV",
            f"🎉 <b>Rút thưởng thành công:</b> <b>{total_success}</b> lượt",
            f"⏳ <b>Đã rút từ trước hôm nay:</b> {total_already} NV",
        ]
        if elapsed is not None:
            lines.append(f"⏱ <b>Thời gian hoàn thành:</b> {elapsed} giây")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

        grouped_lines = cls.format_grouped_login_items(items_won, max_chars_per_item=8, bullet="  • ")
        if grouped_lines:
            lines.append(f"🎁 <b>DANH SÁCH VẬT PHẨM QUÝ ({len(valuable_items)} món):</b>")
            for gl in grouped_lines:
                lines.append(gl)
            if filtered_count > 0:
                lines.append(f"\n🗑 <i>(Đã lọc ẩn {filtered_count} vật phẩm thường: Dược Thảo, Ngày, Giờ, Đá, Vật Liệu, Đồng, Kim Điệp)</i>")
        else:
            lines.append("🎁 <b>VẬT PHẨM TRÚNG THƯỞNG:</b>")
            lines.append("<i>(Hôm nay không rút trúng vật phẩm hiếm, toàn bộ nhận vật phẩm thường)</i>")
            if filtered_count > 0:
                lines.append(f"<i>(Đã lọc ẩn {filtered_count} vật phẩm thường: Dược Thảo, Ngày, Giờ, Đá, Vật Liệu, Đồng, Kim Điệp)</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("🟢 <i>Đang khôi phục trạng thái treo Online nhận quà 24/7...</i>")
        return "\n".join(lines)
