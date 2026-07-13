from __future__ import annotations

import sys
import types

import pytest


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _AstrBotConfig(dict):
    def save_config(self):
        pass


class _Context:
    pass


class _Star:
    def __init__(self, context):
        self.context = context
        if not hasattr(context, "_plugin_kv"):
            context._plugin_kv = {}

    async def get_kv_data(self, key, default=None):
        return self.context._plugin_kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.context._plugin_kv[key] = value

    async def delete_kv_data(self, key):
        self.context._plugin_kv.pop(key, None)


class _AstrMessageEvent:
    pass


class _MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def __iter__(self):
        return iter(self.chain)

    def __len__(self):
        return len(self.chain)

    def __getitem__(self, index):
        return self.chain[index]


class _Plain:
    def __init__(self, text=""):
        self.text = text


class _MediaComponent:
    def __init__(self, file="", url="", path=""):
        self.file = file
        self.url = url
        self.path = path

    @classmethod
    def fromURL(cls, url):
        return cls(file=url, url=url)

    @classmethod
    def fromFileSystem(cls, path):
        return cls(file=path, path=path)


class _Image(_MediaComponent):
    pass


class _Video(_MediaComponent):
    pass


def _decorator(*args, **kwargs):
    def apply(func):
        return func

    return apply


def _command_group(*args, **kwargs):
    def apply(func):
        func.command = _decorator
        return func

    return apply


class _EventMessageType:
    ALL = "all"


class _Filter:
    EventMessageType = _EventMessageType
    command = staticmethod(_decorator)
    command_group = staticmethod(_command_group)
    event_message_type = staticmethod(_decorator)
    llm_tool = staticmethod(_decorator)


def _register(*args, **kwargs):
    return _decorator(*args, **kwargs)


def _install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    components = types.ModuleType("astrbot.api.message_components")

    api.logger = _Logger()
    api.AstrBotConfig = _AstrBotConfig
    event.filter = _Filter()
    event.AstrMessageEvent = _AstrMessageEvent
    event.MessageChain = _MessageChain
    star.Context = _Context
    star.Star = _Star
    star.register = _register
    components.Plain = _Plain
    components.Image = _Image
    components.Video = _Video

    astrbot.api = api
    api.event = event
    api.star = star
    api.message_components = components

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.api.event", event)
    sys.modules.setdefault("astrbot.api.star", star)
    sys.modules.setdefault("astrbot.api.message_components", components)


_install_astrbot_stubs()


@pytest.fixture
def plugin_factory():
    import main
    from tests.fakes.runtime import FakeContext, FakeEvent, plugin_config

    def create(mode="legacy"):
        config = plugin_config()
        config["compatibility"] = {"mode": mode}
        return main.ImageGenPlugin(FakeContext(), config), FakeEvent()

    return create
