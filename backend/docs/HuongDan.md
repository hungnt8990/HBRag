# HƯỚNG DẪN DEPLOY API LÊN UBUNTU

Cách đúng nhất là tạo `systemd service`. Khi bạn tắt SSH/session, Ubuntu vẫn giữ app chạy, tự restart nếu crash, và tự chạy lại sau reboot.

Bạn đang ở server user `ragteam`, project path là:

    /home/ragteam/hbrag-project/HBRag/backend

**1\. Đảm bảo backend chạy được bằng Python venv**  
Trước hết trong `backend` phải có `.venv` và đã cài requirements:

    cd /home/ragteam/hbrag-project/HBRag/backend
    . .venv/bin/activate
    python -m pip install -r requirements.txt

Test nhanh:

    .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

Nếu chạy OK thì nhấn `Ctrl+C`, rồi tạo service.

**2\. Tạo service systemd**

    sudo nano /etc/systemd/system/hbrag-backend.service

Dán nội dung này:

    [Unit]
    Description=HBRag FastAPI Backend
    After=network.target
    
    [Service]
    Type=simple
    User=ragteam
    WorkingDirectory=/home/ragteam/hbrag-project/HBRag/backend
    EnvironmentFile=/home/ragteam/hbrag-project/HBRag/backend/.env
    ExecStart=/home/ragteam/hbrag-project/HBRag/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    Restart=always
    RestartSec=5
    
    [Install]
    WantedBy=multi-user.target

Mình dùng:

    .venv/bin/python -m uvicorn

thay vì:

    .venv/bin/uvicorn

vì cách này ổn hơn nếu binary `uvicorn` chưa được tạo script riêng.

**3\. Start service**

    sudo systemctl daemon-reload
    sudo systemctl enable hbrag-backend
    sudo systemctl start hbrag-backend

Kiểm tra:

    sudo systemctl status hbrag-backend

Nếu thấy `active (running)` là OK. Lúc này bạn tắt SSH thì backend vẫn chạy.

**4\. Xem log**

    journalctl -u hbrag-backend -f

**5\. Restart khi sửa code hoặc `.env`**

    sudo systemctl restart hbrag-backend

**6\. Stop nếu cần**

    sudo systemctl stop hbrag-backend

**7\. Test từ server**

    curl http://localhost:8000/health

Test từ máy khác:

    curl http://<IP_UBUNTU_SERVER>:8000/health

Nếu máy khác không gọi được, mở firewall:

    sudo ufw allow 8000/tcp
    sudo ufw status

Một lưu ý rất quan trọng: file `.env` của bạn đang có API key/password thật. Trên Ubuntu nên khóa quyền đọc:

    chmod 600 /home/ragteam/hbrag-project/HBRag/backend/.env

Sau khi tạo service, bạn chỉ cần nhớ 3 lệnh này là đủ dùng hằng ngày:

    sudo systemctl status hbrag-backend
    sudo systemctl restart hbrag-backend
    journalctl -u hbrag-backend -f