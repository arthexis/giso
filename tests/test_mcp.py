from __future__ import annotations

import inspect

import pytest

from giso import Giso, McpInspectionError
import giso.mcp as mcp_module


ENDPOINT = "https://mcp.example.test/mcp"


def response(body, *, headers=None, status=200):
    if isinstance(body, dict):
        body = mcp_module.json.dumps(body)
    return mcp_module._McpHttpResponse(
        status=status,
        headers=headers or {"Content-Type": "application/json"},
        body=body,
    )


def rpc_result(request_id, result):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def modern_discover():
    return rpc_result(
        1,
        {
            "supportedVersions": [mcp_module.MODERN_PROTOCOL_VERSION],
            "capabilities": {"tools": {"listChanged": True}},
            "_meta": {
                "io.modelcontextprotocol/serverInfo": {
                    "name": "example-mcp",
                    "version": "1.2.3",
                }
            },
        },
    )


def tool(
    name="charger.status",
    *,
    description="Read charger status",
    input_schema=None,
):
    if input_schema is None:
        input_schema = {
            "type": "object",
            "properties": {
                "charger_id": {"type": "string"},
                "verbose": {"type": "boolean", "default": False},
            },
            "required": ["charger_id"],
        }
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "annotations": {"readOnlyHint": True},
    }


class McpHttpHarness:
    def __init__(self, monkeypatch, handler):
        self.calls = []

        def post(cls, endpoint, payload, *, headers, allow_empty=False):
            self.calls.append(
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "headers": dict(headers),
                    "allow_empty": allow_empty,
                }
            )
            return handler(payload, headers, allow_empty)

        monkeypatch.setattr(Giso, "_post_mcp", classmethod(post))


def modern_harness(monkeypatch, tools=None):
    tools = [tool()] if tools is None else tools

    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": tools}))
        raise AssertionError(payload)

    return McpHttpHarness(monkeypatch, handler)


def test_mcp_modern_inspection_exposes_tools(monkeypatch):
    harness = modern_harness(monkeypatch)

    giso = Giso().mcp(ENDPOINT)

    operation = giso.charger.status
    assert operation.__doc__ == "Read charger status"
    assert operation.mcp_tool.name == "charger.status"
    assert operation.mcp_tool.path == "charger.status"
    assert operation.mcp_tool.annotations == {"readOnlyHint": True}
    assert giso.mcp_server.protocol_version == mcp_module.MODERN_PROTOCOL_VERSION
    assert giso.mcp_server.era == "modern"
    assert giso.mcp_server.server_info["name"] == "example-mcp"

    discover_call, tools_call = harness.calls
    assert discover_call["headers"]["Mcp-Method"] == "server/discover"
    assert tools_call["headers"]["Mcp-Method"] == "tools/list"
    assert tools_call["headers"]["MCP-Protocol-Version"] == mcp_module.MODERN_PROTOCOL_VERSION


def test_mcp_string_form_routes_to_resolver(monkeypatch):
    modern_harness(monkeypatch)

    giso = Giso(f"mcp:{ENDPOINT}")

    assert giso.charger.status.mcp_tool.name == "charger.status"


def test_mcp_tool_signature_comes_from_json_schema(monkeypatch):
    modern_harness(monkeypatch)

    operation = Giso().mcp(ENDPOINT).charger.status
    signature = inspect.signature(operation)

    assert tuple(signature.parameters) == ("charger_id", "verbose")
    assert signature.parameters["charger_id"].default is inspect.Parameter.empty
    assert signature.parameters["charger_id"].annotation is str
    assert signature.parameters["verbose"].default is False
    assert signature.parameters["verbose"].annotation is bool


def test_mcp_inspection_never_calls_tool(monkeypatch):
    modern_harness(monkeypatch)
    giso = Giso().mcp(ENDPOINT)

    with pytest.raises(McpInspectionError, match="inspection-only"):
        giso.charger.status(charger_id="cp-1")

    assert not giso.results.history


def test_mcp_records_provenance_without_private_headers(monkeypatch):
    modern_harness(monkeypatch)

    giso = Giso().mcp(
        ENDPOINT,
        headers={"Authorization": "Bearer secret"},
    )

    assert giso.provenance == [
        {
            "type": "mcp",
            "source": f"mcp:{ENDPOINT}",
            "endpoint": ENDPOINT,
            "protocol_version": mcp_module.MODERN_PROTOCOL_VERSION,
            "era": "modern",
            "tools": "charger.status",
        }
    ]
    assert "secret" not in repr(giso.provenance)


def test_mcp_modern_tools_list_is_paginated(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        cursor = payload["params"].get("cursor")
        if cursor is None:
            return response(
                rpc_result(
                    payload["id"],
                    {
                        "tools": [tool("charger.status")],
                        "nextCursor": "page-2",
                    },
                )
            )
        assert cursor == "page-2"
        return response(
            rpc_result(
                payload["id"],
                {"tools": [tool("logs.search")]},
            )
        )

    harness = McpHttpHarness(monkeypatch, handler)

    giso = Giso().mcp(ENDPOINT)

    assert giso.charger.status.mcp_tool.name == "charger.status"
    assert giso.logs.search.mcp_tool.name == "logs.search"
    assert [call["payload"]["method"] for call in harness.calls] == [
        "server/discover",
        "tools/list",
        "tools/list",
    ]


def test_mcp_falls_back_to_legacy_initialize(monkeypatch):
    def handler(payload, headers, allow_empty):
        method = payload["method"]
        if method == "server/discover":
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        if method == "initialize":
            return response(
                rpc_result(
                    2,
                    {
                        "protocolVersion": "2025-11-25",
                        "serverInfo": {"name": "legacy-mcp", "version": "0.9"},
                        "capabilities": {"tools": {}},
                    },
                ),
                headers={
                    "Content-Type": "application/json",
                    "MCP-Session-Id": "session-123",
                },
            )
        if method == "notifications/initialized":
            assert allow_empty is True
            assert headers["MCP-Session-Id"] == "session-123"
            return response("", status=202)
        if method == "tools/list":
            assert headers["MCP-Session-Id"] == "session-123"
            assert headers["MCP-Protocol-Version"] == "2025-11-25"
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        raise AssertionError(payload)

    harness = McpHttpHarness(monkeypatch, handler)

    giso = Giso().mcp(ENDPOINT)

    assert giso.mcp_server.era == "legacy"
    assert giso.mcp_server.protocol_version == "2025-11-25"
    assert giso.mcp_server.server_info["name"] == "legacy-mcp"
    assert [call["payload"]["method"] for call in harness.calls] == [
        "server/discover",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_mcp_decodes_sse_json_rpc_response():
    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
    )
    message = Giso._decode_mcp_response(
        response(
            body,
            headers={"Content-Type": "text/event-stream"},
        )
    )

    assert message["result"] == {"tools": []}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("logs.search", "logs.search"),
        ("charger-reset", "charger_reset"),
        ("2fa.status", "tool_2fa.status"),
    ],
)
def test_mcp_tool_names_map_to_python_paths(name, expected):
    assert Giso._mcp_tool_path(name) == expected


def test_mcp_rejects_ambiguous_normalized_tool_paths(monkeypatch):
    modern_harness(
        monkeypatch,
        tools=[
            tool("charger-reset"),
            tool("charger_reset"),
        ],
    )
    giso = Giso()

    with pytest.raises(McpInspectionError, match="same Giso path"):
        giso.mcp(ENDPOINT)

    assert not giso.operations
    assert not giso.provenance


def test_mcp_rejects_malformed_tool_atomically(monkeypatch):
    modern_harness(
        monkeypatch,
        tools=[
            tool("charger.status"),
            {"name": "broken", "inputSchema": "not-a-schema"},
        ],
    )
    giso = Giso()

    with pytest.raises(McpInspectionError, match="inputSchema"):
        giso.mcp(ENDPOINT)

    assert not giso.operations
    assert not giso.provenance


def test_mcp_rejects_server_with_no_tools(monkeypatch):
    modern_harness(monkeypatch, tools=[])

    with pytest.raises(McpInspectionError, match="exposes no tools"):
        Giso().mcp(ENDPOINT)


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://example.test/mcp",
        "ftp://example.test/mcp",
    ],
)
def test_mcp_endpoint_is_validated(endpoint):
    error = TypeError if endpoint == "" else ValueError
    with pytest.raises(error):
        Giso._validate_mcp_endpoint(endpoint)


def test_mcp_allows_loopback_http():
    assert (
        Giso._validate_mcp_endpoint("http://127.0.0.1:8000/mcp")
        == "http://127.0.0.1:8000/mcp"
    )


def test_mcp_named_constructor_branch(monkeypatch):
    modern_harness(monkeypatch)

    giso = Giso(remote=f"mcp:{ENDPOINT}")

    assert giso.remote.charger.status.mcp_tool.name == "charger.status"
    assert giso.provenance[0]["source"] == f"mcp:{ENDPOINT}"


def test_mcp_prepare_returns_validated_request(monkeypatch):
    modern_harness(monkeypatch)
    giso = Giso().mcp(
        ENDPOINT,
        headers={"Authorization": "Bearer secret"},
    )

    request = giso.charger.status.prepare(
        charger_id="cp-1",
        verbose=True,
    )

    assert request.name == "charger.status"
    assert request.arguments == {
        "charger_id": "cp-1",
        "verbose": True,
    }
    assert request.endpoint == ENDPOINT
    assert request.protocol_version == mcp_module.MODERN_PROTOCOL_VERSION
    assert request.era == "modern"
    assert request.session_id is None
    assert request.headers == {"Authorization": "Bearer secret"}
    assert not giso.results.history


def test_mcp_prepare_does_not_inject_json_schema_defaults(monkeypatch):
    modern_harness(monkeypatch)

    request = Giso().mcp(ENDPOINT).charger.status.prepare(charger_id="cp-1")

    assert request.arguments == {"charger_id": "cp-1"}


def test_mcp_prepare_preserves_legacy_session_context(monkeypatch):
    def handler(payload, headers, allow_empty):
        method = payload["method"]
        if method == "server/discover":
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
        if method == "initialize":
            return response(
                rpc_result(
                    2,
                    {
                        "protocolVersion": "2025-11-25",
                        "serverInfo": {"name": "legacy-mcp"},
                        "capabilities": {"tools": {}},
                    },
                ),
                headers={
                    "Content-Type": "application/json",
                    "MCP-Session-Id": "session-123",
                },
            )
        if method == "notifications/initialized":
            return response("", status=202)
        if method == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)

    request = Giso().mcp(ENDPOINT).charger.status.prepare(charger_id="cp-1")

    assert request.era == "legacy"
    assert request.protocol_version == "2025-11-25"
    assert request.session_id == "session-123"


def test_mcp_prepare_rejects_missing_required_argument(monkeypatch):
    modern_harness(monkeypatch)

    with pytest.raises(McpInspectionError, match="missing required"):
        Giso().mcp(ENDPOINT).charger.status.prepare()


def test_mcp_prepare_respects_additional_properties_false(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "charger_id": {"type": "string"},
        },
        "required": ["charger_id"],
        "additionalProperties": False,
    }
    modern_harness(
        monkeypatch,
        tools=[tool(input_schema=schema)],
    )

    operation = Giso().mcp(ENDPOINT).charger.status

    with pytest.raises(McpInspectionError, match="no allowed field"):
        operation.prepare(charger_id="cp-1", unknown=True)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (3, "expects string"),
        (True, "expects string"),
    ],
)
def test_mcp_prepare_validates_primitive_types(monkeypatch, value, message):
    modern_harness(monkeypatch)

    with pytest.raises(McpInspectionError, match=message):
        Giso().mcp(ENDPOINT).charger.status.prepare(charger_id=value)


def test_mcp_prepare_validates_enum(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["ready", "faulted"],
            }
        },
        "required": ["state"],
    }
    modern_harness(monkeypatch, tools=[tool(input_schema=schema)])

    with pytest.raises(McpInspectionError, match="must be one of"):
        Giso().mcp(ENDPOINT).charger.status.prepare(state="unknown")


def test_mcp_prepare_validates_nested_objects_and_arrays(monkeypatch):
    schema = {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["id", "enabled"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["targets"],
    }
    modern_harness(monkeypatch, tools=[tool(input_schema=schema)])
    operation = Giso().mcp(ENDPOINT).charger.status

    request = operation.prepare(
        targets=[{"id": "cp-1", "enabled": True}],
    )
    assert request.arguments == {
        "targets": [{"id": "cp-1", "enabled": True}],
    }

    with pytest.raises(McpInspectionError, match="expects boolean"):
        operation.prepare(
            targets=[{"id": "cp-1", "enabled": "yes"}],
        )


def test_mcp_prepare_works_under_named_constructor_branch(monkeypatch):
    modern_harness(monkeypatch)
    giso = Giso(remote=f"mcp:{ENDPOINT}")

    request = giso.remote.charger.status.prepare(charger_id="cp-1")

    assert request.name == "charger.status"
    assert request.arguments == {"charger_id": "cp-1"}
    assert not giso.results.history


def test_mcp_execute_calls_modern_tool(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            assert headers["Mcp-Method"] == "tools/call"
            assert headers["Mcp-Name"] == "charger.status"
            assert headers["MCP-Protocol-Version"] == mcp_module.MODERN_PROTOCOL_VERSION
            assert payload["params"]["name"] == "charger.status"
            assert payload["params"]["arguments"] == {"charger_id": "cp-1"}
            return response(
                rpc_result(
                    payload["id"],
                    {"content": [{"type": "text", "text": "{\"status\":\"ready\"}"}]},
                )
            )
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    result = giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert result == {"status": "ready"}
    assert giso.results.last == result
    assert giso.results.history[-1][0] == "mcp.charger.status"


def test_mcp_execute_calls_legacy_tool_with_session(monkeypatch):
    def handler(payload, headers, allow_empty):
        method = payload["method"]
        if method == "server/discover":
            return response({
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            })
        if method == "initialize":
            return response(
                rpc_result(2, {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "legacy-mcp"},
                    "capabilities": {"tools": {}},
                }),
                headers={
                    "Content-Type": "application/json",
                    "MCP-Session-Id": "session-123",
                },
            )
        if method == "notifications/initialized":
            return response("", status=202)
        if method == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if method == "tools/call":
            assert headers["MCP-Session-Id"] == "session-123"
            assert headers["MCP-Protocol-Version"] == "2025-11-25"
            assert "Mcp-Method" not in headers
            assert "Mcp-Name" not in headers
            return response(rpc_result(
                payload["id"],
                {"content": [{"type": "text", "text": "ready"}]},
            ))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    result = giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert result == "ready"


def test_mcp_execute_prefers_structured_content(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            return response(rpc_result(payload["id"], {
                "structuredContent": {"status": "ready"},
                "content": [{"type": "text", "text": "ignored"}],
            }))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    result = giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert result == {"status": "ready"}


def test_mcp_execute_preserves_multiple_and_non_text_content(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            return response(rpc_result(payload["id"], {
                "content": [
                    {"type": "text", "text": "ready"},
                    {"type": "image", "data": "abc", "mimeType": "image/png"},
                ]
            }))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    result = giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert result == [
        "ready",
        {"type": "image", "data": "abc", "mimeType": "image/png"},
    ]


def test_mcp_execute_surfaces_rpc_error(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            return response({
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32000, "message": "tool rejected"},
            })
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    with pytest.raises(mcp_module.McpExecutionError, match="tool rejected") as captured:
        giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert captured.value.result == {"code": -32000, "message": "tool rejected"}
    assert not giso.results.history


def test_mcp_execute_surfaces_tool_error(monkeypatch):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            return response(rpc_result(payload["id"], {
                "isError": True,
                "content": [{"type": "text", "text": "charger unavailable"}],
            }))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    with pytest.raises(mcp_module.McpExecutionError, match="charger unavailable") as captured:
        giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert captured.value.result["isError"] is True
    assert not giso.results.history


@pytest.mark.parametrize("result_type", ["task", "input_required"])
def test_mcp_execute_rejects_unimplemented_continuations(monkeypatch, result_type):
    def handler(payload, headers, allow_empty):
        if payload["method"] == "server/discover":
            return response(modern_discover())
        if payload["method"] == "tools/list":
            return response(rpc_result(payload["id"], {"tools": [tool()]}))
        if payload["method"] == "tools/call":
            return response(rpc_result(payload["id"], {
                "resultType": result_type,
                "content": [],
            }))
        raise AssertionError(payload)

    McpHttpHarness(monkeypatch, handler)
    giso = Giso().mcp(ENDPOINT)

    with pytest.raises(mcp_module.McpExecutionError, match="unsupported resultType") as captured:
        giso.execute(giso.charger.status.prepare(charger_id="cp-1"))

    assert captured.value.result["resultType"] == result_type


def test_mcp_direct_call_remains_blocked(monkeypatch):
    modern_harness(monkeypatch)
    giso = Giso().mcp(ENDPOINT)

    with pytest.raises(McpInspectionError, match="inspection-only"):
        giso.charger.status(charger_id="cp-1")
