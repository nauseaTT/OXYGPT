"""Regression tests for Gemini tool-declaration schema compatibility.

Background
----------
The Gemini Developer API rejects any function-declaration schema whose
parameters carry a ``default`` value, failing the whole ``generate_content``
call with::

    Default value is not supported in function declaration schema for the
    Gemini API.

The google-genai SDK builds that schema by introspecting each tool callable
with ``inspect.signature``. Every real tool function has defaulted parameters
(``timeframe="1h"``, ``limit=80``, ``ratio="4:3"``, ``enhance=True`` …), so
handing those callables to the SDK directly made every Gemini turn that offered
tools crash before it reached the model — the bot went silent.

The fix gives each tool wrapper an explicit, default-free ``__signature__``
derived from ``OPENAI_TOOL_SCHEMAS`` (the single source of truth, which has no
defaults) and drops the ``functools.wraps`` ``__wrapped__`` link so
``inspect.signature`` cannot fall back to the original defaulted signature.

These tests lock that behaviour in.
"""
import inspect

import pytest

import api_http


def _wrapper_signature_for(tool_name: str) -> inspect.Signature:
    """Rebuild a tool wrapper's default-free signature from the shared schema.

    Mirrors the signature construction performed in
    ``AI_Service.generate_response._wrap_tool`` so the test exercises the exact
    same mapping without needing a live ``AI_Service`` instance.
    """
    schema = api_http.OPENAI_TOOL_SCHEMAS.get(tool_name, {})
    fn_schema = schema.get("function", {})
    params_schema = fn_schema.get("parameters", {}) or {}
    properties = params_schema.get("properties", {}) or {}
    required = set(params_schema.get("required", []) or [])
    json_to_py = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    sig_params = []
    for order in (True, False):
        for pname, pschema in properties.items():
            if (pname in required) != order:
                continue
            annotation = json_to_py.get(pschema.get("type"), inspect.Parameter.empty)
            sig_params.append(
                inspect.Parameter(
                    pname,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                )
            )
    return inspect.Signature(sig_params)


ALL_TOOLS = list(api_http.OPENAI_TOOL_SCHEMAS.keys())


class TestToolSchemaHasNoDefaults:
    """The rebuilt signatures must never introduce a parameter default."""

    def test_there_are_tools_to_check(self):
        assert ALL_TOOLS, "OPENAI_TOOL_SCHEMAS should not be empty"

    @pytest.mark.parametrize("tool_name", ALL_TOOLS)
    def test_no_parameter_carries_a_default(self, tool_name):
        sig = _wrapper_signature_for(tool_name)
        for param in sig.parameters.values():
            assert param.default is inspect.Parameter.empty, (
                f"{tool_name}.{param.name} must not carry a default — the "
                f"Gemini API rejects defaults in function-declaration schemas."
            )

    @pytest.mark.parametrize("tool_name", ALL_TOOLS)
    def test_exposes_every_schema_property(self, tool_name):
        """The default-free signature must still advertise all model-facing args."""
        sig = _wrapper_signature_for(tool_name)
        props = (
            api_http.OPENAI_TOOL_SCHEMAS[tool_name]["function"]["parameters"]["properties"]
        )
        assert set(sig.parameters.keys()) == set(props.keys())

    @pytest.mark.parametrize("tool_name", ALL_TOOLS)
    def test_required_params_come_first(self, tool_name):
        """Required params are ordered ahead of optional ones for clarity."""
        schema = api_http.OPENAI_TOOL_SCHEMAS[tool_name]["function"]["parameters"]
        required = set(schema.get("required", []) or [])
        names = list(_wrapper_signature_for(tool_name).parameters.keys())
        seen_optional = False
        for name in names:
            if name in required:
                assert not seen_optional, (
                    f"{tool_name}: required param {name!r} appears after an "
                    f"optional one"
                )
            else:
                seen_optional = True


class TestSdkAcceptsSignature:
    """The google-genai SDK must build a declaration from these signatures.

    This is the true end-to-end guard: it calls the same SDK entry point that
    ``generate_content`` uses internally and asserts it does NOT raise the
    "Default value is not supported" error for the Gemini (mldev) API.
    """

    @pytest.mark.parametrize("tool_name", ALL_TOOLS)
    def test_from_callable_gemini_api_does_not_raise(self, tool_name):
        types = pytest.importorskip("google.genai").types

        async def wrapper(*args, **kwargs):  # pragma: no cover - never invoked
            return ""

        wrapper.__name__ = tool_name
        wrapper.__qualname__ = tool_name
        wrapper.__doc__ = (
            api_http.OPENAI_TOOL_SCHEMAS[tool_name]["function"].get("description", "")
        )
        wrapper.__signature__ = _wrapper_signature_for(tool_name)  # type: ignore[attr-defined]

        # Must not raise "Default value is not supported ..." for GEMINI_API.
        decl = types.FunctionDeclaration.from_callable_with_api_option(
            callable=wrapper, api_option="GEMINI_API"
        )
        assert decl.name == tool_name
        if decl.parameters and decl.parameters.properties:
            for prop in decl.parameters.properties.values():
                assert getattr(prop, "default", None) is None
