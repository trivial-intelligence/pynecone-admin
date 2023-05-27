import datetime

from passlib.context import CryptContext
import pynecone as pc
from sqlmodel import Column, DateTime, Field, func


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(pc.Model, table=True):
    """A local User model with bcrypt password hashing."""
    username: str = Field(unique=True, nullable=False, index=True)
    password_hash: str = Field(nullable=False)
    enabled: bool = False
    admin: bool = False

    def do_hash_password(self):
        """Rehash the value of password_hash is not an identifiable password hash."""
        if not pwd_context.identify(self.password_hash):
            self.password_hash = pwd_context.hash(self.password_hash)

    def verify(self, secret: str) -> bool:
        """Returns True if the secret matches this user's password_hash."""
        return pwd_context.verify(
            secret,
            self.password_hash,
        )

    # Tell pynecone-admin to hash the password when saving this object
    __pynecone_admin_save_object_hook__ = do_hash_password


class AuthSession(pc.Model, table=True):
    """Correlate a session_id with an arbitrary user_id."""
    user_id: int = Field(index=True, nullable=False)
    session_id: str = Field(unique=True, index=True, nullable=False)
    expiration: datetime.datetime = Field(sa_column=Column(DateTime(timezone=True), server_default=func.now()), nullable=False)

    def dict(self, *args, **kwargs) -> dict:
        """Convert the object to a serializable dictionary."""
        d = super().dict()
        if self.expiration:
            d["expiration"] = self.expiration.replace(microsecond=0).isoformat()
        return d