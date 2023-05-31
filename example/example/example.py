from __future__ import annotations

import enum
import logging
import typing as t
import uuid

import pynecone as pc
import sqlalchemy
from sqlmodel import col, or_

from pynecone_admin import auth, crud
from pynecone_admin.auth_models import AuthSession, User


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class Hero(pc.Model, table=True):
    name: str
    secret_name: str
    age: t.Optional[int] = None


class F4(enum.Enum):
    o1 = "option 1"
    o2 = "option 2"
    o3 = "option 3"


class Stuff(pc.Model, table=True):
    f1: str
    f2: int = 42
    f3: bool = False
    f4: F4 | None = None
    f5: uuid.UUID | None = None
    f6: bool | None = None
    f7: float | None = None
    hidden: str = "hidden"

    def dict(self, *args, **kwargs):
        d = super().dict(*args, **kwargs)
        if self.f4 is not None:
            d["f4"] = self.f4.name
        if self.f5 is not None:
            d["f5"] = str(self.f5)
        return d

    def __pynecone_admin_save_object_hook__(self):
        print("saving object hook")

    def __pynecone_admin_load_object_hook__(self):
        print("loaded object hook")

    def __pynecone_admin_load_row_hook__(self):
        print(f"loaded row: {self}")

    def __pynecone_admin_delete_object_hook__(self):
        print("delete object hook")

    @classmethod
    def __pynecone_admin_filter_where_hook__(cls, filter_value):
        if filter_value.startswith("f2=="):
            val = filter_value.partition("==")[2]
            try:
                val = int(val)
            except ValueError:
                pass
            else:
                print(f"filter where f2 == {val}")
                return cls.f2 == val
        if filter_value == "f3":
            print("filter where f3 is True")
            return cls.f3 == True
        elif filter_value == "!f3":
            print("filter where f3 is False")
            return cls.f3 == False
        print(f"filter prefix match for {filter_value}")
        return or_(
            col(cls.f1).ilike(f"{filter_value}%"),
            *[
                col(selected_column).cast(sqlalchemy.String).ilike(f"{filter_value}%")
                for selected_column in [cls.f2, cls.f4]
            ],
        )


Stuff.__pynecone_admin_fields__ = [f for f in Stuff.__fields__ if f != "hidden"]


@auth.authenticated_user_id
class State(pc.State):
    @pc.cached_var
    def authenticated_user(self) -> User:
        if self.authenticated_user_id >= 0:
            with pc.session() as session:
                logger.debug(
                    getattr(self, "get_current_page")()
                    + f" Lookup authenticated_user_id: {self.authenticated_user_id}"
                )
                user = session.exec(
                    User.select.where(User.id == self.authenticated_user_id)
                ).one_or_none()
                if user:
                    return user
        return User()


def index() -> pc.Component:
    return pc.center(
        pc.vstack(
            pc.heading("Welcome to Pynecone!", font_size="2em"),
            pc.link("Protected Page", href="/protected"),
            pc.link("CRUD Models", href="/crud"),
        ),
        padding_top="10%",
    )


@auth.login_required(State)
def protected() -> pc.Component:
    return pc.center(
        pc.vstack(
            pc.heading("You are logged in as ", State.authenticated_user.username),
            pc.text(
                "You are ",
                pc.cond(State.authenticated_user.admin, "ADMIN", "just a peon"),
            ),
            pc.link("CRUD Models", href="/crud"),
        ),
        padding_top="10%",
    )


app = pc.App(state=State)
app.add_page(index)
app.add_page(protected, route="/protected")
crud.add_crud_routes(
    app,
    [AuthSession, User, Hero, Stuff],
    # login and access control are optional
    login_component=auth.default_login_component,
    can_access_resource=lambda state: state.authenticated_user.admin,
)
app.compile()
