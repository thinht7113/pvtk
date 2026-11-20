"""
PVTK Mass Teleporter Module
Supports batch teleporting all characters to either 'Thánh Vực' (Map ID 11) or 'Quảng Trường Tân Nguyệt' (Map ID 38).
"""

import time
from typing import List, Tuple, Dict, Any

from core.config import settings
from modules.auth import AuthManager
from modules.teleport import MapTeleporter
from modules.base_runner import CharacterLoopRunner

def is_in_target_map(current_map: str, target_map_name: str) -> bool:
    """Checks if character's current map matches target map name."""
    if "Thánh Vực" in target_map_name or "Thanh Vuc" in target_map_name:
        return "Thánh Vực" in current_map or "Thanh Vuc" in current_map
    if "Quảng Trường" in target_map_name or "Quang Truong" in target_map_name:
        return "Quảng Trường" in current_map or "Quang Truong" in current_map
    return target_map_name.lower() in current_map.lower()


class TeleportRunner(CharacterLoopRunner):
    def should_process_character(self, char, task: Tuple) -> bool:
        username = task[2] if len(task) >= 3 else ""
        watcher_users = {"toilabaon", "thinht7113", "thinht7114"}
        if username.lower() in watcher_users:
            self.log(f"  \033[93m[BỎ QUA - ACC THEO DÕI ĐẶC BIỆT]\033[0m '{username}' là acc theo dõi SGD/Lì Xì. Cố định tại Tân Nguyệt Trấn, không dịch chuyển.")
            return False
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        if "characters_teleported" not in account_result:
            account_result["characters_teleported"] = 0
            account_result["characters_already_in"] = 0
            account_result["characters_skipped_level"] = 0

        target_map_name = task[5]
        target_map_id = task[6]
        char_lv = getattr(char, "level", 0)

        # Lọc yêu cầu cấp độ (Level requirement filtering):
        # 1. Tân Nguyệt Trấn (Map ID 37): Yêu cầu cấp độ > 15
        if target_map_id in (37, 38) or "tân nguyệt" in target_map_name.lower() or "tan nguyet" in target_map_name.lower():
            if char_lv <= 15:
                self.log(f"{char_tag} \033[93m[BỎ QUA - CẤP KHÔNG ĐỦ]\033[0m Nhân vật cấp {char_lv} <= 15 (Yêu cầu cấp > 15 để dịch chuyển Tân Nguyệt Trấn).")
                account_result["characters_skipped_level"] += 1
                return

        # 2. Thánh Vực (Map ID 11): Yêu cầu cấp độ >= 35
        elif target_map_id == 11 or "thánh vực" in target_map_name.lower() or "thanh vuc" in target_map_name.lower():
            if char_lv < 35:
                self.log(f"{char_tag} \033[93m[BỎ QUA - CẤP KHÔNG ĐỦ]\033[0m Nhân vật cấp {char_lv} < 35 (Yêu cầu cấp >= 35 để dịch chuyển Thánh Vực).")
                account_result["characters_skipped_level"] += 1
                return

        if is_in_target_map(char.map_name, target_map_name):
            self.log(f"{char_tag} \033[92m[ĐÃ Ở ĐÚNG MAP]\033[0m Vị trí hiện tại: {char.map_name}")
            account_result["characters_already_in"] += 1
        else:
            self.log(f"{char_tag} [DỊCH CHUYỂN] Đang chuyển từ '{char.map_name}' sang '{target_map_name}'...")
            tele_ok = MapTeleporter.teleport_to_map(game_conn, target_map_id)
            if tele_ok:
                self.log(f"{char_tag} \033[92m[THÀNH CÔNG]\033[0m Đã dịch chuyển sang {target_map_name}!")
                account_result["characters_teleported"] += 1
            else:
                self.log(f"{char_tag} \033[91m[THẤT BẠI]\033[0m Không thể dịch chuyển.")


def run_all_teleport(accounts: List[Tuple], target_map_name: str, target_map_id: int, max_workers: int = None):
    total = len(accounts)
    print("\n" + "=" * 80)
    print(f"BẮT ĐẦU DỊCH CHUYỂN TỚI {target_map_name.upper()} CHO {total} TÀI KHOẢN")
    print("=" * 80)

    start_time = time.time()
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None, target_map_name, target_map_id)
        for i, acc in enumerate(accounts)
    ]
    
    results_by_user = {}
    try:
        runner = TeleportRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers)
        all_results = runner.run_all()
        for res in all_results:
            if res and "username" in res:
                results_by_user[res["username"]] = res
    except KeyboardInterrupt:
        print("\n\n\033[91m[BUỘC DỪNG]\033[0m Người dùng nhấn Ctrl+C. Tắt ngay lập tức...")
        import os
        os._exit(0)

    total_teleported = sum(r.get("characters_teleported", 0) for r in results_by_user.values())
    total_already = sum(r.get("characters_already_in", 0) for r in results_by_user.values())
    total_skipped = sum(r.get("characters_skipped_level", 0) for r in results_by_user.values())
    failed_accounts = sum(1 for r in results_by_user.values() if not r.get("completed", False))

    elapsed = round(time.time() - start_time, 1)
    print("\n" + "=" * 75)
    print(f"           BẢNG TỔNG KẾT DỊCH CHUYỂN BẢN ĐỒ ({target_map_name.upper()})")
    print("=" * 75)
    print(f"-> Bản đồ đích: {target_map_name} (Map ID: {target_map_id})")
    print(f"-> Tổng tài khoản xử lý: {total}")
    print(f"-> Thời gian thực hiện: {elapsed} giây")
    print(f"-> Số nhân vật vừa dịch chuyển thành công: \033[92m{total_teleported}\033[0m")
    print(f"-> Số nhân vật đã ở sẵn từ trước: \033[93m{total_already}\033[0m")
    print(f"-> Số nhân vật bỏ qua do chưa đủ cấp độ: \033[93m{total_skipped}\033[0m")
    print(f"-> Số tài khoản gặp lỗi/chưa hoàn tất: {failed_accounts}")
    print("=" * 75)

    return {
        "target_map_name": target_map_name,
        "target_map_id": target_map_id,
        "total_accounts": total,
        "total_teleported": total_teleported,
        "total_already": total_already,
        "total_skipped": total_skipped,
        "failed_accounts": failed_accounts,
        "elapsed": elapsed
    }


class EscapeLagRunner(CharacterLoopRunner):
    def should_process_character(self, char, task: Tuple) -> bool:
        username = task[2] if len(task) >= 3 else ""
        watcher_users = {"toilabaon", "thinht7113", "thinht7114"}
        if username.lower() in watcher_users:
            self.log(f"  \033[93m[BỎ QUA - ACC THEO DÕI ĐẶC BIỆT]\033[0m '{username}' là acc theo dõi SGD/Lì Xì. Cố định tại Tân Nguyệt Trấn, không thoát lag.")
            return False
        return True

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        if "characters_escaped" not in account_result:
            account_result["characters_escaped"] = 0
            account_result["characters_already_in"] = 0
            account_result["char_details"] = []

        map_before = char.map_name
        self.log(f"{char_tag} [THOÁT LAG] Đang thực hiện Thoát lag (Map trước: '{map_before}')...")
        esc_ok, new_map_id, gx, gy, esc_err = MapTeleporter.escape_lag(game_conn)

        # Rời nhân vật để cập nhật lại danh sách nhân vật & lấy tên map mới nhất từ server
        AuthManager.leave_character(game_conn)
        updated_chars = AuthManager.fetch_character_list(game_conn)
        updated_map = map_before
        for uc in updated_chars:
            if uc.id == char.id:
                updated_map = uc.map_name
                break

        coord_str = f"Tọa độ: ({gx}, {gy})" if (gx is not None and gy is not None) else ""
        map_id_str = f"Map ID: {new_map_id}" if new_map_id is not None else ""
        extra_info = f"{coord_str} | {map_id_str}".strip(" |")

        if esc_ok:
            detail_str = f" ({extra_info})" if extra_info else ""
            self.log(f"{char_tag} \033[92m[THOÁT LAG THÀNH CÔNG]\033[0m Bản đồ hiện tại: \033[96m{updated_map}\033[0m{detail_str} [Trước: {map_before}]")
            account_result["characters_escaped"] += 1
            account_result["char_details"].append({
                "name": char.name,
                "level": char.level,
                "map_before": map_before,
                "map_now": updated_map,
                "extra": extra_info or "Đã chuyển về map hồi sinh"
            })
        else:
            if "ở bản đồ này rồi" in esc_err or "o ban do nay roi" in esc_err.lower():
                self.log(f"{char_tag} \033[92m[ĐÃ Ở MAP HỒI SINH]\033[0m Bản đồ hiện tại: \033[96m{updated_map}\033[0m [Đã ở sẵn từ trước]")
                account_result["characters_already_in"] += 1
                account_result["char_details"].append({
                    "name": char.name,
                    "level": char.level,
                    "map_before": map_before,
                    "map_now": updated_map,
                    "extra": "Đang ở map hồi sinh"
                })
            else:
                self.log(f"{char_tag} \033[91m[THOÁT LAG THẤT BẠI]\033[0m {esc_err} | Bản đồ hiện tại: {updated_map}")
                account_result["char_details"].append({
                    "name": char.name,
                    "level": char.level,
                    "map_before": map_before,
                    "map_now": updated_map,
                    "extra": f"Lỗi: {esc_err}"
                })

def run_mass_escape_lag(accounts: List[Tuple], max_workers: int = None):
    total = len(accounts)
    print("\n" + "=" * 80)
    print(f"  BẮT ĐẦU THOÁT LAG VÀ LẤY THÔNG TIN MAP HIỆN TẠI ({total} TÀI KHOẢN)")
    print("=" * 80)

    start_time = time.time()
    account_tasks = [
        (i + 1, total, acc[0], acc[1], acc[2] if len(acc) >= 3 else None)
        for i, acc in enumerate(accounts)
    ]
    
    results_by_user = {}
    try:
        runner = EscapeLagRunner(account_tasks, thread_name="Tài khoản", max_retries=10, max_workers=max_workers)
        all_results = runner.run_all()
        for res in all_results:
            if res and "username" in res:
                results_by_user[res["username"]] = res
    except KeyboardInterrupt:
        print("\n\n\033[91m[BUỘC DỪNG]\033[0m Người dùng nhấn Ctrl+C. Tắt ngay lập tức...")
        import os
        os._exit(0)

    total_escaped = sum(r.get("characters_escaped", 0) for r in results_by_user.values())
    total_already = sum(r.get("characters_already_in", 0) for r in results_by_user.values())
    failed_accounts = sum(1 for r in results_by_user.values() if not r.get("completed", False))
    elapsed = round(time.time() - start_time, 1)

    print("\n" + "=" * 85)
    print("              BẢNG TỔNG KẾT THOÁT LAG & THÔNG TIN MAP HIỆN TẠI")
    print("=" * 85)
    print(f"-> Tổng tài khoản xử lý: {total}")
    print(f"-> Thời gian thực hiện: {elapsed} giây")
    print(f"-> Số nhân vật vừa thoát lag thành công: \033[92m{total_escaped}\033[0m")
    print(f"-> Số nhân vật đã ở sẵn map hồi sinh: \033[93m{total_already}\033[0m")
    print(f"-> Số tài khoản gặp lỗi/chưa hoàn tất: {failed_accounts}")
    print("-" * 85)
    print(f"{'STT':<4} | {'TÊN TÀI KHOẢN':<16} | {'TÊN NHÂN VẬT':<18} | {'LV':<4} | {'BẢN ĐỒ HIỆN TẠI':<22} | {'TỌA ĐỘ / CHI TIẾT'}")
    print("-" * 85)

    char_stt = 1
    for username, r in results_by_user.items():
        details = r.get("char_details", [])
        for d in details:
            c_name = d.get("name", "")
            c_lvl = str(d.get("level", ""))
            c_map = d.get("map_now", "")
            c_extra = d.get("extra", "")
            print(f"  {char_stt:<2} | {username:<16} | {c_name:<18} | {c_lvl:<4} | \033[96m{c_map:<22}\033[0m | {c_extra}")
            char_stt += 1

    print("=" * 85)

    return {
        "total_accounts": total,
        "total_escaped": total_escaped,
        "total_already": total_already,
        "failed_accounts": failed_accounts,
        "elapsed": elapsed,
        "results": results_by_user
    }


def run_teleport_menu(accounts: List[Tuple], max_workers: int = 3):
    """
    Sub-menu gom 3 tùy chọn dịch chuyển & thoát lag:
    1. Dịch chuyển toàn bộ sang Thánh Vực (Map ID: 11 - Yêu cầu Lv >= 35)
    2. Dịch chuyển toàn bộ sang Tân Nguyệt Trấn (Map ID: 37 - Yêu cầu Lv > 15)
    3. Thoát lag (Cài Đặt / H.Thống -> Thoát lag & Hiển thị thông tin Map hiện tại - Mọi level)
    0. Quay lại Menu chính
    """
    total = len(accounts)
    while True:
        print("\n" + "=" * 75)
        print(f"       DỊCH CHUYỂN BẢN ĐỒ / THOÁT LAG (TỔNG: {total} TÀI KHOẢN ĐANG CHỌN)")
        print("=" * 75)
        print("  [1] Dịch chuyển toàn bộ sang Thánh Vực (Map ID: 11 - Yêu cầu Lv >= 35)")
        print("  [2] Dịch chuyển toàn bộ sang Tân Nguyệt Trấn (Map ID: 37 - Yêu cầu Lv > 15)")
        print("  [3] Thoát lag (Cài Đặt / H.Thống -> Thoát lag & Hiển thị thông tin Map - Mọi level)")
        print("  [0] Quay lại Menu chính")
        print("=" * 75)

        try:
            sub = input("-> Vui lòng chọn chức năng [0-3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if sub == "0":
            break
        elif not sub:
            continue
        elif sub == "1":
            run_all_teleport(accounts, target_map_id=11, target_map_name="Thánh Vực", max_workers=max_workers)
            try:
                input("\nNhấn Enter để tiếp tục...")
            except (KeyboardInterrupt, EOFError):
                pass
        elif sub == "2":
            run_all_teleport(accounts, target_map_id=37, target_map_name="Tân Nguyệt Trấn", max_workers=max_workers)
            try:
                input("\nNhấn Enter để tiếp tục...")
            except (KeyboardInterrupt, EOFError):
                pass
        elif sub == "3":
            run_mass_escape_lag(accounts, max_workers=max_workers)
            try:
                input("\nNhấn Enter để tiếp tục...")
            except (KeyboardInterrupt, EOFError):
                pass
        else:
            print("\033[91m[LỖI]\033[0m Lựa chọn không hợp lệ. Vui lòng nhập từ 0 đến 3.")


# Alias compatibility
run_teleport_all = run_all_teleport


if __name__ == "__main__":
    import os, sys
    from core.config import settings
    from modules.free_proxy import FreeProxyManager
    from modules.auth import AuthManager

    acc_file = getattr(settings, "accounts_file", None)
    if not acc_file or not os.path.exists(acc_file):
        for candidate in ["accounts_tuyet_vo_than.txt", "accounts.txt", "accountsNV.txt"]:
            if os.path.exists(candidate):
                acc_file = candidate
                break

    if not acc_file or not os.path.exists(acc_file):
        print("\033[91m[LỖI]\033[0m Không tìm thấy file tài khoản hợp lệ!")
        sys.exit(1)

    accounts = AuthManager.load_accounts(acc_file)
    if not accounts:
        print(f"\033[91m[LỖI]\033[0m Không có tài khoản trong file '{acc_file}'!")
        sys.exit(1)

    proxy_accounts = FreeProxyManager.assign_proxies_to_accounts(accounts)
    max_w = getattr(settings, "max_concurrent_accounts", 10) or 10
    run_teleport_menu(proxy_accounts, max_workers=max_w)
