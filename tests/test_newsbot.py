from datetime import datetime, timezone

from newsbot.main import Article, article_id, build_messages, clean, deduplicate, exclude_previously_sent, load_sent_ids, save_sent, select_balanced


def article(category: str, title: str) -> Article:
    return Article(category, title, "https://example.com", "설명입니다.", datetime.now(timezone.utc))


def test_clean_html():
    assert clean("<b>AI</b>&nbsp; 뉴스") == "AI 뉴스"


def test_deduplicate_titles():
    assert len(deduplicate([article("AI", "새 AI 모델 출시!"), article("AI", "새 AI 모델 출시")])) == 1


def test_balanced_selection():
    chosen = select_balanced([article("AI", "a"), article("AI", "b"), article("Claude", "c")], 2)
    assert {x.category for x in chosen} == {"AI", "Claude"}


def test_kakao_text_limit():
    a = Article("AI", "제목" * 100, "https://example.com", "", datetime.now(timezone.utc))
    [(text, _)] = build_messages([a], ["요약" * 100])
    assert len(text) <= 200


def test_sent_history_round_trip(tmp_path):
    path = tmp_path / "sent.json"
    a = article("AI", "이미 보낸 기사")
    save_sent([a], {}, path)
    sent = load_sent_ids(path)
    assert article_id(a) in sent
    assert exclude_previously_sent([a, article("AI", "새 기사")], sent)[0].title == "새 기사"
