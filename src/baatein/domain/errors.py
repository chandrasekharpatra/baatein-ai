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
