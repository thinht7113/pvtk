"""
PVTK World Map & Teleport Module
Handles:
1. Direct Teleport to 'Thánh Vực' (ChildMapID: 11)
2. Direct Teleport to 'Quảng Trường Tân Nguyệt' (ChildMapID: 38)
3. Generic Teleport (0x291B)
"""

import sys
import time
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from typing import Tuple, Optional
from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto

THANH_VUC_CHILD_MAP_ID = 11         # Di Tích Thánh Vực (Trung lập)
TAN_NGUYET_TRAN_CHILD_MAP_ID = 37   # Tân Nguyệt Trấn (Trung lập)
QUANG_TRUONG_CHILD_MAP_ID = 38      # Quảng Trường Tân Nguyệt (Trung lập)

class MapTeleporter:
    @staticmethod
    def teleport_to_map(conn: PVTKConnection, map_id: int) -> bool:
        """
        Sends Opcode 0x291B (CG_SCENE_WORLDMAP_ENTER) to instantly teleport
        the current character into any target child map ID.
        """
        try:
            msg = Message(proto.CG_SCENE_WORLDMAP_ENTER if hasattr(proto, "CG_SCENE_WORLDMAP_ENTER") else 0x291B)
            msg.putInt(map_id)
            msg.putInt(64)  # 64 = flag dịch chuyển bản đồ thế giới

            if not conn.send_message(msg):
                return False

            # TURBO: Drain response nhanh hơn (0.3s thay vì 1.5s)
            t_end = time.time() + 0.3
            while time.time() < t_end:
                res = conn.receive_message(timeout=0.1)
                if not res:
                    break
                if res.type == 0x291B:
                    break
            return True
        except Exception:
            return False

    @classmethod
    def teleport_to_thanh_vuc(cls, conn: PVTKConnection) -> bool:
        """Teleports current character to 'Thánh Vực' (ID: 11)."""
        return cls.teleport_to_map(conn, THANH_VUC_CHILD_MAP_ID)

    @classmethod
    def teleport_to_tan_nguyet_tran(cls, conn: PVTKConnection) -> bool:
        """Teleports current character to 'Tân Nguyệt Trấn' (ID: 37)."""
        return cls.teleport_to_map(conn, TAN_NGUYET_TRAN_CHILD_MAP_ID)

    @classmethod
    def teleport_to_quang_truong(cls, conn: PVTKConnection) -> bool:
        """Teleports current character to 'Quảng Trường Tân Nguyệt' (ID: 38)."""
        return cls.teleport_to_map(conn, QUANG_TRUONG_CHILD_MAP_ID)

    @classmethod
    def escape_lag(cls, conn: PVTKConnection) -> Tuple[bool, Optional[int], Optional[int], Optional[int], str]:
        """
        Sends Opcode 0x290A (CG_SCENE_GO_MAP) with mapID=-1, gx=0, gy=0, blockFlag=64.
        Mimics in-game menu: 'H.Thống' -> 'Thoát lag' (GameWorld.doJumpRebornMap()).
        Returns: (success, map_id, gx, gy, error_msg)
        """
        try:
            msg = Message(0x290A)
            msg.putShort(-1)
            msg.putByte(0)
            msg.putByte(0)
            msg.putInt(64)

            if not conn.send_message(msg):
                return False, None, None, None, "Không thể gửi gói tin Thoát lag (0x290A)"

            t_end = time.time() + 2.5
            while time.time() < t_end:
                res = conn.receive_message(timeout=0.2)
                if not res:
                    continue
                if res.type == 0x290A:
                    code = res.getByte()
                    if code == 0:
                        new_map_id = res.getInt()
                        if 30000 <= new_map_id <= 31999:
                            _ = res.getShort()
                        gx = res.getByte()
                        gy = res.getByte()
                        return True, new_map_id, gx, gy, ""
                    else:
                        err = res.getString() if res.hasRemaining() else f"Mã phản hồi {code}"
                        return False, None, None, None, err

            return True, None, None, None, "Đã gửi lệnh thoát lag"
        except Exception as e:
            return False, None, None, None, str(e)
