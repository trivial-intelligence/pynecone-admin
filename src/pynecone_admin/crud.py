from __future__ import annotations

import datetime
import enum
import json
import logging
import time
import typing as t
import urllib.parse
import uuid

import pydantic
import pynecone as pc
from pynecone import utils
import sqlalchemy
from sqlmodel import col, or_

from .auth import login_required
from .utils import color_mode, debounce_input, fix_local_event_handlers


logger = logging.getLogger(__name__)


QUERY_PARAM_DEFAULTS = {
    "page_size": 10,
    "offset": 0,
    "filter": "",
}


class FormComponent(t.Protocol):
    def __call__(
        self, *children: pc.Component, on_submit: pc.event.EventHandler, **kwargs: t.Any
    ) -> pc.Component:
        ...


def default_form_component(
    *children: pc.Component, on_submit: pc.event.EventHandler, **kwargs: t.Any
) -> pc.Component:
    return pc.form(*children, on_submit=on_submit, **kwargs)


class FieldComponent(t.Protocol):
    def __call__(
        self,
        field: pydantic.Field,
        value: t.Any,
        on_change: pc.event.EventHandler,
        on_set_default: pc.event.EventHandler,
        **kwargs: t.Any,
    ) -> pc.Component:
        ...


def default_field_component(
    field: pydantic.Field,
    value: t.Any,
    on_change: pc.event.EventHandler,
    on_set_default: pc.event.EventHandler,
    **kwargs: t.Any,
) -> pc.Component:
    kwargs["is_required"] = kwargs.pop("is_required", field.required)
    attrs_if_required = {"color": "red"} if field.required else {}
    field_name_and_type = field.name + f" ({field.type_.__name__})"
    if field.default is None:
        value_is_default = value.to_string() == "null"
    else:
        value_is_default = value == field.default
    label = pc.form_label(
        pc.flex(
            pc.text(field_name_and_type),
            pc.spacer(),
            pc.cond(
                value_is_default,
                pc.text(
                    "(NULL)" if field.default is None else "(default)",
                    **attrs_if_required,
                ),
                pc.text(
                    "(reset to default)", on_click=on_set_default, cursor="pointer"
                ),
            ),
        ),
    )
    input_control = None
    # XXX: support alternative primary keys
    if field.name == "id":
        label = pc.form_label(field_name_and_type)  # no resetting of id
        input_control = pc.cond(
            value,
            pc.input(
                is_read_only=True,
                value=value.to_string().to(str),
                **kwargs,
            ),
            pc.input(
                is_read_only=True,
                value="(new)",
                **kwargs,
            ),
        )
    elif issubclass(field.type_, (str, float, uuid.UUID)):
        input_control = debounce_input(
            pc.input(
                placeholder=field.name,
                value=value.to(str) | "",
                on_change=on_change,
                **kwargs,
            ),
        )
        if issubclass(field.type_, uuid.UUID):
            input_control = pc.hstack(
                input_control,
                pc.button(
                    "🎲",
                    on_click=lambda: on_change("random"),
                ),
            )
    elif issubclass(field.type_, datetime.datetime):
        input_control = pc.hstack(
            debounce_input(
                pc.input(
                    type_="datetime-local",
                    placeholder=field.name,
                    value=value.to(str) | "",
                    on_change=on_change,
                    **kwargs,
                )
            ),
            pc.button(
                "Now",
                on_click=lambda: on_change("now"),
            ),
        )
    elif issubclass(field.type_, enum.Enum):
        options = [
            pc.option(label=f"{key}: {enum_value.value}", value=key)
            for key, enum_value in field.type_.__members__.items()
        ]
        input_control = pc.select(
            options=options,
            value=value.to(str) | "",
            on_change=on_change,
            placeholder=repr(field.type_),
        )
    elif issubclass(field.type_, bool):
        input_control = pc.checkbox(
            value.to_string(),
            is_checked=value,
            on_change=on_change,
            **kwargs,
        )
    elif issubclass(field.type_, int):
        input_control = pc.number_input(
            input_mode="numeric",
            value=value | "",
            on_change=on_change,
            **kwargs,
        )
    if input_control is None:
        return pc.text(f"Unsupported field: {field.name} ({field.type_})", **kwargs)
    return pc.form_control(label, input_control)


def format_query_string(params: dict[str, t.Any]) -> str:
    """Convert query params to router string

    Args:
        params: the query_params from router_data

    Returns:
        The query string
    """
    return urllib.parse.urlencode({k.replace("_", "-"): v for k, v in params.items()})


def fields(model_clz: t.Type[pc.Model]) -> dict[str, pydantic.Field]:
    selected_fields: list[str] = getattr(
        model_clz, "__pynecone_admin_fields__", model_clz.__fields__.keys()
    )
    return {key: model_clz.__fields__[key] for key in selected_fields}


def add_crud_routes(
    app: pc.App,
    objs: t.Sequence[t.Type[pc.Model]],
    form_component: FormComponent | None = None,
    field_component: FieldComponent | None = None,
    can_access_resource: t.Callable[[pc.State], bool] | None = None,
    prefix: str = "/crud",
):
    if form_component is None:
        form_component = default_form_component
    if field_component is None:
        field_component = default_field_component
    if can_access_resource is None:
        # if the user does not provide access control, allow all
        def can_access_resource(_):
            return True

    PER_MODEL_CRUD_STATES = {}

    class CRUDState(app.state):
        pass

    def CRUDSubStateFor(model_clz: t.Type[pc.Model]) -> t.Type[pc.State]:
        def set_subfield(self, field_name: str, value: str | None):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            self.form_message = ""
            field = self.current_obj.__fields__[field_name]
            if value is not None:
                if issubclass(field.type_, (int, float)):
                    try:
                        # cast directly as the type_
                        value = field.type_(value)
                    except ValueError as exc:
                        self.form_message = str(exc)
                        return
                # special type initialization handling
                if issubclass(field.type_, enum.Enum):
                    try:
                        value = field.type_.__members__[value]
                    except KeyError as exc:
                        self.form_message = str(exc)
                        return
                if issubclass(field.type_, datetime.datetime):
                    if value == "now":
                        # TODO: sane timezone handling?
                        value = datetime.datetime.now()
                    else:
                        try:
                            value = datetime.datetime.fromisoformat(value)
                        except ValueError as exc:
                            self.form_message = str(exc)
                if issubclass(field.type_, uuid.UUID):
                    if value == "random":
                        value = uuid.uuid4()
                    else:
                        try:
                            # try to parse uuid from int, falling back to str or whatever
                            value = uuid.UUID(int=int(value))
                        except ValueError:
                            try:
                                value = uuid.UUID(value)
                            except ValueError as exc:
                                self.form_message = str(exc)
                                return
            logger.debug(f"set_subfield({model_clz.__name__}) {field_name}={value}")
            setattr(self.current_obj, field_name, value)
            # re-assign to parent attribute
            self.current_obj = self.current_obj

        def load_current_obj(self):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            if self.obj_id is not None:
                try:
                    obj_id = int(self.obj_id)
                except ValueError:
                    self.reset()
                    return
                with pc.session() as session:
                    try:
                        self.current_obj = session.exec(
                            model_clz.select.where(model_clz.id == obj_id)
                        ).one_or_none()
                    except Exception as exc:
                        self.db_message = str(exc)
                        return
                    else:
                        self.db_message = ""
                    if self.current_obj is not None:
                        hook = getattr(
                            self.current_obj,
                            "__pynecone_admin_load_object_hook__",
                            None,
                        )
                        if hook:
                            hook()
                        logger.debug(f"load {obj_id}: {self.current_obj}")
                    else:
                        logging.info(f"{obj_id} is not found")
                        return self.redirect_back_to_table()

        def save_current_obj(self):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            hook = getattr(
                self.current_obj, "__pynecone_admin_save_object_hook__", None
            )
            if hook:
                hook()
            logger.info(f"persist {self.current_obj} to db")
            with pc.session() as session:
                try:
                    session.add(self.current_obj)
                    session.commit()
                    session.refresh(self.current_obj)
                    self.db_message = f"Persist {self.current_obj}"
                except Exception as exc:
                    self.db_message = str(exc)
                    return
            return self.redirect_back_to_table()

        def delete_current_obj(self):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            if self.current_obj.id is not None:
                hook = getattr(
                    self.current_obj, "__pynecone_admin_delete_object_hook__", None
                )
                if hook:
                    hook()
                logger.info(f"delete {self.current_obj} from db")
                with pc.session() as session:
                    try:
                        session.delete(self.current_obj)
                        session.commit()
                        self.db_message = f"Deleted {self.current_obj}"
                    except Exception as exc:
                        self.db_message = str(exc)
                        return
            return self.redirect_back_to_table()

        def reset(self):
            self.current_obj = model_clz()
            self.db_message = ""

        def redir_to_new(self):
            return pc.redirect(self.get_current_page() + "/new")

        def refresh(self):
            self._trigger_update = time.time()

        def offset(self) -> int:
            return int(
                self.get_query_params().get("offset", QUERY_PARAM_DEFAULTS["offset"])
            )

        def page_size(self) -> int:
            return int(
                self.get_query_params().get(
                    "page_size", QUERY_PARAM_DEFAULTS["page_size"]
                )
            )

        def filter_value(self) -> str:
            return self.get_query_params().get("filter", QUERY_PARAM_DEFAULTS["filter"])

        def obj_page(self):
            if self.authenticated_user_id < 0 or not can_access_resource(self):
                return []  # no viewie
            if self.get_current_page() != "/" + utils.format.format_route(
                f"{prefix}/{model_clz.__name__}"
            ):
                return []  # page/table not active
            self._page_params = (
                self.get_query_params()
            )  # cache these to redirect after editing
            logger.debug(
                f"get page: {self._trigger_update} {self.offset} {self.page_size} {self.filter_value}"
            )

            def hook(row):
                _hook = getattr(row, "__pynecone_admin_load_row_hook__", None)
                if _hook:
                    _hook()
                return row

            def filter_hook(filter_value):
                _hook = getattr(model_clz, "__pynecone_admin_filter_where_hook__", None)
                if _hook is not None:
                    return _hook(filter_value)
                # default implementation just slowly scans every column
                return or_(
                    col(getattr(model_clz, field_name))
                    .cast(sqlalchemy.String)
                    .ilike(f"%{filter_value}%")
                    for field_name in fields(model_clz)
                )

            with pc.session() as session:
                select_stmt = model_clz.select
                if self.filter_value != "":
                    select_stmt = select_stmt.where(filter_hook(self.filter_value))
                return [
                    hook(row)
                    for row in session.exec(
                        select_stmt.order_by(model_clz.id.asc())
                        .offset(self.offset)
                        .limit(self.page_size)
                    )
                ]

        obj_page.__annotations__ = {"return": list[model_clz]}

        def redirect_back_to_table(self):
            self.reset()
            # do NOT carry obj_id back to the table
            self.get_query_params().pop("obj_id", None)
            return self.redirect_with_params(
                url=self.get_current_page().rpartition("/")[0],
                **self._page_params,
            )

        def redirect_with_params(self, url=None, **params):
            if url is None:
                url = self.get_current_page()
            # copy the query_params, so that new hydrate event has a delta,
            # otherwise we update the actual dict here, and the redirect doesn't
            # trigger reassignment to router_data, since the value has no change
            query_params = self.get_query_params().copy()
            query_params.update(params)
            for param, default_value in QUERY_PARAM_DEFAULTS.items():
                # clean up URL by removing default values
                if query_params.get(param) == default_value:
                    query_params.pop(param, None)
            if query_params:
                url = url + "?{}".format(format_query_string(query_params))
            logger.debug(f"Redirect to {url}")
            return pc.redirect(url)

        def prev_page(self):
            offset = self.offset - self.page_size
            if offset < 0:
                offset = 0
            return self.redirect_with_params(offset=offset)

        def next_page(self):
            return self.redirect_with_params(offset=self.offset + self.page_size)

        def set_page_size(self, v: str):
            try:
                return self.redirect_with_params(page_size=int(v))
            except ValueError:
                pass

        def set_filter_value(self, v: str):
            return self.redirect_with_params(filter=v, offset=0)

        def has_next_results(self) -> bool:
            return len(self.obj_page) == self.page_size

        event_handlers = (
            set_subfield,
            load_current_obj,
            save_current_obj,
            delete_current_obj,
            reset,
            redir_to_new,
            refresh,
            redirect_back_to_table,
            redirect_with_params,
            prev_page,
            next_page,
            set_page_size,
            set_filter_value,
        )
        substate_clz_name = f"CRUDSubStateFor{model_clz.__name__}"
        substate_clz = type(
            substate_clz_name,
            (CRUDState,),
            {
                "__annotations__": {
                    "current_obj": model_clz,
                    "_trigger_update": float,
                    "_page_params": dict[str, t.Any],
                    "db_message": str,
                    "form_message": str,
                },
                "current_obj": model_clz(),
                "_trigger_update": 0.0,
                "_page_params": {},
                "db_message": "",
                "form_message": "",
                "filter_value": pc.cached_var(filter_value),
                "offset": pc.cached_var(offset),
                "page_size": pc.cached_var(page_size),
                "obj_page": pc.cached_var(obj_page),
                "has_next_results": pc.cached_var(has_next_results),
                **{handler.__name__: handler for handler in event_handlers},
            },
        )
        return fix_local_event_handlers(substate_clz)

    def substate_for(model_clz: t.Type[pc.Model]) -> t.Type[pc.State]:
        return PER_MODEL_CRUD_STATES.setdefault(
            model_clz.__name__,
            CRUDSubStateFor(model_clz=model_clz),
        )

    def create_update_delete(
        model_clz: t.Type[pc.Model],
    ) -> pc.Component:
        SubState = substate_for(model_clz)
        controls = []
        for field_name, field in fields(model_clz).items():
            value = pc.vars.BaseVar(
                name=f"{SubState.current_obj.name}.{field_name}",
                type_=field.type_,
                state=SubState.current_obj.state,
            )
            default_value = pc.vars.BaseVar(
                name=json.dumps(field.default),
                type_=field.type_,
                state="",
                is_local=True,
            )
            on_change = lambda v: SubState.set_subfield(field_name, v)
            on_set_default = lambda: SubState.set_subfield(field_name, default_value)
            controls.append(field_component(field, value, on_change, on_set_default))

        if controls:
            controls.append(
                pc.hstack(
                    pc.button("Save", type_="submit"),
                    pc.button("Discard", on_click=SubState.redirect_back_to_table),
                    pc.button("Delete", on_click=SubState.delete_current_obj),
                ),
            )
        return form_component(
            pc.text(SubState.db_message, width="50vw"),
            pc.text(SubState.form_message, width="50vw"),
            *controls,
            on_submit=SubState.save_current_obj,
        )

    def format_cell(obj, col) -> pc.Td:
        value = pc.vars.BaseVar(
            name=f"{obj.name}.{col}",
            type_=obj.type_.__fields__[col].type_,
            state=obj.state,
        )
        if value.type_ == bool:
            value = pc.cond(value, "✅", "❌")
        return pc.td(value)

    def pagination_controls(State) -> pc.Component:
        return pc.hstack(
            pc.cond(
                State.offset > 0,
                pc.button("< Prev", on_click=State.prev_page),
                pc.button("< Prev", is_disabled=True),
            ),
            pc.text("Page Size: "),
            pc.number_input(
                input_mode="numeric",
                value=State.page_size,
                on_change=State.set_page_size,
                width="10vw",
            ),
            pc.button("✨", on_click=State.redir_to_new),
            pc.button("🔄", on_click=State.refresh),
            pc.cond(
                State.has_next_results,
                pc.button("Next >", on_click=State.next_page),
                pc.button("Next >", is_disabled=True),
            ),
        )

    def filter_component(SubState: pc.State) -> pc.Component:
        return pc.hstack(
            debounce_input(
                pc.input(
                    placeholder="filter",
                    value=SubState.filter_value,
                    on_change=SubState.set_filter_value,
                ),
                debounce_timeout=500,
            ),
            pc.cond(
                SubState.filter_value != QUERY_PARAM_DEFAULTS["filter"],
                pc.button(
                    "Clear",
                    on_click=lambda: SubState.set_filter_value(
                        QUERY_PARAM_DEFAULTS["filter"]
                    ),
                ),
            ),
        )

    def table_component(model_clz: t.Type[pc.Model]) -> pc.Component:
        SubState = substate_for(model_clz)
        return pc.fragment(
            filter_component(SubState),
            pagination_controls(SubState),
            pc.table_container(
                pc.table(
                    pc.thead(
                        pc.tr(*[pc.th(col) for col in fields(model_clz)]),
                    ),
                    pc.tbody(
                        pc.foreach(
                            SubState.obj_page,
                            lambda u: pc.tr(
                                *[
                                    format_cell(
                                        obj=u,
                                        col=col,
                                    )
                                    for col in fields(model_clz)
                                ],
                                on_click=pc.redirect(
                                    "/"
                                    + utils.format.format_route(
                                        f"{prefix}/{u.type_.__name__}/"
                                    )
                                    + "/"
                                    + u.id.to_string().to(str),
                                ),
                                cursor="pointer",
                                _hover={
                                    "background": pc.cond(
                                        color_mode == "dark",
                                        "var(--chakra-colors-whiteAlpha-200)",
                                        "var(--chakra-colors-blackAlpha-200)",
                                    ),
                                },
                            ),
                        ),
                    ),
                )
            ),
            pagination_controls(SubState),
        )

    def breadcrumb_navigation(
        breadcrumb_links: list[tuple[str, str]],
        separator: str
        | pc.Component = pc.text(">", padding_left="5px", padding_right="5px"),
    ) -> list[pc.Component]:
        children = [pc.link("pynecone-admin", href=f"{prefix}", font_weight="bold")]
        for link_name, href in breadcrumb_links:
            children.append(separator)
            children.append(pc.link(link_name, href=href))
        return children

    def header_component(
        breadcrumb_links: list[tuple[str, str]] | None = None
    ) -> pc.Component:
        breadcrumb_links = breadcrumb_navigation(breadcrumb_links or [])
        return pc.flex(
            *breadcrumb_links,
            pc.spacer(),
            pc.button(
                pc.cond(color_mode == "light", pc.icon(tag="moon"), pc.icon(tag="sun")),
                on_click=pc.toggle_color_mode,
            ),
            pc.cond(
                app.state.authenticated_user_id > -1,
                pc.button(
                    "Logout",
                    on_click=app.state.do_logout,
                    margin_left="2vw",
                ),
            ),
            width="100%",
            padding_top="1vh",
            padding_left="1vw",
            padding_right="1vw",
            padding_bottom="5vh",
        )

    def make_page(model_clz: t.Type[pc.Model]) -> pc.Component:
        table = table_component(model_clz)

        @login_required(State=app.state)
        def page() -> pc.Component:
            return pc.vstack(
                header_component(breadcrumb_links=[(pc.text(model_clz.__name__), "#")]),
                table,
                align_items="flex-start",
                padding_left="2vw",
            )

        return page

    def make_modal(model_clz: t.Type[pc.Model]) -> pc.Component:
        crud_component = create_update_delete(model_clz)

        @login_required(State=app.state)
        def page() -> pc.Component:
            obj_id = pc.vars.BaseVar(
                name="obj_id",
                type_=int,
                state=app.state.get_full_name(),
            )
            return pc.vstack(
                header_component(
                    breadcrumb_links=[
                        (
                            pc.text(model_clz.__name__),
                            "/" + utils.format.format_route(f"{prefix}/{obj.__name__}"),
                        ),
                        (pc.text(obj_id), "#"),
                    ]
                ),
                crud_component,
                padding_left="2vw",
            )

        return page

    def all_models() -> pc.Component:
        return pc.vstack(
            header_component(),
            *(
                pc.link(
                    obj.__name__,
                    href=utils.format.format_route(f"{prefix}/{obj.__name__}"),
                )
                for obj in objs
            ),
            padding_left="2vw",
        )

    for obj in objs:
        app.add_page(
            make_page(obj),
            route=f"{prefix}/{obj.__name__}",
            title=f"pynecrud: {obj.__name__}",
        )
        app.add_page(
            make_modal(obj),
            route=f"{prefix}/{obj.__name__}/[obj_id]",
            title=f"pynecone-admin: {obj.__name__} > Edit",
            on_load=substate_for(obj).load_current_obj,
        )
    app.add_page(all_models, prefix, title="pynecone-admin: All Models")
