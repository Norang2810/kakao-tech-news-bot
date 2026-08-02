from __future__ import annotations

import os
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .main import DIGEST_URL, KST


def webhook_wait_url(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["wait"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def wait_for_published_digest(build_id: str, attempts: int = 30, interval: float = 5.0) -> bool:
    marker = f'name="digest-build-id" content="{build_id}"'
    for attempt in range(attempts):
        try:
            response = requests.get(DIGEST_URL, timeout=15, headers={"Cache-Control": "no-cache"})
            if response.ok and marker in response.text:
                return True
        except requests.RequestException:
            pass
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False


def notification_payload(test_mode: bool = False) -> dict:
    today = datetime.now(KST).strftime("%m/%d")
    prefix = "🧪 **Discord 알림 테스트 성공**" if test_mode else f"📰 **[{today}] 오늘의 테크 브리핑이 도착했습니다.**"
    return {
        "username": "AI Tech Newsbot",
        "content": f"{prefix}\n{DIGEST_URL}",
        "embeds": [
            {
                "title": "오늘의 핵심 기술 뉴스 확인하기",
                "description": "OpenAI · Claude · 일론 머스크 · AI 우선\n핵심 기사 7개 + 추가 기사 10개",
                "url": DIGEST_URL,
                "color": 5793266,
                "footer": {"text": "카카오톡 브리핑 전송 및 웹페이지 게시 완료"},
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def notify_discord() -> bool:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    test_mode = os.getenv("DISCORD_TEST_MODE", "").lower() in {"1", "true", "yes"}
    if not webhook:
        if test_mode:
            raise RuntimeError("DISCORD_WEBHOOK_URL Secret이 등록되지 않았습니다.")
        print("DISCORD_WEBHOOK_URL이 없어 Discord 알림을 건너뜁니다.")
        return False
    if not test_mode:
        build_id = os.getenv("DIGEST_BUILD_ID", "").strip()
        if not build_id:
            raise RuntimeError("DIGEST_BUILD_ID가 없습니다.")
        if not wait_for_published_digest(build_id):
            raise RuntimeError("최신 digest 페이지가 제한 시간 안에 게시되지 않았습니다.")
    response = requests.post(webhook_wait_url(webhook), json=notification_payload(test_mode), timeout=30)
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"Discord Webhook 전송 실패: HTTP {response.status_code} {response.text[:300]}")
    print("Discord 테스트 알림 전송 완료" if test_mode else "Discord 채널 알림 전송 완료")
    return True


if __name__ == "__main__":
    notify_discord()
