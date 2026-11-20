import sys
import time
import os
import json
import subprocess
from typing import List, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from core.config import settings
from modules.proxy_hub import ProxyHub
from modules.script_manager import ScriptManager


def print_banner():
    print("\n" + "=" * 80)
    print("        🚀 HỆ THỐNG GHI KỊCH BẢN THÔNG MINH (ACTION RECORDER HUB)")
    print("=" * 80)
    print("  -> Đăng nhập 1 Acc Trưởng điều khiển trực tiếp trên Client (Điện thoại / Giả lập)")
    print("  -> Hệ thống sẽ tự động bắt gói tin và nội suy ra kịch bản (Action) cấp cao.")
    print("  -> Tính năng kéo quân Live Mirror đã bị loại bỏ vì lý do chiến đấu không hợp lệ.")
    print("=" * 80)


def run_action_mirror_cli(accounts: List[Tuple] = None):
    """Main CLI entry point for Script Recording session."""
    print_banner()

    print("\n-> Đang khởi tạo bộ thu nhận tín hiệu Proxy Hub...")

    # 1. Tự động map cổng mạng (adb reverse) cho tất cả giả lập LDPlayer đang chạy
    try:
        out = subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', 'devices'], capture_output=True, text=True).stdout
        running_devs = []
        for line in out.splitlines()[1:]:
            if 'device' in line and 'offline' not in line:
                dev = line.split()[0]
                running_devs.append(dev)
                for p in [10001, 10002, 10003]:
                    subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', dev, 'reverse', f'tcp:{p}', f'tcp:{p}'], capture_output=True)
                subprocess.run([r'E:\LDPlayer\LDPlayer9\adb.exe', '-s', dev, 'shell', 'su 0 sysctl -w net.ipv4.conf.all.route_localnet=1; su 0 iptables -t nat -F OUTPUT; su 0 iptables -t nat -A OUTPUT -p tcp -d 10.0.2.2 --dport 10002 -j REDIRECT'], capture_output=True)
        if running_devs:
            print(f"🔄 Đã map cổng mạng giả lập (adb reverse [10001, 10002, 10003] + NAT 10.0.2.2) cho: {running_devs}")
    except Exception:
        pass

    # 2. Start Proxy Hub on all ports [10001, 10002, 10003]
    proxy_hub = ProxyHub(
        target_host=settings.game_host,
        target_port=settings.game_port,
        listen_host="0.0.0.0",
        listen_ports=[10001, 10002, 10003]
    )
    # Không gán slave nào cả vì đây chỉ là Recorder
    proxy_hub.attach_slaves([])
    proxy_hub.start()

    print("\n" + "=" * 80)
    print("                  HƯỚNG DẪN KẾT NỐI ACC TRƯỞNG & GHI KỊCH BẢN")
    print("=" * 80)
    print("  1. Mở Game Client (Giả lập LDPlayer / Điện thoại / PC) chứa Acc Trưởng.")
    print("  2. Đăng nhập qua cổng Proxy Hub: IP \033[92m127.0.0.1\033[0m | Cổng: \033[92m10001, 10002 hoặc 10003\033[0m")
    print("     (Tool đã tự động map adb reverse cho toàn bộ các giả lập đang chạy!)")
    print("  3. Thao tác trên Acc Trưởng để Tool tự động ghi lại.")
    print("--------------------------------------------------------------------------------")
    print("  - Gõ \033[93m'S'\033[0m (Enter): Mở menu chọn 1 trong 10 Ô Nhớ (Slots 1 - 10) để LƯU KỊCH BẢN.")
    print("  - Gõ \033[91m'Q'\033[0m (Enter): Thoát phiên ghi (KHÔNG tự động lưu).")
    print("================================================================================")

    # 3. Interactive Controller Loop
    try:
        while True:
            cmd = input("\n[ACTION RECORDER] Nhập lệnh [S, Q]: ").strip().upper()
            if cmd == "Q":
                print("\n-> Đang dừng phiên Ghi Kịch Bản và ngắt kết nối an toàn...")
                if proxy_hub.recorded_actions:
                    print(f"\n💡 \033[93m[KỊCH BẢN ĐÃ GHI]\033[0m Phiên này có {len(proxy_hub.recorded_actions)} thao tác đã được ghi lại!")
                    try:
                        sv_choice = input("-> Bạn có muốn lưu kịch bản này vào Slot [1-10] không? [Y/N] (Nhấn Enter = Có): ").strip().upper()
                        if sv_choice in ("", "Y", "YES"):
                            ScriptManager.prompt_save_cli(proxy_hub.recorded_actions, target_server=f"{proxy_hub.target_host}:{proxy_hub.target_port}")
                    except (KeyboardInterrupt, EOFError):
                        pass
                break

            elif cmd == "S":
                ScriptManager.prompt_save_cli(proxy_hub.recorded_actions, target_server=f"{proxy_hub.target_host}:{proxy_hub.target_port}")

            else:
                if cmd:
                    print(f"❌ Lệnh '{cmd}' không hợp lệ! Vui lòng nhập: S (Lưu kịch bản), Q (Thoát).")

    except (KeyboardInterrupt, EOFError):
        print("\n[ĐÃ DỪNG PHIÊN]")
    finally:
        # Tự động sao lưu kịch bản nếu có hành động được ghi
        if proxy_hub.recorded_actions:
            try:
                backup_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "scripts", "autosave_last_recorded.json"))
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                with open(backup_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "title": f"Tự Động Lưu ({len(proxy_hub.recorded_actions)} bước)",
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "total_steps": len(proxy_hub.recorded_actions),
                        "steps": proxy_hub.recorded_actions
                    }, f, ensure_ascii=False, indent=2)
                print(f"  \033[92m[TỰ ĐỘNG SAO LƯU]\033[0m Đã tạo bản sao lưu kịch bản tại: scripts/autosave_last_recorded.json ({len(proxy_hub.recorded_actions)} bước)")
            except Exception:
                pass

        proxy_hub.stop()
        print("\n\033[92m[ĐÃ ĐÓNG AN TOÀN]\033[0m Phiên Ghi Kịch Bản đã được đóng sạch sẽ.\n")
