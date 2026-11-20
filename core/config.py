"""
Configuration loader for PVTK Tool.
Loads settings from .env file with fallback to config.json and defaults.
Supports per-server account file isolation (Mỗi máy chủ 1 file tài khoản riêng biệt).
"""

import os
import re
import json
import unicodedata
from typing import Any, Dict, List


def parse_env_file(filepath: str) -> Dict[str, str]:
    env_vars = {}
    if not os.path.exists(filepath):
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("\"'").strip()
                env_vars[key] = val
    return env_vars


def slugify_server_name(name: str) -> str:
    """Converts server name like 'PHỤNG THIÊN (Cụm 6)' to 'accounts_phung_thien.txt'."""
    nfkd = unicodedata.normalize('NFKD', name)
    no_accents = "".join([c for c in nfkd if not unicodedata.combining(c)])
    no_accents = no_accents.replace('đ', 'd').replace('Đ', 'D')
    clean = re.sub(r'[^a-zA-Z0-9]+', '_', no_accents).strip('_').lower()
    clean = clean.replace('cum_', '').replace('server_', 's')
    return f"accounts_{clean}.txt" if clean else "accounts.txt"


DEFAULT_SERVERS = [
    {
        "name": "Tuyệt Vô Thần (Cụm 3)",
        "game_host": "123.30.142.210",
        "game_port": 10000,
        "master_host": "123.30.142.219",
        "master_port": 30000,
        "accounts_file": "accounts_tuyet_vo_than.txt",
        "desc": "Máy chủ chính thức Tuyệt Vô Thần"
    },
    {
        "name": "PHỤNG THIÊN (Cụm 6)",
        "game_host": "123.30.142.163",
        "game_port": 10010,
        "master_host": "123.30.142.219",
        "master_port": 30000,
        "accounts_file": "accounts_phung_thien.txt",
        "desc": "Máy chủ Phụng Thiên"
    },
    {
        "name": "Chín Tầng Mây (Cụm 10)",
        "game_host": "123.30.142.179",
        "game_port": 10010,
        "master_host": "123.30.142.219",
        "master_port": 30000,
        "accounts_file": "accounts_chin_tang_may.txt",
        "desc": "Máy chủ Chín Tầng Mây"
    },
    {
        "name": "Kỳ Thương (Cụm 8)",
        "game_host": "123.30.142.137",
        "game_port": 10010,
        "master_host": "123.30.142.219",
        "master_port": 30000,
        "accounts_file": "accounts_ky_thuong.txt",
        "desc": "Máy chủ Kỳ Thương"
    },
    {
        "name": "Đấu Trường (Cụm 7)",
        "game_host": "123.30.142.137",
        "game_port": 10000,
        "master_host": "123.30.142.219",
        "master_port": 30000,
        "accounts_file": "accounts_dau_truong.txt",
        "desc": "Máy chủ Đấu Trường"
    }
]


class Settings:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.reload()

    def reload(self):
        """Reloads settings from disk, ensuring live updates take effect immediately."""
        env_path = os.path.join(self.base_dir, ".env")
        json_path = os.path.join(self.base_dir, "config.json")

        self.env = parse_env_file(env_path)

        self.json_data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    self.json_data = json.load(f)
            except Exception:
                self.json_data = {}

        # Multi-server configuration
        self.servers = self.json_data.get("servers", DEFAULT_SERVERS)
        if not self.servers:
            self.servers = DEFAULT_SERVERS

        self.selected_server_index = self.json_data.get("selected_server_index", 1)
        if self.selected_server_index >= len(self.servers) or self.selected_server_index < 0:
            self.selected_server_index = 0

        active_srv = self.servers[self.selected_server_index]

        # Concurrency & Performance
        self.max_concurrent_accounts = int(self.get("MAX_CONCURRENT_ACCOUNTS", self.json_data.get("threading", {}).get("max_concurrent_accounts", 15)))
        self.question_delay_sec = float(self.get("QUESTION_DELAY_SEC", self.json_data.get("threading", {}).get("delay_between_requests", 0.5)))

        # Target Map & Rules
        self.target_map_name = self.get("TARGET_MAP_NAME", self.json_data.get("quiz_settings", {}).get("target_map_name", "Thánh Vực"))

        # Lottery Settings
        self.lottery_target_map_name = self.get("LOTTERY_TARGET_MAP_NAME", "Quảng Trường Tân Nguyệt")
        self.lottery_target_map_id = int(self.get("LOTTERY_TARGET_MAP_ID", 38))
        self.lottery_target_lucky_number = int(self.get("LOTTERY_TARGET_LUCKY_NUMBER", 321))
        self.lottery_max_stop_number = int(self.get("LOTTERY_MAX_STOP_NUMBER", 1000))

        # Mail & COD Settings
        mail_cfg = self.json_data.get("mail_settings", {})
        self.mail_receiver_target = self.get("MAIL_RECEIVER_TARGET", mail_cfg.get("receiver_target", ""))
        self.mail_receive_silver = int(self.get("MAIL_RECEIVE_SILVER", mail_cfg.get("receive_silver", 510000)))
        self.mail_send_silver = int(self.get("MAIL_SEND_SILVER", mail_cfg.get("send_silver", 1)))
        self.mail_content = self.get("MAIL_CONTENT", mail_cfg.get("content", "PVTK"))

        # Master Server
        master_cfg = self.json_data.get("master_server", {})
        self.master_host = active_srv.get("master_host") or master_cfg.get("host") or self.get("MASTER_HOST", "123.30.142.219")
        self.master_port = int(active_srv.get("master_port") or master_cfg.get("port") or self.get("MASTER_PORT", 30000))

        # Game Server
        game_cfg = self.json_data.get("game_server", {})
        self.game_host = active_srv.get("game_host") or game_cfg.get("host") or self.get("GAME_HOST", "123.30.142.163")
        self.game_port = int(active_srv.get("game_port") or game_cfg.get("port") or self.get("GAME_PORT", 10010))
        self.game_server_name = active_srv.get("name") or game_cfg.get("name") or self.get("GAME_SERVER_NAME", "PHỤNG THIÊN (Cụm 6)")

        # Per-Server Account File Isolation
        acc_file_name = active_srv.get("accounts_file")
        if not acc_file_name:
            acc_file_name = slugify_server_name(self.game_server_name)
            active_srv["accounts_file"] = acc_file_name

        self.accounts_file_basename = acc_file_name
        self.accounts_file = os.path.join(self.base_dir, acc_file_name)

        # Storage directory
        self.storage_dir = os.path.join(self.base_dir, "storage")
        os.makedirs(self.storage_dir, exist_ok=True)

        # Ensure server account file exists
        if not os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, "w", encoding="utf-8") as f:
                    f.write(f"# Danh sách tài khoản cho máy chủ: {self.game_server_name}\n# Định dạng: username|password\n")
            except Exception:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.env.get(key, os.environ.get(key, default))

    def sync_env_file(self):
        """Automatically updates .env with full server list and individual accounts files."""
        env_path = os.path.join(self.base_dir, ".env")
        try:
            content = f"""# ================================================================
# PVTK HEADLESS PROTOCOL TOOL - CẤU HÌNH MÔI TRƯỜNG (.ENV)
# ================================================================

# Số lượng tài khoản chạy đồng thời (Song song / Multi-threading)
MAX_CONCURRENT_ACCOUNTS={self.max_concurrent_accounts}

# Độ trễ giữa các câu hỏi hoặc hành động (Giây)
QUESTION_DELAY_SEC={self.question_delay_sec}

# ================================================================
# CẤU HÌNH SỰ KIỆN VẤN ĐÁP (22:00 - 22:30 HÀNG NGÀY)
# ================================================================
TARGET_MAP_NAME={self.target_map_name}

# ================================================================
# CẤU HÌNH SỰ KIỆN RÚT THƯỞNG MAY MẮN (08:00 - 23:59 HÀNG NGÀY)
# ================================================================
LOTTERY_TARGET_MAP_NAME={self.lottery_target_map_name}
LOTTERY_TARGET_MAP_ID={self.lottery_target_map_id}
LOTTERY_TARGET_LUCKY_NUMBER={self.lottery_target_lucky_number}
LOTTERY_MAX_STOP_NUMBER={self.lottery_max_stop_number}

# ================================================================
# CẤU HÌNH GỬI THƯ GIAO DỊCH BẠC (COD MAIL)
# ================================================================
MAIL_RECEIVER_TARGET={self.mail_receiver_target}
MAIL_RECEIVE_SILVER={self.mail_receive_silver}
MAIL_SEND_SILVER={self.mail_send_silver}
MAIL_CONTENT={self.mail_content}

# ================================================================
# CẤU HÌNH MÁY CHỦ HIỆN TẠI (ĐANG HOẠT ĐỘNG TRÊN TOOL)
# ================================================================
# Máy chủ Master Gateway
MASTER_HOST={self.master_host}
MASTER_PORT={self.master_port}

# Máy chủ Game World đang chọn
GAME_HOST={self.game_host}
GAME_PORT={self.game_port}
GAME_SERVER_NAME={self.game_server_name}

# File tài khoản riêng biệt đang được nạp cho máy chủ hiện tại
ACCOUNTS_FILE={self.accounts_file_basename}

# ================================================================
# DANH SÁCH FILE TÀI KHOẢN RIÊNG BIỆT CHO TỪNG MÁY CHỦ
# (Mỗi máy chủ quản lý và chạy độc lập 1 file riêng)
# ================================================================
"""
            for i, srv in enumerate(self.servers, 1):
                s_name = srv.get("name", f"Server {i}")
                s_host = srv.get("game_host", "127.0.0.1")
                s_port = srv.get("game_port", 10000)
                s_file = srv.get("accounts_file") or slugify_server_name(s_name)
                var_key = "ACCOUNTS_FILE_" + re.sub(r'[^a-zA-Z0-9]+', '_', unicodedata.normalize('NFKD', s_name).encode('ASCII', 'ignore').decode('utf-8')).strip('_').upper()
                content += f"# [Máy chủ {i}] {s_name} ({s_host}:{s_port})\n"
                content += f"{var_key}={s_file}\n\n"

            with open(env_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    def save_json(self):
        json_path = os.path.join(self.base_dir, "config.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.json_data, f, ensure_ascii=False, indent=2)
            self.sync_env_file()
        except Exception:
            pass

    def select_server(self, index: int) -> bool:
        """Switches active server and loads its corresponding accounts_file."""
        if 0 <= index < len(self.servers):
            self.selected_server_index = index
            self.json_data["selected_server_index"] = index
            srv = self.servers[index]
            self.game_server_name = srv.get("name", "PVTK Server")
            self.game_host = srv.get("game_host", "123.30.142.210")
            self.game_port = int(srv.get("game_port", 10000))
            self.master_host = srv.get("master_host", "123.30.142.219")
            self.master_port = int(srv.get("master_port", 30000))

            acc_name = srv.get("accounts_file") or slugify_server_name(self.game_server_name)
            srv["accounts_file"] = acc_name
            self.accounts_file_basename = acc_name
            self.accounts_file = os.path.join(self.base_dir, acc_name)

            self.json_data["servers"] = self.servers
            self.json_data["game_server"] = {
                "name": self.game_server_name,
                "host": self.game_host,
                "port": self.game_port
            }
            self.json_data["master_server"] = {
                "host": self.master_host,
                "port": self.master_port
            }
            self.save_json()
            self.reload()
            return True
        return False

    def set_server_accounts_file(self, server_index: int, custom_filename: str) -> bool:
        """Sets a custom accounts file for a specific server."""
        if 0 <= server_index < len(self.servers):
            self.servers[server_index]["accounts_file"] = custom_filename
            self.json_data["servers"] = self.servers
            self.save_json()
            self.reload()
            return True
        return False

    def add_server(self, name: str, game_host: str, game_port: int, master_host: str = "123.30.142.219", master_port: int = 30000, desc: str = "", accounts_file: str = "") -> int:
        if not accounts_file:
            accounts_file = slugify_server_name(name)
        new_srv = {
            "name": name,
            "game_host": game_host,
            "game_port": int(game_port),
            "master_host": master_host,
            "master_port": int(master_port),
            "accounts_file": accounts_file,
            "desc": desc
        }
        self.servers.append(new_srv)
        self.json_data["servers"] = self.servers
        self.save_json()
        return len(self.servers) - 1

    def delete_server(self, index: int) -> bool:
        if 0 <= index < len(self.servers) and len(self.servers) > 1:
            self.servers.pop(index)
            if self.selected_server_index >= len(self.servers):
                self.selected_server_index = 0
            self.json_data["servers"] = self.servers
            self.json_data["selected_server_index"] = self.selected_server_index
            self.save_json()
            self.reload()
            return True
        return False


settings = Settings()
