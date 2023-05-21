import pynecone as pc
from sqlalchemy import UniqueConstraint


class User(pc.Model, table=True):
    __table_args__ = (UniqueConstraint("username"),)
    username: str = ""
    password_hash: str = ""
    enabled: bool = False
    admin: bool = False


class AuthSession(pc.Model, table=True):
    __table_args__ = (UniqueConstraint("session_id"),)
    user_id: int
    session_id: str
