import os
import sys
import json
import time
from typing import List, Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(MODULE_DIR)
SCRIPTS_DIR = os.path.join(TOOL_DIR, "scripts")


class ScriptManager:
    """
    Manages 10 discrete script slots (Slot 1 to 10) for saving and running
    different leveling routes, daily quests, boss runs, etc.
    """

    MAX_SLOTS = 10

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        # Migrate old recorded_leveling_script.json to slot_01.json if needed
        old_file = os.path.join(SCRIPTS_DIR, "recorded_leveling_script.json")
        slot1_file = cls.get_slot_path(1)
        if os.path.exists(old_file) and not os.path.exists(slot1_file):
            try:
                with open(old_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                old_data["slot_index"] = 1
                if "title" not in old_data:
                    old_data["title"] = "Kịch Bản Cày Cấp Tân Thủ (Mặc Định)"
                with open(slot1_file, "w", encoding="utf-8") as f:
                    json.dump(old_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    @classmethod
    def get_slot_path(cls, slot_idx: int) -> str:
        return os.path.join(SCRIPTS_DIR, f"slot_{slot_idx:02d}.json")

    @classmethod
    def get_slot_info(cls, slot_idx: int) -> Dict[str, Any]:
        cls._ensure_dir()
        path = cls.get_slot_path(slot_idx)
        if not os.path.exists(path):
            return {
                "slot": slot_idx,
                "is_saved": False,
                "title": "(Trống - Chưa có kịch bản)",
                "total_steps": 0,
                "created_at": "--",
                "target_server": "--",
                "path": path,
                "data": None
            }
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            steps = data.get("steps", [])
            return {
                "slot": slot_idx,
                "is_saved": True,
                "title": data.get("title", f"Kịch Bản Ô #{slot_idx}"),
                "total_steps": len(steps),
                "created_at": data.get("created_at", "--"),
                "target_server": data.get("target_server", "--"),
                "path": path,
                "data": data
            }
        except Exception:
            return {
                "slot": slot_idx,
                "is_saved": False,
                "title": "(Tệp lỗi - Không đọc được)",
                "total_steps": 0,
                "created_at": "--",
                "target_server": "--",
                "path": path,
                "data": None
            }

    @classmethod
    def get_all_slots(cls) -> List[Dict[str, Any]]:
        return [cls.get_slot_info(i) for i in range(1, cls.MAX_SLOTS + 1)]

    @classmethod
    def print_slots_table(cls, title_header: str = "DANH SÁCH 10 Ô NHỚ KỊCH BẢN (SLOT 1 - 10)"):
        slots = cls.get_all_slots()
        print("\n" + "=" * 90)
        print(f"                     {title_header}")
        print("=" * 90)
        print(f"{'SLOT':<6} | {'TRẠNG THÁI':<11} | {'TÊN KỊCH BẢN':<36} | {'BƯỚC':<8} | {'NGÀY LƯU':<19}")
        print("-" * 90)
        for s in slots:
            slot_label = f" [{s['slot']:>2}] "
            if s["is_saved"]:
                status_str = "\033[92m● ĐÃ LƯU\033[0m   "
                name_str = f"\033[93m{s['title'][:35]:<35}\033[0m"
                steps_str = f"{s['total_steps']:>3} bước"
                date_str = s["created_at"]
            else:
                status_str = "\033[90m○ TRỐNG\033[0m    "
                name_str = f"\033[90m{s['title'][:35]:<35}\033[0m"
                steps_str = "  --    "
                date_str = "--"
            print(f"{slot_label} | {status_str} | {name_str} | {steps_str} | {date_str}")
        print("=" * 90)

    @classmethod
    def save_to_slot(cls, slot_idx: int, actions: List[Dict[str, Any]], title: str, target_server: str = "") -> bool:
        cls._ensure_dir()
        if slot_idx < 1 or slot_idx > cls.MAX_SLOTS:
            return False
        path = cls.get_slot_path(slot_idx)
        payload = {
            "slot_index": slot_idx,
            "title": title.strip() or f"Kịch Bản Ô #{slot_idx}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target_server": target_server,
            "total_steps": len(actions),
            "steps": actions
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"\033[91m[LỖI LƯU TỆP]\033[0m Không thể lưu vào {path}: {e}")
            return False

    @classmethod
    def prompt_save_cli(cls, actions: List[Dict[str, Any]], target_server: str = "") -> Optional[int]:
        """
        Interactive CLI prompt when user presses 'S' in Proxy Hub.
        Shows 10 slots, lets user pick which slot [1-10] to save, and optionally name it.
        """
        if not actions:
            print("\n\033[93m[CHƯA CÓ DỮ LIỆU]\033[0m Bạn chưa thực hiện thao tác nào trên Acc Trưởng để lưu!")
            return None

        cls.print_slots_table("CHỌN Ô NHỚ ĐỂ LƯU KỊCH BẢN (SLOT 1 - 10)")
        print(f"-> Tổng số thao tác Acc Trưởng vừa thực hiện: \033[92m{len(actions)}\033[0m bước")
        print("-> Nhập số thứ tự Slot muốn lưu [1 - 10] (Nhấn Enter = Slot trống đầu tiên, 'C' = Hủy)")

        first_empty_slot = 1
        for s in cls.get_all_slots():
            if not s["is_saved"]:
                first_empty_slot = s["slot"]
                break

        while True:
            try:
                choice = input(f"-> Chọn Slot [1-10] (Enter = Slot {first_empty_slot}): ").strip()
                if choice.upper() == "C":
                    print("[ĐÃ HỦY LƯU KỊCH BẢN]")
                    return None
                if not choice:
                    target_slot = first_empty_slot
                else:
                    target_slot = int(choice)
                    if target_slot < 1 or target_slot > cls.MAX_SLOTS:
                        print("❌ Vui lòng nhập từ 1 đến 10!")
                        continue
                break
            except ValueError:
                print("❌ Vui lòng nhập số hợp lệ từ 1 đến 10!")
            except (KeyboardInterrupt, EOFError):
                print("\n[ĐÃ HỦY]")
                return None

        existing = cls.get_slot_info(target_slot)
        default_title = f"Kịch Bản Cày Cấp Slot #{target_slot}"
        if existing["is_saved"]:
            default_title = existing["title"]
            print(f"\n⚠️ \033[93mCẢNH BÁO:\033[0m Slot [{target_slot}] đang có dữ liệu: '{existing['title']}' ({existing['total_steps']} bước).")
            confirm = input(f"-> Bạn có chắc chắn muốn GHI ĐÈ vào Slot [{target_slot}] không? [Y/N] (Enter=Y): ").strip().upper()
            if confirm and confirm != "Y":
                print("[ĐÃ HỦY GHI ĐÈ]")
                return None

        print(f"\n-> Nhập tên gợi nhớ cho Kịch Bản Slot [{target_slot}] (Ví dụ: Tân thủ Lv 1-10, Chuỗi Dã Tẩu, Boss Bang...)")
        custom_name = input(f"-> Tên kịch bản (Enter = '{default_title}'): ").strip()
        final_title = custom_name if custom_name else default_title

        ok = cls.save_to_slot(target_slot, actions, final_title, target_server)
        if ok:
            print(f"\n\033[92m[LƯU THÀNH CÔNG]\033[0m Đã lưu {len(actions)} bước vào \033[93mSlot [{target_slot}]: {final_title}\033[0m!")
            print(f"-> File lưu: \033[96m{cls.get_slot_path(target_slot)}\033[0m")
            print("-> Bạn có thể mở Option 12 bất kỳ lúc nào để phát lại kịch bản này cho toàn bộ Acc Clone!\n")
            return target_slot
        return None

    @classmethod
    def prompt_select_cli(cls) -> Optional[Dict[str, Any]]:
        """
        Interactive CLI prompt when user enters Option 12.
        Shows 10 slots and lets user choose which slot to run for the batch.
        """
        cls.print_slots_table("CHỌN KỊCH BẢN MUỐN CHẠY CHO ĐOÀN QUÂN (SLOT 1 - 10)")
        saved_slots = [s for s in cls.get_all_slots() if s["is_saved"]]
        if not saved_slots:
            print("\n\033[91m[CHƯA CÓ KỊCH BẢN NÀO ĐƯỢC LƯU]\033[0m")
            print("-> Vui lòng vào Option 11 chơi Acc Trưởng, sau đó gõ phím 'S' để lưu kịch bản vào 1 trong 10 Slot trước!")
            return None

        default_slot = saved_slots[0]["slot"]
        while True:
            try:
                choice = input(f"-> Chọn Slot kịch bản muốn chạy [1-10] (Enter = Slot {default_slot}, 'C' = Hủy): ").strip()
                if choice.upper() == "C":
                    print("[ĐÃ HỦY CHỌN KỊCH BẢN]")
                    return None
                if not choice:
                    target_slot = default_slot
                else:
                    target_slot = int(choice)
                    if target_slot < 1 or target_slot > cls.MAX_SLOTS:
                        print("❌ Vui lòng nhập từ 1 đến 10!")
                        continue

                slot_info = cls.get_slot_info(target_slot)
                if not slot_info["is_saved"] or not slot_info["data"]:
                    print(f"❌ Slot [{target_slot}] hiện đang TRỐNG! Vui lòng chọn Slot đã có dữ liệu.")
                    continue

                return slot_info
            except ValueError:
                print("❌ Vui lòng nhập số hợp lệ từ 1 đến 10!")
            except (KeyboardInterrupt, EOFError):
                print("\n[ĐÃ HỦY]")
                return None
