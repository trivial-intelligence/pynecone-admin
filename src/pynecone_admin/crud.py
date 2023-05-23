from __future__ import annotations

import logging
import time
import typing as t

import pynecone as pc
from pynecone import utils

from .auth import login_required
from .utils import debounce_input, fix_local_event_handlers


logger = logging.getLogger(__name__)


def add_crud_routes(
    app: pc.App,
    objs: t.Sequence[t.Type[pc.Model]],
    can_access_resource: t.Callable[[pc.State], bool] | None,
    prefix: str = "/crud",
):
    PER_MODEL_CRUD_STATES = {}

    class CRUDState(app.state):
        pass

    # if the user does not provide access control, allow all
    can_access_resource = can_access_resource or (lambda _: True)

    def CRUDSubStateFor(model_clz: t.Type[pc.Model]) -> t.Type[pc.State]:
        def set_subfield(self, field_name, value):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            field = self.current_obj.__fields__[field_name]
            if field.type_ in [int, float]:
                try:
                    value = field.type_(value)
                except ValueError:
                    return
            logger.debug(f"set_subfield({model_clz.__name__}) {field_name}={value}")
            setattr(self.current_obj, field_name, value)
            # re-assign to parent attribute
            self.current_obj = self.current_obj

        def load_current_obj(self):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            logger.debug("load_current_obj dirty_vars: %s", getattr(self, "dirty_vars"))
            logger.debug(getattr(self, "router_data"))
            if self.obj_id is not None:
                try:
                    obj_id = int(self.obj_id)
                except ValueError:
                    self.reset()
                    return
                breakpoint()
                with pc.session() as session:
                    self.current_obj = session.exec(
                        model_clz.select.where(model_clz.id == obj_id)
                    ).one_or_none()
                    if self.current_obj is not None:
                        logger.debug(f"load {obj_id}: {self.current_obj}")
                    else:
                        logging.info(f"{obj_id} is not found")
                        self.reset()

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
                session.add(self.current_obj)
                session.commit()
                session.refresh(self.current_obj)
            return pc.redirect(self.get_current_page().rpartition("/")[0])

        def delete_current_obj(self):
            if not can_access_resource(self):
                return  # no changes unless you are admin
            if self.current_obj.id is not None:
                logger.info(f"delete {self.current_obj} from db")
                with pc.session() as session:
                    session.delete(self.current_obj)
                    session.commit()
            return pc.redirect(self.get_current_page().rpartition("/")[0])

        def reset(self):
            self.current_obj = model_clz()
            return pc.redirect(self.get_current_page().rpartition("/")[0])

        def redir_to_new(self):
            return pc.redirect(self.get_current_page() + "/new")

        def refresh(self):
            self._trigger_update = time.time()

        def obj_page(self):
            if self.authenticated_user_id < 0 or not can_access_resource(self):
                return []  # no viewie
            if self.get_current_page() != "/" + utils.format.format_route(f"{prefix}/{model_clz.__name__}"):
                return []  # page/table not active
            logger.debug(f"get page: {self._trigger_update} {self.offset} {self.page_size}")
            with pc.session() as session:
                return [
                    row
                    for row in session.exec(
                        model_clz.select.order_by(model_clz.id.asc())
                        .offset(self.offset)
                        .limit(self.page_size)
                    )
                ]

        def prev_page(self):
            self.offset = self.offset - self.page_size
            if self.offset < 0:
                self.offset = 0

        def next_page(self):
            self.offset = self.offset + self.page_size

        def set_page_size(self, v: str):
            try:
                self.page_size = int(v)
            except ValueError:
                pass

        def has_next_results(self) -> bool:
            return len(self.obj_page) == self.page_size

        obj_page.__annotations__ = {"return": list[model_clz]}

        event_handlers = (
            set_subfield,
            load_current_obj,
            save_current_obj,
            delete_current_obj,
            reset,
            redir_to_new,
            refresh,
            prev_page,
            next_page,
            set_page_size,
        )
        substate_clz_name = f"CRUDSubStateFor{model_clz.__name__}"
        substate_clz = type(
            substate_clz_name,
            (CRUDState,),
            {
                "__annotations__": {
                    "current_obj": model_clz,
                    "page_size": int,
                    "offset": int,
                    "_trigger_update": float,
                },
                "current_obj": model_clz(),
                "_trigger_update": 0.0,
                "offset": 0,
                "page_size": 10,
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
        for field_name, field in model_clz.__fields__.items():
            value = getattr(SubState.current_obj, field_name)
            on_change = lambda v: getattr(SubState, "set_subfield")(
                field_name,
                v,
            )
            if field.type_ == str:
                controls.append(
                    debounce_input(
                        pc.input(
                            placeholder=field_name, value=value, on_change=on_change
                        )
                    )
                )
            elif field_name == "id":
                controls.append(
                    pc.cond(
                        value,
                        pc.input(
                            is_read_only=True,
                            value=value.to_string().to(str),
                        ),
                        pc.input(
                            is_read_only=True,
                            value="(new)",
                        ),
                    )
                )
            elif field.type_ == bool:
                controls.append(
                    pc.checkbox(
                        field_name,
                        is_checked=value,
                        on_change=on_change,
                    ),
                )
            elif field.type_ in [int, float]:
                controls.append(
                    pc.number_input(
                        input_mode="numeric",
                        value=value | 0,
                        on_change=on_change,
                    ),
                )
            else:
                controls.append(
                    pc.text(f"Unsupported field: {field_name} ({field.type_})")
                )

        if controls:
            controls.append(
                pc.hstack(
                    pc.button("Save", type_="submit"),
                    pc.button("Discard", on_click=SubState.reset),
                    pc.button("Delete", on_click=SubState.delete_current_obj),
                ),
            )
        return pc.vstack(pc.form(*controls, on_submit=SubState.save_current_obj))

    def format_cell(obj, col) -> pc.Td:
        value = getattr(obj, col)
        if value.type_ == bool:
            value = pc.cond(value, "✅", "❌")
        if col == "id":
            # the "edit" link
            value = pc.link(
                value,
                href="/"
                + utils.format.format_route(f"{prefix}/{obj.type_.__name__}/")
                + "/"
                + obj.id.to_string().to(str),
            )
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

    def enum(model_clz: t.Type[pc.Model]) -> pc.Component:
        SubState = substate_for(model_clz)
        return pc.vstack(
            pagination_controls(SubState),
            pc.table_container(
                pc.table(
                    pc.thead(
                        pc.tr(*[pc.th(col) for col in model_clz.__fields__]),
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
                                    for col in model_clz.__fields__
                                ]
                            ),
                        ),
                    ),
                )
            ),
            pagination_controls(SubState),
        )

    def make_page(model_clz: t.Type[pc.Model]) -> pc.Component:
        enum_component = enum(model_clz)

        @login_required(State=app.state)
        def page() -> pc.Component:
            return pc.center(
                pc.vstack(
                    pc.hstack(
                        pc.link("All Models", href=prefix),
                        pc.text(">"),
                        pc.heading(model_clz.__name__),
                    ),
                    enum_component,
                ),
                padding_top="5%",
            )

        return page

    def make_modal(model_clz: t.Type[pc.Model]) -> pc.Component:
        crud_component = create_update_delete(model_clz)

        @login_required(State=app.state)
        def page() -> pc.Component:
            return pc.vstack(
                crud_component,
                padding_top="5%",
            )

        return page

    def all_models() -> pc.Component:
        return pc.vstack(
            *(
                pc.link(
                    obj.__name__,
                    href=utils.format.format_route(f"{prefix}/{obj.__name__}"),
                )
                for obj in objs
            ),
            padding_top="5%",
            padding_left="10%",
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
