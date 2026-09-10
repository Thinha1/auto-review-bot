import pytest

from app.security import InvalidWebhookUrlError, SecretCipher, validate_discord_webhook_url


def test_secret_cipher_round_trip_and_wrong_key() -> None:
    cipher = SecretCipher("a" * 32)
    encrypted = cipher.encrypt("https://discord.com/api/webhooks/1/token")
    assert "discord.com" not in encrypted
    assert cipher.decrypt(encrypted).startswith("https://discord.com")
    with pytest.raises(ValueError, match="cannot be decrypted"):
        SecretCipher("b" * 32).decrypt(encrypted)


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/token",
        "https://evil.example/api/webhooks/1/token",
        "https://discord.com.evil.example/api/webhooks/1/token",
        "https://discord.com@evil.example/api/webhooks/1/token",
        "https://discord.com:8443/api/webhooks/1/token",
        "https://discord.com/api/users/1",
    ],
)
def test_discord_webhook_url_rejects_ssrf_targets(url: str) -> None:
    with pytest.raises(InvalidWebhookUrlError):
        validate_discord_webhook_url(url)


def test_discord_webhook_url_accepts_official_endpoint() -> None:
    url = "https://discord.com/api/webhooks/123/token-value"
    assert validate_discord_webhook_url(url) == url
