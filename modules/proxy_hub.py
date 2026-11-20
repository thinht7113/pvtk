"""
PVTK Sniffer Proxy Hub Module (Proxy Ghi Nhận & Đồng Bộ Toàn Bộ Tương Tác A-Z)
Intercepts packets between Master Character Game Client and Game Server,
recording 100% of player actions (Quests, NPC, Dialogs, Items, Skills, Attributes, Battles)
with exact timestamps, parsed parameters, and raw binary payloads.
"""

import sys
import os
import time
import json
import base64
import socket
import select
import threading
from typing import List, Dict, Any, Callable, Optional
from concurrent.futures import ThreadPoolExecutor

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.message import Message
import core.protocol as proto
from core.config import settings
from modules.slave_agent import SlaveAgent, ActionTask
from . import bag_parser

PRINT_LOCK = threading.Lock()

def hub_print(msg: str):
    with PRINT_LOCK:
        print(msg)


class ProxyHub:
    def __init__(self, target_host: str, target_port: int, listen_host: str = "0.0.0.0", listen_ports: Any = [10001, 10002, 10003]):
        self.target_host = target_host
        self.target_port = target_port
        self.listen_host = listen_host
        if isinstance(listen_ports, int):
            self.listen_ports = [listen_ports]
            self.listen_port = listen_ports
        elif isinstance(listen_ports, (list, tuple)):
            self.listen_ports = list(listen_ports)
            self.listen_port = self.listen_ports[0] if self.listen_ports else 10001
        else:
            self.listen_ports = [10001, 10002, 10003]
            self.listen_port = 10001
            
        self.is_running = False
        self.server_sockets: List[socket.socket] = []
        self.slaves: List[SlaveAgent] = []
        self.master_char_id: int = 0
        self.master_bag_bytes: bytes = b""
        self.last_move_packet: Optional[str] = None
        self.recorded_actions: List[Dict[str, Any]] = []
        self.executor = ThreadPoolExecutor(max_workers=20)
        self.start_record_time = time.time()
        self.last_action_time = time.time()
        self.last_battle_finish_time: float = 0.0

    def attach_slaves(self, slaves: List[Any]):
        """No longer attaches slaves, kept for backward compatibility."""
        pass

    def start(self) -> bool:
        """Starts the proxy server on all specified ports (e.g. 10001, 10002, 10003)."""
        self.is_running = True
        self.start_record_time = time.time()
        self.last_action_time = time.time()

        opened_ports = []
        for port in self.listen_ports:
            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_socket.bind((self.listen_host, port))
                server_socket.listen(5)
                self.server_sockets.append(server_socket)
                opened_ports.append(port)

                proxy_thread = threading.Thread(target=self._accept_loop, args=(server_socket, port), daemon=True)
                proxy_thread.start()
            except OSError as e:
                hub_print(f"\033[93m[CẢNH BÁO PROXY HUB]\033[0m Cổng {port} không thể mở: {e}")

        if opened_ports:
            hub_print(f"\033[92m[PROXY HUB ONLINE]\033[0m Đang lắng nghe kết nối tại các cổng: {opened_ports}")
            hub_print(f"-> Đích chuyển tiếp: Master Server ({settings.master_port}) + Game Server ({self.target_port})")
            return True
        else:
            hub_print(f"\033[91m[LỖI PROXY HUB]\033[0m Không thể mở bất kỳ cổng nào trong {self.listen_ports}!")
            return False

    def _accept_loop(self, server_sock: socket.socket, port: int):
        while self.is_running:
            try:
                client_sock, client_addr = server_sock.accept()
                hub_print(f"\n\033[93m[ACC TRƯỞNG KẾT NỐI - CỔNG {port}]\033[0m Nhận kết nối từ Game Client: {client_addr}")
                
                # Handle connection in dedicated worker thread
                threading.Thread(
                    target=self._handle_client_session,
                    args=(client_sock,),
                    daemon=True
                ).start()
            except Exception:
                break

    def _handle_client_session(self, client_sock: socket.socket):
        """Intelligently detects Master Login (30000) vs Game Play (10010) and handles duplex forwarding."""
        upstream_sock = None
        client_buffer = bytearray()

        try:
            # 1. Read first packet from Client to detect destination (Master vs Game Server)
            client_sock.settimeout(10.0)
            initial_data = client_sock.recv(4096)
            if not initial_data:
                client_sock.close()
                return

            client_buffer.extend(initial_data)
            
            # Detect opcode
            msg_type = 0
            if len(initial_data) >= 4:
                msg_type = (initial_data[2] << 8) | initial_data[3]

            # Route to Master Server (30000) or Game Server (10010 / 10000)
            is_master = ((msg_type >> 8) == 0x13) or (0x1300 <= msg_type <= 0x13FF) or msg_type in (proto.LC_CHECKEDITION, proto.LC_PLAYER_LOGIN)
            dest_host = settings.master_host if is_master else self.target_host
            dest_port = settings.master_port if is_master else self.target_port
            dest_name = "Master Server (30000)" if is_master else f"Game Server ({dest_port})"

            hub_print(f"  \033[96m-> [ĐIỀU HƯỚNG THÔNG MINH]\033[0m Gói 0x{msg_type:04X} -> Chuyển tiếp tới {dest_name} [{dest_host}:{dest_port}]")

            upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            upstream_sock.connect((dest_host, dest_port))

            # Forward initial packet and dispatch actions to slaves
            self._parse_and_forward_client_packets(client_buffer, upstream_sock)

            # 2. Duplex Forwarding Loop
            socks = [client_sock, upstream_sock]
            client_sock.settimeout(None)
            server_buffer = bytearray()

            while self.is_running:
                readable, _, exceptional = select.select(socks, [], socks, 1.0)
                if exceptional:
                    break

                for s in readable:
                    if s is client_sock:
                        data = client_sock.recv(4096)
                        if not data:
                            hub_print("  \033[90m[CLIENT ĐÃ ĐÓNG KẾT NỐI]\033[0m Game Client chủ động ngắt kết nối.")
                            return
                        client_buffer.extend(data)
                        self._parse_and_forward_client_packets(client_buffer, upstream_sock)

                    elif s is upstream_sock:
                        data = upstream_sock.recv(4096)
                        if not data:
                            hub_print("  \033[91m[MÁY CHỦ ĐÓNG KẾT NỐI]\033[0m Game Server đã ngắt kết nối.")
                            return
                        client_sock.sendall(data)
                        # Sniff Server→Client packets for auto-teleport detection
                        server_buffer.extend(data)
                        self._parse_server_packets(server_buffer)

        except Exception as e:
            hub_print(f"  \033[90m[NGẮT KẾT NỐI PROXY]\033[0m {e}")
        finally:
            if client_sock:
                try:
                    client_sock.close()
                except Exception:
                    pass
            if upstream_sock:
                try:
                    upstream_sock.close()
                except Exception:
                    pass
            hub_print("  \033[91m[ACC TRƯỞNG NGẮT KẾT NỐI]\033[0m Đã đóng phiên chuyển tiếp an toàn.")

    def _parse_and_forward_client_packets(self, buffer: bytearray, upstream_sock: socket.socket):
        """Extracts complete PVTK binary messages from stream buffer, enforces 5s battle cooldown,
        forwards packets upstream to Game Server, and dispatches actions to slaves.
        Wire format: [2 bytes total_len] [2 bytes msg_type] [payload]
        total_len includes itself (= 4 + payload_len)
        """
        while len(buffer) >= 4:
            total_len = (buffer[0] << 8) | buffer[1]
            if total_len < 4:
                # Invalid packet header, forward byte directly and shift
                upstream_sock.sendall(bytes(buffer[:1]))
                del buffer[:1]
                continue
            if len(buffer) < total_len:
                break  # Wait for full packet to arrive

            raw_wire_packet = bytes(buffer[:total_len])
            del buffer[:total_len]

            msg_type = (raw_wire_packet[2] << 8) | raw_wire_packet[3]
            payload = raw_wire_packet[4:]
            packet_data = raw_wire_packet[2:]  # msg_type + payload

            # BẮT BUỘC: Kiểm tra Cooldown chiến đấu 5s trước khi chuyển tiếp gói vào trận tới Server
            if msg_type in (proto.CG_FIGHT_ENTER_LOCALBATTEL, proto.CG_FIGHT_ENTER_REMOTEBATTLE, 
                            proto.CG_TASK_TEAM_BOSS_FIGHT, proto.CG_TASK_COUNTRY_BOSS_FIGHT, proto.CG_TASK_ARENA_FIGHT):
                if self.last_battle_finish_time > 0:
                    elapsed = time.time() - self.last_battle_finish_time
                    if elapsed < 5.0:
                        wait_sec = round(5.0 - elapsed, 2)
                        hub_print(f"  \033[96m⏳ [COOLDOWN CHIẾN ĐẤU] Đang giữ nhịp đợi {wait_sec}s theo quy định Server PVTK trước khi vào trận...\033[0m")
                        time.sleep(wait_sec)

            # Chuyển tiếp gói tin hợp lệ tới Game Server
            upstream_sock.sendall(raw_wire_packet)

            # Ghi nhận thời điểm kết thúc trận nếu là gói thắng trận 0x30D6
            if msg_type == proto.CG_FIGHT_RUN_LOCALBATTLE:
                self.last_battle_finish_time = time.time()

            msg = Message(msg_type, payload)
            try:
                self._handle_master_packet(msg, raw_bytes=packet_data)
            except Exception as e:
                hub_print(f"  \033[91m[LỖI PARSE GÓI 0x{msg_type:04X}]\033[0m {e}")

    def _parse_server_packets(self, buffer: bytearray):
        """Sniffs Server→Client packets."""
        while len(buffer) >= 4:
            total_len = (buffer[0] << 8) | buffer[1]
            if total_len < 4 or len(buffer) < total_len:
                break
                
            raw_wire = bytes(buffer[:total_len])
            del buffer[:total_len]
            msg_type = (raw_wire[2] << 8) | raw_wire[3]
            payload = raw_wire[4:]
            
            # Cache Master Bag Bytes (0x2EE3 từ Server)
            if msg_type == 0x2EE3:
                self.master_bag_bytes = payload
                try:
                    with open("dump_bag.bin", "wb") as f:
                        f.write(payload)
                except Exception:
                    pass

    def _handle_master_packet(self, msg: Message, raw_bytes: bytes = b""):
        """Sniffs and triggers slave broadcast for recognized interaction opcodes from A to Z."""
        opcode = msg.msg_type
        raw_b64 = base64.b64encode(raw_bytes).decode("utf-8") if raw_bytes else ""

        # 0. Sniff Master Account & Character Entry
        if opcode == proto.LC_PLAYER_LOGIN:
            master_user = msg.getString() if msg.hasRemaining() else "Acc Trưởng"
            hub_print("\n" + "=" * 80)
            hub_print(f"🎯 \033[93m[ACC TRƯỞNG ĐĂNG NHẬP]\033[0m Tài khoản: \033[92m{master_user}\033[0m")
            hub_print("=" * 80)

        elif opcode == proto.CG_LOGIN_ACTORENTER:
            char_id = msg.getInt() if msg.hasRemaining() else 0
            self.master_char_id = char_id
            hub_print("\n" + "=" * 80)
            hub_print(f"🎯 \033[92m[ACC TRƯỞNG ĐÃ VÀO THẾ GIỚI GAME]\033[0m (ID Nhân Vật: #{char_id})")
            hub_print("=" * 80 + "\n")

        # 0b. CG_TEAM_INVITE (0x34BD) / CG_TEAM_NEW (0x34C4) - Mời Vào Tổ Đội
        elif opcode in (proto.CG_TEAM_INVITE, proto.CG_TEAM_NEW, 0x34BD, 0x34C4):
            hub_print(f"\n\033[93m[ACC TRƯỞNG -> MỜI VÀO TỔ ĐỘI]\033[0m")

        # 1. CG_TASK_ACCEPT (0x38A6) - Nhận Nhiệm Vụ
        elif opcode == proto.CG_TASK_ACCEPT:
            npc_id = msg.getShort()
            mission_id = msg.getShort()
            hub_print(f"\n\033[96m[ACC TRƯỞNG -> NHẬN NHIỆM VỤ]\033[0m Nhiệm vụ: \033[92m#{mission_id}\033[0m | NPC: \033[93m#{npc_id}\033[0m")
            self._record_action({
                "action": "accept_quest",
                "desc": f"Nhận Q #{mission_id} (NPC #{npc_id})",
                "opcode": f"0x{opcode:04X}",
                "params": {"npc_id": npc_id, "mission_id": mission_id},
                "raw_hex_b64": raw_b64
            })

        # 2. CG_TASK_DELIVER (0x38A7) - Trả Nhiệm Vụ
        elif opcode == proto.CG_TASK_DELIVER:
            npc_id = msg.getShort()
            mission_id = msg.getShort()
            reward_item_id = msg.getInt() if msg.hasRemaining() else 0
            hub_print(f"\n\033[92m[ACC TRƯỞNG -> TRẢ NHIỆM VỤ]\033[0m Nhiệm vụ: \033[92m#{mission_id}\033[0m | NPC: \033[93m#{npc_id}\033[0m ★")
            self._record_action({
                "action": "deliver_quest",
                "desc": f"Trả Q #{mission_id} (NPC #{npc_id})",
                "opcode": f"0x{opcode:04X}",
                "params": {"npc_id": npc_id, "mission_id": mission_id, "reward_id": reward_item_id},
                "raw_hex_b64": raw_b64
            })

        # 3. CG_SCENE_GETNPC (0x2917) - Đối Thoại NPC
        elif opcode == proto.CG_SCENE_GETNPC:
            if msg.hasRemaining():
                msg.getByte()  # size
                npc_id = msg.getByte() if msg.hasRemaining() else 0
                hub_print(f"\033[90m[ACC TRƯỞNG -> ĐỐI THOẠI NPC]\033[0m NPC: #{npc_id}")
                self._record_action({
                    "action": "talk_npc",
                    "desc": f"Đối thoại NPC #{npc_id}",
                    "opcode": f"0x{opcode:04X}",
                    "params": {"npc_id": npc_id},
                    "raw_hex_b64": raw_b64
                })

        # 4. CG_SCENE_PLAYER_EVENT (0x290C) - Chọn Sự Kiện NPC
        elif opcode == proto.CG_SCENE_PLAYER_EVENT:
            event_id = msg.getInt()
            choose = msg.getByte()
            hub_print(f"\033[90m[ACC TRƯỞNG -> CHỌN SỰ KIỆN NPC]\033[0m Sự kiện: #{event_id} (Dòng {choose})")
            self._record_action({
                "action": "player_event",
                "desc": f"Chọn sự kiện #{event_id} (Dòng {choose})",
                "opcode": f"0x{opcode:04X}",
                "params": {"event_id": event_id, "choose": choose},
                "raw_hex_b64": raw_b64
            })

        # 5. CG_AUTO_SELL (0x2F05) - Bán Auto Đồ Rác
        elif opcode == proto.CG_AUTO_SELL:
            items = []
            if msg.hasRemaining():
                size = msg.getByte()
                for _ in range(size):
                    if len(msg.data) - msg.offset >= 8:
                        item_id = msg.getInt()
                        slot = msg.getShort()
                        qty = msg.getShort()
                        items.append({"item_id": item_id, "slot": slot, "qty": qty})

            item_count_str = f" ({len(items)} món)" if items else ""
            hub_print(f"\n\033[93m[ACC TRƯỞNG -> BẤM 'BÁN AUTO' [OK]]\033[0m{item_count_str}")
            self._record_action({
                "action": "auto_sell",
                "desc": f"Bán Auto Đồ Rác{item_count_str}",
                "opcode": f"0x{opcode:04X}",
                "params": {"items": items},
                "raw_hex_b64": raw_b64
            })

        # 5b. CG_ACTOR_PLAYERBAG (0x2EE1) - Thao tác Mặc đồ / Bơm máu
        elif opcode == proto.CG_ACTOR_PLAYERBAG:
            wait_type = msg.getByte() if msg.hasRemaining() else 0
            action_type = msg.getByte() if msg.hasRemaining() else 0
            slot_pos = msg.getShort() if msg.hasRemaining() else 0
            equip_pos = 0
            if action_type == 1:
                equip_pos = msg.getByte() if msg.hasRemaining() else 0
            is_upgrade = msg.getBoolean() if msg.hasRemaining() else False
            num = msg.getInt() if (action_type == 8 and msg.hasRemaining()) else 1

            action_name = "MẶC TRANG BỊ" if action_type == 1 else ("SỬ DỤNG ITEM" if action_type in (3, 8) else f"TÚI ĐỒ (Loại {action_type})")
            
            # Tra cứu tên và ID vật phẩm từ cache túi đồ của Acc Trưởng
            found_item_id = -1
            found_item_name = ""
            if self.master_bag_bytes:
                found_item_id, found_item_name = bag_parser.get_item_by_slot(self.master_bag_bytes, slot_pos)

            hub_print(f"\n\033[96m[ACC TRƯỞNG -> {action_name}]\033[0m Tại Ô túi #{slot_pos}" + (f" ({found_item_name})" if found_item_name else ""))

            # Ghi lại hành động vào kịch bản
            action_key = "equip_item" if action_type == 1 else "use_item"
            desc_text = f"{action_name} [{found_item_name}] (Ô #{slot_pos})" if found_item_name else f"{action_name} (Ô #{slot_pos})"
            self._record_action({
                "action": action_key,
                "desc": desc_text,
                "opcode": f"0x{opcode:04X}",
                "params": {
                    "wait_type": wait_type,
                    "action_type": action_type,
                    "slot_pos": slot_pos,
                    "equip_pos": equip_pos,
                    "is_upgrade": is_upgrade,
                    "num": num,
                    "item_id": found_item_id,
                    "item_name": found_item_name
                },
                "raw_hex_b64": raw_b64
            })

        # 6. CG_SKILL_SHOP_LEARN (0x36B2) - Học Kỹ Năng
        elif opcode == proto.CG_SKILL_SHOP_LEARN:
            shop_id = msg.getShort()
            skill_id = msg.getInt()
            level = msg.getByte()
            is_country = msg.getBoolean() if msg.hasRemaining() else False
            hub_print(f"\n\033[95m[ACC TRƯỞNG -> HỌC KỸ NĂNG]\033[0m Kỹ năng: \033[92m#{skill_id}\033[0m (Cấp {level}) tại Shop #{shop_id}")
            self._record_action({
                "action": "learn_skill",
                "desc": f"Học Kỹ Năng #{skill_id} Cấp {level}",
                "opcode": f"0x{opcode:04X}",
                "params": {"shop_id": shop_id, "skill_id": skill_id, "level": level, "is_country": is_country},
                "raw_hex_b64": raw_b64
            })

        # 6b. CG_SKILL_INITIATIVE_SET (0x36B9) - Cài Đặt Tự Động Xuất Chiêu (Auto Skill)
        elif opcode == proto.CG_SKILL_INITIATIVE_SET:
            skill_type = msg.getByte() if msg.hasRemaining() else 0
            skill_id = msg.getInt() if msg.hasRemaining() else 0
            hub_print(f"\n\033[95m[ACC TRƯỞNG -> CÀI ĐẶT AUTO SKILL]\033[0m Kỹ năng: \033[92m#{skill_id}\033[0m")
            self._record_action({
                "action": "set_auto_skill",
                "desc": f"Cài đặt Auto Skill #{skill_id}",
                "opcode": f"0x{opcode:04X}",
                "params": {"skill_type": skill_type, "skill_id": skill_id},
                "raw_hex_b64": raw_b64
            })

        # 7. CG_SCENE_GO_MAP (0x290A) / CG_SCENE_GO_CITY (0x290E) / CG_SCENE_WORLDMAP_ENTER (0x291B) - Dịch Chuyển Cổng / Map
        elif opcode in (proto.CG_SCENE_GO_MAP, 0x290E, 0x291B, 0x291C, 0x291D):
            raw_payload = bytes(msg.data)
            map_id = ((raw_payload[0] << 8) | raw_payload[1]) if len(raw_payload) >= 2 else 0
            hub_print(f"\033[94m[ACC TRƯỞNG -> QUA CỔNG DỊCH CHUYỂN / ĐỔI MAP]\033[0m Sang Bản đồ: #{map_id} (0x{opcode:04X})")
            self._record_action({
                "action": "change_map",
                "desc": f"Chuyển sang Map #{map_id}",
                "opcode": f"0x{opcode:04X}",
                "params": {"map_id": map_id, "payload_hex": raw_payload.hex(), "opcode": opcode},
                "raw_hex_b64": raw_b64
            })

        # 8. Combat & Monster Battles (0x30D5 Local, 0x30D7 Remote/Group, 0x38B8 Boss, 0x38CF Country Boss)
        elif opcode in (proto.CG_FIGHT_ENTER_LOCALBATTEL, proto.CG_FIGHT_ENTER_REMOTEBATTLE, proto.CG_TASK_TEAM_BOSS_FIGHT, proto.CG_TASK_COUNTRY_BOSS_FIGHT, proto.CG_TASK_ARENA_FIGHT):
            group_id = msg.getShort() if msg.hasRemaining() else 0
            is_remote = (opcode == proto.CG_FIGHT_ENTER_REMOTEBATTLE)
            battle_type_str = f"Quái nhóm #{group_id}" if is_remote and group_id else f"Quái (0x{opcode:04X})"
            hub_print(f"\n\033[91m[ACC TRƯỞNG -> VÀO ĐÁNH {battle_type_str.upper()}]\033[0m")
            self._record_action({
                "action": "fight",
                "desc": f"Đánh {battle_type_str}",
                "opcode": f"0x{opcode:04X}",
                "params": {"group_id": group_id, "is_remote": is_remote, "opcode": opcode},
                "raw_hex_b64": raw_b64
            })

        # 8b. CG_FIGHT_RUN_LOCALBATTLE (0x30D6) - Kết Quả Trận Đánh Cục Bộ (Thắng Quái)
        elif opcode == proto.CG_FIGHT_RUN_LOCALBATTLE:
            raw_payload = bytes(msg.data)  # Copy to avoid mutation
            hub_print(f"\033[92m[ACC TRƯỞNG -> KẾT THÚC ĐÁNH QUÁI (0x30D6)]\033[0m")
            
            # TỰ ĐỘNG GÁN GHÉP BATTLE ID (OPTION 9/10 CỦA USER)
            # Trích xuất group_id từ gói 0x30D6 và cập nhật ngược lại bước "npc_interact" hoặc "event" thành bước "fight" chuẩn xác
            if len(raw_payload) >= 20:
                extracted_gid = int.from_bytes(raw_payload[18:20], "big")
                if extracted_gid > 0 and len(self.recorded_actions) > 0:
                    for i in range(len(self.recorded_actions) - 1, max(-1, len(self.recorded_actions) - 6), -1):
                        prev_action = self.recorded_actions[i]
                        # Cập nhật nếu bước trước là sự kiện NPC hoặc 0x30D5 bị thiếu group_id
                        if prev_action.get("opcode") in ("0x2AFC", "0x290C", "0x2917", "0x30D5") or (prev_action.get("action") == "fight" and prev_action.get("params", {}).get("group_id", 0) == 0):
                            hub_print(f"  \033[96m[AUTO-PATCH KỊCH BẢN]\033[0m Đã gán tự động Quái #{extracted_gid} vào hành động {prev_action.get('opcode')} trước đó!")
                            prev_action["action"] = "fight"
                            prev_action["desc"] = f"Đánh Quái #{extracted_gid} (Auto-Patch)"
                            prev_action["opcode"] = "0x30D5"
                            prev_action["params"] = {
                                "group_id": extracted_gid,
                                "is_remote": False,
                                "opcode": 0x30D5
                            }
                            break
            self._record_action({
                "action": "fight_result",
                "desc": "Báo cáo chiến thắng đánh quái",
                "opcode": f"0x{opcode:04X}",
                "params": {"payload_hex": raw_payload.hex()},
                "raw_hex_b64": raw_b64
            })

        # 8c. CG_FIGHT_BATTLE_DOPLAN (0x30D9) - Kế Hoạch Tấn Công Từng Hiệp (Remote Battle)
        elif opcode == proto.CG_FIGHT_BATTLE_DOPLAN:
            pass

        elif opcode == 0x30DA:
            pass

        # 9. Xử lý Gói tin Sắp xếp Túi Đồ (0x2EE3)
        elif opcode == 0x2EE3:
            hub_print(f"\033[92m[ACC TRƯỞNG -> SẮP XẾP TÚI ĐỒ (0x2EE3)]\033[0m")
            self._record_action({
                "action": "raw_packet",
                "desc": "Sắp xếp túi đồ (0x2EE3)",
                "opcode": f"0x{opcode:04X}",
                "params": {"payload_hex": bytes(msg.data).hex()},
                "raw_hex_b64": raw_b64
            })

        # 10. CG_ACTOR_ADD_POINT (0x2EE5) - Cộng Điểm Tiềm Năng
        elif opcode == 0x2EE5:
            point_type = msg.getByte() if msg.hasRemaining() else 0
            count = msg.getInt() if msg.hasRemaining() else 1
            hub_print(f"\n\033[92m[ACC TRƯỞNG -> CỘNG ĐIỂM TIỀM NĂNG]\033[0m Loại: #{point_type} (Cộng {count} điểm)")
            self._record_action({
                "action": "add_point",
                "desc": f"Cộng {count} điểm tiềm năng (loại #{point_type})",
                "opcode": f"0x{opcode:04X}",
                "params": {"point_type": point_type, "count": count},
                "raw_hex_b64": raw_b64
            })

        # 10b. Lưu lại gói tin di chuyển / 0x138B
        elif opcode in (0x138B, getattr(proto, 'CG_PLAYER_MOVE', 0x2906)):
            self.last_move_packet = raw_b64
            return

        # 11. Bỏ qua hoàn toàn các gói tin Đăng nhập, Đăng xuất, Thiết bị, Dò đường, Di chuyển, Tải ảnh, Nhịp tim
        elif (
            opcode < 0x2000  # Hệ thống / Master login / Logout / Session check (0x0001, 0x0002, 0x0003, 0x1388, 0x138A, 0x138F...)
            or opcode in (
                0x2711, 0x2712, 0x2713, 0x2714, 0x2715, 0x2716, 0x2717, 0x271C,  # Đăng nhập / Chọn NV / Đổi tài khoản
                0x2907, 0x2908, 0x2909, 0x2916, 0x2918, 0x291F, 0x2927, 0x2928,  # Di chuyển / Dò đường / Ảnh map
                0x2B64, 0x2B68, 0x2B6C,  # Phần thưởng online / Popup UI
                0x38A5, 0x38A8, 0x38A9,  # Truy vấn danh sách nhiệm vụ UI client
                0x3E81, 0x3E82, 0x3E84, 0x3E85  # Nhịp tim giữ kết nối
            )
        ):
            pass

        # 12. Tự động đồng bộ 100% MỌI TƯƠNG TÁC THỰC SỰ (OPTION / MENU / NHIỆM VỤ / BẤM NÚT)
        else:
            raw_payload = bytes(msg.data)
            _op = opcode
            hub_print(f"  \033[96m[ACC TRƯỞNG -> TƯƠNG TÁC (0x{opcode:04X})]\033[0m Ghi nhận packet: {len(raw_payload)} bytes")
            self._record_action({
                "action": "raw_packet",
                "desc": f"Tương tác Option 0x{opcode:04X}",
                "opcode": f"0x{opcode:04X}",
                "params": {"payload_hex": raw_payload.hex()},
                "raw_hex_b64": raw_b64
            })

    def _record_action(self, action_dict: Dict[str, Any]):
        """Records action timestamp and human relative delay."""
        if getattr(self, 'last_move_packet', None) is not None:
            move_b64 = self.last_move_packet
            self.last_move_packet = None  # Clear to avoid duplicates
            
            now = time.time()
            delay = round(now - self.last_action_time, 2)
            if delay < 0.2:
                delay = 0.5
            elif delay > 5.0:
                delay = 2.0

            move_action = {
                "action": "raw_packet",
                "desc": "Đồng bộ vị trí (CG_PLAYER_MOVE)",
                "opcode": "0x138B",
                "params": {"payload_hex": ""},
                "raw_hex_b64": move_b64,
                "delay": delay,
                "timestamp": round(now, 2)
            }
            self.last_action_time = now
            self.recorded_actions.append(move_action)

        now = time.time()
        delay = round(now - self.last_action_time, 2)
        if delay < 0.2:
            delay = 0.5
        elif delay > 5.0:
            delay = 2.0

        action_dict["delay"] = delay
        action_dict["timestamp"] = round(now, 2)
        self.last_action_time = now
        self.recorded_actions.append(action_dict)

    def save_recorded_script(self, filepath: str = "scripts/recorded_leveling_script.json"):
        """Saves all recorded actions to a well-structured, production-ready JSON script."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        script_payload = {
            "title": f"Lộ trình Auto Leveling PVTK ({len(self.recorded_actions)} bước)",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target_server": f"{self.target_host}:{self.target_port}",
            "total_steps": len(self.recorded_actions),
            "steps": self.recorded_actions
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(script_payload, f, ensure_ascii=False, indent=2)
        hub_print(f"\n\033[92m[ĐÃ LƯU KỊCH BẢN THÀNH CÔNG]\033[0m")
        hub_print(f"  -> Tổng số bước hành động đã lưu: \033[93m{len(self.recorded_actions)}\033[0m bước")
        hub_print(f"  -> Tệp lưu: \033[96m{filepath}\033[0m")
        hub_print(f"  -> Bạn có thể dùng chức năng 'Chạy Kịch Bản Theo Đợt' để tự động cày hết 100 acc bất cứ lúc nào!")

    def stop(self):
        """Stops the proxy hub."""
        self.is_running = False
        for s in self.server_sockets:
            try:
                s.close()
            except Exception:
                pass
        self.server_sockets.clear()
        self.executor.shutdown(wait=False)
