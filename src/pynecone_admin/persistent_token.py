"""Use localStorage to persist an identifier in the browser for cross-tab sharing."""

import typing as t

import pynecone as pc
from pynecone import utils
from pynecone.compiler.templates import PyneconeJinjaEnvironment
from pynecone.vars import Var


_EFFECT_TEMPLATE = """
useEffect(() => {
    const TOKEN_KEY = "persistent_token"
    if (typeof window !== "undefined") {
        if (!window.localStorage.getItem(TOKEN_KEY)) {
            window.localStorage.setItem(TOKEN_KEY, getToken());
        }
        {{on_change_trigger}}
    }
}, [])"""
PERSISTENT_TOKEN_EFFECT = PyneconeJinjaEnvironment().from_string(_EFFECT_TEMPLATE)
PERSISTENT_TOKEN_ON_CHANGE = Var.create("window.localStorage.getItem(TOKEN_KEY)")


class PersistentToken(pc.Component):
    """Component triggers on_change after loading persistent_token from localStorage."""

    library = "/utils/state"
    tag = "getToken"

    def render(self) -> str:
        """This component has no visual element, it only defines a hook"""
        return ""

    def _get_hooks(self) -> t.Optional[str]:
        chain = ",".join(
            [
                utils.format.format_event(event)
                for event in self.event_triggers["on_change"].events
            ]
        )
        return PERSISTENT_TOKEN_EFFECT.render(on_change_trigger=f"Event([{chain}])")

    @classmethod
    def get_controlled_triggers(cls) -> t.Dict[str, Var]:
        return {"on_change": PERSISTENT_TOKEN_ON_CHANGE}
