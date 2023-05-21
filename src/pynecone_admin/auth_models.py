from passlib.context import CryptContext
import pynecone as pc
from sqlalchemy import UniqueConstraint


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(pc.Model, table=True):
    __table_args__ = (UniqueConstraint("username"),)
    username: str = ""
    password_hash: str = ""
    enabled: bool = False
    admin: bool = False

    def do_hash_password(self):
        if not pwd_context.identify(self.password_hash):
            self.password_hash = pwd_context.hash(self.password_hash)

    def verify(self, secret: str) -> bool:
        return pwd_context.verify(
            secret,
            self.password_hash,
        )

    __pynecone_admin_save_object_hook__ = do_hash_password


class AuthSession(pc.Model, table=True):
    __table_args__ = (UniqueConstraint("session_id"),)
    user_id: int
    session_id: str
