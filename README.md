# pynecone-admin

[![main branch test status](https://github.com/trivial-intelligence/pynecone-admin/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/trivial-intelligence/pynecone-admin/actions/workflows/test.yml?query=branch%3Amain)
[![PyPI version](https://badge.fury.io/py/pynecone-admin.svg)](https://pypi.org/project/pynecone-admin)

A generic CRUD `Model` boilerplate for the python-based full stack
[pynecone](https://pynecone.io) framework.

* Simple, extendable login state and redirect helpers
* `add_crud_routes` method adds Create, Update, Read, Delete
  capabilities to specified models

## Example

...

## Usage

1. Include `pynecone-admin` in your project `requirements.txt`.
2. Call `pynecone_admin.add_crud_routes(app=app, obs=[pynecone_admin.User, Widget], prefix="/crud")`
3. `pc init && pc run`
4. Access models at `/crud`. (First user to login becomes admin)

## Changelog

### v0.1a0 - 2023-05-20

Alpha Release
