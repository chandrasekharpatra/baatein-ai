from datetime import timedelta

import jwt

from baatein.domain.errors import InvalidTokenError
from baatein.ports.clock import Clock

ALGORITHM = "HS256"
ISSUER = "baatein"


class JwtAccessTokens:
    def __init__(self, secret: str, ttl: timedelta, clock: Clock) -> None:
        self._secret = secret
        self._ttl = ttl
        self._clock = clock

    def issue(self, user_id: str) -> str:
        now = self._clock.now()
        claims = {"sub": user_id, "iss": ISSUER, "iat": now, "exp": now + self._ttl}
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)

    def verify(self, token: str) -> str:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["sub", "exp", "iss"], "verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(f"invalid access token: {exc}") from exc
        if claims["exp"] <= self._clock.now().timestamp():
            raise InvalidTokenError("access token has expired")
        subject: str = claims["sub"]
        return subject
