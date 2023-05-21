import typing as t

import pynecone as pc
import sqlmodel

from .auth_models import AuthSession, User
from .persistent_token import PersistentToken
from .utils import add_computed_var, add_event_handler, fix_local_event_handlers


def authenticated_user_id(State: t.Type[pc.State]) -> t.Type[pc.State]:
    if getattr(State, "persistent_token", None) is not None:
        return State

    State.add_var("persistent_token", type_=str, default_value="")

    def _login(self, user_id: int):
        if self.authenticated_user_id > 0:
            return
        with pc.session() as session:
            session.add(AuthSession(user_id=user_id, session_id=self.current_token))
            session.commit()
        self.persistent_token = self.persistent_token

    State._login = _login

    @add_event_handler(State)
    def do_logout(self):
        with pc.session() as session:
            for auth_session in session.exec(
                AuthSession.select.where(AuthSession.session_id == self.current_token)
            ).all():
                session.delete(auth_session)
            session.commit()
        self.persistent_token = self.persistent_token

    @add_computed_var(State)
    @pc.cached_var
    def current_token(self) -> str:
        token = self.get_token()
        return self.persistent_token or token

    @add_computed_var(State)
    @pc.cached_var
    def authenticated_user_id(self) -> int:
        with pc.session() as session:
            s = session.exec(
                AuthSession.select.where(AuthSession.session_id == self.current_token),
            ).first()
            if s:
                return s.user_id
        return -1

    return State


LOGON_STATE_FOR_STATE = {}


def _create_first_admin_user(
    session: sqlmodel.Session,
    username: str,
    password: str,
) -> User:
    user = User()
    user.username = username
    user.password_hash = password
    user.enabled = True
    user.admin = True
    session.add(user)
    session.commit()
    print(f"Created first new admin user: {username}")
    return user


def default_logon_component(State: t.Type[pc.State]) -> pc.Component:
    if State not in LOGON_STATE_FOR_STATE:

        @fix_local_event_handlers
        class LogonState(State):
            username: str = ""
            password: str = ""
            error_message: str = ""

            def on_submit(self):
                self.error_message = ""
                with pc.session() as session:
                    user = session.exec(
                        User.select.where(User.username == self.username)
                    ).one_or_none()
                    if user is None:
                        # if this is the first time logging in, create the user and make them admin
                        if (
                            session.exec(
                                User.select.limit(1),
                            ).one_or_none()
                            is None
                        ):
                            user = _create_first_admin_user(
                                session,
                                self.username,
                                self.password,
                            )
                if user is not None and user.enabled and user.verify(self.password):
                    State._login(self, user.id)
                if user is not None and not user.enabled:
                    self.password = ""
                    return type(self).set_error_message("This account is disabled.")
                if user is None or not user.verify(self.password):
                    self.password = ""
                    return type(self).set_error_message(
                        "There was a problem logging in, please try again.",
                    )
                self.username = self.password = ""

        LOGON_STATE_FOR_STATE[State] = LogonState
    LogonState = LOGON_STATE_FOR_STATE[State]

    return pc.vstack(
        pc.cond(
            LogonState.error_message != "",
            pc.text(LogonState.error_message),
            pc.box(),
        ),
        pc.form(
            pc.input(
                placeholder="username",
                value=LogonState.username,
                on_change=LogonState.set_username,
            ),
            pc.password(value=LogonState.password, on_change=LogonState.set_password),
            pc.button("Logon", type_="submit"),
            on_submit=LogonState.on_submit,
        ),
    )


def login_required(
    State: t.Type[pc.State],
    login_component: t.Callable[[t.Type[pc.State]], pc.Component] | None = None,
) -> pc.Component:
    if not hasattr(State, "authenticated_user_id"):
        raise TypeError(
            f"{State} should have 'authenticated_user_id' var, did you use "
            "@pynecone_admin.auth.authenticated_user_id decorator on the app state?"
        )

    if login_component is None:
        login_component = default_logon_component

    def comp(original_component) -> pc.Component:
        return pc.box(
            PersistentToken.create(on_change=State.set_persistent_token),
            pc.cond(
                State.authenticated_user_id > 0,
                pc.vstack(
                    original_component(),
                    pc.button("Logout", on_click=State.do_logout),
                ),
                login_component(State),
            ),
        )

    return comp
