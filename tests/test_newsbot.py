from datetime import datetime, timezone

from newsbot.main import (
    Article,
    article_id,
    build_list_messages,
    clean,
    deduplicate,
    exclude_previously_sent,
    load_sent_ids,
    needs_korean_translation,
    render_digest,
    save_sent,
    select_balanced,
    summarize_body,
)


def article(category: str, title: str, score: float = 1.0) -> Article:
    return Article(category, title, "https://news.google.com/a", title, datetime.now(timezone.utc), "테스트 매체", score=score)


def test_clean_html():
    assert clean("<b>AI</b>&nbsp; 뉴스") == "AI 뉴스"


def test_deduplicate_titles():
    assert len(deduplicate([article("AI", "새 AI 모델 출시!"), article("AI", "새 AI 모델 출시")])) == 1


def test_balanced_selection():
    chosen = select_balanced([article("AI", "a"), article("AI", "b"), article("Claude", "c")], 2)
    assert {item.category for item in chosen} == {"AI", "Claude"}


def test_body_summary_does_not_repeat_title():
    item = article("OpenAI", "OpenAI가 새로운 에이전트를 공개했다")
    body = (
        "오픈AI는 개발자가 여러 도구를 연결할 수 있는 실행 환경을 선보였다. "
        "새 기능은 반복적인 코딩 작업과 테스트 과정을 자동으로 처리하도록 설계됐다. "
        "사용자는 실행 과정에서 필요한 권한과 외부 연결 범위를 직접 제한할 수 있다."
    )
    summary, bullets = summarize_body(item, body)
    assert summary != item.title
    assert bullets


def test_rss_title_repetition_uses_neutral_fallback():
    item = article("개발", "새로운 개발 도구 공개")
    summary, _ = summarize_body(item, "")
    assert summary != item.title
    assert "최신 소식" in summary


def test_single_message_shows_clean_three_item_preview():
    items = [article("AI", f"기사 {index}") for index in range(7)]
    messages = build_list_messages(items)
    assert len(messages) == 1
    assert messages[0]["object_type"] == "list"
    assert len(messages[0]["contents"]) == 3
    assert messages[0]["contents"][0]["description"]
    assert messages[0]["buttons"][0]["title"] == "추가 기사 더보기"


def test_selection_prioritizes_openai_claude_and_elon():
    items = [
        article("AI", "일반 AI", 10),
        article("OpenAI", "OpenAI 소식", 5),
        article("Claude", "Claude 소식", 4),
        article("일론 머스크", "머스크 소식", 3),
    ]
    chosen = select_balanced(items, 4)
    assert [item.category for item in chosen[:3]] == ["OpenAI", "Claude", "일론 머스크"]


def test_selection_backfills_to_limit_without_priority_news():
    items = [article("IT", f"일반 기술 기사 {index}", 10 - index) for index in range(9)]
    chosen = select_balanced(items, 7)
    assert len(chosen) == 7


def test_render_digest_has_primary_and_extra_links(tmp_path):
    primary = [article("AI", f"핵심 기사 {index}") for index in range(7)]
    extras = [article("개발", f"추가 기사 {index}") for index in range(10)]
    path = tmp_path / "index.html"
    render_digest(primary, extras, path)
    content = path.read_text(encoding="utf-8")
    assert "오늘의 핵심 기사 7개" in content
    assert "추가 기사 9" in content
    assert content.count('<article id="article-') == 7


def test_sent_history_round_trip(tmp_path):
    path = tmp_path / "sent.json"
    item = article("AI", "이미 보낸 기사")
    save_sent([item], {}, path)
    sent = load_sent_ids(path)
    assert article_id(item) in sent
    assert exclude_previously_sent([item, article("AI", "새 기사")], sent)[0].title == "새 기사"


def test_english_detection_for_translation():
    assert needs_korean_translation("OpenAI releases a powerful new coding agent for developers")
    assert not needs_korean_translation("OpenAI가 새로운 코딩 에이전트를 공개했다")


def test_article_id_uses_original_title_after_translation():
    original = article("AI", "OpenAI releases a new model")
    translated = Article(**{**original.__dict__, "title": "오픈AI가 새 모델을 공개했다", "original_title": original.title})
    assert article_id(original) == article_id(translated)
