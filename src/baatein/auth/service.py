from baatein.domain.errors import InvalidCredentialsError, InvalidTokenError, WeakPasswordError
from baatein.domain.ids import new_id
from baatein.domain.models import User
from baatein.ports.auth import AccessTokens, PasswordHasher
from baatein.ports.clock import Clock
from baatein.ports.repos import UserRepo
from baatein.voice.languages import LanguageRegistry


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(
        self,
        users: UserRepo,
        hasher: PasswordHasher,
        tokens: AccessTokens,
        languages: LanguageRegistry,
        clock: Clock,
        default_language: str,
        min_password_length: int = 8,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens
        self._languages = languages
        self._clock = clock
        self._default_language = default_language
        self._min_password_length = min_password_length
        self._dummy_hash = hasher.hash(new_id())

    async def signup(
        self,
        email: str,
        password: str,
        display_name: str | None = None,
        language: str | None = None,
    ) -> User:
        if len(password) < self._min_password_length:
            raise WeakPasswordError(self._min_password_length)
        language = language or self._default_language
        self._languages.get(language)
        user = User(
            id=new_id(),
            email=normalize_email(email),
            password_hash=self._hasher.hash(password),
            created_at=self._clock.now(),
            display_name=display_name.strip() if display_name and display_name.strip() else None,
            language=language,
        )
        await self._users.add(user)
        return user

    async def login(self, email: str, password: str) -> User:
        user = await self._users.get_by_email(normalize_email(email))
        if user is None:
            self._hasher.verify(self._dummy_hash, password)
            raise InvalidCredentialsError
        if not self._hasher.verify(user.password_hash, password):
            raise InvalidCredentialsError
        return user

    def issue_token(self, user: User) -> str:
        return self._tokens.issue(user.id)

    async def authenticate(self, token: str) -> User:
        user = await self._users.get(self._tokens.verify(token))
        if user is None:
            raise InvalidTokenError("access token refers to an unknown user")
        return user
