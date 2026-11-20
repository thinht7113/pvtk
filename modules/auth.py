"""
PVTK Authentication & Character Management Module
Handles:
1. Master Gateway Version Check (0x1388) & Login (0x138A)
2. Game Server Handshake (0x0001)
3. Fetching Character List (0x2711)
4. Selecting Character to Enter World (0x0002)
5. Leaving Character to Selection Screen (0x0003) & Refreshing State (0x2711)
"""

import os
import sys
import time
import random
import threading
import hashlib
from typing import List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.message import Message
from core.network import PVTKConnection
import core.protocol as proto
from core.device_faker import DeviceFaker
from core.captcha_solver import CaptchaSolver

# Controlled-concurrency semaphore (10 parallel logins) to avoid Master Gateway backlog drop
MASTER_LOGIN_SEMAPHORE = threading.Semaphore(10)


class CharacterInfo:
    """Represents a character retrieved from the PVTK Master Server."""
    def __init__(self, char_id: int, name: str, sex: int, race: int, job: int, level: int, vip: int, map_name: str, status: int = 0):
        self.id = char_id
        self.name = name
        self.sex = sex
        self.race = race
        self.job = job
        self.level = level
        self.vip = vip
        self.map_name = map_name
        self.status = status
        self.hp = 0
        self.hp_max = 0
        self.mp = 0
        self.mp_max = 0
        self.exp = 0
        self.country_id = -1
        self.country_name = ""
        self.country_rank = 0
        self.gold = 0    # money1: KNB (Vàng)
        self.leaf = 0    # money2: Lá
        self.silver = 0  # money3: Bạc

    def is_in_thanh_vuc(self) -> bool:
        return "Thánh Vực" in self.map_name or "Thanh Vuc" in self.map_name

    def __repr__(self):
        return f"<Character [{self.id}] Name='{self.name}' Lv={self.level} Map='{self.map_name}'>"


class AuthManager:
    @staticmethod
    def load_accounts(filepath: str) -> List[Tuple[str, str, Optional[str]]]:
        """
        Loads accounts from file.
        Supports formats:
        - username|password
        - username|password|proxy (e.g. 103.14.2.1:8080 or socks5://user:pass@ip:port)
        """
        accounts = []
        if not filepath or not os.path.exists(filepath):
            return accounts

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    user, pwd, proxy = parts[0], parts[1], parts[2]
                    accounts.append((user, pwd, proxy if proxy else None))
                elif len(parts) >= 2:
                    user, pwd = parts[0], parts[1]
                    accounts.append((user, pwd, None))
        return accounts

    @staticmethod
    def master_login(master_host: str, master_port: int, username: str, password: str, max_retries: int = 2, proxy: Optional[str] = None) -> Tuple[Optional[Tuple[int, int, int]], str]:
        """
        Connects to Master Gateway server, sends version check (0x1388) and login (0x138A) with retry.
        Uses high-throughput concurrency semaphore (3 parallel) so multi-threaded workers log in smoothly without gateway drops.
        Uses DeviceFaker to generate a realistic, unique mobile device profile for each account.
        Returns: ((ukey, session_id, login_setting), error_message)
        """
        dev = DeviceFaker.get_device_fingerprint(username)

        with MASTER_LOGIN_SEMAPHORE:
            time.sleep(random.uniform(0.15, 0.40))
            for attempt in range(max_retries):
                conn = PVTKConnection(master_host, master_port, timeout=8.0, proxy=proxy)
                if not conn.connect():
                    conn.close()
                    if attempt < max_retries - 1:
                        time.sleep(0.8 + random.uniform(0.2, 0.5))
                        continue
                    return None, f"Không thể kết nối tới Master Server {master_host}:{master_port}"

                try:
                    # 1. Version Check (Opcode 0x1388 - LC_CHECKEDITION)
                    msg_ver = Message(proto.LC_CHECKEDITION if hasattr(proto, "LC_CHECKEDITION") else 0x1388)
                    msg_ver.putShort(1000)
                    msg_ver.putInt(proto.CLIENT_BUILD_VERSION)
                    msg_ver.putInt(111)
                    msg_ver.putByte(5)
                    msg_ver.putString("")
                    msg_ver.putByte(0)

                    if not conn.send_message(msg_ver):
                        if attempt < max_retries - 1:
                            time.sleep(0.6 + random.uniform(0.1, 0.4))
                            continue
                        return None, "Gửi gói tin Kiểm tra phiên bản thất bại"

                    res_ver = conn.receive_message(timeout=7.0)
                    if not res_ver:
                        if attempt < max_retries - 1:
                            time.sleep(0.6 + random.uniform(0.1, 0.4))
                            continue
                        return None, "Không nhận được phản hồi kiểm tra phiên bản"

                    # 2. Master Login (Opcode 0x138A - LC_PLAYER_LOGIN)
                    msg_login = Message(proto.LC_PLAYER_LOGIN)
                    msg_login.putString(username)
                    msg_login.putString(password)
                    msg_login.putString(dev["client_ip"])
                    msg_login.putString(dev["os"])
                    msg_login.putString(dev["handset"])

                    if not conn.send_message(msg_login):
                        if attempt < max_retries - 1:
                            time.sleep(0.6 + random.uniform(0.1, 0.4))
                            continue
                        return None, "Gửi gói tin Đăng nhập thất bại"

                    res_login = conn.receive_message(timeout=8.0)
                    if not res_login:
                        if attempt < max_retries - 1:
                            time.sleep(0.6 + random.uniform(0.1, 0.4))
                            continue
                        return None, "Không nhận được phản hồi đăng nhập"

                    if res_login.type == proto.LC_PLAYER_LOGIN:
                        status = res_login.getByte()
                        if status == 0:
                            ukey = res_login.getLong()
                            session_id = res_login.getInt()
                            login_setting = res_login.getInt()
                            return (ukey, session_id, login_setting), ""
                        else:
                            err_msg = res_login.getString() if res_login.hasRemaining() else f"Lỗi không xác định ({status})"
                            return None, f"Đăng nhập thất bại: {err_msg}"
                    else:
                        if attempt < max_retries - 1:
                            time.sleep(0.6 + random.uniform(0.1, 0.4))
                            continue
                        return None, f"Phản hồi không mong muốn (type=0x{res_login.type:04X})"

                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(0.6 + random.uniform(0.1, 0.4))
                        continue
                    return None, f"Ngoại lệ khi đăng nhập Master Server: {e}"
                finally:
                    conn.close()

            return None, "Vượt quá số lần thử đăng nhập tối đa"

    @staticmethod
    def connect_game_server(game_host: str, game_port: int, ukey: int, session_id: int, max_retries: int = 3, proxy: Optional[str] = None) -> Optional[PVTKConnection]:
        for attempt in range(max_retries):
            conn = PVTKConnection(game_host, game_port, timeout=10.0, proxy=proxy)
            if not conn.connect():
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                return None

            try:
                # Send Handshake (Opcode 0x0001 - CG_LOGIN_PLAYERCHECK)
                msg_handshake = Message(proto.CG_LOGIN_PLAYERCHECK)
                msg_handshake.putLong(ukey)
                msg_handshake.putInt(session_id)
                msg_handshake.putByte(5)
                msg_handshake.putInt(proto.CLIENT_BUILD_VERSION)
                msg_handshake.putByte(2)

                if not conn.send_message(msg_handshake):
                    conn.close()
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    return None

                res = conn.receive_message(timeout=8.0)
                if res and res.type == proto.CG_LOGIN_PLAYERCHECK:
                    code = res.getByte()
                    if code == 0:
                        return conn

                conn.close()
            except Exception:
                conn.close()

            if attempt < max_retries - 1:
                time.sleep(0.5)

        return None

    @classmethod
    def reconnect_game_session(cls, game_host: str, game_port: int, master_host: str, master_port: int, username: str, password: str, proxy: Optional[str] = None) -> Tuple[Optional[PVTKConnection], int]:
        """
        Safely re-establishes a fresh Game Server session by requesting a new session token from Master Server.
        Guarantees that reconnect never fails due to expired one-time session_id.
        """
        login_data, err_reason = cls.master_login(master_host, master_port, username, password, max_retries=3, proxy=proxy)
        if not login_data:
            if "Sai tài khoản/mật khẩu" in err_reason or "bị khóa" in err_reason or "không tồn tại" in err_reason:
                try:
                    from core.tele_notifier import TeleNotifier
                    TeleNotifier.get_instance().send_error_alert(
                        title="Lỗi Tài Khoản (Không Thể Vào Game)",
                        detail=f"Tài khoản: <b>{username}</b>\nLý do: <code>{err_reason}</code>\nTrạng thái: Không thể kết nối lại phiên game (Bị khóa hoặc sai mật khẩu)!",
                        level="CRITICAL",
                        source=f"[Session Reconnect | {username}]",
                        cooldown=300.0
                    )
                except Exception:
                    pass
            return None, 0
        ukey, session_id, login_setting = login_data
        time.sleep(0.2)
        conn = cls.connect_game_server(game_host, game_port, ukey, session_id, proxy=proxy)
        if conn:
            try:
                cls.fetch_character_list(conn)
            except Exception:
                conn.close()
                return None, 0
        return conn, login_setting

    @staticmethod
    def fetch_character_list(conn: PVTKConnection) -> Optional[List[CharacterInfo]]:
        """
        Requests the list of characters on the current account via Opcode 0x2711 (CG_LOGIN_ACTORLIST).
        """
        try:
            msg = Message(proto.CG_LOGIN_ACTORLIST)
            res = conn.send_and_wait(msg, expected_type=proto.CG_LOGIN_ACTORLIST, timeout=8.0)
            
            if not res:
                raise ConnectionError("Timeout hoặc mất kết nối khi tải danh sách nhân vật.")

            char_count = res.getByte()
            characters: List[CharacterInfo] = []

            for _ in range(char_count):
                char_id = res.getInt()
                name = res.getString()
                sex = res.getByte()
                race = res.getByte()
                job = res.getByte()
                level = res.getByte()
                vip = res.getByte()
                map_name = res.getString()
                icon1 = res.getInt()
                icon2 = res.getInt()
                icon3 = res.getInt()
                status = res.getInt()

                if status & 4 != 0:
                    res.getShort()
                if status & 512 != 0:
                    is_off = res.getBoolean()
                    if not is_off:
                        res.getShort()
                        res.getString()

                c = CharacterInfo(char_id, name, sex, race, job, level, vip, map_name, status)
                try:
                    from modules.character_registry import CharacterRegistry
                    from core.config import settings
                    stats = CharacterRegistry.get_character_stats(settings.game_server_name, getattr(conn, "username", ""), len(characters))
                    if stats.get("hp_max", 0) > 0:
                        c.hp_max = stats["hp_max"]
                        c.hp = stats.get("hp", c.hp_max)
                    if stats.get("mp_max", 0) > 0:
                        c.mp_max = stats["mp_max"]
                        c.mp = stats.get("mp", c.mp_max)
                except Exception:
                    pass
                characters.append(c)

            return characters

        except Exception as e:
            if isinstance(e, ConnectionError):
                raise e
            raise ConnectionError(f"Lỗi parse danh sách nhân vật: {e}")

    @staticmethod
    def compute_character_icon1(sex: int, race: int = 0, job: int = 1, model: int = 0) -> int:
        """
        Computes icon1 bitmask for character appearance (Head, Hair, Face, Hands, Feet)
        matching ListPlayer.setStyleDataToIcon():
        - bit 0: sex (0 = Nam, 1 = Nữ)
        - bit 1-2: race (0 = Đông Phương)
        - bit 3-6: job (1 = Hiệp Khách, 2 = Tu Chân, 3 = Chiến Sĩ, 4 = Pháp Sư)
        - bit 7-10: hairStyle (1, 2, 3)
        - bit 11-12: hairColor (0)
        - bit 13-16: faceStyle (1, 2)
        - bit 17: hand (0x20000)
        - bit 21: feet (0x200000)
        """
        bit_sex = sex & 0x01
        bit_race = race & 0x03
        bit_job = job & 0x0F

        icon1 = (bit_sex << 0) | (bit_race << 1) | (bit_job << 3)
        icon1 |= 0x20000   # hand = 1 (1 << 17)
        icon1 |= 0x200000  # feet = 1 (1 << 21)

        # Hair and Face model style
        if model == 0:
            hair_style = 1
            hair_color = 0
            face_style = 1
        elif model == 1:
            hair_style = 2
            hair_color = 0
            face_style = 1
        else:
            hair_style = 3
            hair_color = 0
            face_style = 2

        icon1 |= (hair_style & 0x0F) << 7
        icon1 |= (hair_color & 0x03) << 11
        icon1 |= (face_style & 0x0F) << 13

        return icon1

    @staticmethod
    def generate_clean_character_name() -> str:
        """
        Generates a clean, random character name guaranteed to have NO letter 'i' or 'I'
        (Tránh bị hệ thống game che dấu sao * như k*em, tr*eu, th*en, ph*).
        """
        prefixes = [
            "long", "phong", "tuyet", "vu", "than", "bang", "hoa", "anh", "duc", "ba", "dao",
            "quang", "hoang", "son", "van", "ha", "hung", "ngoc", "bach", "dong", "nam", "bac",
            "tay", "han", "lam", "thuc", "nga", "nguyet", "duong"
        ]
        suffixes = [
            "khach", "vuong", "de", "dao", "tam", "huyen", "than", "ton", "long", "phong", "son",
            "quang", "bao", "chau", "quan", "cuong", "mon", "truong", "tung", "bang", "ha",
            "van", "hoang", "nga", "nguyet"
        ]
        p = random.choice(prefixes)
        s = random.choice(suffixes)
        rand_id = random.randint(10, 999)
        name = f"{p}{s}{rand_id}"
        return name.replace('i', '').replace('I', '')

    @staticmethod
    def create_character(conn: PVTKConnection, name: Optional[str] = None, sex: Optional[int] = None, job: int = 1, race: int = 0, device_id: Optional[str] = None) -> Tuple[bool, Optional[CharacterInfo], str]:
        """
        Creates a new character on the current account via Opcode 0x2712 (CG_LOGIN_CREATEACTOR).
        - job: 1 (Default: Hiệp Khách)
        - race: 0 (Default: Đông Phương)
        - sex: 0 (Nam) or 1 (Nữ), random if None
        - name: Random clean name (no 'i') if None
        Returns: (is_success, CharacterInfo or None, message)
        """
        if sex is None:
            sex = random.choice([0, 1])
        
        if not name:
            name = AuthManager.generate_clean_character_name()

        if not device_id:
            device_id = hashlib.md5(f"device_create_{name}".encode("utf-8")).hexdigest()[:16]

        sex_name = "Nam" if sex == 0 else "Nữ"
        model_idx = random.choice([0, 1, 2])
        icon1 = AuthManager.compute_character_icon1(sex=sex, race=race, job=job, model=model_idx)

        for attempt in range(3):
            try:
                # Drain residual packets
                while conn.receive_message(timeout=0.02):
                    pass

                msg = Message(proto.CG_LOGIN_CREATEACTOR)
                msg.putByte(sex)        # 0 = Nam, 1 = Nữ (Chuẩn Define.SEX_MALE / SEX_FEMALE)
                msg.putByte(race)       # 0 = Đông Phương
                msg.putByte(job)        # 1 = Hiệp Khách
                msg.putInt(icon1)       # icon1 (Tóc, Khuôn mặt, Đầu, Tay, Chân)
                msg.putInt(0)           # icon2
                msg.putInt(0)           # icon3
                msg.putString(name)     # Tên nhân vật (100% không có chữ i)
                msg.putString(device_id)# Device ID

                res = conn.send_and_wait(msg, expected_type=proto.CG_LOGIN_CREATEACTOR, timeout=7.0)
                if not res:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    return False, None, "Không nhận được phản hồi tạo nhân vật từ Server"

                time.sleep(0.3)
                # Re-fetch character list to get newly created character
                chars = AuthManager.fetch_character_list(conn)
                for c in chars:
                    if c.name.lower() == name.lower():
                        return True, c, f"Tạo nhân vật '{c.name}' (Phái: Hiệp Khách | Giới tính: {sex_name}) thành công!"

                if chars:
                    return True, chars[-1], f"Tạo nhân vật '{chars[-1].name}' (Phái: Hiệp Khách | Giới tính: {sex_name}) thành công!"

                # If name was taken, retry with a different clean name
                name = AuthManager.generate_clean_character_name()
                time.sleep(0.4)
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.5)
                    continue
                return False, None, f"Lỗi tạo nhân vật: {e}"

        return False, None, "Tạo nhân vật thất bại sau 3 lần thử"

    @staticmethod
    def enter_character(conn: PVTKConnection, char_id: int, login_setting: int = 0, device_id: Optional[str] = None, char_info: Optional[CharacterInfo] = None) -> bool:
        """
        Enters world with the selected character via Opcode 0x0002 (CG_LOGIN_ACTORENTER).
        Sends unique Android Device ID / MAC fingerprint per account.
        """
        if not device_id:
            device_id = hashlib.md5(f"device_{char_id}".encode("utf-8")).hexdigest()[:16]

        setattr(conn, "last_enter_error", "")
        for attempt in range(4):
            try:
                # 1. Drain residual packets
                while conn.receive_message(timeout=0.02):
                    pass

                msg = Message(proto.CG_LOGIN_ACTORENTER)
                msg.putInt(char_id)
                msg.putInt(proto.CLIENT_ACTOR_VERSION)
                msg.putInt(login_setting)
                msg.putString("")
                msg.putByte(2)
                msg.putString("")
                msg.putString(device_id)  # Android Device ID (Settings.System.ANDROID_ID / MAC)
                msg.putString("")         # Phone

                res = conn.send_and_wait(msg, expected_type=proto.CG_LOGIN_ACTORENTER, timeout=5.0)
                if not res:
                    setattr(conn, "last_enter_error", "Timeout hoặc không nhận được phản hồi vào game")
                    if attempt < 3:
                        time.sleep(0.6)
                        continue
                    return False

                # Check if Server requires Image Captcha (Code -13 / 243 / 0xF3) or returns error
                if res.hasRemaining():
                    code = res.getByte()
                    if code in (-13, 243):
                        err_text = res.getString() if res.hasRemaining() else ""
                        img_bytes = res.getBytes() if res.hasRemaining() else b""
                        if img_bytes:
                            solved_code = CaptchaSolver.solve_bytes(img_bytes)
                            if solved_code:
                                print(f"[{char_id}] \033[93m[AI CAPTCHA SOLVER]\033[0m Đã nhận diện mã kiểm chứng: '\033[92m{solved_code}\033[0m'. Đang gửi lại xác thực...")
                                msg_retry = Message(proto.CG_LOGIN_ACTORENTER)
                                msg_retry.putInt(char_id)
                                msg_retry.putInt(proto.CLIENT_ACTOR_VERSION)
                                msg_retry.putInt(login_setting)
                                msg_retry.putString(solved_code)
                                msg_retry.putByte(2)
                                msg_retry.putString("")
                                msg_retry.putString(device_id)
                                msg_retry.putString("")

                                res_retry = conn.send_and_wait(msg_retry, expected_type=proto.CG_LOGIN_ACTORENTER, timeout=5.0)
                                if res_retry:
                                    res = res_retry
                                    if res.hasRemaining():
                                        code2 = res.getByte()
                                        if code2 == 0:
                                            print(f"[{char_id}] \033[92m[AI CAPTCHA SOLVER]\033[0m Xác thực Captcha thành công!")
                    elif code < 0:
                        err_msg = res.getString() if res.hasRemaining() else f"Mã lỗi {code}"
                        setattr(conn, "last_enter_error", err_msg)
                        # Xử lý Kẹt Acc (Code -2)
                        if code == -2 or "đăng nhập ở client khác" in err_msg.lower():
                            print(f"[{char_id}] \033[93m[KẸT ACC]\033[0m Nhân vật đang bị kẹt online. Đang ép server đẩy acc ra...")
                            time.sleep(1.0)
                            return False

                        # Tự động khôi phục nhân vật đang trong trạng thái xóa
                        if "xóa" in err_msg or "xoa" in err_msg.lower():
                            print(f"[{char_id}] \033[93m[TỰ ĐỘNG KHÔI PHỤC]\033[0m Nhân vật đang bị đánh dấu xóa. Đang gửi lệnh khôi phục (0x2714)...")
                            try:
                                recover_msg = Message(0x2714)  # CG_LOGIN_RECOVERACTOR
                                recover_msg.putInt(char_id)
                                conn.send_and_wait(recover_msg, expected_type=0x2714, timeout=4.0)
                                time.sleep(0.5)
                                print(f"[{char_id}] \033[92m[KHÔI PHỤC XONG]\033[0m Đã hủy trạng thái xóa. Đang thử vào game lại...")
                            except Exception:
                                pass
                            if attempt < 3:
                                time.sleep(0.5)
                                continue
                                
                        print(f"[{char_id}] \033[93m[SERVER TỪ CHỐI VÀO GAME]\033[0m {err_msg} (Lần thử {attempt + 1}/4)")
                        if attempt < 3:
                            wait_busy = 3.0 * (attempt + 1) if "bận" in err_msg.lower() else 1.0
                            time.sleep(wait_busy)
                            continue
                        return False

                # Trích xuất trực tiếp chỉ số thực tế 100% của nhân vật (HP, MP, EXP, Level) từ Server (Player.fromBytes)
                if res and res.data and char_info:
                    try:
                        char_id_bytes = char_id.to_bytes(4, "big")
                        pos = res.data.find(char_id_bytes)
                        if pos != -1:
                            m = Message(data=res.data[pos:])
                            m.getInt()      # id
                            m.getString()  # name
                            m.getString()  # info
                            m.getString()  # title
                            m.getByte()    # sex
                            m.getByte()    # race
                            m.getByte()    # job
                            lvl = m.getByte()    # level
                            m.getByte()    # level2
                            m.getInt()     # exp2
                            m.getInt()     # expMax2
                            m.getInt()     # icon1
                            m.getInt()     # icon2
                            m.getInt()     # icon3
                            m.getInt()     # setting
                            m.getInt()     # status
                            m.getByte()    # mode
                            country_id = m.getInt() # countryID
                            if country_id >= 0:
                                char_info.country_id = country_id
                                char_info.country_name = m.getString() # countryName
                                char_info.country_rank = m.getByte()   # countryRank
                            exp = m.getInt()      # exp
                            exp_max = m.getInt()  # expMax
                            hp = m.getInt()       # hp
                            mp = m.getInt()       # mp
                            if lvl > 0:
                                char_info.level = lvl
                            if hp > 0:
                                char_info.hp = hp
                                char_info.hp_max = max(getattr(char_info, "hp_max", hp), hp)
                            if mp >= 0:
                                char_info.mp = mp
                                char_info.mp_max = max(getattr(char_info, "mp_max", mp), mp)
                            if exp >= 0:
                                char_info.exp = exp

                            # Trích xuất chuẩn xác bộ ba tiền tệ: KNB, Lá, Bạc (money1, money2, money3)
                            cp = m.getShort()
                            sp = m.getInt()
                            for _ in range(5):  # str, con, agi, ilt, wis
                                m.getShort()
                            char_info.gold = m.getInt()    # money1: KNB (Vàng)
                            char_info.leaf = m.getInt()    # money2: Lá
                            char_info.silver = m.getInt()  # money3: Bạc
                    except Exception:
                        pass

                # Drain world entry packets
                time.sleep(0.15)
                while conn.receive_message(timeout=0.02):
                    pass

                setattr(conn, "last_enter_error", "")
                return True
            except Exception as e:
                setattr(conn, "last_enter_error", f"Ngoại lệ: {e}")
                if attempt < 3:
                    time.sleep(0.6)
                    continue
                return False
        return False

    @staticmethod
    def leave_character(conn: PVTKConnection) -> bool:
        """
        Leaves current character and returns to character selection screen.
        Sequence:
        1. Opcode 0x0003 (CG_LOGIN_ACTORLEAVE) -> Server replies with 0x0003
        2. Opcode 0x2711 (CG_LOGIN_ACTORLIST) -> Server replies with 0x2711
        Prepares the socket cleanly for entering the next character.
        """
        try:
            while conn.receive_message(timeout=0.02):
                pass

            msg_leave = Message(proto.CG_LOGIN_ACTORLEAVE)
            msg_leave.putByte(1)  # 1 = return to character selection screen
            res_leave = conn.send_and_wait(msg_leave, expected_type=proto.CG_LOGIN_ACTORLEAVE, timeout=1.5)

            time.sleep(0.05)

            # Request 0x2711 (Character List)
            msg_list = Message(proto.CG_LOGIN_ACTORLIST)
            res_list = conn.send_and_wait(msg_list, expected_type=proto.CG_LOGIN_ACTORLIST, timeout=1.5)

            time.sleep(0.05)
            while conn.receive_message(timeout=0.02):
                pass

            return res_leave is not None or res_list is not None
        except Exception:
            return False
