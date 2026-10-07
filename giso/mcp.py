from __future__ import annotations

import base64
import inspect
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .ansible import Giso as AnsibleGiso


MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"


class McpInspectionError(RuntimeError):
    """Raised when an MCP server cannot be inspected safely."""


class McpExecutionError(RuntimeError):
    """Raised when a prepared MCP tool request cannot execute successfully."""

    def __init__(self, message: str, *, result: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class McpPromptArgument:
    """One advertised MCP prompt argument."""

    name: str
    description: str
    required: bool


@dataclass(frozen=True)
class McpPromptSpec:
    """Read-only metadata for one MCP prompt."""

    name: str
    path: str
    title: str
    description: str
    arguments: tuple[McpPromptArgument, ...]
    icons: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class McpPromptMessage:
    """One structured message returned by prompts/get."""

    role: str
    content: Mapping[str, Any]


@dataclass(frozen=True)
class McpPromptResult:
    """Resolved prompt content returned by prompts/get."""

    description: str
    messages: tuple[McpPromptMessage, ...]


@dataclass(frozen=True)
class McpResourceSpec:
    """Read-only metadata for one MCP resource."""

    uri: str
    name: str
    path: str
    title: str
    description: str
    mime_type: str | None
    annotations: Mapping[str, Any]


@dataclass(frozen=True)
class McpResourceContent:
    """One decoded MCP resource content item."""

    uri: str
    mime_type: str | None
    value: Any
    annotations: Mapping[str, Any]


@dataclass(frozen=True)
class McpToolSpec:
    """Read-only metadata for one MCP tool."""

    name: str
    path: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    annotations: Mapping[str, Any]


@dataclass(frozen=True)
class McpToolRequest:
    """A validated MCP tool request."""

    name: str
    path: str
    arguments: Mapping[str, Any]
    endpoint: str
    protocol_version: str
    era: str
    session_id: str | None
    headers: Mapping[str, str]
    input_responses: Mapping[str, Any] | None = None
    request_state: str | None = None


@dataclass(frozen=True)
class McpInputRequired:
    """A manual multi-round-trip continuation for a tool call."""

    request: McpToolRequest
    input_requests: Mapping[str, Any]
    request_state: str | None

    def respond(self, responses: Mapping[str, Any]) -> McpToolRequest:
        return McpToolRequest(
            name=self.request.name,
            path=self.request.path,
            arguments=dict(self.request.arguments),
            endpoint=self.request.endpoint,
            protocol_version=self.request.protocol_version,
            era=self.request.era,
            session_id=self.request.session_id,
            headers=dict(self.request.headers),
            input_responses=dict(responses),
            request_state=self.request_state,
        )


@dataclass(frozen=True)
class McpTask:
    """A resumable MCP task returned by tools/call."""

    request: McpToolRequest
    task_id: str
    status: str
    poll_interval_ms: int | None = None
    ttl_ms: int | None = None


@dataclass(frozen=True)
class McpTaskInputRequired:
    """Input required while an MCP task is running."""

    task: McpTask
    input_requests: Mapping[str, Any]

    def respond(self, responses: Mapping[str, Any]) -> "McpTaskUpdate":
        return McpTaskUpdate(self.task, dict(responses))


@dataclass(frozen=True)
class McpTaskUpdate:
    """Responses supplied to an input-required MCP task."""

    task: McpTask
    input_responses: Mapping[str, Any]


@dataclass(frozen=True)
class McpServerSpec:
    """Negotiated metadata for one inspected MCP server."""

    endpoint: str
    protocol_version: str
    era: str
    server_info: Mapping[str, Any]
    capabilities: Mapping[str, Any]


@dataclass(frozen=True)
class _McpHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: str


@dataclass(frozen=True)
class _McpSession:
    protocol_version: str
    era: str
    session_id: str | None
    server_info: Mapping[str, Any]
    capabilities: Mapping[str, Any]


class Giso(AnsibleGiso):
    """A Giso that can inspect tools exposed by Streamable HTTP MCP servers."""

    def fold(self, *sources: Any) -> "Giso":
        for source in self._flatten(sources):
            if isinstance(source, str) and source.startswith("mcp:"):
                self.mcp(source[len("mcp:") :])
            else:
                super().fold(source)
        return self

    def mcp(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> "Giso":
        """Inspect one MCP server and fold its tools without calling them."""
        endpoint = self._validate_mcp_endpoint(endpoint)
        private_headers = self._validate_mcp_headers(headers)
        session = self._open_mcp_session(endpoint, private_headers)
        tools = self._list_mcp_tools(endpoint, private_headers, session)
        resources = self._list_mcp_resources(endpoint, private_headers, session)
        prompts = self._list_mcp_prompts(endpoint, private_headers, session)
        if not tools and not resources and not prompts:
            raise McpInspectionError(
                f"MCP server {endpoint!r} exposes no tools, resources, or prompts"
            )

        child = type(self)(name=self.__name__)
        seen_paths: dict[str, str] = {}
        for payload in tools:
            spec = self._mcp_tool_spec(payload)
            previous = seen_paths.get(spec.path)
            if previous is not None and previous != spec.name:
                raise McpInspectionError(
                    f"MCP tools {previous!r} and {spec.name!r} map to the same "
                    f"Giso path {spec.path!r}"
                )
            seen_paths[spec.path] = spec.name
            child._attach_operation(
                spec.path,
                self._mcp_inspection_callable(
                    spec,
                    endpoint=endpoint,
                    headers=private_headers,
                    session=session,
                ),
            )

        seen_resource_paths: dict[str, str] = {}
        for payload in resources:
            spec = self._mcp_resource_spec(payload)
            previous = seen_resource_paths.get(spec.path)
            if previous is not None and previous != spec.uri:
                raise McpInspectionError(
                    f"MCP resources {previous!r} and {spec.uri!r} map to the same "
                    f"Giso path {spec.path!r}"
                )
            seen_resource_paths[spec.path] = spec.uri
            child._attach_operation(
                f"resources.{spec.path}.read",
                self._mcp_resource_reader(
                    spec,
                    endpoint=endpoint,
                    headers=private_headers,
                    session=session,
                ),
            )


        seen_prompt_paths: dict[str, str] = {}
        for payload in prompts:
            spec = self._mcp_prompt_spec(payload)
            previous = seen_prompt_paths.get(spec.path)
            if previous is not None && previous != spec.name:
                raise McpInspectionError(
                    f"MCP prompts {previous!r} and {spec.name!r} map to the same "
                    f"Giso path {spec.path!r}"
                )
            seen_prompt_paths[spec.path] = spec.name
            child._attach_operation(
                f"prompts.{spec.path}",
                self._mcp_prompt_callable(
                    spec,
                    endpoint=endpoint,
                    headers=private_headers,
                    session=session,
                ),
            )

        server_spec = McpServerSpec(
            endpoint=endpoint,
            protocol_version=session.protocol_version,
            era=session.era,
            server_info=dict(session.server_info),
            capabilities=dict(session.capabilities),
        )
        child.mcp_server = server_spec
        provenance = {
            "type": "mcp",
            "source": f"mcp:{endpoint}",
            "endpoint": endpoint,
            "protocol_version": session.protocol_version,
            "era": session.era,
            "tools": ",".join(spec_name for spec_name in sorted(seen_paths.values())),
        }
        if seen_resource_paths:
            provenance["resources"] = ",".join(
                resource_uri for resource_uri in sorted(seen_resource_paths.values())
            )
        if seen_prompt_paths:
            provenance["prompts"] = ",".join(
                prompt_name for prompt_name in sorted(seen_prompt_paths.values())
            )
        if provenance not in child.provenance:
            child.provenance.append(provenance)
        self.fold(child)
        self.mcp_server = server_spec
        return self

    def execute(self, request: Any) -> Any:
        """Execute or continue MCP requests, otherwise delegate to parent request types."""
        if isinstance(request, McpToolRequest):
            result = self._call_mcp_tool(request)
            return self._handle_mcp_tool_result(request, result)
        if isinstance(request, McpTask):
            return self._poll_mcp_task(request)
        if isinstance(request, McpTaskUpdate):
            return self._update_mcp_task(request)
        return super().execute(request)

    @classmethod
    def _call_mcp_tool(cls, request: McpToolRequest) -> Mapping[str, Any]:
        params = {
            "name": request.name,
            "arguments": dict(request.arguments),
        }
        if request.input_responses is not None:
            params["inputResponses"] = dict(request.input_responses)
        if request.request_state is not None:
            params["requestState"] = request.request_state
        if request.era == "modern":
            payload = cls._modern_request_payload(
                "tools/call",
                request_id=100,
                params=params,
            )
            headers = {
                **request.headers,
                "MCP-Protocol-Version": request.protocol_version,
                "Mcp-Method": "tools/call",
                "Mcp-Name": request.name,
            }
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": params,
            }
            headers = {
                **request.headers,
                "MCP-Protocol-Version": request.protocol_version,
            }
            if request.session_id:
                headers["MCP-Session-Id"] = request.session_id

        response = cls._post_mcp(
            request.endpoint,
            payload,
            headers=headers,
        )
        message = cls._decode_mcp_response(response)
        error = message.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                cls._rpc_error_message("tools/call", error),
                result=error,
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpExecutionError("MCP tools/call returned no result")

        if result.get("isError") is True:
            raise McpExecutionError(
                cls._mcp_tool_error_message(request.name, result),
                result=result,
            )
        return result

    def _handle_mcp_tool_result(
        self,
        request: McpToolRequest,
        result: Mapping[str, Any],
    ) -> Any:
        result_type = result.get("resultType")
        if result_type == "input_required":
            input_requests = result.get("inputRequests")
            if not isinstance(input_requests, dict):
                input_requests = {}
            request_state = result.get("requestState")
            if request_state is not None and not isinstance(request_state, str):
                raise McpExecutionError(
                    "MCP input_required returned invalid requestState",
                    result=result,
                )
            return McpInputRequired(
                request=request,
                input_requests=dict(input_requests),
                request_state=request_state,
            )
        if result_type == "task":
            return self._mcp_task_from_result(request, result)

        value = self._mcp_result_value(result)
        return self.results.add(f"mcp.{request.path}", value)

    @classmethod
    def _mcp_task_from_result(
        cls,
        request: McpToolRequest,
        result: Mapping[str, Any],
    ) -> McpTask:
        task_id = result.get("taskId")
        status = result.get("status")
        if not isinstance(task_id, str) or not task_id:
            raise McpExecutionError("MCP task result has no valid taskId", result=result)
        if not isinstance(status, str) or not status:
            raise McpExecutionError("MCP task result has no valid status", result=result)
        poll_interval = result.get("pollIntervalMs")
        ttl = result.get("ttlMs")
        return McpTask(
            request=request,
            task_id=task_id,
            status=status,
            poll_interval_ms=poll_interval if isinstance(poll_interval, int) else None,
            ttl_ms=ttl if isinstance(ttl, int) else None,
        )

    def _poll_mcp_task(self, task: McpTask) -> Any:
        result = self._mcp_task_request(
            task,
            "tasks/get",
            {"taskId": task.task_id},
        )
        status = result.get("status")
        if status == "working":
            return self._mcp_task_from_result(task.request, result)
        if status == "input_required":
            updated = self._mcp_task_from_result(task.request, result)
            input_requests = result.get("inputRequests")
            if not isinstance(input_requests, dict):
                input_requests = {}
            return McpTaskInputRequired(updated, dict(input_requests))
        if status == "completed":
            final = result.get("result")
            if not isinstance(final, dict):
                raise McpExecutionError("Completed MCP task returned no result", result=result)
            if final.get("isError") is True:
                raise McpExecutionError(
                    self._mcp_tool_error_message(task.request.name, final),
                    result=final,
                )
            value = self._mcp_result_value(final)
            return self.results.add(f"mcp.{task.request.path}", value)
        if status in {"failed", "cancelled"}:
            raise McpExecutionError(
                f"MCP task {task.task_id!r} ended with status {status!r}",
                result=result,
            )
        raise McpExecutionError(
            f"MCP task {task.task_id!r} returned unknown status {status!r}",
            result=result,
        )

    def _update_mcp_task(self, update: McpTaskUpdate) -> McpTask:
        self._mcp_task_request(
            update.task,
            "tasks/update",
            {
                "taskId": update.task.task_id,
                "inputResponses": dict(update.input_responses),
            },
        )
        return update.task

    @classmethod
    def _mcp_task_request(
        cls,
        task: McpTask,
        method: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request = task.request
        if request.era != "modern":
            raise McpExecutionError("MCP task continuation requires the modern protocol")

        payload = cls._modern_request_payload(
            method,
            request_id=200,
            params=params,
        )
        response = cls._post_mcp(
            request.endpoint,
            payload,
            headers={
                **request.headers,
                "MCP-Protocol-Version": request.protocol_version,
                "Mcp-Method": method,
                "Mcp-Name": task.task_id,
            },
        )
        message = cls._decode_mcp_response(response)
        error = message.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                cls._rpc_error_message(method, error),
                result=error,
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpExecutionError(f"MCP {method} returned no result")
        return result

    @classmethod
    def _mcp_result_value(cls, result: Mapping[str, Any]) -> Any:
        if "structuredContent" in result:
            return result["structuredContent"]

        content = result.get("content")
        if content is None:
            return None
        if not isinstance(content, list):
            return content
        decoded = [cls._mcp_content_value(item) for item in content]
        if not decoded:
            return None
        if len(decoded) == 1:
            return decoded[0]
        return decoded

    @staticmethod
    def _mcp_content_value(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        if item.get("type") != "text":
            return dict(item)
        text = item.get("text")
        if not isinstance(text, str):
            return dict(item)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    @staticmethod
    def _mcp_tool_error_message(name: str, result: Mapping[str, Any]) -> str:
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        return f"MCP tool {name!r} failed: {text}"
        return f"MCP tool {name!r} failed"

    @classmethod
    def _open_mcp_session(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
    ) -> _McpSession:
        modern = cls._probe_modern_mcp(endpoint, headers)
        if modern is not None:
            return modern
        return cls._initialize_legacy_mcp(endpoint, headers)

    @classmethod
    def _probe_modern_mcp(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
    ) -> _McpSession | None:
        payload = cls._modern_request_payload("server/discover", request_id=1)
        response = cls._post_mcp(
            endpoint,
            payload,
            headers={
                **headers,
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "server/discover",
            },
        )
        message = cls._decode_mcp_response(response)
        error = message.get("error")
        if isinstance(error, dict):
            if error.get("code") == -32601:
                return None
            raise McpInspectionError(cls._rpc_error_message("server/discover", error))

        result = message.get("result")
        if not isinstance(result, dict):
            raise McpInspectionError("MCP server/discover returned no result")
        supported = result.get("supportedVersions")
        if not isinstance(supported, list) or MODERN_PROTOCOL_VERSION not in supported:
            return None

        server_info = cls._server_info_from_result(message, result)
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        return _McpSession(
            protocol_version=MODERN_PROTOCOL_VERSION,
            era="modern",
            session_id=None,
            server_info=server_info,
            capabilities=capabilities,
        )

    @classmethod
    def _initialize_legacy_mcp(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
    ) -> _McpSession:
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "giso", "version": "0.1.0"},
            },
        }
        response = cls._post_mcp(endpoint, payload, headers=headers)
        message = cls._decode_mcp_response(response)
        cls._raise_rpc_error("initialize", message)

        result = message.get("result")
        if not isinstance(result, dict):
            raise McpInspectionError("MCP initialize returned no result")
        protocol_version = result.get("protocolVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise McpInspectionError("MCP initialize returned no protocol version")

        session_id = cls._header_value(response.headers, "MCP-Session-Id")
        request_headers = {
            **headers,
            "MCP-Protocol-Version": protocol_version,
        }
        if session_id:
            request_headers["MCP-Session-Id"] = session_id
        initialized = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        cls._post_mcp(
            endpoint,
            initialized,
            headers=request_headers,
            allow_empty=True,
        )

        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict):
            server_info = {}
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = {}
        return _McpSession(
            protocol_version=protocol_version,
            era="legacy",
            session_id=session_id,
            server_info=server_info,
            capabilities=capabilities,
        )

    @classmethod
    def _list_mcp_tools(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ) -> tuple[Mapping[str, Any], ...]:
        tools: list[Mapping[str, Any]] = []
        cursor: str | None = None
        request_id = 10

        while True:
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            if session.era == "modern":
                payload = cls._modern_request_payload(
                    "tools/list",
                    request_id=request_id,
                    params=params,
                )
                request_headers = {
                    **headers,
                    "MCP-Protocol-Version": session.protocol_version,
                    "Mcp-Method": "tools/list",
                }
            else:
                payload = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                }
                request_headers = {
                    **headers,
                    "MCP-Protocol-Version": session.protocol_version,
                }
                if session.session_id:
                    request_headers["MCP-Session-Id"] = session.session_id

            response = cls._post_mcp(endpoint, payload, headers=request_headers)
            message = cls._decode_mcp_response(response)
            cls._raise_rpc_error("tools/list", message)
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpInspectionError("MCP tools/list returned no result")
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpInspectionError("MCP tools/list returned invalid tools")
            for tool in page:
                if not isinstance(tool, dict):
                    raise McpInspectionError("MCP tools/list returned an invalid tool")
                tools.append(tool)

            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise McpInspectionError("MCP tools/list returned an invalid nextCursor")
            cursor = next_cursor
            request_id += 1

        return tuple(tools)

    @classmethod
    def _list_mcp_prompts(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ) -> tuple[Mapping[str, Any], ...]:
        if "prompts" not in session.capabilities:
            return ()

        prompts: list[Mapping[str, Any]] = []
        cursor: str | None = None
        request_id = 60
        while True:
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            payload, request_headers = cls._mcp_request(
                session,
                headers,
                "prompts/list",
                request_id=request_id,
                params=params,
            )
            response = cls._post_mcp(endpoint, payload, headers=request_headers)
            message = cls._decode_mcp_response(response)
            cls._raise_rpc_error("prompts/list", message)
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpInspectionError("MCP prompts/list returned no result")
            page = result.get("prompts")
            if not isinstance(page, list):
                raise McpInspectionError("MCP prompts/list returned invalid prompts")
            for prompt in page:
                if not isinstance(prompt, dict):
                    raise McpInspectionError("MCP prompts/list returned an invalid prompt")
                prompts.append(prompt)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise McpInspectionError("MCP prompts/list returned an invalid nextCursor")
            cursor = next_cursor
            request_id += 1
        return tuple(prompts)

    @classmethod
    def _mcp_prompt_spec(cls, payload: Mapping[str, Any]) -> McpPromptSpec:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise McpInspectionError("MCP prompt has no valid name")
        name = name.strip()
        title = payload.get("title")
        description = payload.get("description")
        raw_arguments = payload.get("arguments")
        arguments: list[McpPromptArgument] = []
        if raw_arguments is not None:
            if not isinstance(raw_arguments, list):
                raise McpInspectionError(f"MCP prompt {name!r} has invalid arguments")
            seen = set()
            for argument in raw_arguments:
                if not isinstance(argument, dict):
                    raise McpInspectionError(f"MCP prompt {name!r} has invalid argument")
                arg_name = argument.get("name")
                if not isinstance(arg_name, str) or not cls._valid_public_identifier(arg_name):
                    raise McpInspectionError(
                        f"MCP prompt {name!r} argument has invalid name"
                    )
                if arg_name in seen:
                    raise McpInspectionError(
                        f"MCP prompt {name!r} repeats argument {arg_name!r}"
                    )
                seen.add(arg_name)
                arg_description = argument.get("description")
                arguments.append(
                    McpPromptArgument(
                        name=arg_name,
                        description=arg_description if isinstance(arg_description, str) else "",
                        required=argument.get("required") is True,
                    )
                )
        icons = payload.get("icons")
        if not isinstance(icons, list):
            icons = []
        return McpPromptSpec(
            name=name,
            path=cls._mcp_tool_path(name),
            title=title if isinstance(title, str) else "",
            description=description if isinstance(description, str) else "",
            arguments=tuple(arguments),
            icons=tuple(dict(icon) for icon in icons if isinstance(icon, dict)),
        )

    @classmethod
    def _mcp_prompt_callable(
        cls,
        spec: McpPromptSpec,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ):
        def prompt(**kwargs: str) -> McpPromptResult:
            expected = {argument.name: argument for argument in spec.arguments}
            unknown = sorted(set(kwargs) - set(expected))
            if unknown:
                raise McpInspectionError(
                    f"MCP prompt {spec.name!r} has no argument(s): {', '.join(unknown)}"
                )
            missing = sorted(
                argument.name
                for argument in spec.arguments
                if argument.required and argument.name not in kwargs
            )
            if missing:
                raise McpInspectionError(
                    f"MCP prompt {spec.name!r} is missing required argument(s): "
                    f"{', '.join(missing)}"
                )
            for name, value in kwargs.items():
                if not isinstance(value, str):
                    raise McpInspectionError(
                        f"MCP prompt {spec.name!r} argument {name!r} expects str"
                    )
            return cls._get_mcp_prompt(
                spec,
                kwargs,
                endpoint=endpoint,
                headers=headers,
                session=session,
            )

        parameters = []
        for argument in spec.arguments:
            parameters.append(
                inspect.Parameter(
                    argument.name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=(
                        inspect.Parameter.empty if argument.required else None
                    ),
                    annotation=str,
                )
            )
        prompt.__name__ = spec.path.rsplit(".", 1)[-1]
        prompt.__doc__ = spec.description or spec.title
        prompt.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        prompt.mcp_prompt = spec  # type: ignore[attr-defined]
        return prompt

    @classmethod
    def _get_mcp_prompt(
        cls,
        spec: McpPromptSpec,
        arguments: Mapping[str, str],
        *,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ) -> McpPromptResult:
        params: dict[str, Any] = {"name": spec.name}
        if arguments:
            params["arguments"] = dict(arguments)
        payload, request_headers = cls._mcp_request(
            session,
            headers,
            "prompts/get",
            request_id=70,
            params=params,
            name=spec.name,
        )
        response = cls._post_mcp(endpoint, payload, headers=request_headers)
        message = cls._decode_mcp_response(response)
        error = message.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                cls._rpc_error_message("prompts/get", error),
                result=error,
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpExecutionError("MCP prompts/get returned no result")
        if result.get("resultType") == "input_required":
            raise McpExecutionError(
                "MCP prompts/get input_required continuation is not implemented",
                result=result,
            )
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise McpExecutionError("MCP prompts/get returned invalid messages", result=result)
        decoded: list[McpPromptMessage] = []
        for item in messages:
            if not isinstance(item, dict):
                raise McpExecutionError("MCP prompt message must be an object", result=result)
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"}:
                raise McpExecutionError("MCP prompt message has invalid role", result=result)
            if not isinstance(content, dict):
                raise McpExecutionError("MCP prompt message has invalid content", result=result)
            decoded.append(
                McpPromptMessage(role=role, content=dict(content))
            )
        description = result.get("description")
        return McpPromptResult(
            description=description if isinstance(description, str) else "",
            messages=tuple(decoded),
        )

    @classmethod
    def _list_mcp_resources(
        cls,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ) -> tuple[Mapping[str, Any], ...]:
        if "resources" not in session.capabilities:
            return ()

        resources: list[Mapping[str, Any]] = []
        cursor: str | None = None
        request_id = 40
        while True:
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            payload, request_headers = cls._mcp_request(
                session,
                headers,
                "resources/list",
                request_id=request_id,
                params=params,
            )
            response = cls._post_mcp(endpoint, payload, headers=request_headers)
            message = cls._decode_mcp_response(response)
            cls._raise_rpc_error("resources/list", message)
            result = message.get("result")
            if not isinstance(result, dict):
                raise McpInspectionError("MCP resources/list returned no result")
            page = result.get("resources")
            if not isinstance(page, list):
                raise McpInspectionError("MCP resources/list returned invalid resources")
            for resource in page:
                if not isinstance(resource, dict):
                    raise McpInspectionError("MCP resources/list returned an invalid resource")
                resources.append(resource)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise McpInspectionError("MCP resources/list returned an invalid nextCursor")
            cursor = next_cursor
            request_id += 1
        return tuple(resources)

    @classmethod
    def _mcp_resource_spec(cls, payload: Mapping[str, Any]) -> McpResourceSpec:
        uri = payload.get("uri")
        name = payload.get("name")
        if not isinstance(uri, str) or not uri:
            raise McpInspectionError("MCP resource has no valid uri")
        if not isinstance(name, str) or not name.strip():
            raise McpInspectionError(f"MCP resource {uri!r} has no valid name")
        name = name.strip()
        title = payload.get("title")
        description = payload.get("description")
        mime_type = payload.get("mimeType")
        annotations = payload.get("annotations")
        return McpResourceSpec(
            uri=uri,
            name=name,
            path=cls._mcp_tool_path(name),
            title=title if isinstance(title, str) else "",
            description=description if isinstance(description, str) else "",
            mime_type=mime_type if isinstance(mime_type, str) else None,
            annotations=dict(annotations) if isinstance(annotations, dict) else {},
        )

    @classmethod
    def _mcp_resource_reader(
        cls,
        spec: McpResourceSpec,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ):
        def read() -> Any:
            return cls._read_mcp_resource(
                spec,
                endpoint=endpoint,
                headers=headers,
                session=session,
            )

        read.__name__ = "read"
        read.__doc__ = spec.description or spec.title or f"Read MCP resource {spec.name}"
        read.mcp_resource = spec  # type: ignore[attr-defined]
        return read

    @classmethod
    def _read_mcp_resource(
        cls,
        spec: McpResourceSpec,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ) -> Any:
        payload, request_headers = cls._mcp_request(
            session,
            headers,
            "resources/read",
            request_id=50,
            params={"uri": spec.uri},
            name=spec.uri,
        )
        response = cls._post_mcp(endpoint, payload, headers=request_headers)
        message = cls._decode_mcp_response(response)
        error = message.get("error")
        if isinstance(error, dict):
            raise McpExecutionError(
                cls._rpc_error_message("resources/read", error),
                result=error,
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpExecutionError("MCP resources/read returned no result")
        if result.get("resultType") == "input_required":
            raise McpExecutionError(
                "MCP resources/read input_required continuation is not implemented",
                result=result,
            )
        contents = result.get("contents")
        if not isinstance(contents, list):
            raise McpExecutionError("MCP resources/read returned invalid contents", result=result)
        decoded = [cls._mcp_resource_content(item) for item in contents]
        if len(decoded) == 1:
            return decoded[0]
        return decoded

    @staticmethod
    def _mcp_resource_content(item: Any) -> McpResourceContent:
        if not isinstance(item, dict):
            raise McpExecutionError("MCP resource content must be an object")
        uri = item.get("uri")
        if not isinstance(uri, str) or not uri:
            raise McpExecutionError("MCP resource content has no valid uri")
        mime_type = item.get("mimeType")
        annotations = item.get("annotations")
        if "text" in item:
            value = item["text"]
            if not isinstance(value, str):
                raise McpExecutionError("MCP text resource content must be a string")
        elif "blob" in item:
            blob = item["blob"]
            if not isinstance(blob, str):
                raise McpExecutionError("MCP blob resource content must be base64 text")
            try:
                value = base64.b64decode(blob, validate=True)
            except ValueError as exc:
                raise McpExecutionError("MCP blob resource content is invalid base64") from exc
        else:
            raise McpExecutionError("MCP resource content has neither text nor blob")
        return McpResourceContent(
            uri=uri,
            mime_type=mime_type if isinstance(mime_type, str) else None,
            value=value,
            annotations=dict(annotations) if isinstance(annotations, dict) else {},
        )

    @classmethod
    def _mcp_request(
        cls,
        session: _McpSession,
        headers: Mapping[str, str],
        method: str,
        *,
        request_id: int,
        params: Mapping[str, Any],
        name: str | None = None,
    ) -> tuple[Mapping[str, Any], Mapping[str, str]]:
        if session.era == "modern":
            payload = cls._modern_request_payload(
                method,
                request_id=request_id,
                params=params,
            )
            request_headers = {
                **headers,
                "MCP-Protocol-Version": session.protocol_version,
                "Mcp-Method": method,
            }
            if name is not None:
                request_headers["Mcp-Name"] = name
            return payload, request_headers

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        request_headers = {
            **headers,
            "MCP-Protocol-Version": session.protocol_version,
        }
        if session.session_id:
            request_headers["MCP-Session-Id"] = session.session_id
        return payload, request_headers

    @classmethod
    def _mcp_tool_spec(cls, payload: Mapping[str, Any]) -> McpToolSpec:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise McpInspectionError("MCP tool name must be a non-empty string")
        name = name.strip()
        path = cls._mcp_tool_path(name)

        description = payload.get("description")
        if not isinstance(description, str):
            description = ""
        input_schema = payload.get("inputSchema")
        if not isinstance(input_schema, dict):
            raise McpInspectionError(f"MCP tool {name!r} has no valid inputSchema")
        if input_schema.get("type") not in (None, "object"):
            raise McpInspectionError(
                f"MCP tool {name!r} inputSchema must describe an object"
            )

        output_schema = payload.get("outputSchema")
        if not isinstance(output_schema, dict):
            output_schema = None
        annotations = payload.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}

        return McpToolSpec(
            name=name,
            path=path,
            description=description,
            input_schema=dict(input_schema),
            output_schema=dict(output_schema) if output_schema is not None else None,
            annotations=dict(annotations),
        )

    @classmethod
    def _mcp_inspection_callable(
        cls,
        spec: McpToolSpec,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        session: _McpSession,
    ):
        def operation(**kwargs: Any) -> None:
            raise McpInspectionError(
                f"MCP tool {spec.name!r} is inspection-only; execution is not implemented"
            )

        def prepare(**kwargs: Any) -> McpToolRequest:
            cls._validate_json_schema_value(
                kwargs,
                spec.input_schema,
                path=f"MCP tool {spec.name!r} arguments",
            )
            return McpToolRequest(
                name=spec.name,
                path=spec.path,
                arguments=dict(kwargs),
                endpoint=endpoint,
                protocol_version=session.protocol_version,
                era=session.era,
                session_id=session.session_id,
                headers=dict(headers),
            )

        operation.__name__ = spec.path.rsplit(".", 1)[-1]
        operation.__doc__ = spec.description
        operation.__signature__ = cls._mcp_tool_signature(spec)  # type: ignore[attr-defined]
        operation.mcp_tool = spec  # type: ignore[attr-defined]
        operation.prepare = prepare  # type: ignore[attr-defined]
        return operation

    @classmethod
    def _validate_json_schema_value(
        cls,
        value: Any,
        schema: Mapping[str, Any],
        *,
        path: str,
    ) -> None:
        if "const" in schema and value != schema["const"]:
            raise McpInspectionError(f"{path} must equal {schema['const']!r}")

        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise McpInspectionError(f"{path} must be one of {enum!r}")

        schema_type = schema.get("type")
        allowed_types: list[str] = []
        if isinstance(schema_type, str):
            allowed_types = [schema_type]
        elif isinstance(schema_type, list):
            allowed_types = [item for item in schema_type if isinstance(item, str)]

        if allowed_types:
            if value is None and "null" in allowed_types:
                return
            non_null_types = [item for item in allowed_types if item != "null"]
            if non_null_types and not any(
                cls._json_schema_type_matches(value, item)
                for item in non_null_types
            ):
                expected = " or ".join(non_null_types)
                raise McpInspectionError(f"{path} expects {expected}")

        effective_type = None
        if isinstance(schema_type, str):
            effective_type = schema_type
        elif isinstance(schema_type, list):
            non_null_types = [item for item in schema_type if item != "null"]
            if len(non_null_types) == 1:
                effective_type = non_null_types[0]

        if effective_type == "object" or (
            effective_type is None and isinstance(value, dict) and "properties" in schema
        ):
            if not isinstance(value, dict):
                return
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                properties = {}
            required = schema.get("required")
            required_names = {
                item for item in required
                if isinstance(required, list) and isinstance(item, str)
            } if isinstance(required, list) else set()
            missing = sorted(name for name in required_names if name not in value)
            if missing:
                raise McpInspectionError(
                    f"{path} is missing required field(s): {', '.join(missing)}"
                )

            additional = schema.get("additionalProperties", True)
            for name, item in value.items():
                property_schema = properties.get(name)
                if isinstance(property_schema, dict):
                    cls._validate_json_schema_value(
                        item,
                        property_schema,
                        path=f"{path}.{name}",
                    )
                    continue
                if additional is False:
                    raise McpInspectionError(
                        f"{path} has no allowed field {name!r}"
                    )
                if isinstance(additional, dict):
                    cls._validate_json_schema_value(
                        item,
                        additional,
                        path=f"{path}.{name}",
                    )

        if effective_type == "array" and isinstance(value, list):
            items = schema.get("items")
            if isinstance(items, dict):
                for index, item in enumerate(value):
                    cls._validate_json_schema_value(
                        item,
                        items,
                        path=f"{path}[{index}]",
                    )

    @staticmethod
    def _json_schema_type_matches(value: Any, schema_type: str) -> bool:
        if schema_type == "string":
            return isinstance(value, str)
        if schema_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if schema_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if schema_type == "boolean":
            return isinstance(value, bool)
        if schema_type == "array":
            return isinstance(value, list)
        if schema_type == "object":
            return isinstance(value, dict)
        if schema_type == "null":
            return value is None
        return True

    @classmethod
    def _mcp_tool_signature(cls, spec: McpToolSpec) -> inspect.Signature:
        properties = spec.input_schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = spec.input_schema.get("required")
        if not isinstance(required, list):
            required = []
        required_names = {item for item in required if isinstance(item, str)}

        parameters = []
        for name, schema in properties.items():
            if not cls._valid_public_identifier(name) or not isinstance(schema, dict):
                continue
            if name in required_names:
                default = inspect.Parameter.empty
            elif "default" in schema:
                default = schema["default"]
            else:
                default = None
            parameters.append(
                inspect.Parameter(
                    name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=cls._json_schema_annotation(schema),
                )
            )
        return inspect.Signature(parameters)

    @staticmethod
    def _json_schema_annotation(schema: Mapping[str, Any]) -> Any:
        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            non_null = [item for item in schema_type if item != "null"]
            schema_type = non_null[0] if len(non_null) == 1 else None
        return {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }.get(schema_type, Any)

    @classmethod
    def _mcp_tool_path(cls, name: str) -> str:
        parts = name.split(".")
        normalized = [cls._mcp_identifier(part) for part in parts]
        if any(not part for part in normalized):
            raise McpInspectionError(f"MCP tool name {name!r} cannot map to a Giso path")
        return ".".join(normalized)

    @staticmethod
    def _mcp_identifier(value: str) -> str:
        value = re.sub(r"\W+", "_", value, flags=re.UNICODE).strip("_")
        if not value:
            return ""
        if value[0].isdigit():
            value = f"tool_{value}"
        if not value.isidentifier():
            return ""
        return value

    @staticmethod
    def _validate_mcp_endpoint(endpoint: str) -> str:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise TypeError("MCP endpoint must be a non-empty URL")
        endpoint = endpoint.strip()
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme == "https" and parsed.netloc:
            return endpoint
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            return endpoint
        raise ValueError("MCP Streamable HTTP endpoint must use HTTPS or loopback HTTP")

    @staticmethod
    def _validate_mcp_headers(
        headers: Mapping[str, str] | None,
    ) -> dict[str, str]:
        if headers is None:
            return {}
        if not isinstance(headers, Mapping):
            raise TypeError("MCP headers must be a mapping")
        validated: dict[str, str] = {}
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("MCP header names and values must be strings")
            validated[name] = value
        return validated

    @classmethod
    def _modern_request_payload(
        cls,
        method: str,
        *,
        request_id: int,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_params = dict(params or {})
        meta = request_params.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        request_params["_meta"] = {
            **meta,
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientInfo": {
                "name": "giso",
                "version": "0.1.0",
            },
            "io.modelcontextprotocol/clientCapabilities": {
                "extensions": {
                    "io.modelcontextprotocol/tasks": {},
                },
            },
        }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": request_params,
        }

    @classmethod
    def _post_mcp(
        cls,
        endpoint: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        allow_empty: bool = False,
    ) -> _McpHttpResponse:
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                response_headers = dict(response.headers.items())
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            status = exc.code
        except (OSError, urllib.error.URLError) as exc:
            raise McpInspectionError(f"Cannot connect to MCP endpoint {endpoint!r}") from exc

        if not body and not allow_empty:
            raise McpInspectionError(f"MCP endpoint {endpoint!r} returned an empty response")
        return _McpHttpResponse(
            status=status,
            headers=response_headers,
            body=body,
        )

    @classmethod
    def _decode_mcp_response(cls, response: _McpHttpResponse) -> Mapping[str, Any]:
        content_type = cls._header_value(response.headers, "Content-Type") or ""
        if "text/event-stream" in content_type:
            body = cls._last_sse_data(response.body)
        else:
            body = response.body
        try:
            message = json.loads(body)
        except json.JSONDecodeError as exc:
            raise McpInspectionError("MCP endpoint returned invalid JSON") from exc
        if not isinstance(message, dict):
            raise McpInspectionError("MCP endpoint returned a non-object JSON-RPC message")
        if message.get("jsonrpc") != "2.0":
            raise McpInspectionError("MCP endpoint returned an invalid JSON-RPC response")
        return message

    @staticmethod
    def _last_sse_data(body: str) -> str:
        events = re.split(r"\r?\n\r?\n", body.strip())
        for event in reversed(events):
            data_lines = [
                line[5:].lstrip()
                for line in event.splitlines()
                if line.startswith("data:")
            ]
            if data_lines:
                return "\n".join(data_lines)
        raise McpInspectionError("MCP SSE response contained no data event")

    @classmethod
    def _raise_rpc_error(cls, method: str, message: Mapping[str, Any]) -> None:
        error = message.get("error")
        if isinstance(error, dict):
            raise McpInspectionError(cls._rpc_error_message(method, error))

    @staticmethod
    def _rpc_error_message(method: str, error: Mapping[str, Any]) -> str:
        detail = error.get("message")
        if isinstance(detail, str) and detail:
            return f"MCP {method} failed: {detail}"
        return f"MCP {method} failed"

    @staticmethod
    def _server_info_from_result(
        message: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        meta = result.get("_meta")
        if not isinstance(meta, dict):
            meta = message.get("_meta")
        if isinstance(meta, dict):
            server_info = meta.get("io.modelcontextprotocol/serverInfo")
            if isinstance(server_info, dict):
                return dict(server_info)
        server_info = result.get("serverInfo")
        if isinstance(server_info, dict):
            return dict(server_info)
        return {}

    @staticmethod
    def _header_value(headers: Mapping[str, str], name: str) -> str | None:
        lower = name.lower()
        for key, value in headers.items():
            if key.lower() == lower:
                return value
        return None
