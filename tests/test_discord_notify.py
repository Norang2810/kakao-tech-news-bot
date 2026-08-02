from newsbot import discord_notify


class Response:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = status_code < 400


def test_wait_url_preserves_query():
    result = discord_notify.webhook_wait_url("https://discord.com/api/webhooks/1/token?thread_id=2")
    assert "thread_id=2" in result
    assert "wait=true" in result


def test_wait_for_published_digest(monkeypatch):
    monkeypatch.setattr(discord_notify.requests, "get", lambda *args, **kwargs: Response(text='<meta name="digest-build-id" content="run-7">'))
    assert discord_notify.wait_for_published_digest("run-7", attempts=1, interval=0)


def test_missing_webhook_skips_safely(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert discord_notify.notify_discord() is False


def test_discord_notification_posts_after_publish(monkeypatch):
    calls = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/token")
    monkeypatch.setenv("DIGEST_BUILD_ID", "run-8")
    monkeypatch.setattr(discord_notify, "wait_for_published_digest", lambda build_id: build_id == "run-8")
    monkeypatch.setattr(discord_notify.requests, "post", lambda url, json, timeout: calls.append((url, json)) or Response())
    assert discord_notify.notify_discord() is True
    assert len(calls) == 1
    assert discord_notify.DIGEST_URL in calls[0][1]["content"]
    assert calls[0][1]["allowed_mentions"] == {"parse": []}
