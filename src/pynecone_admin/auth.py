"""Handle authentication in a pynecone app."""
from __future__ import annotations

import datetime
import logging
import typing as t

import pynecone as pc
import sqlmodel

from .auth_models import AuthSession, User
from .persistent_token import PersistentToken
from .utils import (
    add_computed_var,
    add_event_handler,
    debounce_input,
    fix_local_event_handlers,
)


logger = logging.getLogger(__name__)


DEFAULT_AUTH_SESSION_EXPIRATION_DELTA = datetime.timedelta(days=7)


def authenticated_user_id(State: t.Type[pc.State]) -> t.Type[pc.State]:
    """
    Enable persistent authenticated session tracking for the State.

    May be applied as a decorator on the State class definition.

    * Add `persistent_token` str var that is updated on all pages using the
      `@login_required()` decorator
    * Add `do_logout` event to deauth the current session token
    * Add backend `_login` function that can only be called from another
      event handler after securely validating a login, associates the current
      persistent_token with the given user_id. Must pass state instance
      when calling `State._login(self, valid_user_id)`
    * Add `authenticated_user_id` int var associated with the session
        (`-1` for no authenticated user)
    """
    if getattr(State, "authenticated_user_id", None) is not None:
        return State

    State.add_var("persistent_token", type_=str, default_value="")

    @add_event_handler(State)
    def set_persistent_token(self, persistent_token):
        if self.persistent_token != persistent_token:
            # only re-assign if the new value is different
            self.persistent_token = persistent_token

    @add_event_handler(State)
    def do_logout(self):
        with pc.session() as session:
            for auth_session in session.exec(
                AuthSession.select.where(AuthSession.session_id == self.current_token)
            ).all():
                session.delete(auth_session)
            session.commit()
        self.persistent_token = self.persistent_token

    def _login(
        self,
        user_id: int,
        expiration_delta: datetime.timedelta = DEFAULT_AUTH_SESSION_EXPIRATION_DELTA,
    ):
        if self.authenticated_user_id > -1:
            return
        if user_id < 0:
            return
        do_logout(self)
        with pc.session() as session:
            session.add(
                AuthSession(
                    user_id=user_id,
                    session_id=self.current_token,
                    expiration=datetime.datetime.now(datetime.timezone.utc)
                    + expiration_delta,
                )
            )
            session.commit()
        self.persistent_token = self.persistent_token

    State._login = _login

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
                AuthSession.select.where(
                    AuthSession.session_id == self.current_token,
                    AuthSession.expiration
                    >= datetime.datetime.now(datetime.timezone.utc),
                ),
            ).first()
            if s:
                return s.user_id
        return -1

    return State


LOGIN_STATE_FOR_STATE = {}


def LoginStateFor(State: t.Type[pc.State]) -> t.Type[pc.State]:
    """
    Create a "LoginState" as a substate of the given state class.

    The LoginState provides fields for username and password, and an event handler, on_submit,
    which checks the username and password against the User model defined in auth_models.

    If no users exist in the database, the first user to login in will be created as an admin user.

    Args:
        State: the state class to create a substate for (typically, app.state)
    """

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
        user.do_hash_password()
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.warning(f"Created first new admin user: {username}")
        return user

    if State not in LOGIN_STATE_FOR_STATE:

        @fix_local_event_handlers
        class LoginState(State):
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
                        if session.exec(User.select.limit(1)).one_or_none() is None:
                            user = _create_first_admin_user(
                                session,
                                self.username,
                                self.password,
                            )
                if user is not None and user.enabled and user.verify(self.password):
                    # mark the user as logged in
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

        LOGIN_STATE_FOR_STATE[State] = LoginState
    return LOGIN_STATE_FOR_STATE[State]


def default_login_component(State: t.Type[pc.State]) -> pc.Component:
    """
    Handle local User model logins.

    Args:
        State: the state class for the app
    """
    LoginState = LoginStateFor(State)

    login_form = pc.form(
        debounce_input(
            pc.input(
                placeholder="username",
                value=LoginState.username,
                on_change=LoginState.set_username,
            ),
        ),
        debounce_input(
            pc.password(
                placeholder="password",
                value=LoginState.password,
                on_change=LoginState.set_password,
            ),
        ),
        pc.button("Login", type_="submit"),
        on_submit=LoginState.on_submit,
    )

    return pc.cond(
        LoginState.is_hydrated == False,
        pc.vstack(
            pc.text("Connecting to Backend"),
            pc.spinner(),
            padding_top="10vh",
        ),
        pc.vstack(
            pc.cond(  # conditionally show error messages
                LoginState.error_message != "",
                pc.text(LoginState.error_message),
            ),
            login_form,
            padding_top="10vh",
        ),
    )


def login_required(
    State: t.Type[pc.State],
    login_component: t.Callable[[t.Type[pc.State]], pc.Component] | None = None,
) -> pc.Component:
    """
    Require login to access a page.

    May be used as a decorator on a page function.

    Args:
        State: the state class for the app (typically app.state)
        login_component: function that will render a login form (and the state necessary to drive it).
            Default will use `default_login_component`.

    The login component must implement some mechanism for calling `State._login(self, user_id)`
    to create a session for the user.
    """
    if not hasattr(State, "authenticated_user_id"):
        authenticated_user_id(State)

    if login_component is None:
        login_component = default_login_component

    def comp(original_component) -> pc.Component:
        return pc.fragment(
            PersistentToken.create(on_change=State.set_persistent_token),
            pc.cond(
                State.authenticated_user_id > -1,
                original_component(),
                login_component(State),
            ),
        )

    return comp
