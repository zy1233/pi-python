"""The adapters rely on private methods of LangChain classes. This file reads their signatures.

``openai_reasoning.py`` and ``deepseek_replay.py`` make subclasses of ``ChatOpenAI`` and
``ChatDeepSeek``. Both subclasses replace ``_get_request_payload``, the OpenAI one also replaces
``_convert_chunk_to_generation_chunk``, and both call ``_convert_input``. None of these is public
API. A LangChain release can rename a parameter, add one or remove a method. A test that sends a
request then fails far from the cause (a ``TypeError`` in the middle of a stream, or a payload with
no thinking in it).

These tests fail first, and the message names the method. They send no request:
``test_openai_reasoning.py`` and ``test_deepseek_replay.py`` do that. If a test here fails after a
LangChain upgrade, read the new code of the method, change the override, and only then change this
file. The upper bounds of the LangChain packages are in the root ``pyproject.toml``.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

# (module, class): the classes that the adapters subclass.
CHAT_CLASSES = [
    pytest.param(("langchain_openai", "ChatOpenAI"), id="ChatOpenAI"),
    pytest.param(("langchain_deepseek", "ChatDeepSeek"), id="ChatDeepSeek"),
]


@pytest.fixture(params=CHAT_CLASSES)
def chat_class(request: pytest.FixtureRequest) -> type:
    module_name, class_name = request.param
    module = pytest.importorskip(module_name)
    return getattr(module, class_name)


def _method(cls: type, name: str) -> Any:
    method = getattr(cls, name, None)
    assert method is not None, (
        f"{cls.__module__}.{cls.__qualname__} has no method `{name}` any more. "
        "The adapters call it or override it (pi_agent_core/adapters/)."
    )
    return method


def _accepts(cls: type, name: str, *args: Any, **kwargs: Any) -> bool:
    """Whether the method of *cls* can be called like ``instance.name(*args, **kwargs)``."""
    signature = inspect.signature(_method(cls, name))
    try:
        signature.bind(None, *args, **kwargs)  # ``None`` is ``self``
    except TypeError:
        return False
    return True


def _make(cls: type) -> Any:
    """An instance that needs no network: the key and the model name are made up."""
    return cls(model="model-for-signature-test", api_key="not-a-real-key")


def test_get_request_payload_is_called_the_way_the_overrides_call_it(chat_class: type):
    """Both subclasses call ``super()._get_request_payload(input_, stop=stop, **kwargs)``.

    LangChain passes more keyword arguments through ``**kwargs`` (``stream``, tool settings), so
    the base method must still take them.
    """
    name = "_get_request_payload"
    assert _accepts(chat_class, name, [HumanMessage("hi")], stop=["x"]), (
        f"`{name}` of {chat_class.__name__} does not take (input_, stop=...) any more"
    )
    parameters = inspect.signature(_method(chat_class, name)).parameters
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()), (
        f"`{name}` of {chat_class.__name__} has no `**kwargs` any more: the adapters pass the "
        "extra keyword arguments of LangChain on to it"
    )


def test_get_request_payload_still_returns_a_dict_with_messages(chat_class: type):
    """The overrides add ``reasoning_content`` to ``payload["messages"]``: a list of dicts."""
    payload = _make(chat_class)._get_request_payload([HumanMessage("hi")], stop=None)
    assert isinstance(payload, dict)
    assert isinstance(payload.get("messages"), list)
    assert payload["messages"], "the payload has no messages"
    assert isinstance(payload["messages"][0], dict)
    assert payload["messages"][0].get("role") == "user"


def test_convert_input_takes_one_argument_and_gives_messages(chat_class: type):
    """The overrides read the messages that LangChain got: ``_convert_input(x).to_messages()``.

    The replay matches those messages, one by one, with the dicts of ``payload["messages"]``.
    """
    assert _accepts(chat_class, "_convert_input", [HumanMessage("hi")]), (
        f"`_convert_input` of {chat_class.__name__} does not take one argument any more"
    )
    converted = _make(chat_class)._convert_input([HumanMessage("hi")])
    messages = converted.to_messages()
    assert [type(m).__name__ for m in messages] == ["HumanMessage"]


def test_convert_chunk_to_generation_chunk_takes_the_raw_chunk_and_gives_a_message_chunk():
    """``ReasoningChatOpenAI`` calls the base method, then reads the raw chunk (a dict) again.

    ``keep_streamed_reasoning(chunk, generation_chunk)`` needs two things: the raw chunk is the
    first positional argument, and the result has a ``message`` that is an ``AIMessageChunk``.
    The call below has the form that LangChain itself uses in ``_astream`` (three positional
    arguments). The override passes ``*args`` on, so it does not depend on the names.
    """
    chat_class = pytest.importorskip("langchain_openai").ChatOpenAI
    name = "_convert_chunk_to_generation_chunk"
    raw = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}}]}
    assert _accepts(chat_class, name, raw, AIMessageChunk, {}), (
        f"`{name}` of ChatOpenAI does not take (chunk, default_chunk_class, base_generation_info) "
        "any more: check that `keep_streamed_reasoning` still gets the same raw chunk"
    )
    generation_chunk = _make(chat_class)._convert_chunk_to_generation_chunk(raw, AIMessageChunk, {})
    assert isinstance(generation_chunk.message, AIMessageChunk)
    assert generation_chunk.message.content == "hello"


def test_openai_adapter_class_still_overrides_the_two_methods():
    """The class of the adapter is built on first use. It must still be a subclass."""
    from pi_agent_core.adapters.openai_reasoning import reasoning_chat_openai

    chat_openai = pytest.importorskip("langchain_openai").ChatOpenAI
    adapter_class = reasoning_chat_openai()
    assert issubclass(adapter_class, chat_openai)
    # The overrides are in the subclass, not in the LangChain class.
    assert "_get_request_payload" in vars(adapter_class)
    assert "_convert_chunk_to_generation_chunk" in vars(adapter_class)


def test_deepseek_adapter_class_still_overrides_the_method():
    from pi_agent_core.adapters.deepseek_replay import replaying_chat_deepseek

    chat_deepseek = pytest.importorskip("langchain_deepseek").ChatDeepSeek
    adapter_class = replaying_chat_deepseek()
    assert issubclass(adapter_class, chat_deepseek)
    assert "_get_request_payload" in vars(adapter_class)
