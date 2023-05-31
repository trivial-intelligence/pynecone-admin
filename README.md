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

### CRUD - Create Read Update Delete

The primary entry point for crud is `pynecone_admin.add_crud_routes`. Two arguments are required:

* `app`: pass an instance of your `pc.App`
* `objs`: pass a sequence of `pc.Model` classes that should be available for editing

Additional arguments are available to customize the crud pages:

* `form_component`: function called to render the object create/edit form, defaults to `pynecone_admin.crud.default_form_component`.
* `field_component`: function called to render a single field in the form, defaults to `pynecone_admin.crud.default_field_component`.
* `login_component`: function called to render the user login form, defaults to to no login form. If protected access is required, pass `pynecone_admin.default_login_component` or another custom login component.
* `can_access_resource`: access control function accepting state instance as first parameter, defaults to open access if not present
* `prefix`: route prefix for admin operations, defaults to `/crud`

### Authentication

The primary entry point for per-page authentication is `pynecone_admin.login_required`. It can be used
as a decorator on a page function.

```python
@pynecone_admin.login_required(State)
def page():
  return pc.text("You will only see this if logged in")
```

A custom `login_component` can be passed as a paramter, see
`pynecone_admin.auth.default_login_component` as an example for how to customize
the logic. A custom component must call `State._login(self, user_id)` to
authenticate a session for the given `user_id`.

If no users exist in the database, the default login component will create the
first user to login as an admin.

## Changelog

### v0.1a0 - 2023-05-20

Alpha Release
