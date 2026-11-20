# PVTK 24/7 Headless Automation Bot

Hệ thống tự động hóa 24/7 cho PVTK chạy qua giao thức TCP nhị phân (headless, tối ưu cho Linux VPS & điều khiển qua Telegram Bot).

## Cấu trúc chính
- `run_247.py`: Điểm khởi chạy 24/7 (tích hợp Telegram Bot, SpecialWatcher, OnlineRewardRunner, Scheduler, xoay log `RotatingFileHandler`).
- `main.py`: Giao diện CLI điều khiển trực tiếp từng tác vụ.
- `telegram_bot.py` & `core/tele_notifier.py`: Điều khiển từ xa và gửi cảnh báo lỗi qua Telegram.
- `scheduler.py`: Lập lịch tự động theo múi giờ Việt Nam (GMT+7).
- `special_watcher_manager.py` & `modules/character_market_watcher.py`: Theo dõi SGD, sàn nhân vật và đoạt lì xì.
- `core/`: Tầng mạng TCP/Proxy (`network.py`), framing gói tin (`message.py`), cấu hình (`config.py`), lưu trữ nguyên tử (`storage_utils.py`), captcha/device faker.
- `modules/`: Các phân hệ nghiệp vụ (`auth.py`, `online_reward_runner.py`, `lottery_runner.py`, `login_lottery_runner.py`, `quiz_solver.py`, `free_proxy.py`, ...).
- `setup_vps.sh`: Script cài đặt `systemd` service (`pvtk-bot.service`).

## Cài đặt nhanh trên VPS
```bash
cp config_247.example.json config_247.json
cp accounts.example.txt accounts_tuyet_vo_than.txt
cp accounts.example.txt accountsNV.txt
# Điền bot_token, chat_id vào config_247.json và tài khoản vào file accounts*.txt
bash setup_vps.sh
```
