# -*- coding: utf-8 -*-
"""
PVTK City Manager Module (Hệ thống Quản lý và Vào Thành Cá Nhân)
Reverse-engineered from PVTK Client (com.hz.core.City, com.hz.main.MsgHandler):
- Opcode 0x290F: CG_CITY_INFO (Truy vấn thông tin Thành Cá Nhân)
- Opcode 0x290E: CG_SCENE_GO_CITY (Dịch chuyển trực tiếp vào Thành Cá Nhân)
"""

import time
import logging
from typing import Optional, Dict, Any, Tuple, List
from core.message import Message
from core.network import PVTKConnection
from core.config import settings
from modules.auth import AuthManager, CharacterInfo
from modules.mail_sender import parse_player_id

logger = logging.getLogger("pvtk_city")


class CityManager:
    @staticmethod
    def parse_target_id(target_str: str) -> Optional[int]:
        """
        Phân tích định dạng ID người chơi hoặc Area ID:
        - Số nguyên: '1906278888' -> 1906278888
        - Area ID: 'am6278888' -> 1906278888
        """
        return parse_player_id(target_str)

    @staticmethod
    def get_city_info(conn: PVTKConnection, target_id: int, timeout: float = 4.0) -> Tuple[bool, Dict[str, Any]]:
        """
        Gửi gói tin Opcode 0x290F (CG_CITY_INFO) để lấy thông tin chi tiết Thành Cá Nhân.
        """
        try:
            msg = Message(0x290F)
            msg.putInt(target_id)
            if not conn.send_message(msg):
                return False, {"error": "Không thể gửi gói tin truy vấn thành (0x290F)"}

            t0 = time.time()
            while time.time() - t0 < timeout:
                res = conn.receive_message(timeout=1.0)
                if not res:
                    continue
                if res.type == 0x290F:
                    code = res.getByte()
                    if code >= 0:
                        city_name = res.getString() if res.hasRemaining() else ""
                        scale_name = res.getString() if res.hasRemaining() else ""
                        status_name = res.getString() if res.hasRemaining() else ""
                        sign = res.getString() if res.hasRemaining() else ""
                        master_id = res.getInt() if res.hasRemaining() else target_id
                        master_name = res.getString() if res.hasRemaining() else ""
                        master_level = res.getByte() if res.hasRemaining() else 0
                        icon1 = res.getInt() if res.hasRemaining() else 0
                        icon2 = res.getInt() if res.hasRemaining() else 0
                        icon3 = res.getInt() if res.hasRemaining() else 0
                        prosperity = res.getInt() if res.hasRemaining() else 0
                        army = res.getInt() if res.hasRemaining() else 0
                        country = res.getString() if res.hasRemaining() else ""
                        union_name = res.getString() if res.hasRemaining() else ""
                        gold = res.getInt() if res.hasRemaining() else 0
                        leaf = res.getInt() if res.hasRemaining() else 0
                        silver = res.getInt() if res.hasRemaining() else 0

                        return True, {
                            "city_name": city_name,
                            "scale_name": scale_name,
                            "status_name": status_name,
                            "sign": sign,
                            "master_id": master_id,
                            "master_name": master_name,
                            "master_level": master_level,
                            "icon1": icon1,
                            "icon2": icon2,
                            "icon3": icon3,
                            "prosperity": prosperity,
                            "army": army,
                            "country": country,
                            "union_name": union_name,
                            "gold": gold,
                            "leaf": leaf,
                            "silver": silver
                        }
                    else:
                        err_msg = res.getString() if res.hasRemaining() else f"Lỗi server (Mã: {code})"
                        return False, {"error": err_msg}

            return False, {"error": "Hết thời gian chờ phản hồi thông tin thành (Timeout)"}
        except Exception as e:
            logger.error(f"Lỗi truy vấn thông tin thành: {e}")
            return False, {"error": str(e)}

    @staticmethod
    def enter_city(conn: PVTKConnection, target_id: int, timeout: float = 4.0) -> Tuple[bool, str]:
        """
        Gửi gói tin Opcode 0x290E (CG_SCENE_GO_CITY) để vào Thành Cá Nhân của target_id.
        blockFlag = 64 (0x40 - com.hz.core.City.doEnterCity).
        """
        try:
            msg = Message(0x290E)
            msg.putInt(target_id)
            msg.putInt(64)  # blockFlag = 0x40
            if not conn.send_message(msg):
                return False, "Không thể gửi gói tin vào thành (0x290E)"

            t0 = time.time()
            while time.time() - t0 < timeout:
                res = conn.receive_message(timeout=1.0)
                if not res:
                    continue
                if res.type == 0x290E:
                    code = res.getByte()
                    if code == 0:
                        return True, "Vào thành thành công"
                    else:
                        err_msg = res.getString() if res.hasRemaining() else f"Lỗi server (Mã: {code})"
                        return False, err_msg

            return False, "Hết thời gian chờ chuyển bản đồ vào thành (Timeout)"
        except Exception as e:
            logger.error(f"Lỗi vào thành: {e}")
            return False, str(e)

    @classmethod
    def query_city_by_account(cls, target_str: str, accounts: List[Tuple[str, str]], proxy: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Dùng 1 tài khoản trong danh sách để truy vấn thông tin Thành của ID chỉ định.
        """
        target_id = cls.parse_target_id(target_str)
        if not target_id:
            return False, {"error": f"ID mục tiêu '{target_str}' không hợp lệ (hỗ trợ dạng số '1906278888' hoặc Area ID 'am6278888')"}

        if not accounts:
            return False, {"error": "Không có tài khoản nào được cấu hình để gửi truy vấn"}

        # Thử lần lượt các tài khoản đầu tiên
        for acc in accounts[:3]:
            u, p = acc[0], acc[1]
            acc_proxy = acc[2] if len(acc) >= 3 and not proxy else proxy
            conn = None
            try:
                login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, u, p, proxy=acc_proxy)
                if not login_data:
                    continue

                conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, login_data[0], login_data[1], proxy=acc_proxy)
                if not conn:
                    continue

                chars = AuthManager.fetch_character_list(conn)
                if not chars:
                    continue

                char = chars[0]
                if not AuthManager.enter_character(conn, char.id, login_data[2]):
                    continue

                time.sleep(0.5)
                # Xả gói tin tồn
                while conn.receive_message(timeout=0.05):
                    pass

                ok, data = cls.get_city_info(conn, target_id)
                AuthManager.leave_character(conn)
                return ok, data
            except Exception as e:
                logger.warning(f"Lỗi truy vấn thành với tài khoản {u}: {e}")
                continue
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        return False, {"error": "Không thể kết nối đến máy chủ game để lấy thông tin thành"}

    @classmethod
    def run_enter_city_single(cls, target_str: str, accounts: List[Tuple[str, str]], account_index: int = 0, proxy: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Thực hiện vào thành cho 1 tài khoản chỉ định để kiểm tra trực tiếp.
        """
        target_id = cls.parse_target_id(target_str)
        if not target_id:
            return False, f"ID mục tiêu '{target_str}' không hợp lệ", None

        if not accounts:
            return False, "Không có tài khoản nào trong danh sách", None

        idx = account_index if 0 <= account_index < len(accounts) else 0
        acc = accounts[idx]
        u, p = acc[0], acc[1]
        if not proxy and len(acc) >= 3:
            proxy = acc[2]

        conn = None
        try:
            login_data, err = AuthManager.master_login(settings.master_host, settings.master_port, u, p, proxy=proxy)
            if not login_data:
                return False, f"Đăng nhập tài khoản '{u}' thất bại: {err}", None

            conn = AuthManager.connect_game_server(settings.game_host, settings.game_port, login_data[0], login_data[1], proxy=proxy)
            if not conn:
                return False, f"Kết nối máy chủ game cho '{u}' thất bại", None

            chars = AuthManager.fetch_character_list(conn)
            if not chars:
                return False, f"Tài khoản '{u}' không có nhân vật nào", None

            char = chars[0]
            if not AuthManager.enter_character(conn, char.id, login_data[2]):
                return False, f"Nhân vật '{char.name}' ({u}) không thể vào game", None

            time.sleep(0.5)
            while conn.receive_message(timeout=0.05):
                pass

            # 1. Lấy thông tin thành trước
            ok_info, city_info = cls.get_city_info(conn, target_id)

            # 2. Dịch chuyển vào thành
            ok_enter, enter_msg = cls.enter_city(conn, target_id)

            AuthManager.leave_character(conn)

            if ok_enter:
                return True, f"Nhân vật '{char.name}' (Cấp {char.level}) đã vào thành công Thành của ID {target_id}!", city_info if ok_info else None
            else:
                return False, f"Không thể vào thành: {enter_msg}", city_info if ok_info else None
        except Exception as e:
            return False, f"Lỗi ngoại lệ khi vào thành: {e}", None
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
