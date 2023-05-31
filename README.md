# pynecone-admin

[![main branch test status](https://github.com/trivial-intelligence/pynecone-admin/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/trivial-intelligence/pynecone-admin/actions/workflows/test.yml?query=branch%3Amain)
[![PyPI version](https://badge.fury.io/py/pynecone-admin.svg)](https://pypi.org/project/pynecone-admin)

A generic CRUD `Model` boilerplate for the python-based full stack
[pynecone](https://pynecone.io) framework.

* Simple, extendable login state and redirect helpers
* `add_crud_routes` method adds Create, Update, Read, Delete
  capabilities to specified models

## Example

See [./example/example/example.py](example.py).

## Usage

1. Include `pynecone-admin` in your project `requirements.txt`.
2. Call `pynecone_admin.add_crud_routes(app=app, obs=[pynecone_admin.User, Widget], prefix="/crud")`
3. `pc init && pc run`
4. Access models at `/crud`.

## API

### Authentication

Per-page authentication is provided by `pynecone_admin.login_required`. It can be used
as a decorator on a page function.

```python
@pynecone_admin.login_required(State)
def page():
  return pc.text("You are logged in as ", State.authenticated_user_id)
```

A custom `login_component` can be passed as a parameter, see
`pynecone_admin.auth.default_login_component` as an example for how to customize
the logic. A custom component must call `State._login(self, user_id)` to
authenticate a session for the given `user_id`.

If no users exist in the database, the default login component will create the
first user to login as an admin.

By default, only `authenticated_user_id` is provided, if access to the provided User model is needed, implement a small helper in the local app `State` (from the example):

```python
class State(pc.State):
    @pc.cached_var
    def authenticated_user(self) -> User:
        if self.authenticated_user_id >= 0:
            with pc.session() as session:
                if user := session.exec(
                  User.select.where(User.id == self.authenticated_user_id),
                ).one_or_none():
                    return user
        return User()
```

#### Persistent Token

Using the `login_required` function will automatically augment the passed `State` class with
additional variables and functionality:

* `current_token`: a persistent (`localStorage`-backed) value that identifies
  the client browser. This value is updated by the `PersistentToken.on_change`
  event handler defined inside the `login_required` function.
* `authenticated_user_id`: the user_id associated with a non-expired auth session
  matching the state's `current_token`. If no such session exists, `-1`.
* `do_logout`: event handler that disassociates the state's
  `current_token` with any valid auth session
* `_login`: backend-only function that associates the state's
  `current_token` with the given `user_id` (values less than 0 are ignored).
  To implement a custom login method, the code must somehow call
  `State._login(self, user_id)` after properly authenticating the user_id.

### CRUD - Create Read Update Delete

The primary entry point is `pynecone_admin.add_crud_routes`. Two arguments are required:

* `app`: pass an instance of your `pc.App`
* `objs`: pass a sequence of `pc.Model` classes that should be available for editing

Additional arguments are available to customize the crud pages:

* `form_component`: function called to render the object create/edit form, defaults to `pynecone_admin.crud.default_form_component`.
* `field_component`: function called to render a single field in the form, defaults to `pynecone_admin.crud.default_field_component`.
* `login_component`: function called to render the user login form, defaults to to no login form. If protected access is required, pass `pynecone_admin.default_login_component` or another custom login component.
* `can_access_resource`: access control function accepting state instance as first parameter, defaults to open access if not present
* `prefix`: route prefix for admin operations, defaults to `/crud`

#### Supported Field Types

See `pynecone_admin.crud.default_field_component`.

**Simple Types**

* `str`, `float`: standard `pc.input`
* `int`: `pc.number_input`
* `bool`: `pc.check_box`

**Special Types**

* `enum.Enum`: `pc.select` drop down box
* `datetime.datetime`: `pc.input` with `type_="datetime-local"`, which renders a date picker on modern 
  browsers.
* `uuid.UUID`: standard `pc.input` with a "random" button that sets the value to `"random"` (handled in `set_subfield`).

Why are these types special? Because pynecone cannot handle them out of the box,
although they work fine via sqlmodel / sqlalchemy. The solution is to convert
them to serializable types by overriding the `dict()` method of the model:

```python
from __future__ import annotations

import datetime
import enum
import uuid

import pynecone as pc
import pynecone_admin
import sqlmodel


class TokenStatus(enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class TokenOwner(pc.Model, table=True):
    user_id: int
    token_uuid: uuid.UUID
    created: datetime.datetime = sqlmodel.Field(
        sa_column=sqlmodel.Column(
            sqlmodel.DateTime(timezone=True),
            server_default=sqlmodel.func.now(),
        ),
    )
    status: TokenStatus
    comment: str | None = None

    def dict(self, *args, **kwargs):
        d = super().dict(*args, **kwargs)
        d["token_uuid"] = str(self.token_uuid) if self.token_uuid else None
        d["created"] = (
            self.created.replace(microsecond=0).isoformat() if self.created else None
        )
        d["status"] = self.status.name if self.status else None
        return d


class State(pc.State):
    pass


app = pc.App(state=State)
pynecone_admin.add_crud_routes(app, [TokenOwner], prefix="/")
app.compile()
```

The save field handler, `set_subfield`, currently handles deserializing these types as
special cases.

#### Customizing Per-Model Behavior

`pynecone-admin` respects specially named hook methods or attributes attached to
Models, to allow behavioral customization:

* `__pynecone_admin_fields__: Sequence[str]`: names of fields to include in the
  table and editor form. Allows for rendering a subset of available fields. Default
  uses all fields defined on the model.
* `__pynecone_admin_load_object_hook__`: method called on each instance
  after it is loaded from the database for editing.
* `__pynecone_admin_load_row_hook__`: method called on each instance after it is loaded
  from the database during enumeration (for the table).
* `__pynecone_admin_filter_where_hook__`: classmethod accepting a string, `filter_value`, that returns
  an expression passable to sqlmodel `where()` selector to filter the table query
  based on a user-supplied value. Default filter hook casts all columns as string and performs
  a wildcard match (`LIKE %{filter_value}%`) on each.
* `__pynecone_admin_save_object_hook__`: method called on each instance
  before it is persisted to the database. The included `User` model, implements this
  hook to hash password strings that don't look like valid hashes before saving.
* `__pynecone_admin_delete_object_hook__`: method called on each instance
  before it is deleted from the database.

## Changelog

### v0.1a0 - 2023-05-31

Alpha Release
