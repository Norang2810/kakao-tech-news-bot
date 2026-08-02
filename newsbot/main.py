from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder

KST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (compatible; KakaoTechDigest/2.0; +https://norang2810.github.io/kakao-tech-news-bot/)"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
STATE_FILE = Path(os.getenv("NEWSBOT_STATE_FILE", "data/sent.json"))
DIGEST_FILE = Path(os.getenv("DIGEST_FILE", "docs/digest/index.html"))
DIGEST_URL = "https://norang2810.github.io/kakao-tech-news-bot/digest/"

SOURCES = {
    "OpenAI": "https://news.google.com/rss/search?q=OpenAI+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "Claude": "https://news.google.com/rss/search?q=Anthropic+Claude+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "AI": "https://news.google.com/rss/search?q=(AI+OR+인공지능)+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "개발": "https://news.google.com/rss/search?q=(개발자+OR+소프트웨어+OR+프로그래밍)+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "IT": "https://news.google.com/rss/search?q=(IT+OR+테크+OR+기술)+when:1d&hl=ko&gl=KR&ceid=KR:ko",
    "GitHub": "https://github.blog/changelog/feed/",
}

IMPORTANT_WORDS = {
    "출시", "공개", "업데이트", "신규", "발표", "지원", "변경", "보안", "취약점", "오픈소스",
    "개발자", "API", "모델", "에이전트", "Agent", "코딩", "규제", "투자", "인수", "무료",
}
CATEGORY_TERMS = {
    "OpenAI": {"openai", "오픈ai", "오픈에이아이", "chatgpt", "챗gpt", "챗지피티", "sam altman", "샘 알트먼"},
    "Claude": {"claude", "클로드", "anthropic", "앤트로픽"},
    "AI": {" ai ", "인공지능", "생성형", "llm", "모델", "에이전트", "머신러닝", "딥러닝"},
    "개발": {"개발자", "개발", "프로그래밍", "코딩", "소프트웨어", "오픈소스", "api", "프레임워크", "언어", "데브옵스"},
    "IT": {"it", "테크", "플랫폼", "클라우드", "반도체", "보안", "데이터", "모바일", "컴퓨팅", "기술기업"},
    "GitHub": {"github", "copilot", "actions", "repository", "pull request"},
}
TRUSTED_SOURCES = ("연합뉴스", "한겨레", "경향신문", "조선", "중앙일보", "동아일보", "전자신문", "ZDNet", "지디넷", "블로터", "ITWorld", "TechCrunch", "The Verge", "Reuters", "Bloomberg", "GitHub", "Anthropic", "OpenAI")
NOISE = ("기자", "무단전재", "재배포", "Copyright", "관련기사", "구독", "로그인", "광고")


@dataclass(frozen=True)
class Article:
    category: str
    title: str
    url: str
    rss_summary: str
    published: datetime
    source: str = ""
    original_url: str = ""
    body: str = ""
    summary: str = ""
    bullets: tuple[str, ...] = ()
    score: float = 0.0


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def strip_source(title: str, source: str) -> str:
    if source and title.endswith(f" - {source}"):
        title = title[: -(len(source) + 3)].strip()
    bracket = re.match(r"^\[([^]]{10,110})\]", title)
    return bracket.group(1).strip() if bracket else title


def entry_time(entry: dict) -> datetime:
    parts = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*parts[:6], tzinfo=timezone.utc) if parts else datetime.now(timezone.utc)


def fetch_articles(hours: int = 30, per_source: int = 15) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles: list[Article] = []
    for category, url in SOURCES.items():
        feed = feedparser.parse(url, agent=USER_AGENT)
        for entry in feed.entries[:per_source]:
            published = entry_time(entry)
            if published < cutoff:
                continue
            source = clean((entry.get("source") or {}).get("title", ""))
            title = strip_source(clean(entry.get("title", "")), source)
            link = entry.get("link", "")
            if title and link:
                articles.append(Article(category, title, link, clean(entry.get("summary", "")), published, source))
    return deduplicate(articles)


def normalized_title(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())[:100]


def title_like(text: str, title: str) -> bool:
    left, right = normalized_title(text), normalized_title(title)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    left_words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", text.lower()))
    right_words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", title.lower()))
    return len(left_words & right_words) / max(1, len(right_words)) >= 0.8


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in sorted(articles, key=lambda item: item.published, reverse=True):
        digest = hashlib.sha1(normalized_title(article.title)[:70].encode()).hexdigest()
        words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", article.title.lower()))
        is_similar = False
        for existing in result:
            other = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", existing.title.lower()))
            if len(words & other) / max(1, len(words | other)) >= 0.48:
                is_similar = True
                break
        if digest not in seen and not is_similar:
            seen.add(digest)
            result.append(article)
    return result


def article_id(article: Article) -> str:
    return hashlib.sha256(normalized_title(article.title).encode()).hexdigest()


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
    current: dict[str, str] = {}
    for key, value in previous.items():
        try:
            if datetime.fromisoformat(value) >= cutoff:
                current[key] = value
        except (TypeError, ValueError):
            pass
    now = datetime.now(timezone.utc).isoformat()
    current.update({article_id(article): now for article in articles})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_article_url(article: Article) -> str:
    if "news.google.com" not in article.url:
        return article.url
    try:
        result = gnewsdecoder(article.url, interval=0)
        return result.get("decoded_url", article.url) if result.get("status") else article.url
    except Exception:
        return article.url


def split_sentences(text: str) -> list[str]:
    text = clean(text)
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text)
    result = []
    for sentence in parts:
        sentence = clean(sentence).strip("•- ")
        if 30 <= len(sentence) <= 240 and not any(word in sentence for word in NOISE):
            result.append(sentence)
    return result


def sentence_score(sentence: str, article: Article, index: int, frequencies: Counter[str]) -> float:
    words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", sentence.lower()))
    title_words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", article.title.lower()))
    overlap = len(words & title_words) * 2.0
    frequency = sum(frequencies[word] for word in words) / max(1, math.sqrt(len(words)))
    length_bonus = 2.0 if 55 <= len(sentence) <= 150 else 0.5
    position_bonus = max(0.0, 2.5 - index * 0.12)
    return overlap + frequency * 0.15 + length_bonus + position_bonus


def summarize_body(article: Article, body: str) -> tuple[str, tuple[str, ...]]:
    sentences = split_sentences(body)
    title_key = normalized_title(article.title)
    sentences = [s for s in sentences if normalized_title(s) != title_key and title_key not in normalized_title(s)]
    if not sentences:
        fallback = clean(article.rss_summary)
        if title_like(fallback, article.title) or not fallback:
            fallback = f"{article.source or '해당 매체'}가 전한 {article.category} 분야의 최신 소식입니다."
        return fallback[:130], (fallback[:160],)
    frequencies: Counter[str] = Counter()
    for sentence in sentences:
        frequencies.update(set(re.findall(r"[가-힣A-Za-z0-9]{2,}", sentence.lower())))
    ranked = sorted(enumerate(sentences), key=lambda pair: sentence_score(pair[1], article, pair[0], frequencies), reverse=True)
    chosen: list[str] = []
    for _, sentence in ranked:
        if all(len(set(sentence) & set(other)) / max(1, len(set(sentence) | set(other))) < 0.75 for other in chosen):
            chosen.append(sentence)
        if len(chosen) == 3:
            break
    summary = chosen[0][:130]
    bullets = tuple(item[:180] for item in (chosen[1:3] or chosen[:1]))
    return summary, bullets


def enrich_article(article: Article) -> Article:
    original_url = decode_article_url(article)
    body = ""
    try:
        response = requests.get(original_url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
        body = trafilatura.extract(response.text, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception:
        pass
    summary, bullets = summarize_body(article, body)
    age_hours = max(0.0, (datetime.now(timezone.utc) - article.published).total_seconds() / 3600)
    searchable = f" {article.title} {article.rss_summary} ".lower()
    keyword_score = sum(1.5 for word in IMPORTANT_WORDS if word.lower() in searchable)
    relevance_score = sum(2.5 for term in CATEGORY_TERMS[article.category] if term in searchable)
    trust_score = 3.0 if any(name.lower() in article.source.lower() for name in TRUSTED_SOURCES) else 0.0
    body_score = min(4.0, len(body) / 1200)
    category_score = 2.0 if article.category in {"OpenAI", "Claude", "개발", "GitHub"} else 1.0
    score = 10.0 - min(8.0, age_hours / 5) + keyword_score + relevance_score + trust_score + body_score + category_score
    return replace(article, original_url=original_url, body=body, summary=summary, bullets=bullets, score=score)


def enrich_articles(articles: list[Article], limit: int = 20) -> list[Article]:
    relevant = []
    for article in articles:
        title_text = f" {article.title} ".lower()
        if article.category == "GitHub" or any(term in title_text for term in CATEGORY_TERMS[article.category]):
            relevant.append(article)
    source_pool = relevant or articles
    candidates: list[Article] = []
    per_category = max(2, limit // len(SOURCES))
    for category in SOURCES:
        matches = [article for article in source_pool if article.category == category]
        candidates.extend(matches[:per_category])
    candidates.extend(article for article in source_pool if article not in candidates)
    candidates = candidates[:limit]
    enriched: list[Article] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(enrich_article, article): article for article in candidates}
        for future in as_completed(futures):
            enriched.append(future.result())
    return sorted(enriched, key=lambda article: (article.score, article.published), reverse=True)


def select_balanced(articles: list[Article], limit: int = 7) -> list[Article]:
    chosen: list[Article] = []
    for category in SOURCES:
        match = next((article for article in articles if article.category == category and article not in chosen), None)
        if match:
            chosen.append(match)
    chosen.extend(article for article in articles if article not in chosen)
    return sorted(chosen[:limit], key=lambda article: article.score, reverse=True)


def request_json(url: str, headers: dict | None = None, form: dict | None = None) -> dict:
    final_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    if form is not None:
        final_headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    request = urllib.request.Request(url, data=body, headers=final_headers, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from exc


def refresh_kakao_token() -> str:
    form = {"grant_type": "refresh_token", "client_id": required("KAKAO_REST_API_KEY"), "refresh_token": required("KAKAO_REFRESH_TOKEN")}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        form["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    data = request_json(KAKAO_TOKEN_URL, form=form)
    if data.get("refresh_token"):
        rotate_github_secret(data["refresh_token"])
    return data["access_token"]


def rotate_github_secret(new_token: str) -> None:
    pat, repo = os.getenv("SECRET_ROTATION_PAT"), os.getenv("GITHUB_REPOSITORY")
    if not pat or not repo:
        raise RuntimeError("새 카카오 리프레시 토큰을 저장할 GitHub 권한이 없습니다.")
    subprocess.run(["gh", "secret", "set", "KAKAO_REFRESH_TOKEN", "--repo", repo, "--body", new_token], env={**os.environ, "GH_TOKEN": pat}, check=True)


def link(url: str) -> dict[str, str]:
    return {"web_url": url, "mobile_web_url": url}


def build_list_messages(articles: list[Article]) -> list[dict]:
    visible = articles[:6]
    if len(visible) >= 4:
        groups = [visible[:3], visible[3:6]]
    else:
        groups = [visible]
    today = datetime.now(KST).strftime("%m/%d")
    messages = []
    for index, group in enumerate(groups, 1):
        contents = []
        for number, article in enumerate(group, 1 + (index - 1) * 3):
            contents.append({"title": f"{number}. {article.title}"[:100], "description": article.summary[:100], "link": link(article.url)})
        template = {"object_type": "list", "header_title": f"[{today} 오늘의 테크 브리핑 {index}/{len(groups)}]", "header_link": link(DIGEST_URL), "contents": contents}
        if index == len(groups):
            template["buttons"] = [{"title": "추가 기사 더보기", "link": link(DIGEST_URL)}]
        messages.append(template)
    return messages


def send_kakao_template(access_token: str, template: dict) -> None:
    result = request_json(KAKAO_SEND_URL, form={"template_object": json.dumps(template, ensure_ascii=False)}, headers={"Authorization": f"Bearer {access_token}"})
    if result.get("result_code") != 0:
        raise RuntimeError(f"카카오 전송 실패: {result}")


def render_digest(primary: list[Article], extras: list[Article], path: Path = DIGEST_FILE) -> None:
    escape = html.escape
    date_text = datetime.now(KST).strftime("%Y년 %m월 %d일")
    cards = []
    for index, article in enumerate(primary, 1):
        bullets = "".join(f"<li>{escape(item)}</li>" for item in article.bullets[:2])
        cards.append(f'''<article id="article-{index}" class="card"><div class="meta"><span>{escape(article.category)}</span><span>{escape(article.source)}</span></div><h2><a href="{escape(article.original_url or article.url)}" target="_blank" rel="noopener">{index}. {escape(article.title)}</a></h2><p class="summary">{escape(article.summary)}</p><ul>{bullets}</ul><a class="read" href="{escape(article.original_url or article.url)}" target="_blank" rel="noopener">원문 기사 읽기 →</a></article>''')
    extra_items = "".join(f'''<li><a href="{escape(article.original_url or article.url)}" target="_blank" rel="noopener"><span>{escape(article.category)}</span>{escape(article.title)}</a></li>''' for article in extras[:10])
    document = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="매일 업데이트되는 IT·개발·AI 핵심 뉴스 브리핑"><title>{date_text} 오늘의 테크 브리핑</title><style>
:root{{--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#64748b;--line:#dfe5ef;--accent:#5b5bd6}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,'Noto Sans KR',sans-serif;line-height:1.65}}main{{width:min(920px,calc(100% - 32px));margin:0 auto;padding:54px 0 80px}}header{{margin-bottom:32px}}.eyebrow{{color:var(--accent);font-weight:800;letter-spacing:.08em}}h1{{font-size:clamp(2rem,5vw,3.4rem);line-height:1.15;margin:.25rem 0}}.lead{{color:var(--muted);font-size:1.05rem}}.card{{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px;margin:16px 0;box-shadow:0 8px 24px #27324a0a}}.meta{{display:flex;gap:8px;color:var(--accent);font-size:.85rem;font-weight:700}}.meta span+span:before{{content:'·';margin-right:8px;color:var(--muted)}}h2{{line-height:1.38;margin:.6rem 0}}a{{color:inherit;text-decoration:none}}h2 a:hover,.read:hover{{color:var(--accent)}}.summary{{font-size:1.05rem;font-weight:650}}li{{margin:.35rem 0}}.read{{display:inline-block;margin-top:8px;color:var(--accent);font-weight:750}}.extras{{margin-top:42px}}.extras ol{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:20px 24px 20px 52px}}.extras a span{{font-size:.75rem;color:var(--accent);border:1px solid #c8c8ff;border-radius:999px;padding:2px 7px;margin-right:8px}}footer{{color:var(--muted);margin-top:38px;font-size:.85rem}}@media(max-width:600px){{main{{padding-top:30px}}.card{{padding:20px}}}}
</style></head><body><main><header><div class="eyebrow">DAILY TECH DIGEST</div><h1>오늘의 테크 브리핑</h1><p class="lead">{date_text} · IT, 개발, AI, OpenAI, Claude 핵심 소식을 본문 기반으로 정리했습니다.</p></header><section><h2>오늘의 핵심 기사 7개</h2>{''.join(cards)}</section><section class="extras"><h2>추가로 볼 만한 기사</h2><ol>{extra_items}</ol></section><footer>자동 수집·요약된 내용이므로 중요한 판단 전에는 원문을 확인하세요.</footer></main></body></html>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"필수 환경변수 {name}가 없습니다.")
    return value


def main() -> None:
    sent = load_sent_ids()
    fetched = fetch_articles(int(os.getenv("NEWS_LOOKBACK_HOURS", "30")))
    fresh = exclude_previously_sent(fetched, sent)
    pool = fresh if len(fresh) >= 17 else fetched
    enriched = enrich_articles(pool, int(os.getenv("CANDIDATE_ARTICLES", "20")))
    primary = select_balanced(enriched, 7)
    extras = [article for article in enriched if article not in primary][:10]
    if len(primary) < 2:
        print("전송할 새 기사가 부족합니다.")
        return
    render_digest(primary, extras)
    messages = build_list_messages(primary)
    if os.getenv("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        print(json.dumps(messages, ensure_ascii=False, indent=2))
        print(f"브리핑 페이지 생성: {DIGEST_FILE}")
        return
    access_token = refresh_kakao_token()
    for template in messages:
        send_kakao_template(access_token, template)
    save_sent(primary + extras, sent)
    print(f"카카오톡 리스트 메시지 {len(messages)}개, 핵심 기사 {len(primary)}개 전송 완료")


if __name__ == "__main__":
    main()
