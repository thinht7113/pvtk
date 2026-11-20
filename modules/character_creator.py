"""
PVTK Mass Character Creator Module (Tao Nhan Vat Hang Loat)
Automatically creates 3 characters per account for new servers.
Default Class/Job: Hiệp Khách (Job ID: 1)
Gender/Sex: Random Male (1) / Female (2)
Race: Đông Phương (0)
"""

import sys
import time
import random
import threading
from typing import List, Tuple, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from modules.auth import AuthManager, CharacterInfo
from modules.character_registry import CharacterRegistry
from core.device_faker import DeviceFaker

PRINT_LOCK = threading.Lock()

def safe_print(msg: str):
    with PRINT_LOCK:
        print(msg)


from modules.base_runner import BaseAccountRunner

class CharacterCreatorRunner(BaseAccountRunner):
    def process_account(self, game_conn, task: Tuple, thread_tag: str, login_setting: int, dev: Dict[str, str], account_result: Dict[str, Any]):
        active_conn = game_conn
        username = task[2]
        password = task[3]
        proxy = task[4] if len(task) >= 5 else None
        target_slots = task[5]

        account_result["existing_count"] = 0
        account_result["created_count"] = 0
        account_result["total_count"] = 0
        account_result["characters"] = []

        try:
            # 3. Check existing characters
            chars = AuthManager.fetch_character_list(active_conn)
            account_result["existing_count"] = len(chars)

            for c in chars:
                account_result["characters"].append({
                    "id": c.id,
                    "name": c.name,
                    "sex": "Nam" if c.sex == 0 else "Nữ",
                    "job": "Hiệp Khách" if c.job == 1 else f"Phái {c.job}",
                    "level": c.level,
                    "is_new": False
                })

            # Register existing characters into fixed slot registry
            CharacterRegistry.register_characters(settings.game_server_name, username, chars)

            if len(chars) >= target_slots:
                account_result["total_count"] = len(chars)
                account_result["completed"] = True
                self.log(f"{thread_tag} \033[92m[ĐÃ ĐỦ {len(chars)}/{target_slots} NV]\033[0m: {', '.join([c.name for c in chars])}")
                return

            needed = target_slots - len(chars)
            self.log(f"{thread_tag} Hiện có \033[93m{len(chars)}/{target_slots}\033[0m nhân vật. Đang tiến hành tạo thêm \033[92m{needed}\033[0m nhân vật...")

            # 4. Create missing characters
            created_now = 0
            for slot in range(len(chars) + 1, target_slots + 1):
                rand_sex = random.choice([0, 1])  # 0 = Nam, 1 = Nữ (Chuẩn Game Define.SEX_MALE / SEX_FEMALE)
                sex_name = "Nam" if rand_sex == 0 else "Nữ"

                ok, new_char, msg = AuthManager.create_character(
                    active_conn,
                    sex=rand_sex,
                    job=1,  # Mặc định: Hiệp Khách
                    race=0, # Đông Phương
                    device_id=dev["android_id"]
                )

                if ok and new_char:
                    created_now += 1
                    account_result["characters"].append({
                        "id": new_char.id,
                        "name": new_char.name,
                        "sex": sex_name,
                        "job": "Hiệp Khách",
                        "level": 1,
                        "is_new": True
                    })
                    self.log(f"{thread_tag} -> \033[92m[TẠO THÀNH CÔNG SLOT {slot}]\033[0m Nhân vật: \033[93m'{new_char.name}'\033[0m (Phái: Hiệp Khách | Giới tính: {sex_name})")
                else:
                    self.log(f"{thread_tag} -> \033[91m[TẠO THẤT BẠI SLOT {slot}]\033[0m {msg}")

                time.sleep(0.3)

            # Final verification & Registry Update
            final_chars = AuthManager.fetch_character_list(active_conn)
            CharacterRegistry.register_characters(settings.game_server_name, username, final_chars)
            account_result["created_count"] = created_now
            account_result["total_count"] = len(final_chars)
            if len(final_chars) >= target_slots:
                account_result["completed"] = True
                account_result["error"] = ""
            else:
                account_result["completed"] = False
                account_result["error"] = f"Mới tạo được {len(final_chars)}/{target_slots} nhân vật"

        finally:
            if active_conn and active_conn != game_conn:
                try:
                    active_conn.close()
                except Exception:
                    pass

def run_mass_create_characters(accounts: List[Tuple], target_slots: int = 3, max_workers: int = 6, stop_event: Optional[threading.Event] = None):
    """
    Runs concurrent character creation across all selected accounts.
    Allows user to choose whether to create 1, 2, or 3 characters per account.
    """
    total = len(accounts)

    if sys.stdin and sys.stdin.isatty():
        print("\n" + "=" * 80)
        print("                 THIẾT LẬP TẠO NHÂN VẬT CHO MỖI TÀI KHOẢN")
        print("=" * 80)
        print("  [3] Tạo đủ 3 nhân vật / mỗi acc (Mặc định - Cày tối đa 3 nhân vật)")
        print("  [1] Chỉ tạo 1 nhân vật / mỗi acc (Chỉ cày duy nhất 1 nhân vật)")
        print("  [2] Tạo 2 nhân vật / mỗi acc")
        print("-" * 80)
        try:
            choice_slot = input("-> Bạn muốn tạo bao nhiêu nhân vật cho mỗi tài khoản? [1, 2, 3] (Nhấn Enter = 3): ").strip()
            if choice_slot in ["1", "2", "3"]:
                target_slots = int(choice_slot)
            else:
                target_slots = 3
        except (KeyboardInterrupt, EOFError):
            print("\n[ĐÃ HỦY]")
            return {"cancelled": True}

    print("\n" + "=" * 80)
    print(f"  BẮT ĐẦU TỰ ĐỘNG TẠO NHÂN VẬT CHO {total} TÀI KHOẢN")
    print(f"  -> Mục tiêu: {target_slots} nhân vật mỗi tài khoản | Phái: 100% Hiệp Khách")
    print("=" * 80)

    start_time = time.time()
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None, target_slots)
        for i, acc in enumerate(accounts)
    ]
    
    try:
        runner = CharacterCreatorRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers, stop_event=stop_event)
        all_results = runner.run_all()
    except KeyboardInterrupt:
        print("\n\n\033[91m[BUỘC DỪNG]\033[0m Người dùng nhấn Ctrl+C. Tắt ngay lập tức...")
        import os
        os._exit(0)

    elapsed = round(time.time() - start_time, 1)
    results_by_user = {res["username"]: res for res in all_results if res and "username" in res}
    final_results = [results_by_user.get(acc[0], {"username": acc[0], "total_count": 0, "error": "Chưa hoàn thành"}) for acc in accounts]

    # SUMMARY DASHBOARD
    total_created = sum(r.get("created_count", 0) for r in final_results)
    total_chars = sum(r.get("total_count", 0) for r in final_results)
    completed_accs = sum(1 for r in final_results if r.get("completed", False))

    print("\n" + "=" * 80)
    print("                  BẢNG TỔNG KẾT TẠO NHÂN VẬT HÀNG LOẠT")
    print("=" * 80)
    print(f"-> Tổng tài khoản xử lý: \033[96m{total}\033[0m")
    print(f"-> Tài khoản đủ 3 nhân vật: \033[92m{completed_accs} / {total}\033[0m (100% Phái Hiệp Khách)")
    print(f"-> Số nhân vật mới vừa tạo: \033[93m{total_created}\033[0m")
    print(f"-> Tổng nhân vật hiện có trên toàn bộ acc: \033[92m{total_chars}\033[0m")
    print(f"-> Tổng thời gian thực hiện: \033[93m{elapsed} giây\033[0m")
    print("=" * 80)

    return {
        "total": total,
        "completed_accs": completed_accs,
        "total_created": total_created,
        "total_chars": total_chars,
        "elapsed": elapsed,
        "cancelled": bool(stop_event and stop_event.is_set())
    }
