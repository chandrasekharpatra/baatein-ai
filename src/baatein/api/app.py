import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from importlib import resources
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from injector import Injector
from pydantic import BaseModel, EmailStr, Field, ValidationError

from baatein.accounts import AccountExistsError, Accounts
from baatein.auth.service import AuthService
from baatein.config import Settings
from baatein.domain.errors import (
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotAdminError,
    SignupClosedError,
    WeakPasswordError,
)
from baatein.domain.models import User
from baatein.memory.finalizer import Finalizer
from baatein.memory.seed import SeedError, SeedFile, SeedResult, problems
from baatein.memory.service import MemoryService
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


class ForgetMeRequest(BaseModel):
    password: str = Field(max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    language: str
    persona: str
    is_admin: bool = False

    @classmethod
    def of(cls, user: User, is_admin: bool = False) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            language=user.language,
            persona=user.persona,
            is_admin=is_admin,
        )


class AccountOut(UserOut):
    memories: int
    calls: int
    last_call_at: datetime | None


class AccountUpload(BaseModel):
    seed: SeedFile
    replace: bool = False
    password: str | None = Field(default=None, max_length=256)
    """Instead of the file's; a replaced account keeps its own if neither gives one."""


class LoadedOut(BaseModel):
    account: UserOut
    added: SeedResult
    replaced: bool


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CallOut(BaseModel):
    server_url: str
    participant_token: str
    room_name: str


MAX_UPLOAD_BYTES = 1_000_000

ERROR_STATUS: dict[type[Exception], int] = {
    DuplicateEmailError: status.HTTP_409_CONFLICT,
    AccountExistsError: status.HTTP_409_CONFLICT,
    SignupClosedError: status.HTTP_403_FORBIDDEN,
    NotAdminError: status.HTTP_403_FORBIDDEN,
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


def accounts_service(request: Request) -> Accounts:
    service: Accounts = request.app.state.injector.get(Accounts)
    return service


AccountsDep = Annotated[Accounts, Depends(accounts_service)]


async def admin_user(user: CurrentUser, accounts: AccountsDep) -> User:
    if not accounts.is_admin(user):
        raise NotAdminError
    return user


AdminUser = Annotated[User, Depends(admin_user)]


async def read_upload(request: Request) -> AccountUpload:
    """The request body, refused past MAX_UPLOAD_BYTES before it is all read."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"the file is over {MAX_UPLOAD_BYTES // 1_000_000} MB",
            )
    try:
        return AccountUpload.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise SeedError(problems(exc)) from exc


def create_app(injector: Injector) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        # Summaries of finished calls are written here, in the long-running API process.
        summaries = None
        if injector.get(Settings).finalize.interval_seconds > 0:
            summaries = asyncio.create_task(injector.get(Finalizer).run_forever())
        yield
        if summaries is not None:
            summaries.cancel()
            with suppress(asyncio.CancelledError):
                await summaries
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

    settings = injector.get(Settings)

    def user_out(user: User) -> UserOut:
        return UserOut.of(user, settings.auth.is_admin(user.email))

    @app.get("/auth/signup")
    async def signup_status() -> dict[str, bool]:
        """Whether the page shows "Sign up"."""
        return {"enabled": settings.auth.signup_enabled}

    @app.post("/auth/signup", status_code=status.HTTP_201_CREATED)
    async def signup(body: SignupRequest, auth: Auth) -> SessionOut:
        if not settings.auth.signup_enabled:
            raise SignupClosedError
        user = await auth.signup(
            body.email, body.password, body.display_name, body.language, body.persona
        )
        return SessionOut(access_token=auth.issue_token(user), user=user_out(user))

    @app.post("/auth/login")
    async def login(body: LoginRequest, auth: Auth) -> SessionOut:
        user = await auth.login(body.email, body.password)
        return SessionOut(access_token=auth.issue_token(user), user=user_out(user))

    @app.get("/me")
    async def me(user: CurrentUser) -> UserOut:
        return user_out(user)

    @app.post("/me/forget", status_code=status.HTTP_204_NO_CONTENT)
    async def forget_me(body: ForgetMeRequest, user: CurrentUser, auth: Auth) -> None:
        """Erase everything Baatein remembers about the user; the account stays. The password
        is asked again, so a borrowed, logged-in phone can't wipe someone's memories."""
        await auth.login(user.email, body.password)
        await injector.get(MemoryService).forget_user(user.id)

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

    @app.get("/admin/accounts")
    async def list_accounts(_admin: AdminUser, accounts: AccountsDep) -> list[AccountOut]:
        return [
            AccountOut(
                **user_out(row.user).model_dump(),
                memories=row.memories,
                calls=row.calls,
                last_call_at=row.last_call_at,
            )
            for row in await accounts.list()
        ]

    @app.post("/admin/accounts", status_code=status.HTTP_201_CREATED)
    async def load_account(
        _admin: AdminUser,
        accounts: AccountsDep,
        upload: Annotated[AccountUpload, Depends(read_upload)],
    ) -> LoadedOut:
        """Create a test account from a seed file, or load it again with `replace`."""
        loaded = await accounts.load(upload.seed, replace=upload.replace, password=upload.password)
        return LoadedOut(
            account=user_out(loaded.user), added=loaded.added, replaced=loaded.replaced
        )

    @app.get("/admin/accounts/{user_id}/seed")
    async def download_account(
        user_id: str, _admin: AdminUser, accounts: AccountsDep
    ) -> JSONResponse:
        """The account as a seed file, which loads back as the same account."""
        seed = await accounts.export(user_id)
        if seed is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such account")
        name = (seed.user.email if seed.user else user_id).split("@")[0]
        return JSONResponse(
            seed.model_dump(mode="json", exclude_defaults=True),
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )

    return app


def _error_handler(code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if code == status.HTTP_401_UNAUTHORIZED else None
        return JSONResponse({"detail": str(exc)}, status_code=code, headers=headers)

    return handle
