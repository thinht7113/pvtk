import os
import json
import time
import struct
import socket
import select
import threading
import base64
import subprocess
from typing import List, Dict, Any, Optional, Tuple

from modules.adb_controller import ADBController
from modules.adb_setup import auto_login_account, auto_select_character, auto_return_to_char_select, auto_logout_account, auto_exit_game_world
from core.message import Message
from core.config import settings
import core.protocol as proto
from modules import bag_parser

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PRINT_LOCK = threading.Lock()

def safe_print(msg):
    with PRINT_LOCK:
        try:
            print(msg, flush=True)
        except Exception:
            try:
                print(str(msg).encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
            except Exception:
                pass


def build_wire_packet(msg_type: int, payload: bytes = b"") -> bytes:
    """
    Tạo gói tin TCP đúng chuẩn PVTK wire format:
    [2 bytes total_len (Big-Endian)] [2 bytes msg_type (Big-Endian)] [payload]
    total_len = 4 + len(payload)
    """
    total_len = 4 + len(payload)
    header = struct.pack(">Hh", total_len, msg_type)
    return header + payload


def build_smart_fight_result_packet(params: dict, raw_b64: str = "", char_info: Optional[dict] = None) -> Optional[bytes]:
    """
    Tạo gói tin 0x30D6 cân chỉnh theo HP/MP và Level thực tế của nhân vật đang chạy,
    tránh gửi hardcode HP 432 của acc trưởng khiến Server từ chối 'Không công nhận chiến đấu'.
    """
    payload_hex = params.get("payload_hex", "")
    raw_payload = None
    if payload_hex:
        try:
            raw_payload = bytes.fromhex(payload_hex)
        except Exception:
            pass
    elif raw_b64:
        try:
            decoded = base64.b64decode(raw_b64)
            if len(decoded) > 2:
                raw_payload = decoded[2:]  # Bỏ 2 bytes msg_type 0x30D6
        except Exception:
            pass

    if not raw_payload or len(raw_payload) < 20:
        if raw_payload:
            return build_wire_packet(0x30D6, raw_payload)
        return None

    # Lấy thông tin nhân vật thực tế
    char_level = 1
    char_hp = 0
    char_mp = 0
    if char_info:
        char_level = char_info.get("level", 1) or 1
        char_hp = char_info.get("hp", 0) or 0
        char_mp = char_info.get("mp", 0) or 0

    # QUAN TRỌNG: Nếu chưa có HP/MP thực tế từ server:
    if char_hp <= 0 or char_mp <= 0:
        # Nếu payload gốc chứa HP cao (> 300) nhưng nhân vật thực tế là Lv.1-2,
        # tuyệt đối KHÔNG gửi payload gốc vì chắc chắn bị Server từ chối (-7)!
        m_shp = int.from_bytes(raw_payload[0:4], "big") if len(raw_payload) >= 4 else 0
        if char_level <= 2 and m_shp > 300:
            char_hp = 182 if char_level == 1 else 262
            char_mp = 109 if char_level == 1 else 129
        else:
            return build_wire_packet(0x30D6, raw_payload)

    hp_max = char_hp
    mp_max = char_mp

    try:
        from modules.headless_battle import HeadlessBattleEngine
        # Trích xuất seed và group_id từ payload gốc của acc trưởng
        target_seed = int.from_bytes(raw_payload[16:18], "big")
        target_gid = int.from_bytes(raw_payload[18:20], "big")

        start_hp, start_mp, end_hp, end_mp, seed, gid, round_count, tail_data = HeadlessBattleEngine.simulate(
            player_hp_max=hp_max,
            player_mp_max=mp_max,
            group_id=target_gid,
            seed=target_seed,
            player_cur_hp=hp_max,
            player_cur_mp=mp_max,
            player_level=char_level,
            raw_script_payload=raw_payload,
            ignore_payload_hp=False
        )

        new_payload = struct.pack(">iiiihh", start_hp, start_mp, end_hp, end_mp, seed, gid) + tail_data
        return build_wire_packet(0x30D6, new_payload)
    except Exception:
        # Fallback về payload gốc nếu tính toán gặp sự cố
        return build_wire_packet(0x30D6, raw_payload)


def extract_battle_group_id(current_step_idx: int, script_data: list) -> int:
    """Trích xuất Battle Group ID từ bước hiện tại hoặc bước fight_result kế tiếp."""
    # Tìm ngược lại tối đa 3 bước để lấy group_id từ bước fight/enter_remote_battle
    for idx in range(current_step_idx, max(-1, current_step_idx - 4), -1):
        step = script_data[idx]
        gid = step.get("params", {}).get("group_id", 0)
        if gid and gid > 0:
            return gid
            
    # Thử tìm trong payload_hex của fight_result (nếu có)
    if current_step_idx + 1 < len(script_data):
        next_step = script_data[current_step_idx + 1]
        if next_step.get("action") == "fight_result":
            p_hex = next_step.get("params", {}).get("payload_hex", "")
            if p_hex and len(p_hex) >= 40:
                try:
                    p_bytes = bytes.fromhex(p_hex)
                    if len(p_bytes) >= 20:
                        return int.from_bytes(p_bytes[18:20], "big")
                except Exception:
                    pass
    return 0


def _extract_group_id_from_raw(raw_b64: str) -> int:
    if not raw_b64:
        return 0
    try:
        raw_bytes = base64.b64decode(raw_b64)
        if len(raw_bytes) >= 4:
            return int.from_bytes(raw_bytes[2:4], "big")
    except Exception:
        pass
    return 0


def build_packet_from_params(action: str, params: dict, raw_b64: str = "", step_opcode: str = "", char_info: Optional[dict] = None, bag_bytes: bytes = b"") -> Optional[bytes]:
    """
    Tái tạo gói tin đúng chuẩn từ action type và params đã lưu trong script JSON.
    Trả về bytes sẵn sàng gửi qua TCP socket (đã có length header).
    Trả về None nếu không biết cách tái tạo → fallback sang raw_hex_b64.
    """
    try:
        if action == "accept_quest":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            npc_id = params.get("npc_id", 0)
            mission_id = params.get("mission_id", 0)
            payload = struct.pack(">hh", npc_id, mission_id)
            return build_wire_packet(0x38A6, payload)

        elif action == "deliver_quest":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            npc_id = params.get("npc_id", 0)
            mission_id = params.get("mission_id", 0)
            reward_id = params.get("reward_id", 0)
            payload = struct.pack(">hhi", npc_id, mission_id, reward_id)
            return build_wire_packet(0x38A7, payload)

        elif action in ("equip_item", "use_item", "use_bag_item", "equip_by_name"):
            slot_pos = params.get("slot_pos", 0)
            item_id = params.get("item_id", params.get("target_item_id", -1))
            item_name = params.get("item_name", "")
            
            # SMART EQUIP / USE: Tìm đúng ô (slot_pos) hiện tại của vật phẩm đó dựa vào item_id hoặc item_name
            if bag_bytes:
                actual_slot = -1
                target_label = item_name or (f"ID #{item_id}" if item_id > 0 else "")
                
                # 1. Tìm theo item_id nếu có
                if item_id > 0:
                    actual_slot = bag_parser.get_slot_by_item_id(bag_bytes, item_id)
                    
                # 2. Nếu chưa thấy và có item_name, tìm theo tên
                if actual_slot == -1 and item_name:
                    actual_slot = bag_parser.get_slot_by_item_name(bag_bytes, item_name)
                    
                if actual_slot != -1:
                    if actual_slot != slot_pos:
                        safe_print(f"🔄 [SMART {action.upper()}] Vật phẩm '{target_label}' đã đổi vị trí từ Ô #{slot_pos} sang Ô #{actual_slot}!")
                    slot_pos = actual_slot
                else:
                    cur_id, cur_name = bag_parser.get_item_by_slot(bag_bytes, slot_pos)
                    if cur_id != -1:
                        safe_print(f"🎒 [{action.upper()}] Ô #{slot_pos} chứa: '{cur_name}' (ID: {cur_id})")
                    elif target_label:
                        safe_print(f"⚠️ [SMART {action.upper()}] Không tìm thấy '{target_label}' trong túi! Dùng lại Ô #{slot_pos} cũ...")
            
            # Ghi đè slot_pos mới trực tiếp vào gói tin raw gốc để bảo toàn toàn bộ cấu trúc (equip_pos, is_upgrade, num...)
            if raw_b64:
                raw = bytearray(base64.b64decode(raw_b64))
                if len(raw) >= 6:
                    raw[4:6] = slot_pos.to_bytes(2, 'big')
                    opcode = (raw[0] << 8) | raw[1]
                    payload = raw[2:]
                    return build_wire_packet(opcode, payload)
            
            # Dự phòng nếu không có raw_b64 (rất hiếm)
            action_type = params.get("action_type", 1 if "equip" in action else 3)
            wait_type = params.get("wait_type", 0)
            equip_pos = params.get("equip_pos", -1)
            is_upgrade = params.get("is_upgrade", False)
            if equip_pos >= 0:
                payload = struct.pack(">bbHbb", wait_type, action_type, slot_pos, equip_pos, 1 if is_upgrade else 0)
            else:
                payload = struct.pack(">bbH", wait_type, action_type, slot_pos)
            return build_wire_packet(0x2EE1, payload)

        elif action == "talk_npc":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            npc_id = params.get("npc_id", 0)
            payload = struct.pack(">BB", 1, npc_id & 0xFF)
            return build_wire_packet(0x2917, payload)

        elif action == "player_event":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            event_id = params.get("event_id", 0)
            choose = params.get("choose", 0)
            payload = struct.pack(">iB", event_id, choose & 0xFF)
            return build_wire_packet(0x290C, payload)

        elif action == "change_map":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            opcode_val = params.get("opcode", 0x290A)
            payload_hex = params.get("payload_hex", "")
            if payload_hex:
                payload = bytes.fromhex(payload_hex)
            else:
                map_id = params.get("map_id", 0)
                payload = struct.pack(">h", map_id)
            return build_wire_packet(opcode_val, payload)

        elif action == "fight":
            opcode_str = params.get("opcode", 0x30D5)
            opcode_val = opcode_str if isinstance(opcode_str, int) else int(opcode_str, 0)
            group_id = params.get("group_id", 0)
            is_remote = params.get("is_remote", None)

            if is_remote is True:
                opcode_val = 0x30D7
            elif is_remote is False:
                opcode_val = 0x30D5

            if opcode_val == 0x30D5 and group_id > 0:
                opcode_val = 0x30D7

            # Fix for backward compatibility with old proxy_hub auto-patched scripts without group_id
            if opcode_val == 0x30D7 and group_id == 0:
                group_id = _extract_group_id_from_raw(raw_b64)

            if opcode_val in (0x30D5, 0x30D7, 0x38B8, 0x38CF, 0x38BF):
                # Fight battles include group_id (both remote and local)
                if group_id > 0:
                    payload = struct.pack(">h", group_id)
                else:
                    payload = b""
                return build_wire_packet(opcode_val, payload)
            else:
                # Fight triggered by NPC interaction (0x290C, 0x2917, 0x2AFC...)
                # Return None to use fallback_from_raw_b64 and send the exact interaction packet
                return None

        elif action == "fight_result":
            wire_packet = build_smart_fight_result_packet(params, raw_b64, char_info)
            if wire_packet:
                safe_print(f"💉 [FIGHT_RESULT] Opcode=0x30D6 Size={len(wire_packet)}B (ĐỂ GAME CLIENT TỰ GỬI)")
            return b"SKIP_ACTION"

        elif action == "learn_skill":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            shop_id = params.get("shop_id", 0)
            skill_id = params.get("skill_id", 0)
            level = params.get("level", 1)
            is_country = params.get("is_country", False)
            payload = struct.pack(">hiB", shop_id, skill_id, level & 0xFF)
            payload += struct.pack(">B", 1 if is_country else 0)
            return build_wire_packet(0x36B2, payload)

        elif action == "set_auto_skill":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            skill_type = params.get("skill_type", 0)
            skill_id = params.get("skill_id", 0)
            payload = struct.pack(">Bi", skill_type & 0xFF, skill_id)
            return build_wire_packet(0x36B9, payload)

        elif action == "add_point":
            if raw_b64:
                return fallback_from_raw_b64(raw_b64)
            point_type = params.get("point_type", 0)
            count = params.get("count", 1)
            payload = struct.pack(">Bi", point_type & 0xFF, count)
            return build_wire_packet(0x2EE5, payload)

        # Block đã được gộp lên trên

        elif action == "auto_sell":
            items = params.get("items", [])
            if items:
                valid_items = []
                for it in items:
                    t_id = it.get("item_id", 0)
                    t_slot = it.get("slot", 0)
                    t_qty = it.get("qty", 1)
                    
                    # --- KIỂM TRA TÚI ĐỒ TRƯỚC KHI BÁN ---
                    if bag_bytes:
                        # Người dùng muốn bán rác không bị ràng buộc item_id. 
                        # Do đó ta lấy đúng item_id của clone đang nằm ở ô t_slot này để bán!
                        from modules.bag_parser import get_item_by_slot
                        found_id, _ = get_item_by_slot(bag_bytes, t_slot)
                        if found_id > 0:
                            valid_items.append((found_id, t_slot, t_qty))
                        else:
                            # Nếu ô này trống, bỏ qua
                            pass
                    else:
                        valid_items.append((t_id, t_slot, t_qty))
                        
                if not valid_items:
                    return b"SKIP_ACTION"
                    
                payload = struct.pack(">B", len(valid_items) & 0xFF)
                for t_id, t_slot, t_qty in valid_items:
                    payload += struct.pack(">ihh", t_id, t_slot, t_qty)
                return build_wire_packet(0x2F05, payload)
            elif raw_b64:
                return fallback_from_raw_b64(raw_b64)
            else:
                return build_wire_packet(0x2F05, struct.pack(">B", 0))

        elif action == "raw_packet":
            # Lấy opcode từ params, hoặc từ step-level opcode field
            opcode_str = params.get("opcode", step_opcode) or step_opcode or "0x0000"
            if isinstance(opcode_str, str):
                opcode_val = int(opcode_str, 0) if opcode_str.startswith("0x") else int(opcode_str)
            else:
                opcode_val = opcode_str
            payload_hex = params.get("payload_hex", "")
            if payload_hex:
                payload = bytes.fromhex(payload_hex)
            else:
                payload = b""
            return build_wire_packet(opcode_val, payload)

        else:
            # Unknown action → fallback to raw_b64
            return None

    except Exception:
        return None


def fallback_from_raw_b64(raw_b64: str) -> Optional[bytes]:
    """
    Fallback: Nếu không thể tái tạo từ params, dùng raw_hex_b64.
    raw_hex_b64 chứa [msg_type (2 bytes)] + [payload], 
    CẦN thêm 2 bytes length header trước khi gửi.
    """
    if not raw_b64:
        return None
    try:
        raw_bytes = base64.b64decode(raw_b64)
        if len(raw_bytes) < 2:
            return None
        # raw_bytes = msg_type(2 bytes) + payload
        total_len = 2 + len(raw_bytes)  # 2 (length header) + 2 (msg_type) + payload
        header = struct.pack(">H", total_len)
        return header + raw_bytes
    except Exception:
        return None


class GlobalProxyHub:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = GlobalProxyHub()
        return cls._instance

    def __init__(self, listen_port=10002):
        self.listen_port = listen_port
        self.server_sock = None
        self.is_running = False
        self.runners = []  # Danh sách ScriptRunnerThread
        self.sessions = []
        self._lock = threading.Lock()
        
        self.ukey_to_runner = {} # Mapping: ukey -> ScriptRunnerThread
        
    def register_runner(self, runner):
        with self._lock:
            if runner not in self.runners:
                self.runners.append(runner)
                
    def unregister_runner(self, runner):
        with self._lock:
            if runner in self.runners:
                self.runners.remove(runner)

    def find_runner_for_acc(self, acc_name: str):
        with self._lock:
            for r in self.runners:
                if getattr(r, "current_acc_name", None) and r.current_acc_name.lower() == acc_name.lower():
                    return r
            return None

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("0.0.0.0", self.listen_port))
            self.server_sock.listen(20)
            safe_print(f"🌐 [GLOBAL HUB] Đang lắng nghe Proxy tại 0.0.0.0:{self.listen_port}...")
            threading.Thread(target=self._accept_loop, daemon=True).start()
        except Exception as e:
            safe_print(f"❌ [GLOBAL HUB] Lỗi mở cổng {self.listen_port}: {e}")
            self.is_running = False

    def _accept_loop(self):
        session_id_counter = 1
        while self.is_running:
            try:
                client_sock, addr = self.server_sock.accept()
                session = EmulatorProxySession(
                    client_sock=client_sock, 
                    target_host=settings.game_host, 
                    target_port=settings.game_port, 
                    session_id=session_id_counter, 
                    hub=self
                )
                self.sessions.append(session)
                threading.Thread(target=session.start, daemon=True).start()
                session_id_counter += 1
            except Exception as e:
                if self.is_running:
                    safe_print(f"❌ [GLOBAL HUB] Lỗi accept: {e}")
                break

    def stop(self):
        self.is_running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except:
                pass
        for s in self.sessions:
            s.stop()


class EmulatorProxySession:
    """Đại diện cho 1 kết nối mạng giữa 1 giả lập và Game Server / Master Server"""
    def __init__(self, client_sock: socket.socket, target_host: str, target_port: int, session_id: int, hub=None):
        self.client_sock = client_sock
        self.server_sock = None
        self.target_host = target_host
        self.target_port = target_port
        self.session_id = session_id
        self.hub = hub
        self.runner = None
        self.is_running = False
        self.is_master = False
        
        # Buffer gói tin từ server để theo dõi trận đấu & xác nhận response
        self.recent_server_packets: List[bytes] = []
        self._server_lock = threading.Lock()
        
        # Buffer & event theo dõi trận đánh từ Game Client trên giả lập
        self.recent_client_packets: List[bytes] = []
        self._client_lock = threading.Lock()
        self.battle_finished_event = threading.Event()
        self.is_client_in_battle = False
        
        self.bag_bytes = b""
        self._current_proxy = None  # SOCKS5 proxy đang dùng cho session này
        
        # Nhân vật mục tiêu cần bảo đảm đăng nhập qua ghi đè gói 0x0002
        self.target_char_id: Optional[int] = None
        self.target_char_name: str = ""

    def reset_battle_event(self):
        """Đặt lại trạng thái chờ trận đấu mới"""
        self.battle_finished_event.clear()
        self.is_client_in_battle = False

    def _rewrite_client_actor_enter(self, data: bytes) -> bytes:
        """
        Bảo vệ tuyệt đối: Ghi đè Char ID trong gói tin 0x0002 (CG_LOGIN_ACTORENTER)
        nếu client chọn sai nhân vật trên giao diện do cơ chế đảo vị trí của game.
        """
        if not self.target_char_id or len(data) < 8:
            return data
        try:
            offset = 0
            new_data = bytearray(data)
            modified = False
            while offset + 8 <= len(data):
                pkt_len = (data[offset] << 8) | data[offset + 1]
                if pkt_len < 4 or offset + pkt_len > len(data):
                    break
                msg_type = (data[offset + 2] << 8) | data[offset + 3]
                if msg_type == 0x0002:
                    orig_id = int.from_bytes(data[offset + 4 : offset + 8], "big")
                    if orig_id != self.target_char_id:
                        new_data[offset + 4 : offset + 8] = self.target_char_id.to_bytes(4, "big")
                        modified = True
                        target_name = self.target_char_name or str(self.target_char_id)
                        safe_print(f"[Proxy {self.session_id}] 🎯 [REWRITE 0x0002] Tự động đổi Char ID đăng nhập: {orig_id} -> \033[92m{self.target_char_id}\033[0m ('\033[96m{target_name}\033[0m')")
                offset += pkt_len
            if modified:
                return bytes(new_data)
        except Exception:
            pass
        return data

    def start(self):
        self.is_running = True
        try:
            safe_print(f"[Proxy {self.session_id}] 📥 [SESSION] Reading initial packet from emulator...")
            self.client_sock.settimeout(10.0)
            initial_data = self.client_sock.recv(4096)
            if not initial_data:
                safe_print(f"[Proxy {self.session_id}] ❌ [SESSION] No initial data, closing")
                self.stop()
                return

            # Chờ đủ 4 bytes để đọc length
            while len(initial_data) < 4:
                chunk = self.client_sock.recv(4096)
                if not chunk: break
                initial_data += chunk

            if len(initial_data) >= 4:
                pkt_len = (initial_data[0] << 8) | initial_data[1]
                # Chờ đủ gói tin đầu tiên
                while len(initial_data) < pkt_len:
                    chunk = self.client_sock.recv(4096)
                    if not chunk: break
                    initial_data += chunk

            safe_print(f"[Proxy {self.session_id}] 📥 [SESSION] Received {len(initial_data)} bytes from emulator (Initial)")

            # Nhận diện Opcode gói tin đầu tiên để điều hướng thông minh
            msg_type = 0
            if len(initial_data) >= 4:
                msg_type = (initial_data[2] << 8) | initial_data[3]
                pkt_len = (initial_data[0] << 8) | initial_data[1]
                initial_data = initial_data[:pkt_len]

            self.is_master = ((msg_type >> 8) == 0x13) or (0x1300 <= msg_type <= 0x13FF)

            dest_host = settings.master_host if self.is_master else self.target_host
            dest_port = settings.master_port if self.is_master else self.target_port
            dest_name = "Master Server (30000)" if self.is_master else f"Game Server ({dest_port})"

            safe_print(f"[Proxy {self.session_id}] 🔀 [SESSION] Packet 0x{msg_type:04X} -> Routing to {dest_name} [{dest_host}:{dest_port}]")

            # Gắn runner dựa trên Opcode đầu tiên
            if msg_type == proto.LC_PLAYER_LOGIN:
                msg = Message(msg_type, initial_data[4:])
                if msg.hasRemaining():
                    username = msg.getString()
                    if self.hub:
                        self.runner = self.hub.find_runner_for_acc(username)
                        if self.runner:
                            safe_print(f"[Proxy {self.session_id}] 🔗 [MASTER] Ánh xạ Master Session cho tài khoản: {username}")
            
            elif msg_type == proto.CG_LOGIN_PLAYERCHECK:
                msg = Message(msg_type, initial_data[4:pkt_len])
                if msg.hasRemaining():
                    ukey = msg.getLong()
                    session_id = msg.getInt()
                    if self.hub:
                        self.runner = self.hub.ukey_to_runner.get(session_id)
                        safe_print(f"[Proxy {self.session_id}] 🔍 [DEBUG] Extracted Game SessionID: {session_id}. Found runner? {self.runner is not None}")
                        if self.runner:
                            safe_print(f"[Proxy {self.session_id}] 🔗 [GAME] Ánh xạ Game Session cho tài khoản: {getattr(self.runner, 'current_acc_name', 'Unknown')} (SessionID: {session_id})")
                            self.runner.active_session = self

                            safe_print(f"[Proxy {self.session_id}] 🔗 [SESSION] Connecting to {dest_host}:{dest_port}...")

            # ===== KẾT NỐI QUA SOCKS5 PROXY (TRÁNH CAPTCHA) =====
            from modules.free_proxy import FreeProxyManager
            from core.network import parse_proxy_string, _connect_socks5
            
            # (Người dùng yêu cầu sử dụng IP chính khi chạy kịch bản)
            proxy_str = None
            connected = False
            
            if proxy_str:
                try:
                    proxy_info = parse_proxy_string(proxy_str)
                    safe_print(f'[Proxy {self.session_id}] 🌐 [SESSION] Kết nối qua Proxy: {proxy_info["host"]}:{proxy_info["port"]}')
                    self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.server_sock.settimeout(5.0)
                    self.server_sock.connect((proxy_info["host"], proxy_info["port"]))
                    
                    ok = _connect_socks5(self.server_sock, dest_host, dest_port, 
                                         user=proxy_info.get("user"), password=proxy_info.get("password"))
                    if ok:
                        self.server_sock.settimeout(None)
                        connected = True
                        self._current_proxy = proxy_str
                        safe_print(f"[Proxy {self.session_id}] ✅ [SESSION] Connected to {dest_host}:{dest_port} via Proxy")
                    else:
                        safe_print(f"[Proxy {self.session_id}] ⚠️ [SESSION] Proxy SOCKS5 handshake thất bại, đổi sang IP thật...")
                        FreeProxyManager.mark_proxy_dead(proxy_str)
                        try: self.server_sock.close()
                        except: pass
                        self.server_sock = None
                except Exception as e:
                    safe_print(f"[Proxy {self.session_id}] ⚠️ [SESSION] Proxy lỗi ({e}), đổi sang IP thật...")
                    FreeProxyManager.mark_proxy_dead(proxy_str)
                    try: self.server_sock.close()
                    except: pass
                    self.server_sock = None
            
            if not connected:
                # Fallback: Kết nối trực tiếp (IP thật)
                safe_print(f'[Proxy {self.session_id}] [SESSION] Sử dụng IP thật (Bypass Proxy)...')
                self._current_proxy = None
                self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.server_sock.settimeout(10.0)
                self.server_sock.connect((dest_host, dest_port))
                self.server_sock.settimeout(None)
            
            safe_print(f"[Proxy {self.session_id}] ✅ [SESSION] Connected to {dest_host}:{dest_port}")
            
            initial_data = self._rewrite_client_actor_enter(initial_data)
            self.server_sock.sendall(initial_data)
            safe_print(f"[Proxy {self.session_id}] 📤 [SESSION] Forwarded initial packet to server")

            self.client_sock.settimeout(None)
            threading.Thread(target=self._duplex_loop, daemon=True).start()
            safe_print(f"[Proxy {self.session_id}] ✅ [SESSION] Duplex loop started for {dest_name}.")
        except socket.timeout:
            safe_print(f"[Proxy {self.session_id}] ❌ [SESSION] Timeout connecting to server")
            self.stop()
        except ConnectionRefusedError:
            safe_print(f"[Proxy {self.session_id}] ❌ [SESSION] Connection refused by server ({dest_host}:{dest_port})")
            self.stop()
        except Exception as e:
            safe_print(f"[Proxy {self.session_id}] ❌ [SESSION] Error: {type(e).__name__}: {e}")
            self.stop()

    def _duplex_loop(self):
        socks = [self.client_sock, self.server_sock]
        try:
            while self.is_running:
                readable, _, errored = select.select(socks, [], socks, 1.0)
                # FIX: Xử lý socket lỗi (OOB data, connection reset)
                if errored:
                    safe_print(f"[Proxy {self.session_id}] ❌ [DUPLEX] Socket error detected, closing session")
                    break
                for s in readable:
                    if s is self.client_sock:
                        data = self.client_sock.recv(4096)
                        if not data:
                            self.stop()
                            return
                        data = self._rewrite_client_actor_enter(data)
                        self.server_sock.sendall(data)
                        self._analyze_client_data(data)
                    elif s is self.server_sock:
                        data = self.server_sock.recv(4096)
                        if not data:
                            self.stop()
                            return
                        self.client_sock.sendall(data)
                        self._analyze_server_data(data)
        except Exception as e:
            # FIX: Log exception thay vì nuốt, giúp debug khi mất kết nối
            safe_print(f"[Proxy {self.session_id}] ❌ [DUPLEX] Lỗi kết nối: {type(e).__name__}: {e}")
        finally:
            # Giải phóng proxy về pool khi session kết thúc
            if self._current_proxy:
                try:
                    from modules.free_proxy import FreeProxyManager
                    FreeProxyManager.release_proxy(self._current_proxy)
                except Exception:
                    pass
            self.stop()

    def _analyze_client_data(self, data: bytes):
        """Phân tích dữ liệu từ Client giả lập gửi lên Server (bắt gói 0x30D6 khi đánh xong quái)"""
        with self._client_lock:
            self.recent_client_packets.append(data)
            if len(self.recent_client_packets) > 100:
                self.recent_client_packets.pop(0)

        try:
            offset = 0
            while offset + 4 <= len(data):
                pkt_len = (data[offset] << 8) | data[offset + 1]
                if pkt_len < 4 or offset + pkt_len > len(data):
                    break
                msg_type = (data[offset + 2] << 8) | data[offset + 3]
                
                if msg_type == proto.LC_PLAYER_LOGIN:
                    msg = Message(msg_type, data[offset + 4 : offset + pkt_len])
                    if msg.hasRemaining():
                        username = msg.getString()
                        if self.hub:
                            self.runner = self.hub.find_runner_for_acc(username)
                            if self.runner:
                                safe_print(f"[Proxy {self.session_id}] 🔗 [MASTER] Ánh xạ Master Session cho tài khoản: {username}")
                elif msg_type == proto.CG_LOGIN_PLAYERCHECK:
                    msg = Message(msg_type, data[offset + 4 : offset + pkt_len])
                    if msg.hasRemaining():
                        ukey = msg.getLong()
                        session_id = msg.getInt()
                        if self.hub:
                            self.runner = self.hub.ukey_to_runner.get(session_id)
                            safe_print(f"[Proxy {self.session_id}] 🔍 [DEBUG] Extracted Game SessionID in Duplex: {session_id}. Found runner? {self.runner is not None}")
                            if self.runner:
                                safe_print(f"[Proxy {self.session_id}] 🔗 [GAME] Ánh xạ Game Session cho tài khoản: {getattr(self.runner, 'current_acc_name', 'Unknown')} (SessionID: {session_id})")
                                self.runner.active_session = self
                elif msg_type == 0x0002: # CG_LOGIN_ACTORENTER
                    if pkt_len >= 8:
                        char_id = int.from_bytes(data[offset + 4 : offset + 8], "big")
                        if self.runner:
                            self.runner.user_selected_char_id = char_id
                            try:
                                from modules.character_registry import CharacterRegistry
                                from core.config import settings
                                CharacterRegistry.set_last_char_id(settings.game_server_name, getattr(self.runner, "current_acc_name", ""), char_id)
                            except Exception:
                                pass
                            if hasattr(self.runner, "character_entered_world_event"):
                                self.runner.character_entered_world_event.set()
                elif msg_type in (0x2907, 0x2909, 0x138A, 0x2EEA, 0x2AFC, 0x2EE1, 0x38A6, 0x38A7, 0x30D7):
                    if self.runner and hasattr(self.runner, "character_entered_world_event"):
                        self.runner.character_entered_world_event.set()

                if msg_type in (0x30D5, 0x30D7):
                    self.is_client_in_battle = True
                elif msg_type == 0x30D6:
                    self.is_client_in_battle = False
                    self.battle_finished_event.set()
                    if self.runner:
                        safe_print(f"{self.runner._tag} ⚔️ [CLIENT -> SERVER] Game Client trên giả lập đã đánh xong và gửi kết quả chiến đấu (0x30D6)!")
                offset += pkt_len
        except Exception:
            pass

    def _analyze_server_data(self, data: bytes):
        """Phân tích dữ liệu Server gửi về (Cập nhật Stats, Báo lỗi, Kết thúc trận đấu)"""
        with self._server_lock:
            self.recent_server_packets.append(data)
            if len(self.recent_server_packets) > 100:
                self.recent_server_packets.pop(0)

        # Trích xuất ukey từ Master Server Response (0x138A)
        if self.is_master and self.runner and self.hub:
            try:
                offset = 0
                while offset + 4 <= len(data):
                    pkt_len = (data[offset] << 8) | data[offset + 1]
                    if pkt_len < 4 or offset + pkt_len > len(data):
                        break
                    msg_type = (data[offset + 2] << 8) | data[offset + 3]
                    if msg_type == proto.LC_PLAYER_LOGIN:
                        msg = Message(msg_type, data[offset + 4 : offset + pkt_len])
                        if msg.hasRemaining():
                            code = msg.getByte()
                            if code == 0:
                                ukey = msg.getLong()
                                session_id = msg.getInt()
                                if self.is_master and self.runner and self.hub:
                                    self.hub.ukey_to_runner[session_id] = self.runner
                                    safe_print(f"[Proxy {self.session_id}] 🔑 Bắt được SessionID Master Login: {session_id} -> Map với luồng ADB")
                    offset += pkt_len
            except Exception:
                pass

        # Bắt phản hồi kết thúc chiến đấu từ Server (0x30D6 kết quả Local Battle, 0x30D8 kết thúc Remote Battle)
        # Và bắt lỗi 0x30D7/0x30D5 (server từ chối tạo trận đấu)
        try:
            offset = 0
            while offset + 4 <= len(data):
                pkt_len = (data[offset] << 8) | data[offset + 1]
                if pkt_len < 4 or offset + pkt_len > len(data):
                    break
                msg_type = (data[offset + 2] << 8) | data[offset + 3]
                payload = data[offset + 4 : offset + pkt_len]
                
                if msg_type == 0x2B29:
                    self._parse_player_stats(payload)
                elif msg_type == 0x2711:
                    self._parse_actor_list(payload)
                elif msg_type == 0x2EE3:
                    self.bag_bytes = payload
                    if self.runner and hasattr(self.runner, "character_entered_world_event"):
                        self.runner.character_entered_world_event.set()
                elif msg_type == 0x2EE4:
                    if self.runner and hasattr(self.runner, "character_entered_world_event"):
                        self.runner.character_entered_world_event.set()
                elif msg_type in (0x290A, 0x0004, 0x38A5, 0x38A8):
                    if self.runner and hasattr(self.runner, "character_entered_world_event"):
                        self.runner.character_entered_world_event.set()
                elif msg_type == 0x30D6:
                    self.is_client_in_battle = False
                    self.battle_finished_event.set()
                    if self.runner:
                        if payload and payload[0] < 128:
                            safe_print(f"{self.runner._tag} ★ [SERVER] Đã công nhận chiến thắng (0x30D6: Thành công) ★")
                        elif payload and payload[0] >= 128:
                            safe_print(f"{self.runner._tag} ⚠️ [SERVER] Server không công nhận kết quả 0x30D6")
                elif msg_type == 0x30D8:
                    # 0x30D8 là Server gửi BATTLE MODEL (Bắt đầu trận đấu), KHÔNG PHẢI KẾT THÚC!
                    self.is_client_in_battle = True
                    if self.runner:
                        safe_print(f"{self.runner._tag} ⚔️ [SERVER] Server đã tạo trận đấu (0x30D8 Battle Notify)!")
                
                # Bắt thông báo lỗi 0x0005 (CG_ERROR_NOTIFY) từ Server (ví dụ: nhóm quái không ở map, npc không có quest...)
                elif msg_type == 0x0005:
                    err_str = ""
                    try:
                        if len(payload) >= 3:
                            s_len = (payload[1] << 8) | payload[2]
                            if len(payload) >= 3 + s_len:
                                err_str = payload[3 : 3 + s_len].decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                    if err_str:
                        lower_err = err_str.lower()
                        if self.runner:
                            safe_print(f"{self.runner._tag} 📢 [SERVER THÔNG BÁO] {err_str}")
                        # Luôn hủy chờ trận nếu nhận được 0x0005 (Error Notify) trong lúc đang chờ hoặc đang trong trận
                        self.is_client_in_battle = False
                        self.battle_finished_event.set()
                        if self.runner:
                            safe_print(f"{self.runner._tag} ⏩ Server báo lỗi '{err_str}' -> Tự động hủy chờ trận & đóng popup!")
                            if hasattr(self.runner, "adb"):
                                self.runner.adb.dismiss_popup(delay=0.3)

                # Bắt thông báo 0x290D (ShowTextMsg) từ Server
                elif msg_type == 0x290D:
                    text_str = ""
                    try:
                        if len(payload) >= 3:
                            s_len = (payload[1] << 8) | payload[2]
                            if len(payload) >= 3 + s_len:
                                text_str = payload[3 : 3 + s_len].decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                    if text_str:
                        lower_text = text_str.lower()
                        if self.runner:
                            safe_print(f"{self.runner._tag} 💬 [SERVER TEXT] {text_str}")
                        # Tương tự với 0x290D (ShowTextMsg), hủy chờ trận nếu có thông báo
                        self.is_client_in_battle = False
                        self.battle_finished_event.set()
                        if self.runner:
                            safe_print(f"{self.runner._tag} ⏩ Server báo '{text_str}' -> Hủy chờ trận đấu!")
                            if hasattr(self.runner, "adb"):
                                self.runner.adb.dismiss_popup(delay=0.3)
                
                offset += pkt_len
        except Exception:
            pass

    def _parse_player_stats(self, payload: bytes):
        try:
            msg = Message(0x2B29, payload)
            if not msg.hasRemaining():
                return
            res_code = msg.getByte()
            p_id = msg.getInt()
            p_name = msg.getString()
            p_info = msg.getString()
            p_title = msg.getString()
            p_sex = msg.getByte()
            p_race = msg.getByte()
            p_job = msg.getByte()
            p_lvl = msg.getByte()
            p_lvl2 = msg.getByte()
            msg.getInt(); msg.getInt()  # exp2, expMax2
            msg.getInt(); msg.getInt(); msg.getInt()  # icons
            msg.getInt(); msg.getInt()  # setting, status
            msg.getByte()  # mode
            cid = msg.getInt()
            if cid >= 0:
                msg.getString()
                msg.getByte()
            exp = msg.getInt()
            exp_max = msg.getInt()
            cur_hp = msg.getInt()
            cur_mp = msg.getInt()

            if self.runner:
                if hasattr(self.runner, "current_char_info") and self.runner.current_char_info:
                    self.runner.current_char_info["hp"] = cur_hp
                    self.runner.current_char_info["mp"] = cur_mp
                    self.runner.current_char_info["level"] = p_lvl
                self.runner._last_stats_sync_time = time.time()
                safe_print(f"{self.runner._tag} 📊 [STATS] Nhân vật '\033[96m{p_name}\033[0m' (Lv.{p_lvl}): HP=\033[92m{cur_hp}\033[0m, MP=\033[94m{cur_mp}\033[0m")
                
                # Cập nhật registry
                if getattr(self.runner, "current_acc_name", ""):
                    try:
                        from modules.character_registry import CharacterRegistry
                        CharacterRegistry.update_character_stats(
                            settings.game_server_name,
                            self.runner.current_acc_name,
                            self.runner.current_char_idx - 1,
                            hp=cur_hp,
                            mp=cur_mp,
                            level=p_lvl,
                            exp=exp
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    def _parse_actor_list(self, payload: bytes):
        try:
            msg = Message(0x2711, payload)
            char_count = msg.getByte()
            chars = []
            for idx in range(char_count):
                char_id = msg.getInt()
                name = msg.getString()
                sex = msg.getByte()
                race = msg.getByte()
                job = msg.getByte()
                level = msg.getByte()
                vip = msg.getByte()
                map_name = msg.getString()
                icon1 = msg.getInt()
                icon2 = msg.getInt()
                icon3 = msg.getInt()
                status = msg.getInt()
                if status & 4 != 0:
                    msg.getShort()
                if status & 512 != 0:
                    is_off = msg.getBoolean()
                    if not is_off:
                        msg.getShort()
                        msg.getString()
                chars.append({
                    "slot": idx + 1,
                    "id": char_id,
                    "name": name,
                    "level": level,
                    "job": job,
                    "map": map_name
                })
            if chars and self.runner:
                self.runner.account_characters = chars
                summary = " | ".join([f"Slot {c['slot']}: \033[96m{c['name']}\033[0m (Lv.{c['level']})" for c in chars])
                safe_print(f"{self.runner._tag} 📋 Server danh sách NV: {summary}")
                
                # Cập nhật CharacterRegistry
                if getattr(self.runner, "current_acc_name", ""):
                    try:
                        from modules.character_registry import CharacterRegistry
                        from modules.auth import CharacterInfo
                        char_objs = [
                            CharacterInfo(c["id"], c["name"], 1, 0, c["job"], c["level"], 0, c["map"])
                            for c in chars
                        ]
                        CharacterRegistry.register_characters(settings.game_server_name, self.runner.current_acc_name, char_objs)
                    except Exception:
                        pass
        except Exception:
            pass

    def get_recent_packets(self) -> List[bytes]:
        """Thread-safe copy of recent server packets."""
        with self._server_lock:
            pkts = list(self.recent_server_packets)
            return pkts

    def clear_recent_packets(self):
        """Thread-safe clear of recent server packets."""
        with self._server_lock:
            self.recent_server_packets.clear()

    def inject_to_server(self, wire_packet: bytes) -> bool:
        """
        Bơm gói tin lên Game Server. 
        QUAN TRỌNG: wire_packet phải đã có đầy đủ header [2 bytes total_len][2 bytes msg_type][payload].
        Sử dụng build_wire_packet() hoặc build_packet_from_params() để tạo.
        """
        if self.is_running and self.server_sock:
            try:
                self.server_sock.sendall(wire_packet)
                return True
            except Exception:
                pass
        return False

    def inject_to_client(self, wire_packet: bytes) -> bool:
        """
        Bơm gói tin xuống Game Client.
        Ép Game Client thực hiện hành động do Server gửi (vd: vào trận chiến đấu 0x30D5).
        """
        if self.is_running and self.client_sock:
            try:
                self.client_sock.sendall(wire_packet)
                return True
            except Exception:
                pass
        return False

    def stop(self):
        self.is_running = False
        if self.client_sock:
            try: self.client_sock.close()
            except: pass
        if self.server_sock:
            try: self.server_sock.close()
            except: pass


class ScriptRunnerThread:
    """Quản lý kịch bản tự động hóa cho 1 giả lập LDPlayer cụ thể"""
    def __init__(self, emu_id: int, device_name: str, listen_port: int):
        super().__init__()
        self.emu_id = emu_id
        self.device_name = device_name
        self.adb = ADBController(device_name=device_name)
        self.listen_port = listen_port  # Giữ lại để code ngoài không lỗi, nhưng không dùng
        self.is_running = False
        
        import subprocess
        for p in [10001, 10002, 10003]:
            subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', device_name, 'reverse', f'tcp:{p}', f'tcp:{p}'], capture_output=True)
        subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', device_name, 'shell', f'su 0 iptables -t nat -A OUTPUT -p tcp -d 123.30.142.219 --dport 30000 -j DNAT --to-destination 10.0.2.2:{listen_port}'], capture_output=True)
        subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', device_name, 'shell', f'su 0 iptables -t nat -A OUTPUT -p tcp -d 123.30.142.163 --dport 10010 -j DNAT --to-destination 10.0.2.2:{listen_port}'], capture_output=True)
        
        self.hub = GlobalProxyHub.get_instance()
        self.hub.register_runner(self)

        self.account_list = []
        self.target_slots = [1, 2, 3]
        self.script_data = []
        self.script_path: str = ""
        self.script_title: str = ""
        self.max_level: Optional[int] = None
        self.account_characters: List[dict] = []
        self.current_acc_name: str = ""
        self.last_played_char_id: Optional[int] = None
        self.is_paused = False
        self.current_step = 0
        self.current_acc_idx = 0
        self.current_char_idx = 1
        self.last_battle_end_time: float = 0.0
        self.battle_start_time: float = 0.0
        self._last_stats_sync_time: float = 0.0
        self.active_session: Optional[EmulatorProxySession] = None

    def _query_last_player_id(self) -> Optional[int]:
        """Truy vấn lastPlayerID từ SQLite recordstoredb trên giả lập (nếu có)"""
        try:
            cmd = "su 0 sqlite3 /data/data/cmn.phongvan.truyenky.v3.ss/databases/recordstoredb \"SELECT HEX(bytes) FROM record WHERE record_number=1;\""
            hex_str = self.adb._run_cmd(["-s", self.adb.device, "shell", cmd]).strip()
            if hex_str and len(hex_str) >= 40:
                raw_bytes = bytes.fromhex(hex_str)
                if self.account_characters:
                    known_ids = {c["id"] for c in self.account_characters}
                    for i in range(len(raw_bytes) - 4):
                        val = int.from_bytes(raw_bytes[i:i+4], "big")
                        if val in known_ids:
                            return val
        except Exception:
            pass
        return None

    @property
    def _tag(self) -> str:
        """Prefix log chuẩn cho mỗi giả lập + nhân vật đang chạy"""
        return f"[EMU-{self.emu_id} SLOT#{self.current_char_idx}]"

    def load_script(self, filepath: str):
        self.script_path = filepath
        self.script_title = ""
        self.max_level = None
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.script_title = data.get("title", os.path.basename(filepath))
                self.script_data = data.get("steps", [])
                self.max_level = data.get("max_level")
                # Nếu là kịch bản lv12 hoặc slot_01 mà chưa có max_level, mặc định đặt là 15 theo yêu cầu
                if self.max_level is None and ("12" in self.script_title.lower() or "slot_01" in filepath.lower()):
                    self.max_level = 15
            elif isinstance(data, list):
                self.script_data = data
                if "12" in filepath.lower() or "slot_01" in filepath.lower():
                    self.max_level = 15
            else:
                self.script_data = []
            
            cond_str = f" [Điều kiện: \033[93mLevel < {self.max_level}\033[0m]" if self.max_level else ""
            safe_print(f"{self._tag} 📜 Đã tải {len(self.script_data)} bước kịch bản: '{self.script_title}'{cond_str}.")
            
    def load_accounts(self, filepath: str):
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                self.account_list = json.load(f)
                safe_print(f"{self._tag} 👥 Đã tải {len(self.account_list)} tài khoản.")

    def get_character_info(self, slot_idx: int, acc_name: str) -> Optional[dict]:
        """
        Lấy thông tin nhân vật tại slot (1-indexed).
        Ưu tiên:
        1. Dữ liệu thời gian thực từ gói tin 0x2711 của Game Server (self.account_characters)
        2. Dữ liệu từ CharacterRegistry
        """
        res = None
        # 1. Từ live packets
        if self.account_characters:
            for c in self.account_characters:
                if c.get("slot") == slot_idx:
                    res = dict(c)
                    break
        
        # 2. Từ CharacterRegistry
        if not res:
            try:
                from modules.character_registry import CharacterRegistry
                c_info = CharacterRegistry.get_character_at_slot(settings.game_server_name, acc_name, slot_idx - 1)
                if c_info:
                    res = {
                        "slot": slot_idx,
                        "id": c_info.get("id", 0),
                        "name": c_info.get("name", f"Slot #{slot_idx}"),
                        "level": c_info.get("level", 1),
                        "job": c_info.get("job_id", 1),
                        "map": c_info.get("map", "")
                    }
            except Exception:
                pass
                
        if res:
            res["acc_name"] = acc_name
            if "hp" not in res:
                res["hp"] = 0
            if "mp" not in res:
                res["mp"] = 0
            # Nạp cached hp/mp từ registry nếu có
            try:
                from modules.character_registry import CharacterRegistry
                cached_stats = CharacterRegistry.get_character_stats(settings.game_server_name, acc_name, slot_idx - 1)
                if cached_stats.get("hp", 0) > 0 and res.get("hp", 0) <= 0:
                    res["hp"] = cached_stats["hp"]
                if cached_stats.get("mp", 0) > 0 and res.get("mp", 0) <= 0:
                    res["mp"] = cached_stats["mp"]
            except Exception:
                pass
        return res
        
    def _perform_smart_equip(self, current_level: int):
        if not getattr(self, "active_session", None) or not self.active_session.bag_bytes:
            safe_print(f"{self._tag} ⚠️ [SMART EQUIP] Không có dữ liệu hành trang (chưa nhận được 0x2EE3).")
            return
            
        with self.smart_equip_lock:
            bag = self.active_session.bag_bytes
            safe_print(f"{self._tag} 🔎 [SMART EQUIP] Bắt đầu quét hành trang để tìm đồ tốt nhất...")
            
            # 1. Dùng Trứng Tân Thủ Thương Ưng
            egg_word = "Trứng Tân Thủ Thương Ưng".encode('utf-8')
            egg_idx = bag.find(egg_word)
            if egg_idx != -1:
                slot_pos = -1
                for offset_len in (11, 13, 17):
                    if egg_idx >= offset_len:
                        s_pos = int.from_bytes(bag[egg_idx - offset_len : egg_idx - offset_len + 2], 'big')
                        if 0 <= s_pos < 150:
                            slot_pos = s_pos
                            break
                if slot_pos != -1:
                    safe_print(f"{self._tag} 🥚 [SMART EQUIP] Tìm thấy '{egg_word.decode('utf-8')}' tại slot {slot_pos}. Đang dùng...")
                    use_pkt = build_wire_packet(0x2EE4, struct.pack(">H", slot_pos))
                    if use_pkt:
                        self.active_session.client_sock.sendall(use_pkt)
                        self.active_session.server_sock.sendall(use_pkt)
                        time.sleep(1.0)
            
            # 2. Quét các trang bị khác
            equip_categories = {
                "Kiếm": 2,          # Vũ khí (Ưu tiên 2 thanh kiếm)
                "Mũ": 1,            # Mũ
                "Nón": 1,           # Mũ (tên khác)
                "Vai": 1,           # Vai
                "Giáp": 1,          # Áo
                "Áo": 1,            # Áo (tên khác)
                "Quần": 1,          # Quần
                "Giày": 1,          # Giày
                "Hài": 1,           # Giày (tên khác)
                "Dây Chuyền": 1,    # Dây chuyền
                "Sợi Dây": 1,       # Dây chuyền (tên khác)
                "Nhẫn": 2,          # Nhẫn (Tối đa 2 chiếc)
            }
            
            # Parse toàn bộ items matching keywords
            found_items = {k: [] for k in equip_categories.keys()}
            
            for keyword in equip_categories.keys():
                kw_bytes = keyword.encode('utf-8')
                idx = 0
                while True:
                    idx = bag.find(kw_bytes, idx)
                    if idx == -1:
                        break
                    slot_pos = -1
                    item_id = -1
                    for offset_len in (11, 13, 17):
                        if idx >= offset_len:
                            s_pos = int.from_bytes(bag[idx - offset_len : idx - offset_len + 2], 'big')
                            if 0 <= s_pos < 150:
                                slot_pos = s_pos
                                item_id = int.from_bytes(bag[idx - 4 : idx - 2], 'big')
                                break
                    if slot_pos != -1 and item_id != -1:
                        # --- TÌM TÊN ITEM THỰC SỰ ĐỌC LEVEL ---
                        valid_name = keyword
                        for i in range(idx, max(2, idx-30), -1):
                            slen = int.from_bytes(bag[i-2:i], 'big')
                            if 2 <= slen <= 60:
                                try:
                                    temp_name = bag[i : i+slen].decode('utf-8')
                                    if keyword in temp_name and temp_name.isprintable():
                                        valid_name = temp_name
                                        break
                                except:
                                    pass
                                    
                        full_name = valid_name
                        
                        # Trích xuất Level từ full_name (vd: "Song Kiếm (Lv15)")
                        item_lvl = 0
                        import re
                        m = re.search(r'(?i)(?:lv|cấp)\s*(\d+)', full_name)
                        if m:
                            item_lvl = int(m.group(1))
                            
                        found_items[keyword].append((item_id, slot_pos, full_name, item_lvl))
                    idx += len(kw_bytes)
                    
            # --- START GEAR MEMORY ---
            all_found_gear = []
            for k, items_list in found_items.items():
                all_found_gear.extend(items_list)
                
            if not hasattr(self, 'gear_history'):
                self.gear_history = []
                for item in sorted(all_found_gear, key=lambda x: x[1], reverse=True):
                    sig = (item[0], item[2])
                    if sig not in self.gear_history:
                        self.gear_history.append(sig)
                safe_print(f"{self._tag} 🧠 [MEMORY] Khởi tạo trí nhớ với {len(self.gear_history)} trang bị có sẵn.")
            else:
                new_items = []
                for item in sorted(all_found_gear, key=lambda x: x[1], reverse=True):
                    sig = (item[0], item[2])
                    if sig not in self.gear_history:
                        new_items.append(sig)
                if new_items:
                    for sig in new_items:
                        self.gear_history.append(sig)
                    safe_print(f"{self._tag} 🧠 [MEMORY] Ghi nhớ {len(new_items)} trang bị mới nhận được!")

            def get_history_index(it):
                sig = (it[0], it[2])
                if sig in getattr(self, 'gear_history', []):
                    return self.gear_history.index(sig)
                return -1
            # --- END GEAR MEMORY ---

            # 3. Equip the best items in each category
            for keyword, max_count in equip_categories.items():
                items_list = found_items[keyword]
                if not items_list:
                    continue
                unique_items = []
                seen_slots = set()
                
                # Uu tien LEVEL cao nhat truoc, sau do moi den History Index (item moi nhan)
                def sort_key(it):
                    return (it[3], get_history_index(it))
                    
                for item_id, slot_pos, full_name, item_lvl in sorted(items_list, key=sort_key, reverse=True):
                    if slot_pos not in seen_slots:
                        seen_slots.add(slot_pos)
                        unique_items.append((item_id, slot_pos, full_name, item_lvl))
                        
                if not unique_items:
                    continue
                    
                safe_print(f"{self._tag} [SMART EQUIP] Tim thay {len(unique_items)} mon nhom '{keyword}'. Uu tien Lv cao & Moi nhan.")
                
                equipped_count = 0
                for item_id, slot_pos, full_name, item_lvl in unique_items:
                    if equipped_count >= max_count:
                        break
                    
                    equip_pos = 255  # 0xFF = Auto Equip by Server
                    if max_count == 2 and equipped_count == 1:
                        if keyword == "Kiếm":
                            equip_pos = 1  # Force Left Hand for second sword
                        # For rings, 255 usually auto-fills the empty slot or swaps correctly
                    
                    safe_print(f"{self._tag} 🔄 [SMART EQUIP] Thử trang bị '{full_name}' (Slot {slot_pos})...")
                    
                    with self.active_session._server_lock:
                        start_log_idx = len(self.active_session.recent_server_packets)
                        
                    equip_pkt = build_packet_from_params("equip_item", {"action_type": 1, "slot_pos": slot_pos, "equip_pos": equip_pos, "wait_type": 0})
                    if equip_pkt:
                        self.active_session.server_sock.sendall(equip_pkt)
                        time.sleep(1.0)
                        
                        has_error = False
                        with self.active_session._server_lock:
                            new_pkts = self.active_session.recent_server_packets[start_log_idx:]
                            for p in new_pkts:
                                if len(p) >= 4:
                                    m_type = (p[2] << 8) | p[3]
                                    if m_type in (0x0005, 0x290D):
                                        has_error = True
                                        break
                        if has_error:
                            safe_print(f"{self._tag} ❌ [SMART EQUIP] Không thể trang bị '{full_name}'.")
                        else:
                            safe_print(f"{self._tag} ✅ [SMART EQUIP] Trang bị thành công '{full_name}'!")
                            equipped_count += 1
                            time.sleep(0.5)

    def run_automation_loop(self):
        """Vòng lặp chính tự động hóa: Đăng nhập -> Chọn NV -> Chạy Script -> Đổi NV -> Đổi Acc"""
        self.is_running = True
        self.adb.connect()
        
        while self.is_running and self.current_acc_idx < len(self.account_list):
            acc = self.account_list[self.current_acc_idx]
            acc_name = acc[0] if isinstance(acc, (list, tuple)) else str(acc)
            self.current_acc_name = acc_name
            
            # Check if all characters in the account are >= 15 based on saved local cache
            from modules.character_registry import CharacterRegistry
            from core.config import settings
            
            # Use max level condition from the script definition if available, otherwise 15
            limit_lvl = 15
            if hasattr(self, "script_conditions"):
                for cond in self.script_conditions:
                    if cond["type"] == "LEVEL_LT":
                        limit_lvl = cond["value"]
                        break
                        
            if CharacterRegistry.is_account_fully_completed(settings.game_server_name, acc_name, limit_lvl):
                safe_print(f"\n{'='*70}")
                safe_print(f"{self._tag} ⏩ Bỏ qua tài khoản: \033[92m{acc_name}\033[0m (Tất cả nhân vật đã >= Lv.{limit_lvl} trong bộ nhớ cache)")
                safe_print(f"{'='*70}")
                self.current_acc_idx += 1
                continue
            
            # 1. Đăng nhập Tài khoản (Chỉ làm 1 lần cho cả 3 nhân vật)
            safe_print(f"\n{'='*70}")
            safe_print(f"{self._tag} 🔐 Đăng nhập tài khoản: \033[92m{acc_name}\033[0m (Acc {self.current_acc_idx+1}/{len(self.account_list)})")
            safe_print(f"{'='*70}")
            
            login_success = auto_login_account(self.adb, acc)
            if not login_success:
                safe_print(f"{self._tag} ❌ Đăng nhập tài khoản thất bại. Khởi động lại Game để thử lại...")
                try:
                    self.adb.tap(270, 400, delay=1.0)
                    time.sleep(2.0)
                except Exception:
                    pass
                login_success = auto_login_account(self.adb, acc)
                
            if not login_success:
                safe_print(f"{self._tag} ❌ Đăng nhập tài khoản vẫn thất bại. Bỏ qua tài khoản này.")
                self.current_acc_idx += 1
                continue
                
            safe_print(f"{self._tag} ⏳ Chờ kết nối tới Server chọn nhân vật (tối đa 30s)...")
            game_session_connected = False
            for _ in range(30):
                if getattr(self, "active_session", None) and getattr(self.active_session, "is_running", False):
                    game_session_connected = True
                    break
                time.sleep(1.0)
            
            if not game_session_connected:
                safe_print(f"{self._tag} ❌ Không thể kết nối tới Server chọn nhân vật qua Proxy (Timeout). Khởi động lại Game và bỏ qua tài khoản này.")
                # Thay vì đóng/mở game, chỉ cần nhấp màn hình 1 phát để tắt popup báo lỗi (nếu có) rồi đăng nhập lại
                try:
                    self.adb.tap(270, 400, delay=1.0)
                except Exception:
                    pass
                self.current_acc_idx += 1
                continue
            
            is_at_login_screen = False
            for slot_idx in self.target_slots:
                if not self.is_running:
                    break
                self.current_char_idx = slot_idx
                
                # Check max_level
                if self.max_level is not None:
                    try:
                        from modules.character_registry import CharacterRegistry
                        from core.config import settings
                        c_info = CharacterRegistry.get_character_at_slot(settings.game_server_name, self.current_acc_name, self.current_char_idx - 1)
                        c_lvl = c_info.get("level", 0) if c_info else 0
                        if c_lvl >= self.max_level:
                            safe_print(f"{self._tag} ⏩ Nhân vật {self.current_acc_name} Slot #{self.current_char_idx} đạt Lv.{c_lvl} (>= {self.max_level}). Bỏ qua!")
                            continue
                            
                        # Ép Proxy ghi đè Char ID (đảm bảo an toàn 100% server login đúng nhân vật)
                        if self.active_session and c_info and c_info.get("id"):
                            self.active_session.target_char_id = c_info["id"]
                            self.active_session.target_char_name = c_info.get("name", "")
                            
                        # Truy xuất danh sách nhân vật để tính toán vị trí trên màn hình
                        chars = CharacterRegistry.get_characters(settings.game_server_name, self.current_acc_name)
                    except Exception as e:
                        chars = []
                        
                # 2. Chọn Nhân vật
                safe_print(f"{self._tag} 🎯 Chọn nhân vật Slot #{slot_idx}...")
                target_char_id = self.active_session.target_char_id if self.active_session else None
                success = auto_select_character(
                    self.adb, 
                    char_index=self.current_char_idx, 
                    target_char_id=target_char_id,
                    all_chars=list(chars) if chars else None,
                    last_char_id=None
                )
                if not success:
                    safe_print(f"{self._tag} ❌ Chọn nhân vật slot #{self.current_char_idx} thất bại.")
                    continue
                    
                # 3. Chờ kết nối proxy vào Thế Giới Game (tối đa 15 giây)
                safe_print(f"{self._tag} ⏳ Đợi nhân vật slot #{self.current_char_idx} vào thế giới game...")
                connected = False
                for _ in range(15):
                    if self.active_session and self.active_session.is_running:
                        connected = True
                        break
                    time.sleep(1.0)
                
                if not connected:
                    safe_print(f"{self._tag} ❌ Không bắt được kết nối Proxy Game World. Bỏ qua nhân vật này và khôi phục giả lập...")
                    try:
                        self.adb.tap(270, 400, delay=1.0)
                        auto_logout_account(self.adb)
                        time.sleep(2.0)
                        auto_login_account(self.adb, acc)
                    except Exception:
                        pass
                    continue
                
                safe_print(f"{self._tag} ✅ Đã kết nối Game World thành công!")
                
                # Chờ thêm 2s để server hoàn tất khởi tạo session cho nhân vật
                time.sleep(2.0)
                    
                # 4. Chạy Kịch Bản
                safe_print(f"{self._tag} 🚀 Bắt đầu thực thi {len(self.script_data)} bước kịch bản...")
                self.current_step = 0
                self._execute_script_sequence()
                
                # Khôi phục nếu mất kết nối giữa chừng
                if not getattr(self, "active_session", None) or not getattr(self.active_session, "is_running", False):
                    safe_print(f"{self._tag} ❌ Kịch bản bị gián đoạn do mất kết nối. Đang khôi phục giả lập để chạy nhân vật tiếp theo...")
                    # Thay vì đóng/mở game, chỉ cần nhấp màn hình 1 phát để tắt popup báo lỗi (nếu có)
                    try:
                        self.adb.tap(270, 400, delay=1.0)
                        auto_logout_account(self.adb)
                        time.sleep(2.0)
                        auto_login_account(self.adb, acc)
                    except Exception:
                        pass
                    continue
                
                # 5. Rời khỏi thế giới game
                if slot_idx != self.target_slots[-1]:
                    # Nếu còn slot tiếp theo, trở về màn hình Chọn Nhân Vật
                    auto_return_to_char_select(self.adb)
                    time.sleep(3.0)
                else:
                    # Nếu là slot cuối cùng của acc, rời game ra thẳng màn hình Đăng Nhập
                    auto_exit_game_world(self.adb)
                    is_at_login_screen = True
                    time.sleep(3.0)
                
            # Đã xong các nhân vật của 1 acc. Nếu chưa ở màn hình Đăng Nhập (do bị skip nhân vật cuối), thì đăng xuất
            if not is_at_login_screen:
                auto_logout_account(self.adb)
                time.sleep(2.0)
                
            self.current_acc_idx += 1
            
        safe_print(f"{self._tag} 🎉 Hoàn thành toàn bộ kịch bản cho tất cả tài khoản!")

    def _execute_script_sequence(self):
        """
        Thực thi kịch bản: Tái tạo gói tin đúng format từ params, 
        KHÔNG replay raw bytes. Có kiểm tra phản hồi từ Server.
        """
        total_steps = len(self.script_data)
        consecutive_no_response = 0
        
        while self.current_step < total_steps:
            while self.is_paused:
                time.sleep(1.0)
                
            if not self.active_session or not self.active_session.is_running:
                safe_print(f"{self._tag} ❌ Mất kết nối Proxy. Dừng kịch bản.")
                break
                
            # Check max_level during script
            if self.max_level is not None:
                try:
                    from modules.character_registry import CharacterRegistry
                    from core.config import settings
                    c_info = CharacterRegistry.get_character_at_slot(settings.game_server_name, self.current_acc_name, self.current_char_idx - 1)
                    c_lvl = c_info.get("level", 0) if c_info else 0
                    if c_lvl >= self.max_level:
                        safe_print(f"{self._tag} ⏩ Đã đạt cấp độ tối đa ({c_lvl} >= {self.max_level}). Dừng kịch bản!")
                        break
                except Exception:
                    pass
                    
            step = self.script_data[self.current_step]
            action = step.get("action", "unknown")
            params = step.get("params", {})
            raw_b64 = step.get("raw_hex_b64", "")
            step_opcode = step.get("opcode", "")
            desc = step.get("desc", f"Bước #{self.current_step+1}")
            delay = step.get("delay", 1.0)
            
            # Bước 2: Tái tạo gói tin từ params (ưu tiên)
            curr_bag_bytes = self.active_session.bag_bytes if self.active_session else b""
            
            # --- TÍNH NĂNG MỚI: Bỏ qua skill đã học ---
            if action == "learn_skill" and getattr(self, "current_acc_name", ""):
                try:
                    from modules.character_registry import CharacterRegistry
                    from core.config import settings
                    skill_id = params.get("skill_id", 0)
                    level = params.get("level", 1)
                    learned_skills = CharacterRegistry.get_learned_skills(settings.game_server_name, self.current_acc_name, self.current_char_idx - 1)
                    if str(skill_id) in learned_skills and learned_skills[str(skill_id)] >= level:
                        safe_print(f"{self._tag} ⏩ Bỏ qua bước {self.current_step+1}: Nhân vật đã học Kỹ năng #{skill_id} (Cấp >= {level})")
                        self.current_step += 1
                        continue
                except Exception:
                    pass
            # ------------------------------------------

            wire_packet = build_packet_from_params(action, params, raw_b64, step_opcode, char_info=c_info, bag_bytes=curr_bag_bytes)
            
            if wire_packet == b"SKIP_ACTION":
                self.current_step += 1
                continue
                
            # Bước 2: Fallback sang raw_hex_b64 (thêm length header)
            if wire_packet is None:
                wire_packet = fallback_from_raw_b64(raw_b64)
            
            if wire_packet is None:
                safe_print(f"{self._tag} ⚠️ Bước {self.current_step+1}/{total_steps}: Không thể tạo gói tin cho [{action}]. Bỏ qua.")
                self.current_step += 1
                continue
            
            # Log chi tiết
            opcode_hex = f"0x{(wire_packet[2] << 8 | wire_packet[3]):04X}" if len(wire_packet) >= 4 else "0x????"
            pkt_size = len(wire_packet)
            safe_print(f"{self._tag} 💉 ({self.current_step+1}/{total_steps}) [{action}] {desc} | Opcode={opcode_hex} Size={pkt_size}B")
            
            # Xóa buffer server trước khi inject để kiểm tra response mới
            self.active_session.clear_recent_packets()
            
            # Inject gói tin
            success = self.active_session.inject_to_server(wire_packet)
            if not success:
                safe_print(f"{self._tag} ❌ Gửi gói tin thất bại (socket lỗi). Dừng kịch bản.")
                break
            
            # Xử lý đặc biệt theo loại action
            if action in ["fight", "enter_remote_battle"]:
                # User yêu cầu: "tool vẫn phải chờ trận đấu 6s"
                # Cố định thời gian chờ trận đấu là 6 giây để APK xử lý xong
                safe_print(f"{self._tag}    ⏳ Đang chờ 7s để APK tự động xử lý trận đấu...")
                time.sleep(7.0)
                consecutive_no_response = 0
            elif action in ["accept_quest", "deliver_quest"]:
                # Kiểm tra server có phản hồi không
                has_response = self._check_server_response(timeout=2.0, action_desc=desc)
                if has_response:
                    consecutive_no_response = 0
                else:
                    consecutive_no_response += 1
            elif action == "change_map":
                # Map change cần chờ lâu hơn để tránh bug hiển thị (mất màu, mất NPC)
                has_response = self._check_server_response(timeout=3.0, action_desc=desc)
                time.sleep(5.0) # Đợi thêm 5 giây cho APK load map xong
                if has_response:
                    consecutive_no_response = 0
                else:
                    consecutive_no_response += 1
            elif action == "learn_skill":
                has_response = self._check_server_response(timeout=2.0, action_desc=desc)
                time.sleep(1.0)
                
                # --- TÍNH NĂNG MỚI: Đánh dấu đã học sau khi gửi gói tin ---
                if getattr(self, "current_acc_name", ""):
                    try:
                        from modules.character_registry import CharacterRegistry
                        from core.config import settings
                        skill_id = params.get("skill_id", 0)
                        level = params.get("level", 1)
                        CharacterRegistry.add_learned_skill(settings.game_server_name, self.current_acc_name, self.current_char_idx - 1, skill_id, level)
                    except Exception:
                        pass
                # -----------------------------------------------------------

                if has_response:
                    consecutive_no_response = 0
                else:
                    consecutive_no_response += 1
            else:
                # Các action khác: chờ delay bình thường
                actual_delay = max(0.5, min(float(delay), 5.0))
                time.sleep(actual_delay)
                consecutive_no_response = 0  # Don't track for non-critical actions
            
            # Cảnh báo nếu server liên tục không phản hồi
            if consecutive_no_response >= 5:
                safe_print(f"{self._tag} 🔴 CẢNH BÁO: Server không phản hồi {consecutive_no_response} bước liên tiếp!")
                safe_print(f"{self._tag} 🔴 Nhân vật có thể đang bị kẹt. Kiểm tra lại giả lập.")
                consecutive_no_response = 0  # Reset để tiếp tục thử
                
            self.current_step += 1

    def _check_server_response(self, timeout: float = 2.0, action_desc: str = "") -> bool:
        """
        Kiểm tra xem server có gửi phản hồi (bất kỳ gói tin nào) sau khi inject.
        Trả về True nếu có phản hồi, False nếu timeout.
        """
        start_t = time.time()
        while time.time() - start_t < timeout:
            if not self.active_session or not self.active_session.is_running:
                return False
            
            pkts = self.active_session.get_recent_packets()
            if pkts:
                # Có phản hồi từ server
                safe_print(f"{self._tag}    ✅ Server phản hồi ({len(pkts)} gói tin)")
                return True
            
            time.sleep(0.3)
        
        safe_print(f"{self._tag}    ⚠️ Server KHÔNG phản hồi sau {timeout}s: {action_desc}")
        return False

    def _wait_for_battle_finish(self) -> bool:
        """
        Chờ server xác nhận trận đấu bắt đầu, sau đó chờ kết thúc.
        Trả về True nếu trận đấu diễn ra thực tế, False nếu server bỏ qua.
        """
        # Phase 1: Chờ tối đa 3.5 giây xem Server có kích hoạt trận đấu không
        in_battle = False
        start_wait = time.time()
        while time.time() - start_wait < 3.5:
            if not self.active_session or not self.active_session.is_running:
                return False
            
            # Kiểm tra cờ an toàn từ Server loop thay vì đọc buffer (buffer có thể đã bị consume)
            if self.is_client_in_battle or self.battle_finished_event.is_set():
                in_battle = True
                break
                
            time.sleep(0.1)

        if not in_battle:
            safe_print(f"{self._tag}    ⚠️ Server không kích hoạt trận đấu. Tiếp tục bước sau...")
            return False

        # Phase 2: Đã vào trận → chờ kết thúc (tối đa 60s)
        safe_print(f"{self._tag}    ⚔️ Đã vào trận đấu thực tế! Đang chờ kết quả...")
        timeout = 60
        start_t = time.time()
        while self.is_running and (time.time() - start_t < timeout):
            if not self.active_session or not self.active_session.is_running:
                break
            
            pkts = self.active_session.get_recent_packets()
            for pkt in pkts:
                offset = 0
                while offset + 4 <= len(pkt):
                    pkt_len = (pkt[offset] << 8) | pkt[offset + 1]
                    if pkt_len < 4 or offset + pkt_len > len(pkt):
                        break
                    msg_type = (pkt[offset + 2] << 8) | pkt[offset + 3]
                    if msg_type == 0x30D8:
                        safe_print(f"{self._tag}    🏆 Trận đấu kết thúc!")
                        time.sleep(1.0)
                        return True
                    offset += pkt_len
            
            time.sleep(1.0)
        
        safe_print(f"{self._tag}    ⏰ Trận đấu timeout ({timeout}s). Tiếp tục...")
        return True

    def pause(self):
        self.is_paused = True
        safe_print(f"{self._tag} ⏸️ Đã TẠM DỪNG ở bước {self.current_step+1}")

    def resume(self):
        self.is_paused = False
        safe_print(f"{self._tag} ▶️ Đã TIẾP TỤC từ bước {self.current_step+1}")

    def back(self):
        if self.current_step > 0:
            self.current_step -= 1
            safe_print(f"{self._tag} ⏪ Đã lùi lại bước {self.current_step+1}")
        else:
            safe_print(f"{self._tag} ⏪ Đang ở bước đầu tiên.")

