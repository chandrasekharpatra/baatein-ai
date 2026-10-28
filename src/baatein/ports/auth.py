from dataclasses import dataclass
from typing import Protocol

from baatein.domain.models import User


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str, password: str) -> bool: ...


class AccessTokens(Protocol):
    def issue(self, user_id: str) -> str: ...
    def verify(self, token: str) -> str: ...


@dataclass(frozen=True)
class CallGrant:
    server_url: str
    participant_token: str
    room_name: str


class CallTokenIssuer(Protocol):
    def issue(self, user: User) -> CallGrant: ...
