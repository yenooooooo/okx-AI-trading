#!/bin/bash
echo "[시스템] OKX 자동매매 봇 서버 환경 구성을 시작합니다..."

# 1. 시스템 패키지 업데이트 및 필요 유틸리티 설치
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv nodejs npm -y

# 2. 파이썬 가상환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 3. 백엔드 핵심 라이브러리 설치
pip install -r backend/requirements.txt

# 4. 무중단 실행을 위한 PM2 (Process Manager) 설치
sudo npm install pm2@latest -g

# 5. FastAPI 서버를 백그라운드 무한 루프로 실행 (서버 다운 시 자동 재시작)
# backend 폴더로 이동하여 실행해야 내부 모듈(okx_engine 등) import 오류가 발생하지 않음
cd backend
pm2 start "../venv/bin/python" --name "okx-trading-bot" -- -m uvicorn api_server:app_server --host 0.0.0.0 --port 8000

# 6. 서버 재부팅 시에도 PM2가 봇을 자동으로 살려내도록 설정
pm2 save
pm2 startup

echo "[완료] 봇이 백그라운드에서 성공적으로 가동되었습니다! 'pm2 logs okx-trading-bot' 명령어로 실시간 로그를 확인하세요."
