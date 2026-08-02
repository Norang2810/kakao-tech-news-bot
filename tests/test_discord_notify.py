from newsbot import discord_notify


class Response:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self.ok = status_code < 400
        self.json_data = json_data or {}

    def json(self):
        return self.json_data


def test_wait_url_preserves_query():
    result = discord_notify.webhook_wait_url("https://discord.com/api/webhooks/1/token?thread_id=2")
    assert "thread_id=2" in result
    assert "wait=true" in result


def test_wait_for_published_digest(monkeypatch):
    monkeypatch.setattr(discord_notify.requests, "get", lambda *args, **kwargs: Response(text='<meta name="digest-build-id" content="run-7">'))
    assert discord_notify.wait_for_published_digest("run-7", attempts=1, interval=0)


def test_missing_webhook_skips_safely(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_TEST_MODE", raising=False)
    assert discord_notify.notify_discord() is False


def test_discord_notification_posts_after_publish(monkeypatch):
    calls = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setenv("DISCORD_USER_ID", "437489596071280642")
    monkeypatch.setenv("DIGEST_BUILD_ID", "run-8")
    monkeypatch.setattr(discord_notify, "wait_for_published_digest", lambda build_id: build_id == "run-8")
    monkeypatch.setattr(
        discord_notify.requests,
        "post",
        lambda url, json, timeout: calls.append((url, json))
        or Response(json_data={"mentions": [{"id": "437489596071280642"}]}),
    )
    assert discord_notify.notify_discord() is True
    assert len(calls) == 1
    assert discord_notify.DIGEST_URL in calls[0][1]["content"]
    assert calls[0][1]["content"].startswith("<@437489596071280642>")
    assert calls[0][1]["allowed_mentions"] == {
        "parse": [],
        "users": ["437489596071280642"],
    }


def test_discord_test_mode_skips_page_wait(monkeypatch):
    calls = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setenv("DISCORD_TEST_MODE", "true")
    monkeypatch.setenv("DISCORD_USER_ID", "437489596071280642")
    monkeypatch.delenv("DIGEST_BUILD_ID", raising=False)
    monkeypatch.setattr(discord_notify, "wait_for_published_digest", lambda build_id: (_ for _ in ()).throw(AssertionError("must not wait")))
    monkeypatch.setattr(
        discord_notify.requests,
        "post",
        lambda url, json, timeout: calls.append(json)
        or Response(json_data={"mentions": [{"id": "437489596071280642"}]}),
    )
    assert discord_notify.notify_discord() is True
    assert "테스트 성공" in calls[0]["content"]
    assert calls[0]["content"].startswith("<@437489596071280642>")


def test_notification_without_user_id_disables_all_mentions():
    payload = discord_notify.notification_payload()
    assert payload["allowed_mentions"] == {"parse": [], "users": []}
    assert "<@" not in payload["content"]


def test_invalid_discord_user_id_is_rejected(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setenv("DISCORD_TEST_MODE", "true")
    monkeypatch.setenv("DISCORD_USER_ID", "not-a-user")
    try:
        discord_notify.notify_discord()
    except RuntimeError as exc:
        assert "숫자로 된 Discord 사용자 ID" in str(exc)
    else:
        raise AssertionError("invalid user ID must be rejected")


def test_unresolved_discord_mention_is_rejected(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setenv("DISCORD_TEST_MODE", "true")
    monkeypatch.setenv("DISCORD_USER_ID", "437489596071280642")
    monkeypatch.setattr(
        discord_notify.requests,
        "post",
        lambda *args, **kwargs: Response(json_data={"mentions": []}),
    )
    try:
        discord_notify.notify_discord()
    except RuntimeError as exc:
        assert "실제 멘션으로 처리하지 않았습니다" in str(exc)
    else:
        raise AssertionError("unresolved mention must be rejected")
