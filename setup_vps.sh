#!/bin/bash
# ==============================================================================
# Script Tự Động Cài Đặt & Cấu Hình PVTK Bot 24/7 trên Linux VPS (Systemd Service)
# Chạy lệnh: bash setup_vps.sh
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="pvtk-bot"
PYTHON_CMD="python3"

echo "=================================================================="
echo "    CÀI ĐẶT HỆ THỐNG PVTK 24/7 BOT TRÊN LINUX VPS"
echo "=================================================================="

# 1. Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Vui lòng chạy script với quyền root hoặc: sudo bash setup_vps.sh"
    exit 1
fi

# 2. Cài đặt Python 3 nếu chưa có
if ! command -v $PYTHON_CMD &> /dev/null; then
    echo "[*] Đang cập nhật gói và cài đặt Python 3..."
    if command -v apt-get &> /dev/null; then
        apt-get update -y
        apt-get install -y python3
    elif command -v yum &> /dev/null; then
        yum install -y python3
    fi
fi

# 2.5 Cấu hình múi giờ Việt Nam (Asia/Ho_Chi_Minh - GMT+7) cho VPS
echo "[*] Đang cài đặt múi giờ Việt Nam (Asia/Ho_Chi_Minh)..."
timedatectl set-timezone Asia/Ho_Chi_Minh 2>/dev/null || ln -sf /usr/share/zoneinfo/Asia/Ho_Chi_Minh /etc/localtime 2>/dev/null || true
echo "[+] Múi giờ hiện tại: $(date)"

PYTHON_PATH=$(which $PYTHON_CMD)
echo "[+] Python Path: $PYTHON_PATH"
echo "[+] Thư mục Bot: $SCRIPT_DIR"

# 3. Tạo file Systemd Service để chạy ngầm 24/7 và tự khởi động lại khi reboot
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[*] Đang tạo file dịch vụ systemd tại $SERVICE_FILE..."
cat <<EOF > $SERVICE_FILE
[Unit]
Description=PVTK 24/7 Headless Protocol Automation Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_PATH -u run_247.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
LimitNOFILE=65536
MemoryHigh=550M
MemoryMax=750M
StandardOutput=journal
StandardError=journal
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
Environment=TZ=Asia/Ho_Chi_Minh
Environment=MALLOC_ARENA_MAX=2
Environment=MALLOC_TRIM_THRESHOLD_=131072
Environment=MALLOC_MMAP_THRESHOLD_=131072

[Install]
WantedBy=multi-user.target
EOF

# 4. Kích hoạt và bật dịch vụ
echo "[*] Nạp lại cấu hình systemd..."
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo ""
echo "=================================================================="
echo "🎉 CÀI ĐẶT HOÀN TẤT! BOT ĐANG CHẠY 24/7 DƯỚI DẠNG SYSTEMD SERVICE."
echo "=================================================================="
echo "Các lệnh quản lý bot trên VPS:"
echo "  • Xem trạng thái:  systemctl status $SERVICE_NAME"
echo "  • Dừng bot:        systemctl stop $SERVICE_NAME"
echo "  • Khởi động lại:   systemctl restart $SERVICE_NAME"
echo "  • Xem log trực tiếp: tail -f $SCRIPT_DIR/bot_247.log"
echo "=================================================================="
