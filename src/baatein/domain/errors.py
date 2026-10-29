class BaateinError(Exception):
    pass


class NotFoundError(BaateinError):
    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} '{entity_id}' not found")
        self.entity = entity
        self.entity_id = entity_id


class DuplicateEmailError(BaateinError):
    def __init__(self, email: str) -> None:
        super().__init__(f"a user with email '{email}' already exists")
        self.email = email


class DuplicateTurnError(BaateinError):
    def __init__(self, session_id: str, seq: int) -> None:
        super().__init__(f"turn {seq} already exists in session '{session_id}'")
        self.session_id = session_id
        self.seq = seq


class DuplicatePersonError(BaateinError):
    def __init__(self, name: str) -> None:
        super().__init__(f"a person named '{name}' already exists for this user")
        self.name = name


class InvalidCredentialsError(BaateinError):
    def __init__(self) -> None:
        super().__init__("invalid email or password")


class InvalidTokenError(BaateinError):
    pass


class WeakPasswordError(BaateinError):
    def __init__(self, min_length: int) -> None:
        super().__init__(f"password must be at least {min_length} characters")
        self.min_length = min_length


class ConfigurationError(BaateinError):
    pass
