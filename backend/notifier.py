import os
import asyncio
from dotenv import load_dotenv
from logger import get_logger

logger = get_logger(__name__)

# 환경변수 로드
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(dotenv_path=env_path, override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram(message: str):
    """
    Telegram Bot API를 통해 메시지 전송 (비동기)
    .env에서 토큰과 채팅ID를 읽음
    미설정 시 silently skip
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        # 설정되지 않으면 조용히 반환
        return

    try:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.warning(f"Telegram API 응답 에러: {response.status_code}")
    except Exception as e:
        logger.error(f"Telegram 메시지 전송 실패: {e}")

def send_telegram_sync(message: str):
    """동기 래퍼 - 이미 실행 중인 이벤트 루프 감지 후 안전하게 처리"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # FastAPI/uvicorn 루프가 이미 실행 중이면 fire-and-forget으로 예약
            asyncio.ensure_future(send_telegram(message))
        else:
            loop.run_until_complete(send_telegram(message))
    except Exception as e:
        logger.error(f"Telegram 동기 전송 실패: {e}")
