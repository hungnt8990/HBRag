# Huong dan chay Frontend bang PM2 tren Ubuntu

Frontend la ung dung Next.js. Khi deploy production, build truoc bang `npm run build`, sau do dung PM2 chay `npm run start` de app van chay khi tat SSH/session va tu khoi dong lai sau reboot.

## 1. Di chuyen vao thu muc frontend

```bash
cd /home/ragteam/hbrag-project/HBRag/frontend
```

Neu server dung duong dan khac, thay lai path cho dung vi tri project.

## 2. Cai Node.js va PM2

Kiem tra Node.js:

```bash
node -v
npm -v
```

Neu chua co Node.js/npm:

```bash
sudo apt update
sudo apt install -y nodejs npm
```

Cai PM2:

```bash
sudo npm install -g pm2
```

## 3. Cai package frontend

```bash
npm install
```

## 4. Cau hinh API backend

File production env:

```bash
nano .env.production.local
```

Noi dung hien tai:

```env
NEXT_PUBLIC_API_BASE_URL=http://10.72.113.21:8000
```

Neu backend doi IP/domain/port, sua lai bien nay. Luu y: sau khi sua `NEXT_PUBLIC_API_BASE_URL`, phai build lai frontend.

## 5. Build frontend

```bash
npm run build
```

## 6. Chay frontend bang PM2

Chay port mac dinh `3000`:

```bash
pm2 start npm --name hbrag-frontend -- run start
```

Neu muon chi dinh port ro rang:

```bash
PORT=3000 pm2 start npm --name hbrag-frontend -- run start
```

Kiem tra:

```bash
pm2 status
curl http://localhost:3000
```

Truy cap tu may khac:

```text
http://10.72.113.21:3000
```

## 7. Luu PM2 de tu chay sau reboot

```bash
pm2 save
pm2 startup systemd -u ragteam --hp /home/ragteam
```

Lenh `pm2 startup` se in ra mot lenh `sudo ...`. Copy lenh do va chay tiep, sau do chay lai:

```bash
pm2 save
```

## 8. Quan ly PM2

Xem trang thai:

```bash
pm2 status
```

Xem log:

```bash
pm2 logs hbrag-frontend
```

Restart frontend:

```bash
pm2 restart hbrag-frontend
```

Stop frontend:

```bash
pm2 stop hbrag-frontend
```

Xoa khoi PM2:

```bash
pm2 delete hbrag-frontend
```

## 9. Deploy lai sau khi sua code frontend

```bash
cd /home/ragteam/hbrag-project/HBRag/frontend
npm install
npm run build
pm2 restart hbrag-frontend
```

## 10. Cau hinh CORS backend

Neu frontend chay o:

```text
http://10.72.113.21:3000
```

Thi trong `backend/.env` nen co origin nay:

```env
CORS_ALLOWED_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000","http://10.72.113.21:3000"]
```

Sau khi sua backend `.env`, restart backend:

```bash
sudo systemctl restart hbrag-backend
```

## Lenh nhanh

```bash
cd /home/ragteam/hbrag-project/HBRag/frontend
npm install
npm run build
pm2 start npm --name hbrag-frontend -- run start
pm2 save
pm2 startup systemd -u ragteam --hp /home/ragteam
```
