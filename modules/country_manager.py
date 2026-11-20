import time
import threading
from typing import List, Tuple, Dict, Any, Optional

from core.network import PVTKConnection
from core.message import Message
import core.protocol as proto
from core.device_faker import DeviceFaker
from modules.base_runner import CharacterLoopRunner
from modules.auth import AuthManager

print_lock = threading.Lock()

def safe_print(msg: str):
    with print_lock:
        print(msg, flush=True)


RANK_NAMES = {
    1: "Quốc Vương (Chủ Bang)",
    2: "Đại Đô Đốc (Phó Bang)",
    3: "Trưởng Lão",
    4: "Đại Tướng Quân",
    5: "Quan Viên",
    6: "Bang Chúng / Dân Thường",
}


class CountryManager:
    """Handles PVTK Country / Nation network interactions decompiled from Country.smali."""

    @staticmethod
    def sync_character_country(conn: PVTKConnection, char_id: int) -> Tuple[int, str, int]:
        """
        Truy vấn thông tin quốc gia của nhân vật qua Opcode 0x2B29 (Tab 1).
        Trả về (country_id, country_name, country_rank).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            req = Message(0x2B29)
            req.putInt(char_id)
            req.putByte(1) # Tab 1: Chi tiết nhân vật
            res = conn.send_and_wait(req, expected_type=0x2B29, timeout=3.5)
            if not res or not res.hasRemaining():
                return -1, "", 0
            res.getByte()  # result code
            res.getInt()   # id
            res.getString() # name
            res.getString() # info
            res.getString() # title
            res.getByte() # sex
            res.getByte() # race
            res.getByte() # job
            res.getByte() # lvl
            res.getByte() # lvl2
            res.getInt(); res.getInt() # exp2, expMax2
            res.getInt(); res.getInt(); res.getInt() # icon1, icon2, icon3
            res.getInt(); res.getInt() # setting, status
            res.getByte() # mode
            cid = res.getInt() # countryID
            cname = ""
            crank = 0
            if cid >= 0:
                cname = res.getString() # countryName
                crank = res.getByte()   # countryRank
            return cid, cname, crank
        except Exception:
            return -1, "", 0


    @staticmethod
    def donate(conn: PVTKConnection, donate_type: int, value: int) -> Tuple[bool, str]:
        """
        Quyên góp / Cống hiến quốc gia qua Opcode 0x3AAF (doCountryPeopleDonateAction trong Country.smali).
        donate_type:
          1: Vàng
          2: Bạc
          3: Gỗ (ID 1000)
          4: Đá (ID 1001)
          5: Sắt (ID 1002)
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_PEOPLE_DONATE) # 0x3AAF
            msg.putByte(donate_type)
            msg.putInt(value)
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_PEOPLE_DONATE, timeout=4.0)
            if not res or not res.hasRemaining():
                return False, "Server không phản hồi khi cống hiến (Timeout)"
            
            rs = res.getByte()
            if rs < 0:
                err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi: {rs}"
                return False, err_msg
            
            # rs >= 0: size items
            size = rs
            for _ in range(size):
                if res.hasRemaining():
                    res.getShort() # slotPos
                    res.getByte()  # num
            
            honor = res.getInt() if res.hasRemaining() else 0
            cur_silver = res.getInt() if res.hasRemaining() else 0
            cur_gold = res.getInt() if res.hasRemaining() else 0
            
            type_names = {1: "Vàng", 2: "Bạc", 3: "Gỗ", 4: "Đá", 5: "Sắt"}
            t_name = type_names.get(donate_type, f"Loại {donate_type}")
            return True, f"Cống hiến thành công {value:,} {t_name}! Độ cống hiến hiện tại: {honor:,}"
        except Exception as e:
            return False, f"Lỗi cống hiến: {e}"

    @staticmethod
    def query_country_list(conn: PVTKConnection, keyword: str = "", page: int = 1, page_size: int = 30) -> List[Dict[str, Any]]:
        """
        Lấy danh sách các quốc gia theo từ khóa tìm kiếm (Opcode 0x3A9C).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_LIST) # 0x3A9C
            msg.putByte(0) # searchBuildingID = 0
            msg.putShort(page)
            msg.putInt(page_size)
            msg.putString(keyword.strip())
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_LIST, timeout=4.5)
            if not res or not res.hasRemaining():
                return []
            
            all_size = res.getInt()
            if all_size < 0:
                return []
            
            if not res.hasRemaining():
                return []

            country_num = res.getShort()
            countries = []
            for _ in range(country_num):
                if not res.hasRemaining():
                    break
                cid = res.getInt()
                cname = res.getString()
                tax1 = res.getInt() if res.hasRemaining() else 0
                tax3 = res.getInt() if res.hasRemaining() else 0
                scale = res.getByte() if res.hasRemaining() else 0
                is_applied = res.getBoolean() if res.hasRemaining() else False
                countries.append({
                    "id": cid,
                    "name": cname,
                    "tax1": tax1,
                    "tax3": tax3,
                    "scale": scale,
                    "is_applied": is_applied
                })
            return countries
        except Exception as e:
            safe_print(f"  [LỖI TRUY VẤN DS QUỐC GIA] {e}")
            return []

    @staticmethod
    def search_country_by_name(conn: PVTKConnection, name_keyword: str) -> Optional[Dict[str, Any]]:
        """
        Tìm kiếm quốc gia theo Tên qua Opcode 0x3A9C.
        Tự động thử tìm theo từ khóa trước, nếu không thấy thì thử duyệt qua danh sách tất cả quốc gia.
        """
        kw = name_keyword.strip()
        if not kw:
            return None

        def _match(list_c, target_kw):
            t_low = target_kw.lower()
            # 1. Khớp chính xác tuyệt đối
            for c in list_c:
                if c["name"].lower() == t_low:
                    return c
            # 2. Khớp chuỗi con
            for c in list_c:
                c_low = c["name"].lower()
                if t_low in c_low or c_low in t_low:
                    return c
            # 3. Nếu server chỉ trả đúng 1 kết quả duy nhất
            if len(list_c) == 1:
                return list_c[0]
            return None

        # 1. Tìm trực tiếp theo từ khóa người dùng nhập (VD: "ODYSSEY")
        found = CountryManager.query_country_list(conn, keyword=kw)
        m = _match(found, kw)
        if m:
            return m

        # 2. Fallback: Lấy danh sách toàn bộ server (keyword = "")
        found_all = CountryManager.query_country_list(conn, keyword="", page_size=50)
        m2 = _match(found_all, kw)
        if m2:
            return m2

        return None

    @staticmethod
    def apply_country(conn: PVTKConnection, country_id: int) -> Tuple[bool, str]:
        """
        Xin gia nhập quốc gia theo ID qua Opcode 0x3AA3 (createCountryApply trong MsgHandler.smali).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_APPLY) # 0x3AA3
            msg.putInt(country_id)
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_APPLY, timeout=4.0)
            if not res:
                return False, "Server không phản hồi yêu cầu gia nhập (Timeout)"
            
            if res.hasRemaining():
                rs = res.getByte()
                if rs < 0:
                    err = res.getString() if res.hasRemaining() else f"Mã lỗi {rs}"
                    return False, err
            return True, f"Đã gửi yêu cầu vào quốc gia #{country_id} thành công! (Chờ Quốc Vương duyệt)"
        except Exception as e:
            return False, f"Lỗi xin vào quốc gia: {e}"


    @staticmethod
    def enter_country(conn: PVTKConnection, country_id: int) -> Tuple[bool, str]:
        """
        Dịch chuyển vào bản đồ Quốc Gia qua Opcode 0x3AA1 (createJumpCountryMessage trong MsgHandler.smali).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_ENTER) # 0x3AA1
            msg.putInt(country_id)
            msg.putInt(64) # blockFlag = 64
            msg.putInt(0)  # entryTaxMoney1 = 0
            msg.putInt(0)  # entryTaxMoney3 = 0
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_ENTER, timeout=4.5)
            if not res or not res.hasRemaining():
                return False, "Server không phản hồi dịch chuyển vào quốc gia (Timeout)"
            
            rs = res.getByte()
            if rs < 0:
                err = res.getString() if res.hasRemaining() else f"Mã lỗi {rs}"
                return False, err
            return True, "Đã vào bản đồ Quốc Gia thành công!"
        except Exception as e:
            return False, f"Lỗi vào quốc gia: {e}"

    @staticmethod
    def find_country_mission_npc(conn: PVTKConnection) -> int:
        """
        Tìm ID của NPC 'Nhiệm Vụ Quốc Gia' trong bản đồ hiện tại qua Opcode 0x2917.
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_SCENE_GETNPC) # 0x2917
            msg.putByte(0) # query all NPCs in scene
            res = conn.send_and_wait(msg, expected_type=proto.CG_SCENE_GETNPC, timeout=2.5)
            if res and res.hasRemaining():
                count = res.getByte()
                for _ in range(count):
                    if not res.hasRemaining():
                        break
                    sign_len = res.getByte() & 0xFF
                    res.offset += sign_len
                    npc_id = res.getByte()
                    res.getByte()   # missionGroupID
                    res.getByte()   # npcType
                    res.getShort()  # icon1
                    res.getShort()  # businessIcon
                    npc_name = res.getString() if res.hasRemaining() else ""
                    if "nhiệm vụ" in npc_name.lower():
                        return npc_id
        except Exception:
            pass
        return 0

    @staticmethod
    def get_valid_missions(conn: PVTKConnection) -> List[Dict[str, Any]]:
        """
        Lấy danh sách nhiệm vụ quốc gia khả dụng hôm nay qua Opcode 0x3AB9 (CG_COUNTRY_GET_VALID_MISSION).
        """
        missions = []
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_GET_VALID_MISSION) # 0x3AB9
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_GET_VALID_MISSION, timeout=4.5)
            if not res or not res.hasRemaining():
                return missions

            # Danh sách 1: missionCommonList (NV Dân)
            size1 = res.getShort()
            for _ in range(size1):
                if not res.hasRemaining():
                    break
                res.getByte() # byte0
                left_num = res.getShort()
                right_num = res.getShort()
                m_bytes = res.getBytes()
                if res.hasRemaining():
                    res.getByte() # byte flag in ObjectData
                m_info = CountryManager._parse_mission_info(m_bytes, left_num, right_num, "Dân")
                if m_info:
                    missions.append(m_info)

            # Danh sách 2: missionAppointList (NV Phái)
            if res.hasRemaining():
                size2 = res.getShort()
                for _ in range(size2):
                    if not res.hasRemaining():
                        break
                    res.getByte() # byte0
                    left_num = res.getShort()
                    right_num = res.getShort()
                    m_bytes = res.getBytes()
                    if res.hasRemaining():
                        res.getByte()
                    m_info = CountryManager._parse_mission_info(m_bytes, left_num, right_num, "Phái")
                    if m_info:
                        missions.append(m_info)

        except Exception as e:
            safe_print(f"  [LỖI TRUY VẤN NHIỆM VỤ QG] {e}")
        return missions

    @staticmethod
    def _parse_mission_info(m_bytes: bytes, left_num: int, right_num: int, category: str) -> Optional[Dict[str, Any]]:
        """
        Phân tích chi tiết nhị phân của Mission, trích xuất ID, Tên, yêu cầu Độ Phồn Vinh, Bạc.
        """
        import io
        import struct
        import re

        if not m_bytes or len(m_bytes) < 4:
            return None

        try:
            bio = io.BytesIO(m_bytes)

            def r_short():
                return struct.unpack(">h", bio.read(2))[0]
            def r_byte():
                return struct.unpack(">b", bio.read(1))[0]
            def r_int():
                return struct.unpack(">i", bio.read(4))[0]
            def r_utf():
                data = bio.read(2)
                if len(data) < 2:
                    return ""
                slen = struct.unpack(">H", data)[0]
                if slen == 0:
                    return ""
                return bio.read(slen).decode("utf-8", errors="ignore")

            m_id = r_short()
            name = r_utf()
            level = r_byte()
            simple_desc = r_utf()
            desc = r_utf()
            setting = r_short() & 0xFFFF
            map_id = r_short()
            npc_id = r_byte()
            radar_map_id = r_short()
            if radar_map_id > 0:
                bio.read(2) # radarGx, radarGy
            r_utf() # dialogNotStartNotReady
            r_utf() # dialogStartNotFinish
            r_utf() # dialogStartFinish
            exp = r_int()
            money2 = r_short()
            money3 = r_short()

            # Phần thưởng đặc biệt quốc gia (setting & 0x200)
            if setting & 0x200:
                bio.read(14) # honor, land, prosperity, people, wood, stone, iron (7 * short)

            if setting & 0x8000:
                bio.read(2) # countryArmy (short)

            # Thực thi nhảy map (setting & 0x400)
            if setting & 0x400:
                aj_map = r_short()
                if aj_map > 0:
                    bio.read(2)
                sj_map = r_short()
                if sj_map > 0:
                    bio.read(2)
                bio.read(8) # acceptBattleID(2), submitBattleID(2), mapID2(2), gx(1), gy(1)

            # Đọc phần còn lại (Items, Conditions)
            remaining_bytes = bio.read()

            all_text = f"{name} {simple_desc} {desc}".lower()

            # 1. Kiểm tra yêu cầu Độ Phồn Vinh
            # CONDITION_CITY_DEGREE trong smali là byte 0x1D (29)
            # Hoặc tên/mô tả có từ khóa "phồn vinh", "phồng vinh", "di dời kinh tế"
            requires_prosperity = False
            if "phồn vinh" in all_text or "phồng vinh" in all_text or "di dời" in name.lower() or "kinh tế" in name.lower():
                requires_prosperity = True
            elif b'\x1d' in remaining_bytes:
                requires_prosperity = True

            # 2. Kiểm tra Bạc yêu cầu (CONDITION_MONEY = 0x03)
            req_silver = 0
            m_sil = re.search(r'(?:yêu cầu bạc|bạc|phí)\s*[:=]?\s*(\d+)', all_text)
            if m_sil:
                req_silver = int(m_sil.group(1))

            return {
                "id": m_id,
                "name": name or f"Nhiệm vụ #{m_id}",
                "level": level,
                "npc_id": npc_id,
                "category": category,
                "left_num": left_num,
                "right_num": right_num,
                "requires_prosperity": requires_prosperity,
                "req_silver": req_silver,
                "desc": simple_desc or desc,
            }
        except Exception:
            # Fallback nếu cấu trúc có biến thể: trích xuất sơ bộ
            m_id = struct.unpack(">h", m_bytes[:2])[0] if len(m_bytes) >= 2 else 0
            raw_str = m_bytes.decode("utf-8", errors="ignore").lower()
            req_prop = ("phồn vinh" in raw_str) or ("phồng vinh" in raw_str) or ("di dời" in raw_str)
            return {
                "id": m_id,
                "name": f"Nhiệm vụ #{m_id}",
                "level": 1,
                "npc_id": 0,
                "category": category,
                "left_num": left_num,
                "right_num": right_num,
                "requires_prosperity": req_prop,
                "req_silver": 0,
                "desc": "",
            }

    @staticmethod
    def accept_and_submit_mission(conn: PVTKConnection, npc_id: int, mission_id: int) -> Tuple[bool, str]:
        """
        Nhận và hoàn thành nhiệm vụ Quốc Gia (CG_TASK_ACCEPT 0x38A6 & CG_TASK_DELIVER 0x38A7).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            
            # Gửi CG_TASK_ACCEPT (0x38A6)
            msg = Message(proto.CG_TASK_ACCEPT)
            msg.putShort(npc_id)
            msg.putShort(mission_id)
            res = conn.send_and_wait(msg, expected_type=proto.CG_TASK_ACCEPT, timeout=3.5)
            if not res or not res.hasRemaining():
                return False, "Server không phản hồi nhận nhiệm vụ (Timeout)"

            is_submit = res.getBoolean()
            if is_submit:
                # Dạng nhiệm vụ tự nộp ngay khi nhận
                submit_rs = res.getByte()
                if submit_rs == 0:
                    return True, "Nhận và tự động nộp thành công!"
                else:
                    err = res.getString() if res.hasRemaining() else f"Mã phản hồi {submit_rs}"
                    return False, err
            else:
                # Dạng nhiệm vụ đã vào Quest Log -> Gửi tiếp lệnh Trả nhiệm vụ (CG_TASK_DELIVER)
                while conn.receive_message(timeout=0.01):
                    pass
                deliv_msg = Message(proto.CG_TASK_DELIVER)
                deliv_msg.putShort(npc_id)
                deliv_msg.putShort(mission_id)
                deliv_msg.putInt(0) # selectRewardItemID = 0
                d_res = conn.send_and_wait(deliv_msg, expected_type=proto.CG_TASK_DELIVER, timeout=3.5)
                if not d_res or not d_res.hasRemaining():
                    return True, "Đã nhận nhiệm vụ vào danh sách."
                
                d_rs = d_res.getByte()
                if d_rs == 0:
                    return True, "Trả nhiệm vụ thành công!"
                else:
                    d_err = d_res.getString() if d_res.hasRemaining() else f"Mã lỗi trả Q {d_rs}"
                    return False, d_err
        except Exception as e:
            return False, f"Lỗi nộp Q: {e}"


    @staticmethod
    def leave_country(conn: PVTKConnection) -> Tuple[bool, str]:
        """
        Rời khỏi quốc gia hiện tại qua Opcode 0x3AA7 (createCountryLeaveMsg trong MsgHandler.smali).
        """
        try:
            while conn.receive_message(timeout=0.01):
                pass
            msg = Message(proto.CG_COUNTRY_LEAVE) # 0x3AA7
            res = conn.send_and_wait(msg, expected_type=proto.CG_COUNTRY_LEAVE, timeout=4.0)
            if not res:
                return False, "Server không phản hồi lệnh rời quốc gia (Timeout)"
            
            if res.hasRemaining():
                rs = res.getByte()
                if rs < 0:
                    err = res.getString() if res.hasRemaining() else f"Mã lỗi {rs}"
                    return False, err
            return True, "Đã rời quốc gia thành công!"
        except Exception as e:
            return False, f"Lỗi rời quốc gia: {e}"



class CountryRunner(CharacterLoopRunner):
    """
    Runner đa luồng xử lý các tác vụ Quốc Gia cho toàn bộ danh sách tài khoản.
    """
    def __init__(self, accounts: List[Tuple], mode: str, kwargs: Optional[Dict[str, Any]] = None, max_workers: int = None):
        super().__init__(accounts, thread_name="Quốc Gia", max_retries=3, max_workers=max_workers)
        self.mode = mode
        self.kwargs = kwargs or {}
        self.target_country_name = self.kwargs.get("target_country_name", "")
        self.resolved_country_id = self.kwargs.get("target_country_id", 0)
        self._resolve_lock = threading.Lock()

    def process_character(self, game_conn, char, task: Tuple, char_tag: str, account_result: Dict[str, Any]):
        # Đồng bộ trạng thái quốc gia chính xác từ server
        cid, cname, crank = CountryManager.sync_character_country(game_conn, char.id)
        char.country_id = cid
        char.country_name = cname
        char.country_rank = crank

        rank_title = RANK_NAMES.get(crank, f"Chức vụ #{crank}") if cid > 0 else "Chưa có"

        # 1. CỐNG HIẾN QUỐC GIA
        if self.mode == "donate":
            if cid <= 0:
                self.log(f"{char_tag} \033[93m[BỎ QUA]\033[0m Nhân vật chưa gia nhập quốc gia.")
                return
            d_type = self.kwargs.get("donate_type", 2) # Mặc định Bạc
            d_val = self.kwargs.get("donate_value", 10000)
            self.log(f"{char_tag} 🏰 [QG: {cname}] Đang cống hiến {d_val:,}...")
            ok, msg = CountryManager.donate(game_conn, d_type, d_val)
            if ok:
                self.log(f"{char_tag} \033[92m[THÀNH CÔNG]\033[0m {msg}")
            else:
                self.log(f"{char_tag} \033[91m[THẤT BẠI]\033[0m {msg}")

        # 2. XIN GIA NHẬP QUỐC GIA (Tự động tìm ID từ Tên Quốc Gia nếu nhập Tên)
        elif self.mode == "apply":
            if cid > 0:
                self.log(f"{char_tag} \033[93m[ĐÃ CÓ QUỐC GIA]\033[0m Nhân vật đang ở quốc gia '{cname}' (ID: {cid}). Bỏ qua.")
                return

            # Tự động tìm ID nếu chưa có
            if self.resolved_country_id <= 0 and self.target_country_name:
                with self._resolve_lock:
                    if self.resolved_country_id <= 0:
                        self.log(f"{char_tag} 🔍 Đang tìm kiếm Quốc Gia '{self.target_country_name}' trên server...")
                        c_info = CountryManager.search_country_by_name(game_conn, self.target_country_name)
                        if c_info:
                            self.resolved_country_id = c_info["id"]
                            self.target_country_name = c_info["name"]
                            self.log(f"{char_tag} \033[92m[TÌM THẤY]\033[0m Quốc Gia '{c_info['name']}' (ID: {c_info['id']} | Cấp {c_info.get('scale', 1)})")
                        else:
                            self.log(f"{char_tag} \033[91m[KHÔNG TÌM THẤY]\033[0m Không tìm thấy quốc gia nào có tên '{self.target_country_name}'!")

            target_cid = self.resolved_country_id
            if target_cid <= 0:
                self.log(f"{char_tag} \033[91m[BỎ QUA]\033[0m Không xác định được ID của Quốc Gia '{self.target_country_name}'.")
                return

            self.log(f"{char_tag} 📝 Đang gửi đơn xin gia nhập Quốc Gia '{self.target_country_name}' (ID: #{target_cid})...")
            ok, msg = CountryManager.apply_country(game_conn, target_cid)
            if ok:
                self.log(f"{char_tag} \033[92m[ĐÃ GỬI ĐƠN]\033[0m {msg}")
            else:
                self.log(f"{char_tag} \033[91m[LỖI GỬI ĐƠN]\033[0m {msg}")

        # 3. RỜI QUỐC GIA
        elif self.mode == "leave":
            if cid <= 0:
                self.log(f"{char_tag} ℹ️ Nhân vật không ở quốc gia nào.")
                return
            self.log(f"{char_tag} 🚪 Đang rời khỏi quốc gia '{cname}' (ID: {cid})...")
            ok, msg = CountryManager.leave_country(game_conn)
            if ok:
                self.log(f"{char_tag} \033[92m[ĐÃ RỜI QUỐC GIA]\033[0m {msg}")
            else:
                self.log(f"{char_tag} \033[91m[LỖI RỜI QUỐC GIA]\033[0m {msg}")


def run_country_manager_cli(active_accounts: List[Tuple]):
    """
    Menu tương tác chính cho Hệ thống Quốc Gia.
    """
    while True:
        print("\n" + "=" * 75)
        print("               🏰 HỆ THỐNG QUẢN LÝ & TỰ ĐỘNG HÓA QUỐC GIA")
        print("===========================================================================")
        print("  [1] 💰 Cống Hiến Quốc Gia Tự Động (Quyên góp Bạc / Vàng / Gỗ / Đá / Sắt)")
        print("  [2] 📝 Xin Gia Nhập Quốc Gia Hàng Loạt (Nhập Tên Quốc Gia để tìm và gửi đơn)")
        print("  [3] 🚪 Rời Quốc Gia Hàng Loạt (Tất cả acc)")
        print("  [0] Quay lại Menu chính")
        print("===========================================================================")

        try:
            choice = input("-> Chọn tính năng Quốc Gia [0-3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "0":
            break

        # [1] Cống hiến
        elif choice == "1":
            print("\nChọn loại cống hiến:")
            print("  [1] Vàng")
            print("  [2] Bạc (Khuyên dùng)")
            print("  [3] Gỗ (Vật phẩm)")
            print("  [4] Đá (Vật phẩm)")
            print("  [5] Sắt (Vật phẩm)")
            t_choice = input("-> Chọn loại [1-5] (Enter=2 Bạc): ").strip()
            d_type = int(t_choice) if t_choice in ("1", "2", "3", "4", "5") else 2
            
            type_names = {1: "Vàng", 2: "Bạc", 3: "Gỗ", 4: "Đá", 5: "Sắt"}
            default_val = "10000" if d_type == 2 else "10"
            val_input = input(f"-> Nhập số lượng {type_names[d_type]} muốn cống hiến mỗi nhân vật (Enter={default_val}): ").strip()
            d_val = int(val_input) if val_input.isdigit() else int(default_val)

            print(f"\n🚀 Bắt đầu cống hiến {d_val:,} {type_names[d_type]} cho {len(active_accounts)} tài khoản...")
            runner = CountryRunner(active_accounts, mode="donate", kwargs={"donate_type": d_type, "donate_value": d_val})
            runner.run_all()
            print("\n✅ Hoàn thành cống hiến quốc gia!")

        # [2] Xin gia nhập
        elif choice == "2":
            target_input = input("-> Nhập TÊN Quốc Gia muốn xin vào (Ví dụ: ODYSSEY): ").strip()
            if not target_input:
                print("\033[91m[LỖI]\033[0m Tên Quốc Gia không được để trống!")
                continue
            
            if target_input.isdigit():
                target_cid = int(target_input)
                kwargs = {"target_country_id": target_cid, "target_country_name": f"#{target_cid}"}
                display_target = f"#{target_cid}"
            else:
                kwargs = {"target_country_id": 0, "target_country_name": target_input}
                display_target = f"'{target_input}'"

            print(f"\n🚀 Bắt đầu tìm kiếm và gửi đơn xin vào Quốc Gia {display_target} cho {len(active_accounts)} tài khoản...")
            runner = CountryRunner(active_accounts, mode="apply", kwargs=kwargs)
            runner.run_all()
            print("\n✅ Hoàn thành gửi đơn xin gia nhập!")

        # [3] Rời quốc gia
        elif choice == "3":
            confirm = input("⚠️ Bạn có CHẮC CHẮN muốn cho tất cả tài khoản rời quốc gia? (y/n): ").strip().lower()
            if confirm == "y":
                print(f"\n🚪 Bắt đầu cho {len(active_accounts)} tài khoản rời quốc gia...")
                runner = CountryRunner(active_accounts, mode="leave")
                runner.run_all()
                print("\n✅ Hoàn thành rời quốc gia!")
            else:
                print("Đã hủy thao tác.")

