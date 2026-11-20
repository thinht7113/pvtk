"""
PVTK Character Registry & Slot Index Mapper Module
Maintains a persistent, accurate registry of character slot mappings (Slot 1, Slot 2, Slot 3)
per account and per server in storage/character_registry.json.
Guarantees Option 11 (Tàu Kéo Quân) and all runners enter the exact desired character slot without error.
"""

import sys
import os
import json
import time
import threading
from typing import List, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from modules.auth import CharacterInfo

REGISTRY_LOCK = threading.Lock()
REGISTRY_FILE = os.path.join(settings.base_dir, "storage", "character_registry.json")


def _load_registry() -> Dict[str, Any]:
    """Loads the character registry JSON."""
    if not os.path.exists(REGISTRY_FILE):
        return {}
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_registry(data: Dict[str, Any]):
    """Saves the character registry JSON atomically."""
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    temp_file = REGISTRY_FILE + ".tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, REGISTRY_FILE)
    except Exception:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception:
            pass


class CharacterRegistry:
    @staticmethod
    def get_last_char_id(server_name: str, username: str) -> Optional[int]:
        """Gets the last logged in character ID for an account from the registry."""
        data = _load_registry()
        key = f"{username}_last_char_id"
        if server_name in data and key in data[server_name]:
            return data[server_name][key]
        return None

    @staticmethod
    def set_last_char_id(server_name: str, username: str, char_id: int):
        """Sets the last logged in character ID for an account in the registry."""
        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name not in data:
                data[server_name] = {}
            key = f"{username}_last_char_id"
            if data[server_name].get(key) != char_id:
                data[server_name][key] = char_id
                _save_registry(data)

    @staticmethod
    def get_learned_skills(server_name: str, username: str, char_index: int) -> Dict[str, int]:
        """Lấy danh sách các kỹ năng đã học (skill_id -> max_level)"""
        data = _load_registry()
        if server_name in data and username in data[server_name]:
            acc_data = data[server_name][username]
            if str(char_index) in acc_data:
                return acc_data[str(char_index)].get("skills", {})
        return {}

    @staticmethod
    def add_learned_skill(server_name: str, username: str, char_index: int, skill_id: int, level: int):
        """Lưu lại cấp độ cao nhất của kỹ năng đã học để không bị học trùng ở lần chạy sau"""
        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name not in data:
                data[server_name] = {}
            if username not in data[server_name]:
                data[server_name][username] = {}
            
            idx_str = str(char_index)
            if idx_str not in data[server_name][username]:
                data[server_name][username][idx_str] = {}
                
            char_data = data[server_name][username][idx_str]
            if "skills" not in char_data:
                char_data["skills"] = {}
                
            skill_str = str(skill_id)
            current_level = char_data["skills"].get(skill_str, 0)
            if level > current_level:
                char_data["skills"][skill_str] = level
                _save_registry(data)

    @staticmethod
    def register_characters(server_name: str, username: str, characters: List[CharacterInfo]):
        """
        Registers or updates the exact character slot mapping for an account on a specific server.
        """
        if not characters:
            return

        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name not in data:
                data[server_name] = {}

            slot_list = []
            for idx, c in enumerate(characters):
                sex_str = "Nam" if c.sex == 1 else "Nữ"
                job_str = "Hiệp Khách" if c.job == 1 else f"Phái {c.job}"
                slot_list.append({
                    "slot": idx + 1,        # 1-indexed (Slot 1, Slot 2, Slot 3)
                    "id": c.id,
                    "name": c.name,
                    "job": job_str,
                    "job_id": c.job,
                    "level": c.level,
                    "sex": sex_str,
                    "sex_id": c.sex,
                    "map": c.map_name,
                    "vip": c.vip
                })

            data[server_name][username] = {
                "slots": slot_list,
                "total_slots": len(slot_list),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            _save_registry(data)

    @staticmethod
    def get_character_at_slot(server_name: str, username: str, slot_idx: int) -> Optional[Dict[str, Any]]:
        """
        Gets registered character metadata for a given slot index (0 = Slot 1, 1 = Slot 2, 2 = Slot 3).
        """
        with REGISTRY_LOCK:
            data = _load_registry()
            acc_data = data.get(server_name, {}).get(username, {})
            slots = acc_data.get("slots", [])
            if 0 <= slot_idx < len(slots):
                return slots[slot_idx]
            return None

    @staticmethod
    def is_account_fully_completed(server_name: str, username: str, limit_lvl: int) -> bool:
        """
        Kiểm tra xem TẤT CẢ các nhân vật ĐÃ BIẾT trong tài khoản đều >= limit_lvl chưa.
        Trả về True nếu tài khoản đã đầy đủ dữ liệu (>= 1 nhân vật) VÀ không có nhân vật nào < limit_lvl.
        """
        with REGISTRY_LOCK:
            data = _load_registry()
            acc_data = data.get(server_name, {}).get(username, {})
            slots = acc_data.get("slots", [])
            # Nếu chưa từng đăng nhập (cache rỗng), không thể bỏ qua
            if not slots:
                return False
            # Nếu có ít nhất 1 nhân vật < limit_lvl, không bỏ qua
            for char_info in slots:
                if char_info.get("level", 0) < limit_lvl:
                    return False
            # Tất cả nhân vật đã biết đều >= limit_lvl
            return True

    @staticmethod
    def is_script_completed(server_name: str, username: str, slot_idx: int, script_name: str) -> bool:
        """Checks if a specific character slot has already completed the given script."""
        with REGISTRY_LOCK:
            data = _load_registry()
            slots = data.get(server_name, {}).get(username, {}).get("slots", [])
            if 0 <= slot_idx < len(slots):
                completed = slots[slot_idx].get("completed_scripts", {})
                return script_name in completed
            return False

    @staticmethod
    def mark_script_completed(server_name: str, username: str, slot_idx: int, script_name: str, level: int = 1):
        """Marks a specific character slot as having successfully completed the script."""
        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name in data and username in data[server_name]:
                slots = data[server_name][username].get("slots", [])
                if 0 <= slot_idx < len(slots):
                    if "completed_scripts" not in slots[slot_idx]:
                        slots[slot_idx]["completed_scripts"] = {}
                    slots[slot_idx]["completed_scripts"][script_name] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if level > slots[slot_idx].get("level", 1):
                        slots[slot_idx]["level"] = level
                    _save_registry(data)

    @staticmethod
    def update_character_level(server_name: str, username: str, slot_idx: int, level: int):
        """Updates the recorded level of a character in a specific slot."""
        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name in data and username in data[server_name]:
                slots = data[server_name][username].get("slots", [])
                if 0 <= slot_idx < len(slots):
                    slots[slot_idx]["level"] = level
                    _save_registry(data)

    @staticmethod
    def update_character_stats(server_name: str, username: str, slot_idx: int, hp: Optional[int] = None, mp: Optional[int] = None, hp_max: Optional[int] = None, mp_max: Optional[int] = None, atk: Optional[int] = None, def_val: Optional[int] = None, speed: Optional[int] = None):
        """Caches exact character combat stats from server into persistent storage."""
        with REGISTRY_LOCK:
            data = _load_registry()
            if server_name in data and username in data[server_name]:
                slots = data[server_name][username].get("slots", [])
                if 0 <= slot_idx < len(slots):
                    char_entry = slots[slot_idx]
                    if hp is not None and hp > 0:
                        char_entry["hp"] = hp
                    if mp is not None and mp >= 0:
                        char_entry["mp"] = mp
                    if hp_max is not None and hp_max > 0:
                        char_entry["hp_max"] = hp_max
                    if mp_max is not None and mp_max > 0:
                        char_entry["mp_max"] = mp_max
                    if atk is not None and atk > 0:
                        char_entry["atk"] = atk
                    if def_val is not None and def_val > 0:
                        char_entry["def"] = def_val
                    if speed is not None and speed > 0:
                        char_entry["speed"] = speed
                    char_entry["stats_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    _save_registry(data)

    @staticmethod
    def get_character_stats(server_name: str, username: str, slot_idx: int) -> Dict[str, Any]:
        """Retrieves cached combat stats for a character slot, or empty dict if not cached."""
        with REGISTRY_LOCK:
            data = _load_registry()
            slots = data.get(server_name, {}).get(username, {}).get("slots", [])
            if 0 <= slot_idx < len(slots):
                entry = slots[slot_idx]
                return {
                    "hp": entry.get("hp", 0),
                    "mp": entry.get("mp", 0),
                    "hp_max": entry.get("hp_max", 0),
                    "mp_max": entry.get("mp_max", 0),
                    "atk": entry.get("atk", 0),
                    "def": entry.get("def", 0),
                    "speed": entry.get("speed", 0),
                }
            return {}

    @staticmethod
    def print_registry_dashboard(server_name: str, accounts: Optional[List[Any]] = None):
        """Prints a dashboard showing all registered characters by slot with script completion status."""
        with REGISTRY_LOCK:
            data = _load_registry()
            srv_data = data.get(server_name, {})

        print("\n" + "=" * 80)
        print(f"        DANH BẠ NHÂN VẬT ĐÃ CỐ ĐỊNH THEO SLOT - MÁY CHỦ: {server_name}")
        print("=" * 80)
        print(f"{'STT':<4} | {'TÀI KHOẢN':<14} | {'SLOT 1':<18} | {'SLOT 2':<18} | {'SLOT 3':<18}")
        print("-" * 80)

        target_accs = [acc[0] for acc in accounts] if accounts else list(srv_data.keys())
        if not target_accs:
            print("  (Chưa có tài khoản nào được lưu thông tin nhân vật trên máy chủ này)")
            print("=" * 80)
            return

        for i, uname in enumerate(target_accs, 1):
            acc_entry = srv_data.get(uname, {})
            slots = acc_entry.get("slots", [])

            def format_slot(s_idx):
                if s_idx < len(slots):
                    s = slots[s_idx]
                    status_icon = "★ " if s.get("completed_scripts") else ""
                    return f"{status_icon}{s['name']} (Lv {s['level']})"
                return "\033[90m[Trống]\033[0m"

            s1 = format_slot(0)
            s2 = format_slot(1)
            s3 = format_slot(2)

            print(f"{i:<4} | {uname:<14} | {s1:<27} | {s2:<27} | {s3:<27}")

        print("=" * 80)
