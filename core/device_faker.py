"""
PVTK Device Fingerprint Faker Module
Generates consistent, realistic hardware & network fingerprints for each account.
Prevents server multi-account detection by randomizing client parameters:
- Device Model (Handset: Samsung, Xiaomi, Oppo, Vivo, Nokia, Vsmart, Realme, etc.)
- Operating System & Version (Android 11/10/9.0, Symbian 9.3, iOS, etc.)
- Simulated LAN IP (192.168.1.xxx, 10.0.0.xxx, 172.20.10.xxx)
- CP_ID & Client Platform Type
"""

import hashlib
import random
from typing import Dict, Any

# Curated list of realistic mobile device profiles
REALISTIC_DEVICE_PROFILES = [
    {"handset": "Samsung Galaxy A51", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Samsung Galaxy A50", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Samsung Galaxy A12", "os": "Android 11", "client_type": 5, "cp_id": 111},
    {"handset": "Samsung Galaxy J7 Prime", "os": "Android 8.1.0", "client_type": 5, "cp_id": 111},
    {"handset": "Xiaomi Redmi Note 9", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Xiaomi Redmi 9A", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Xiaomi Redmi Note 8", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Xiaomi Poco X3", "os": "Android 11", "client_type": 5, "cp_id": 111},
    {"handset": "Oppo A5s", "os": "Android 8.1.0", "client_type": 5, "cp_id": 111},
    {"handset": "Oppo A12", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Oppo Reno4", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Oppo A53", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Vivo Y11", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Vivo Y20", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Vivo Y12s", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Realme C11", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Realme C15", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Realme 5i", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Vsmart Joy 3", "os": "Android 9.0", "client_type": 5, "cp_id": 111},
    {"handset": "Vsmart Live 4", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Vsmart Star 4", "os": "Android 10", "client_type": 5, "cp_id": 111},
    {"handset": "Huawei Y7 Pro", "os": "Android 8.1.0", "client_type": 5, "cp_id": 111},
    {"handset": "Huawei Nova 3i", "os": "Android 8.1.0", "client_type": 5, "cp_id": 111},
    {"handset": "Sony Xperia XZ", "os": "Android 8.0", "client_type": 5, "cp_id": 111},
    {"handset": "LG V30", "os": "Android 8.0", "client_type": 5, "cp_id": 111},
    {"handset": "Google Pixel 3", "os": "Android 11", "client_type": 5, "cp_id": 111},
    {"handset": "Google Pixel 4a", "os": "Android 11", "client_type": 5, "cp_id": 111},
    {"handset": "OnePlus Nord", "os": "Android 10", "client_type": 5, "cp_id": 111},
]

LAN_SUBNETS = [
    "192.168.1.",
    "192.168.0.",
    "192.168.100.",
    "10.0.0.",
    "172.20.10.",
    "100.64.12."
]


class DeviceFaker:
    @staticmethod
    def get_device_fingerprint(username: str) -> Dict[str, Any]:
        """
        Deterministically derives a consistent device fingerprint for a specific username.
        Same username always gets the exact same device profile & simulated IP.
        """
        clean_user = username.strip().lower()
        md5_hex = hashlib.md5(clean_user.encode("utf-8")).hexdigest()
        hash_val = int(md5_hex, 16)

        profile_idx = hash_val % len(REALISTIC_DEVICE_PROFILES)
        profile = REALISTIC_DEVICE_PROFILES[profile_idx]

        subnet_idx = (hash_val >> 4) % len(LAN_SUBNETS)
        subnet = LAN_SUBNETS[subnet_idx]
        host_id = ((hash_val >> 8) % 200) + 10  # 10 to 209
        simulated_ip = f"{subnet}{host_id}"

        # 16-character hexadecimal Android Device ID (Settings.System.ANDROID_ID)
        android_id = md5_hex[:16]

        # Formatted MAC Address (e.g. 5C:E0:F6:xx:yy:zz)
        mac_parts = [md5_hex[i:i+2] for i in range(16, 28, 2)]
        mac_address = ":".join(mac_parts)

        # 15-digit realistic IMEI
        imei = f"86{str(hash_val)[-13:]}"

        return {
            "handset": profile["handset"],
            "os": profile["os"],
            "client_type": profile["client_type"],
            "cp_id": profile["cp_id"],
            "client_ip": simulated_ip,
            "android_id": android_id,
            "mac_address": mac_address,
            "imei": imei
        }

    @staticmethod
    def get_human_delay(base_delay: float = 0.5) -> float:
        """
        Adds realistic random jitter (e.g. 80% to 140% of base delay) to simulate human reaction.
        """
        jitter = random.uniform(0.85, 1.35)
        return round(base_delay * jitter, 3)
