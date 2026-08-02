from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import feedparser

KST = timezone(timedelta(hours=9))
USER_AGENT = "kakao-tech-news-digest/1.0"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
STATE_FILE = Path(os.getenv("NEWSBOT_STATE_FILE", "data/sent.json"))

SOURCES = {
    "AI": "https://news.google.com/rss/search?q=AI+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "OpenAI": "https://news.google.com/rss/search?q=OpenAI+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "Claude": "https://news.google.com/rss/search?q=Anthropic+Claude+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "개발": "https://news.google.com/rss/search?q=%EC%86%8C%ED%94%84%ED%8A%B8%EC%9B%A8%EC%96%B4+%EA%B0%9C%EB%B0%9C+IT+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "GitHub": "https://github.blog/changelog/feed/",
}


@dataclass(frozen=True)
class Article:
    category: str
    title: str
    url: str
    summary: str
    published: datetime


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def entry_time(entry: dict) -> datetime:
    parts = entry.get("published_parsed") or entry.get("updated_parsed")
    if parts:
        return datetime(*parts[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_articles(hours: int = 48) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[Article] = []
    for category, url in SOURCES.items():
        feed = feedparser.parse(url, agent=USER_AGENT)
        for entry in feed.entries[:12]:
            published = entry_time(entry)
            if published < cutoff:
                continue
            title = clean(entry.get("title", ""))
            link = entry.get("link", "")
            if title and link:
                articles.append(Article(category, title, link, clean(entry.get("summary", "")), published))
    return deduplicate(articles)


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in sorted(articles, key=lambda x: x.published, reverse=True):
        key = re.sub(r"[^0-9a-z가-힣]", "", article.title.lower())[:70]
        digest = hashlib.sha1(key.encode()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            result.append(article)
    return result


def article_id(article: Article) -> str:
    normalized = re.sub(r"[^0-9a-z가-힣]", "", article.title.lower())[:100]
    return hashlib.sha256(normalized.encode()).hexdigest()


def load_sent_ids(path: Path = STATE_FILE) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def exclude_previously_sent(articles: list[Article], sent: dict[str, str]) -> list[Article]:
    return [article for article in articles if article_id(article) not in sent]


def save_sent(articles: list[Article], previous: dict[str, str], path: Path = STATE_FILE) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    current = {}
    for key, value in previous.items():
        try:
            if datetime.fromisoformat(value) >= cutoff:
                current[key] = value
        except (TypeError, ValueError):
            continue
    now = datetime.now(timezone.utc).isoformat()
    current.update({article_id(article): now for article in articles})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def select_balanced(articles: list[Article], limit: int = 7) -> list[Article]:
    chosen: list[Article] = []
    for category in SOURCES:
        match = next((a for a in articles if a.category == category and a not in chosen), None)
        if match:
            chosen.append(match)
    chosen.extend(a for a in articles if a not in chosen)
    return chosen[:limit]


def fallback_line(article: Article) -> str:
    text = article.summary or article.title
    text = re.split(r"(?<=[.!?。])\s+", text)[0]
    return clean(text)[:105] or article.title[:105]


def request_json(url: str, payload: dict | None = None, headers: dict | None = None, form: dict | None = None) -> dict:
    final_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        final_headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    elif payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        final_headers["Content-Type"] = "application/json"
    else:
        body = None
    req = urllib.request.Request(url, data=body, headers=final_headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc


def refresh_kakao_token() -> str:
    form = {
        "grant_type": "refresh_token",
        "client_id": required("KAKAO_REST_API_KEY"),
        "refresh_token": required("KAKAO_REFRESH_TOKEN"),
    }
    if os.getenv("KAKAO_CLIENT_SECRET"):
        form["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    data = request_json(KAKAO_TOKEN_URL, form=form)
    if data.get("refresh_token"):
        rotate_github_secret(data["refresh_token"])
    return data["access_token"]


def rotate_github_secret(new_token: str) -> None:
    pat = os.getenv("SECRET_ROTATION_PAT")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not pat or not repo:
        raise RuntimeError("카카오 리프레시 토큰이 갱신됐지만 SECRET_ROTATION_PAT 또는 GITHUB_REPOSITORY가 없어 저장할 수 없습니다.")
    subprocess.run(["gh", "secret", "set", "KAKAO_REFRESH_TOKEN", "--repo", repo, "--body", new_token], env={**os.environ, "GH_TOKEN": pat}, check=True)
    print("KAKAO_REFRESH_TOKEN secret 자동 갱신 완료")


def build_messages(articles: list[Article], summaries: list[str]) -> list[tuple[str, str]]:
    today = datetime.now(KST).strftime("%m/%d")
    messages: list[tuple[str, str]] = []
    for index, (article, summary) in enumerate(zip(articles, summaries), 1):
        text = f"[{today} 테크 브리핑 {index}/{len(articles)} · {article.category}]\n{article.title[:80]}\n\n{summary[:105]}"
        messages.append((text[:200], article.url))
    return messages


def send_kakao(access_token: str, text: str, url: str) -> None:
    template = {"object_type": "text", "text": text, "link": {"web_url": url, "mobile_web_url": url}, "button_title": "기사 읽기"}
    result = request_json(KAKAO_SEND_URL, form={"template_object": json.dumps(template, ensure_ascii=False)}, headers={"Authorization": f"Bearer {access_token}"})
    if result.get("result_code") != 0:
        raise RuntimeError(f"카카오 전송 실패: {result}")


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"필수 환경변수 {name}가 없습니다.")
    return value


def main() -> None:
    sent = load_sent_ids()
    fresh = exclude_previously_sent(fetch_articles(int(os.getenv("NEWS_LOOKBACK_HOURS", "26"))), sent)
    articles = select_balanced(fresh, int(os.getenv("MAX_ARTICLES", "7")))
    if not articles:
        print("새 기사가 없어 전송하지 않습니다.")
        return
    summaries = [fallback_line(a) for a in articles]
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        for text, url in build_messages(articles, summaries):
            print(f"{text}\n{url}\n")
        return
    access_token = refresh_kakao_token()
    for text, url in build_messages(articles, summaries):
        send_kakao(access_token, text, url)
    save_sent(articles, sent)
    print(f"카카오톡으로 기사 {len(articles)}개 전송 완료")


if __name__ == "__main__":
    main()
