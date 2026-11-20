"""
PVTK Automated Batch Script Runner Module (Trình Chạy Kịch Bản Cày Cấp Theo Đợt)
Executes a recorded script (.json) across all 3 character slots (Slot 1, Slot 2, Slot 3) of all 100 accounts
in sequential batches (3 accounts per batch) with Smart Completion Tracking (Tránh cày trùng lặp).
"""

import sys
import os
import time
import json
import base64
import random
import threading
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from modules.slave_agent import SlaveAgent
from modules.character_registry import CharacterRegistry
from core.message import Message
import core.protocol as proto

PRINT_LOCK = threading.Lock()

def script_print(msg: str):
    with PRINT_LOCK:
        print(msg)


def load_recorded_script(filepath: str = "scripts/recorded_leveling_script.json") -> Dict[str, Any]:
    """Loads recorded script from JSON file."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        script_print(f"\033[91m[LỖI ĐỌC KỊCH BẢN]\033[0m {e}")
        return None


def execute_action_on_slave(slave: SlaveAgent, step_dict: Dict[str, Any], current_step_idx: int = 0, attempt: int = 0) -> bool:
    """Executes a single recorded step on a slave agent."""
    if not slave.is_online or not slave.conn:
        return False

    # Nếu nhân vật này đã hoàn thành vượt qua bước hiện tại: BỎ QUA KHÔNG THỰC HIỆN ĐỂ TRÁNH LỖI CHIẾN ĐẤU / MAP CŨ
    target_start = getattr(slave, "target_start_step", 0)
    if target_start > current_step_idx:
        slave.last_action_desc = f"⏩ Đã xong trước đó (đang chờ ở Bước {target_start + 1})"
        return True

    action = step_dict.get("action", "")
    params = step_dict.get("params", {})
    raw_b64 = step_dict.get("raw_hex_b64")

    try:
        if action == "accept_quest":
            return slave.accept_quest(params.get("npc_id", 0), params.get("mission_id", 0))

        elif action == "deliver_quest":
            return slave.deliver_quest(params.get("npc_id", 0), params.get("mission_id", 0), params.get("reward_id", 0))

        elif action == "talk_npc":
            return slave.talk_npc(params.get("npc_id", 0))

        elif action == "player_event":
            return slave.player_event(params.get("event_id", 0), params.get("choose", 0))

        elif action == "auto_sell":
            sold = slave.auto_sell_trash()
            return sold >= 0

        elif action == "learn_skill":
            return slave.learn_skill(params.get("shop_id", 0), params.get("skill_id", 0), params.get("level", 1), params.get("is_country", False))

        elif action == "set_auto_skill":
            return slave.set_auto_skill(params.get("skill_type", 0), params.get("skill_id", 0))

        elif action == "change_map":
            payload = bytes.fromhex(params.get("payload_hex", "")) if params.get("payload_hex") else None
            opcode = int(params.get("opcode", "0x290A"), 16) if isinstance(params.get("opcode"), str) else params.get("opcode", proto.CG_SCENE_GO_MAP)
            return slave.change_map(params.get("map_id", 0), raw_payload=payload, opcode=opcode)

        elif action == "fight":
            opcode = int(params.get("opcode", "0x30D5"), 16) if isinstance(params.get("opcode"), str) else params.get("opcode", proto.CG_FIGHT_ENTER_LOCALBATTEL)
            group_id = params.get("group_id", 0)
            is_remote = params.get("is_remote", False)
            
            # Luôn dùng fight_battle để hỗ trợ truyền group_id cho cả 0x30D5 và 0x30D7
            return slave.fight_battle(
                group_id=group_id,
                is_remote=is_remote,
                opcode=opcode
            )

        elif action == "fight_result":
            payload = bytes.fromhex(params.get("payload_hex", "")) if params.get("payload_hex") else None
            # Nếu là lần thử lại (attempt > 0), Clone đã lệch đồ so với Trưởng -> Bỏ qua Payload HP của Trưởng
            ignore_hp = (attempt > 0)
            if ignore_hp:
                script_print(f"       \033[93m[{slave.tag} | Bước {step_dict.get('step_num', '?')}]: Chuyển sang chế độ Tự Lập Trận Đánh (Bỏ qua Payload Trưởng)...\033[0m")
            return slave.send_local_battle_result(raw_0x30d6_payload=payload, ignore_payload_hp=ignore_hp)

        elif action == "equip_by_name":
            # Chức năng đặc biệt: Tự dò tìm slot_pos của vật phẩm bằng tên
            item_name = step_dict.get("name", "")
            if not item_name:
                return False
                
            slot_pos = slave.find_item_slot_by_name(item_name)
            if slot_pos >= 0:
                script_print(f"       \033[92m[{slave.tag} | Bước {step_dict.get('step_num', '?')}]: Đã tìm thấy '{item_name}' ở ô số {slot_pos}. Tiến hành Mặc/Sử dụng...\033[0m")
                # Opcode 0x2EE1 là CG_ACTOR_PLAYERBAG (Dùng cho thao tác mặc đồ, dùng bình máu...)
                # Payload: [slot_pos:2]
                payload = slot_pos.to_bytes(2, 'big')
                return slave.send_raw_interaction(proto.CG_ACTOR_PLAYERBAG, payload, desc=f"Mặc đồ: {item_name}")
            else:
                script_print(f"       \033[91m[{slave.tag} | Bước {step_dict.get('step_num', '?')}]: KHÔNG TÌM THẤY '{item_name}' trong túi!\033[0m")
                return False

        elif action == "raw_packet":
            opcode_val = step_dict.get("opcode", "0")
            opcode = int(opcode_val, 16) if isinstance(opcode_val, str) else (opcode_val or 0)
            
            # Bỏ qua các gói tin rác/thăm dò/phiên đăng nhập để tránh bị Server ngắt kết nối
            if opcode in (0x138F, 0x0001, 0x2927, 0x3E81, 0x3E82, 0x3E84, 0x3E85):
                return True
                
            payload = bytes.fromhex(params.get("payload_hex", "")) if params.get("payload_hex") else b""
            return slave.send_raw_interaction(opcode, payload, desc=step_dict.get("desc", ""))

        elif action in ("equip_item", "use_item", "use_bag_item"):
            wait_type = params.get("wait_type", 0)
            action_type = params.get("action_type", 1 if action == "equip_item" else 3)
            slot_pos = params.get("slot_pos", 0)
            equip_pos = params.get("equip_pos", 0)
            is_upgrade = params.get("is_upgrade", False)
            num = params.get("num", 1)
            item_id = params.get("item_id", -1)

            if item_id and item_id > 0:
                slot = slave.find_slot_by_item_id(item_id)
                if slot >= 0:
                    slot_pos = slot
            return slave.use_bag_item(wait_type, action_type, slot_pos, equip_pos, is_upgrade, num)

        elif action == "add_point":
            return slave.add_point(params.get("point_type", 0), params.get("count", 1))

        elif raw_b64:
            # Fallback: raw_b64 contains full wire packet [2B msg_type][payload]
            raw_bytes = base64.b64decode(raw_b64)
            if len(raw_bytes) >= 2:
                pkt_opcode = (raw_bytes[0] << 8) | raw_bytes[1]
                pkt_payload = raw_bytes[2:] if len(raw_bytes) > 2 else b""
                return slave.send_raw_interaction(pkt_opcode, pkt_payload, desc=step_dict.get("desc", ""))
            return False

        return False
    except Exception:
        return False


def run_single_slave_script_worker(s: SlaveAgent, steps: List[Dict[str, Any]], start_offset_sec: float = 0.0) -> bool:
    """Chạy độc lập kịch bản cho từng tài khoản, LUÔN từ Bước 1 để đồng bộ seed chiến đấu."""
    char_tag = f"{s.character.name} (Lv {s.character.level})" if s.character else s.username
    start_step = getattr(s, "target_start_step", 0)
    total_steps = len(steps)
    last_battle_end_time = 0.0

    # Phân tầng thời gian chạy (Stagger) để các acc không gửi gói tin cùng mili-giây lên cùng 1 IP
    if start_offset_sec > 0:
        time.sleep(start_offset_sec)

    script_print(f"  \033[92m🚀 [{char_tag}]\033[0m Bắt đầu chạy từ Bước {start_step + 1}/{total_steps}...")

    for idx in range(start_step, total_steps):
        if not s.is_online or not s.conn or not getattr(s.conn, "is_connected", False):
            script_print(f"  \033[93m[KẾT NỐI LẠI]\033[0m {char_tag} mất kết nối ở bước {idx+1}, đang tự động kết nối lại...")
            ok = s.connect_and_enter(max_retries=3)
            if not ok or not s.is_online:
                script_print(f"  \033[91m❌ [{char_tag}]\033[0m Không thể kết nối lại sau 3 lần thử, dừng kịch bản.")
                return False
            script_print(f"  \033[92m[KẾT NỐI LẠI THÀNH CÔNG]\033[0m {char_tag} đã kết nối lại, tiếp tục bước {idx+1}...")

        step = steps[idx]
        step_num = idx + 1
        action = step.get("action", "")
        desc = step.get("desc", step.get("action", f"Bước #{step_num}"))
        delay = float(step.get("delay", 0.8))
        if delay < 0.3:
            delay = 0.5
        elif delay > 3.0:
            delay = 2.0

        # Thêm độ lệch ngẫu nhiên nhẹ (jitter 0.02s - 0.08s) để 3 luồng không gửi trùng mili-giây
        delay += random.uniform(0.02, 0.08)

        # Chỉ thử lại bước trong 2 trường hợp đặc biệt:
        # 1. Server KHÔNG CÓ PHẢN HỒI khi nhận/trả nhiệm vụ (lag mạng, rớt gói)
        # 2. Chiến đấu KHÔNG HỢP LỆ (bị Server từ chối do lệch HP/MP)
        # Tất cả các trường hợp còn lại: Chạy bình thường như cũ!
        max_retries = 3
        for attempt in range(max_retries):
            # Nếu chuẩn bị vào trận đánh quái (fight), kiểm tra nếu chưa đủ 5s từ trận trước thì đợi đủ 5s
            if action == "fight" and last_battle_end_time > 0:
                elapsed = time.time() - last_battle_end_time
                if elapsed < 5.0:
                    time.sleep(5.0 - elapsed)

            ok = execute_action_on_slave(s, step, idx, attempt)
            action_desc = s.last_action_desc

            # Kiểm tra xem có đúng 2 trường hợp đặc biệt cần thử lại không:
            needs_retry = False

            # Trường hợp 1: Server không phản hồi quest
            if action in ("accept_quest", "deliver_quest") and "không có phản hồi" in action_desc.lower():
                needs_retry = True

            # Trường hợp 2: Chiến đấu không hợp lệ (bị từ chối)
            elif action in ("fight", "fight_result") and (not ok or any(k in action_desc.lower() for k in ["từ chối", "không đồng bộ", "lỗi kiểm chứng"])):
                # Nếu quái không tồn tại ở bản đồ này -> Tự động nhảy bước tiếp theo, không thử lại
                if any(k in action_desc.lower() for k in ["không tồn tại", "bản đồ này", "bỏ qua", "chưa vào trận"]):
                    needs_retry = False
                else:
                    needs_retry = True

            if needs_retry and attempt < max_retries - 1:
                script_print(f"       \033[93m[{char_tag} | Bước {step_num} (Thử lại {attempt+1}/{max_retries})]: {action_desc}\033[0m")
                if action == "fight_result":
                    s.enter_local_battle()
                    time.sleep(1.5)
                else:
                    time.sleep(1.2)
                continue
            else:
                # Đã thành công hoặc là các trường hợp còn lại -> in log và tiếp tục như cũ
                if any(err_k in action_desc for err_k in ["❌", "⚠️", "Lỗi", "từ chối", "thất bại"]):
                    script_print(f"       \033[91m[{char_tag} | Bước {step_num}]: {action_desc}\033[0m")
                elif any(skip_k in action_desc for skip_k in ["⏩", "bỏ qua", "đã xong", "đã trả", "chờ"]):
                    script_print(f"       \033[90m[{char_tag} | Bước {step_num}]: {action_desc}\033[0m")
                else:
                    script_print(f"       \033[92m[{char_tag} | Bước {step_num}]: {desc} -> {action_desc}\033[0m")
                break

        if action == "fight_result":
            last_battle_end_time = time.time()
            delay = 5.0

        time.sleep(delay)

    script_print(f"  \033[92m🎉 [{char_tag}]\033[0m ĐÃ HOÀN THÀNH TOÀN BỘ KỊCH BẢN!")
    return True


def run_batch_script_for_slaves(slaves: List[SlaveAgent], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Executes the recorded script independently for each slave concurrently."""
    batch_res = {
        "success_slaves": [],
        "failed_slaves": []
    }

    online_slaves = [s for s in slaves if s.is_online]
    if not online_slaves:
        return batch_res

    # 1. Mặc định tất cả nhân vật bắt đầu chạy từ Bước 1
    for s in online_slaves:
        s.target_start_step = 0
        char_tag = f"{s.character.name} (Lv {s.character.level})" if s.character else s.username
        script_print(f"  \033[92m🎯 [{char_tag}]\033[0m: Mặc định bắt đầu từ Bước 1")

    script_print("\n  \033[96m⚡ [CHẠY ĐỘC LẬP ĐA LUỒNG]\033[0m Các acc tự chạy từ bước của riêng mình song song, không cần phải chờ nhau!\033[0m\n")

    with ThreadPoolExecutor(max_workers=len(online_slaves)) as executor:
        futs = {executor.submit(run_single_slave_script_worker, s, steps, i * 0.35): s for i, s in enumerate(online_slaves)}
        for fut in futs:
            s = futs[fut]
            try:
                res = fut.result()
                if res and s.is_online:
                    batch_res["success_slaves"].append(s)
                else:
                    batch_res["failed_slaves"].append(s)
            except Exception:
                batch_res["failed_slaves"].append(s)

    return batch_res


def run_auto_leveling_from_script_cli(accounts: List[Tuple[str, str, Any]], script_path: str = None):
    """
    Main entry point for Option 12: Batch Auto-Leveling across all 3 character slots.
    Prompts user to select from 10 script slots.
    Smart skips any character slot that has already completed this script.
    """
    from modules.script_manager import ScriptManager

    if script_path:
        script_data = load_recorded_script(script_path)
    else:
        selected = ScriptManager.prompt_select_cli()
        if not selected:
            return
        script_data = selected.get("data")
        script_path = selected.get("path")

    if not script_data:
        script_print(f"\n\033[91m[CHƯA CÓ KỊCH BẢN]\033[0m Chưa tìm thấy tệp kịch bản: '{script_path}'")
        script_print("-> Vui lòng mở Option 11 và chơi Acc Trưởng 1 lần, sau đó gõ phím 'S' để lưu kịch bản vào 1 trong 10 Slot trước.")
        return

    steps = script_data.get("steps", [])
    script_title = script_data.get("title", script_data.get("script_name", "Kịch Bản Auto Leveling"))
    total_steps = len(steps)

    print("\n" + "=" * 80)
    print("      CHẠY TỰ ĐỘNG CÀY CẤP TOÀN BỘ NHÂN VẬT THEO KỊCH BẢN ĐÃ LƯU")
    print("=" * 80)
    print(f"-> Kịch bản: \033[92m{script_title}\033[0m (Tổng cộng: \033[93m{total_steps}\033[0m bước hành động)")
    print(f"-> Máy chủ: \033[96m{settings.game_server_name}\033[0m ({settings.game_host}:{settings.game_port})")
    print(f"-> Tổng tài khoản: \033[92m{len(accounts)}\033[0m acc (Tối đa \033[93m{len(accounts) * 3}\033[0m nhân vật trên 3 Slot)")
    print("-" * 80)

    # Character Registry Preview
    CharacterRegistry.print_registry_dashboard(settings.game_server_name, accounts)

    print("\n  [1] Chỉ cày nhân vật ở Slot 1")
    print("  [2] Chỉ cày nhân vật ở Slot 2")
    print("  [3] Chỉ cày nhân vật ở Slot 3")
    print("  [4] \033[92m🌟 CÀY TRỌN GÓI CẢ 3 NHÂN VẬT (Slot 1 -> Slot 2 -> Slot 3) CHO TẤT CẢ ACC\033[0m")
    print("-" * 80)
    try:
        slot_choice = input("-> Chọn chế độ cày nhân vật [1, 2, 3, 4] (Nhấn Enter = 4): ").strip()
        if slot_choice == "1":
            target_slots = [0]
        elif slot_choice == "2":
            target_slots = [1]
        elif slot_choice == "3":
            target_slots = [2]
        else:
            target_slots = [0, 1, 2]
    except (KeyboardInterrupt, EOFError):
        print("\n[ĐÃ HỦY]")
        return

    print("\n" + "-" * 80)
    batch_input = input("-> Số tài khoản cày song song mỗi đợt [1-5] (Khuyến nghị 2 nếu không dùng Proxy để không bị Server ngắt kết nối, Enter=2): ").strip()
    batch_size = int(batch_input) if batch_input.isdigit() and 1 <= int(batch_input) <= 5 else 2
    print(f"-> Đã chọn: \033[92m{batch_size}\033[0m tài khoản / đợt")
    start_all_time = time.time()
    grand_total_completed = 0
    grand_total_skipped = 0
    session_detailed_results = []

    # Nhận diện kịch bản "lv15" (không phân biệt hoa thường, khoảng trắng)
    is_lv15_script = "lv15" in script_title.lower().replace(" ", "").replace("_", "")
    if is_lv15_script:
        print(f"\n\033[93m[ĐIỀU KIỆN ĐẶC BIỆT KỊCH BẢN '{script_title}']\033[0m: Chỉ chạy cho nhân vật < Lv 15. Nhân vật từ Lv 15 trở lên sẽ tự động bỏ qua.")

    for current_slot in target_slots:
        slot_name = f"Slot {current_slot + 1}"
        print("\n" + "=" * 80)
        print(f"     🚀 BẮT ĐẦU CÀY CẤP CHO TOÀN BỘ NHÂN VẬT Ở: \033[93m{slot_name.upper()}\033[0m")
        print("=" * 80)

        # Filter out accounts whose character at this slot has ALREADY completed this script
        pending_accounts = []
        for acc in accounts:
            uname = acc[0]
            if is_lv15_script:
                # Kịch bản 1: Chỉ hoàn thành khi đã đạt đủ Lv >= 15 VÀ đã hoàn thành xong hết kịch bản
                c_info = CharacterRegistry.get_character_at_slot(settings.game_server_name, uname, current_slot)
                c_lvl = c_info.get("level", 1) if c_info else 1
                is_done = CharacterRegistry.is_script_completed(settings.game_server_name, uname, current_slot, script_title)
                if is_done and c_lvl >= 15:
                    script_print(f"  \033[90m[ĐÃ ĐẠT LV 15 & XONG KỊCH BẢN - BỎ QUA]\033[0m {uname:<14} ({slot_name} - Lv {c_lvl}) -> Đã hoàn thành.")
                    grand_total_skipped += 1
                    continue
            else:
                # Các kịch bản khác: Không yêu cầu cấp độ, chỉ cần đã hoàn thành xong
                if CharacterRegistry.is_script_completed(settings.game_server_name, uname, current_slot, script_title):
                    script_print(f"  \033[90m[ĐÃ XONG TRƯỚC ĐÓ - BỎ QUA]\033[0m {uname:<14} ({slot_name}) -> Đã hoàn thành kịch bản này.")
                    grand_total_skipped += 1
                    continue

            pending_accounts.append(acc)

        if not pending_accounts:
            script_print(f"\n\033[92m[HOÀN TẤT 100%]\033[0m Toàn bộ nhân vật ở {slot_name} đều đã chạy kịch bản này hoặc đã đạt cấp yêu cầu!\n")
            continue

        batches = [pending_accounts[i:i + batch_size] for i in range(0, len(pending_accounts), batch_size)]
        total_batches = len(batches)
        print(f"\n-> Cần cày cho \033[92m{len(pending_accounts)}\033[0m nhân vật chưa chạy (Chia làm \033[96m{total_batches}\033[0m đợt)...")

        for b_idx, batch_accs in enumerate(batches, 1):
            print("\n" + "-" * 75)
            print(f"\033[96m=== [{slot_name.upper()} - ĐỢT {b_idx}/{total_batches}]\033[0m Đang nạp {len(batch_accs)} tài khoản: {', '.join([a[0] for a in batch_accs])}")
            print("-" * 75)

            # 1. Initialize and connect batch slaves locked to current_slot
            slaves = [
                SlaveAgent(
                    index=i + 1,
                    username=acc[0],
                    password=acc[1],
                    slot_index=current_slot,
                    proxy=acc[2] if len(acc) >= 3 else None
                )
                for i, acc in enumerate(batch_accs)
            ]

            # Track start levels
            start_levels = {}

            # Staggered connect
            eligible_slaves = []
            for s in slaves:
                ok = s.connect_and_enter()
                if ok:
                    lvl = s.character.level if s.character else 1
                    start_levels[s.username] = lvl
                    script_print(f"  \033[92m[ONLINE]\033[0m {s.tag} -> {s.last_action_desc}")

                    eligible_slaves.append(s)
                else:
                    start_levels[s.username] = 1
                    script_print(f"  \033[91m[OFFLINE]\033[0m {s.tag} -> {s.last_action_desc}")
                time.sleep(0.35)

            if not eligible_slaves:
                script_print(f"\033[93m[BỎ QUA ĐỢT {b_idx}]\033[0m Không có tài khoản nào cần chạy kịch bản.")
                for s in slaves:
                    s.close()
                continue

            # 2. Execute Script Steps on this batch
            script_print(f"\n  -> Đang tự động thực thi {total_steps} bước nhiệm vụ...")
            res = run_batch_script_for_slaves(eligible_slaves, steps)

            # 3. Mark script completion in Registry & record details
            success_unames = {s.username for s in res["success_slaves"]}
            for s in eligible_slaves:
                if s.is_online:
                    c_name = s.character.name if s.character else s.username
                    s_lvl = start_levels.get(s.username, 1)
                    e_lvl = s.character.level if s.character else s_lvl
                    is_ok = s.username in success_unames
                    status_text = "★ HOÀN THÀNH 100%" if is_ok else "⚠️ CHƯA XONG"
                    
                    if is_ok:
                        if is_lv15_script:
                            # Kịch bản 1: Chỉ hoàn thành khi đạt đủ Lv 15 VÀ hoàn thành xong hết kịch bản
                            if e_lvl >= 15:
                                CharacterRegistry.mark_script_completed(
                                    server_name=settings.game_server_name,
                                    username=s.username,
                                    slot_idx=current_slot,
                                    script_name=script_title,
                                    level=e_lvl
                                )
                                grand_total_completed += 1
                                status_text = f"★ HOÀN THÀNH (Lv {e_lvl} ≥ 15)"
                            else:
                                status_text = f"⚠️ Xong lượt (Lv {e_lvl} < 15, cần chạy tiếp)"
                        else:
                            # Các kịch bản khác: Không yêu cầu cấp độ
                            CharacterRegistry.mark_script_completed(
                                server_name=settings.game_server_name,
                                username=s.username,
                                slot_idx=current_slot,
                                script_name=script_title,
                                level=e_lvl
                            )
                            grand_total_completed += 1

                    session_detailed_results.append({
                        "username": s.username,
                        "slot": slot_name,
                        "char_name": c_name,
                        "start_lvl": s_lvl,
                        "end_lvl": e_lvl,
                        "status": status_text,
                        "is_ok": is_ok
                    })

            # 4. Clean close batch to release IP slots
            for s in slaves:
                s.close()
            script_print(f"  \033[92m[HOÀN TẤT ĐỢT {b_idx}]\033[0m Đã lưu tiến độ và đăng xuất an toàn để nhường slot IP.")
            time.sleep(0.8)

    elapsed = time.time() - start_all_time

    # Hiển thị bảng chi tiết kết quả cày cấp từng nhân vật
    if session_detailed_results:
        print("\n" + "=" * 90)
        print("                  BẢNG KẾT QUẢ CÀY CẤP CHI TIẾT (OPTION 12)")
        print("=" * 90)
        print(f"{'STT':<4} | {'TÀI KHOẢN':<14} | {'VỊ TRÍ':<8} | {'TÊN NHÂN VẬT':<16} | {'CẤP ĐỘ':<20} | {'TIẾN ĐỘ KỊCH BẢN'}")
        print("-" * 90)
        for idx, r in enumerate(session_detailed_results, 1):
            s_lvl = r["start_lvl"]
            e_lvl = r["end_lvl"]
            diff = e_lvl - s_lvl
            if diff > 0:
                lvl_str = f"Lv {s_lvl} ➔ \033[92mLv {e_lvl}\033[0m \033[93m(+{diff} Lv)\033[0m"
            else:
                lvl_str = f"Lv {e_lvl} (Không đổi)"
            
            st_color = "\033[92m" if r["is_ok"] else "\033[91m"
            print(f"{idx:<4} | {r['username']:<14} | {r['slot']:<8} | {r['char_name']:<16} | {lvl_str:<32} | {st_color}{r['status']}\033[0m")
        print("=" * 90)

    print("\n" + "=" * 80)
    print("              BẢNG TỔNG KẾT TIẾN ĐỘ CÀY CẤP TOÀN BỘ NHÂN VẬT")
    print("=" * 80)
    print(f"-> Máy chủ: {settings.game_server_name}")
    print(f"-> Kịch bản thực thi: {script_title}")
    print(f"-> Nhân vật vừa hoàn thành trong phiên: \033[92m{grand_total_completed}\033[0m nhân vật")
    print(f"-> Nhân vật đã xong trước đó (bỏ qua): \033[93m{grand_total_skipped}\033[0m nhân vật")
    print(f"-> Tổng thời gian thực hiện: \033[96m{elapsed:.1f}\033[0m giây ({elapsed/60:.1f} phút)")
    print("=" * 80)

    # Show final updated registry
    CharacterRegistry.print_registry_dashboard(settings.game_server_name, accounts)
