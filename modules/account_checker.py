"""
PVTK Account & Character Inspector Module
Fast multi-threaded scanner to check login validity, character counts, levels, and map locations.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from core.config import settings
from modules.auth import AuthManager

def inspect_single_account(account_tuple: Tuple[int, int, str, str]) -> dict:
    idx, total, username, password = account_tuple
    data = {
        "index": idx,
        "username": username,
        "status": "UNKNOWN",
        "characters": [],
        "error": ""
    }

    login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, username, password)
    if not login_data:
        data["status"] = "AUTH_FAILED"
        data["error"] = err
        return data

    ukey, session_id, _ = login_data
    game_conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, ukey, session_id)
    if not game_conn:
        data["status"] = "CONNECT_FAILED"
        data["error"] = "Không thể kết nối Game Server"
        return data

    try:
        chars = AuthManager.fetch_character_list(game_conn)
        data["status"] = "OK"
        for c in chars:
            data["characters"].append({
                "id": c.id,
                "name": c.name,
                "level": c.level,
                "map": c.map_name
            })
        return data
    except Exception as e:
        data["status"] = "ERROR"
        data["error"] = str(e)
        return data
    finally:
        try:
            game_conn.close()
        except Exception:
            pass


def run_account_inspector(accounts: List[Tuple[str, str]], max_workers: int = 5):
    total = len(accounts)
    print("\n" + "=" * 80)
    print(f"  KIỂM TRA THÔNG TIN TOÀN BỘ TÀI KHOẢN & NHÂN VẬT ({total} TÀI KHOẢN)")
    print("=" * 80)

    start_time = time.time()
    account_tasks = [(i + 1, total, user, pwd) for i, (user, pwd) in enumerate(accounts)]
    
    results = []
    print(f"-> Đang quét nhanh với {max_workers} luồng song song...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(inspect_single_account, task): task for task in account_tasks}
        for future in as_completed(futures):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                pass

    # Sort results by original index
    results.sort(key=lambda x: x["index"])

    elapsed = round(time.time() - start_time, 1)

    print("\n" + "-" * 80)
    print(f"{'STT':<4} | {'TÀI KHOẢN':<15} | {'TRẠNG THÁI':<15} | {'DANH SÁCH NHÂN VẬT (Tên - Cấp - Map)'}")
    print("-" * 80)

    valid_count = 0
    fail_count = 0

    for r in results:
        idx = r["index"]
        user = r["username"]
        status = r["status"]
        if status == "OK":
            valid_count += 1
            chars_str = ", ".join([f"{c['name']} (Lv.{c['level']} @ {c['map']})" for c in r["characters"]])
            if not chars_str:
                chars_str = "(Chưa tạo nhân vật)"
            print(f"{idx:<4} | {user:<15} | {'HỢP LỆ':<15} | {chars_str}")
        else:
            fail_count += 1
            print(f"{idx:<4} | {user:<15} | {'LỖI ĐĂNG NHẬP':<15} | {r.get('error', 'Lỗi không xác định')}")

    print("-" * 80)
    print(f"-> Tổng kết: {valid_count}/{total} tài khoản hợp lệ | {fail_count} tài khoản lỗi | Thời gian: {elapsed}s")
    print("=" * 80)
