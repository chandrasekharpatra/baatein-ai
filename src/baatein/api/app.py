from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import resources
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from injector import Injector
from pydantic import BaseModel, EmailStr, Field

from baatein.auth.service import AuthService
from baatein.config import Settings
from baatein.domain.errors import (
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidTokenError,
    WeakPasswordError,
)
from baatein.domain.models import User
from baatein.memory.seed import Seeder, SeedError, SeedFile, SeedResult
from baatein.ports.auth import CallTokenIssuer
from baatein.storage.sqlite.db import Database
from baatein.voice.languages import UnknownLanguageError
from baatein.voice.personas import UnknownPersonaError


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    display_name: str | None = Field(default=None, max_length=80)
    language: str | None = None
    persona: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    language: str
    persona: str

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            language=user.language,
            persona=user.persona,
        )


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CallOut(BaseModel):
    server_url: str
    participant_token: str
    room_name: str


ERROR_STATUS: dict[type[Exception], int] = {
    DuplicateEmailError: status.HTTP_409_CONFLICT,
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    InvalidTokenError: status.HTTP_401_UNAUTHORIZED,
    WeakPasswordError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownLanguageError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownPersonaError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SeedError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

bearer = HTTPBearer(auto_error=False)


def auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.injector.get(AuthService)
    return service


def call_tokens(request: Request) -> CallTokenIssuer:
    issuer: CallTokenIssuer = request.app.state.injector.get(CallTokenIssuer)
    return issuer


Auth = Annotated[AuthService, Depends(auth_service)]


async def current_user(
    auth: Auth,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise InvalidTokenError("missing bearer token")
    return await auth.authenticate(credentials.credentials)


CurrentUser = Annotated[User, Depends(current_user)]


def create_app(injector: Injector) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        await injector.get(Database).close()

    app = FastAPI(title="Baatein API", lifespan=lifespan)
    app.state.injector = injector

    for error, code in ERROR_STATUS.items():
        app.add_exception_handler(error, _error_handler(code))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return resources.files("baatein.api").joinpath("static/index.html").read_text("utf-8")

    @app.post("/auth/signup", status_code=status.HTTP_201_CREATED)
    async def signup(body: SignupRequest, auth: Auth) -> SessionOut:
        user = await auth.signup(
            body.email, body.password, body.display_name, body.language, body.persona
        )
        return SessionOut(access_token=auth.issue_token(user), user=UserOut.of(user))

    @app.post("/auth/login")
    async def login(body: LoginRequest, auth: Auth) -> SessionOut:
        user = await auth.login(body.email, body.password)
        return SessionOut(access_token=auth.issue_token(user), user=UserOut.of(user))

    @app.get("/me")
    async def me(user: CurrentUser) -> UserOut:
        return UserOut.of(user)

    @app.post("/livekit/token")
    async def livekit_token(
        user: CurrentUser, issuer: Annotated[CallTokenIssuer, Depends(call_tokens)]
    ) -> CallOut:
        grant = issuer.issue(user)
        return CallOut(
            server_url=grant.server_url,
            participant_token=grant.participant_token,
            room_name=grant.room_name,
        )

    if injector.get(Settings).dev.seed_enabled:
        add_dev_routes(app, injector)

    return app


def add_dev_routes(app: FastAPI, injector: Injector) -> None:
    """Development-only routes, registered only when BAATEIN_DEV__SEED_ENABLED=true."""

    @app.get("/dev/seed")
    async def seed_status() -> dict[str, bool]:
        return {"enabled": True}

    @app.post("/dev/seed")
    async def seed(body: SeedFile, user: CurrentUser) -> SeedResult:
        """Add artificial memories, storylines, reminders and past calls to your account."""
        return await injector.get(Seeder).load(user, body)


def _error_handler(code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None
        return JSONResponse({"detail": str(exc)}, status_code=code, headers=headers)

    return handle
