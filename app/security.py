"""Secret encryption and outbound URL validation."""

import base64
import hashlib
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

_DISCORD_HOSTS = frozenset(
    {"discord.com", "canary.discord.com", "ptb.discord.com", "discordapp.com"}
)
_WEBHOOK_PATH = re.compile(r"^/api(?:/v\d+)?/webhooks/\d+/[^/]+/?$")


class InvalidWebhookUrlError(ValueError):
    pass


class SecretCipher:
    """Versioned authenticated encryption backed by Fernet."""

    def __init__(self, master_key: str, key_version: int = 1) -> None:
        if len(master_key.encode()) < 32:
            raise ValueError("MASTER_KEY must contain at least 32 bytes")
        derived = base64.urlsafe_b64encode(hashlib.sha256(master_key.encode()).digest())
        self._fernet = Fernet(derived)
        self.key_version = key_version

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored secret cannot be decrypted") from exc


def validate_discord_webhook_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _DISCORD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not _WEBHOOK_PATH.fullmatch(parsed.path)
    ):
        raise InvalidWebhookUrlError("Discord webhook URL is not allowed")
    return url
