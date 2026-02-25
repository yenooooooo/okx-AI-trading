import logging
import json
import os
from datetime import datetime

def get_logger(name):
    """
    구조화된 로깅을 지원하는 로거 인스턴스 반환
    파일(JSON Lines): backend/logs/trading.log
    스트림: 터미널 출력
    """
    logger = logging.getLogger(name)

    # 이미 핸들러가 있으면 중복 추가 방지
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG)

    # 로그 디렉토리 생성
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    # 파일 핸들러 (JSON Lines 포맷)
    log_file = os.path.join(log_dir, 'trading.log')
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)

    # JSON Lines 포맷터
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_obj = {
                'timestamp': datetime.now().isoformat(),
                'level': record.levelname,
                'module': record.name,
                'message': record.getMessage()
            }
            if record.exc_info:
                log_obj['exception'] = self.formatException(record.exc_info)
            return json.dumps(log_obj, ensure_ascii=False)

    file_handler.setFormatter(JsonFormatter())

    # 스트림 핸들러 (콘솔)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_formatter = logging.Formatter('[%(levelname)s] %(asctime)s - %(name)s: %(message)s')
    stream_handler.setFormatter(stream_formatter)

    # 핸들러 추가
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger
