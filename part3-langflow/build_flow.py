#!/usr/bin/env python3
"""Build the three-agent support flow and register it with a running Langflow.

Why a script instead of clicking the canvas: the flow has 9 nodes and 10 edges,
and the agent prompts are long. Building it in code makes the graph reviewable in
a diff, reproducible on a fresh Langflow instance, and impossible to get subtly
wrong by dragging a wire to the neighbouring port.

Graph
-----

    SQL Database (tool) ──▶ Analysis Agent ──┐
                                             ├──▶ Orchestrator Agent ──▶ Chat Output
    Gmail Sender (tool) ──▶ Response Agent ──┘            ▲
                                                          │
                                                     Chat Input

Both Analysis and Response are attached to the Orchestrator as *tools*, not as
fixed pipeline stages. That is what makes routing dynamic: the Orchestrator's
model decides, per message, whether to invoke neither, one, or both. A greeting
touches no tool at all.

Credentials
-----------
Secret fields are never given literal values. They are set to the *name* of a
Langflow Global Variable with `load_from_db=True`, so the exported flow JSON
carries a reference and never the secret itself.

Usage:
    python build_flow.py                 # create or update the flow
    python build_flow.py --export out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

from prompts import ANALYSIS_PROMPT, ORCHESTRATOR_PROMPT, RESPONSE_PROMPT

LANGFLOW_URL = "http://127.0.0.1:7860"
FLOW_NAME = "Support Multi-Agent Flow"

# Langflow's bundled dropdown still lists gemini-2.5-flash, which now returns 404
# for new API keys -- the option list is stale, so model names are set explicitly.
#
# Each agent runs on a DIFFERENT model on purpose. The Gemini free tier meters
# requests per model (20 RPM each), and a single user message can cost six or more
# model calls once tool loops are counted. Spreading the three agents across three
# models triples the effective headroom, which is what makes a 6-7 message live
# demo survivable. The assignment does not pin a model, and the capability
# ordering also matches the work: routing judgement is the hardest job.
# gemini-3.7-flash is deliberately avoided: its free tier allows only ~20
# requests per DAY, which one debugging session exhausts. These three have
# materially larger free quotas.
ORCHESTRATOR_MODEL = "gemini-3.6-flash"
ANALYSIS_MODEL = "gemini-3.5-flash"
RESPONSE_MODEL = "gemini-flash-lite-latest"
POSTGRES_URL_VAR = "SUPPORT_DATABASE_URL"
GEMINI_KEY_VAR = "GEMINI_API_KEY"
GMAIL_ADDRESS_VAR = "GMAIL_ADDRESS"
GMAIL_PASSWORD_VAR = "GMAIL_APP_PASSWORD"

# Langflow's frontend encodes handle JSON with " replaced by this character.
QUOTE_SENTINEL = "œ"  # œ

def agent_tools_metadata(tool_name: str, description: str) -> dict:
    """Give an Agent-as-tool a distinct, meaningful identity.

    This is not cosmetic -- it fixes a real collision. Langflow's Agent component
    hardcodes `tool_name="Call_Agent"` (see lfx/components/models_and_agents/
    agent.py), so EVERY agent exposed as a tool is called
    `Call_Agent_message_response`, no matter what its display name is. With two
    specialist agents attached to one orchestrator, the names collide: the
    orchestrator asks for the Analysis Agent and reaches the Response Agent
    instead, which has no SQL tool and therefore answers with nothing.

    `tools_metadata` renames each tool by matching on its default tag. The
    `json_response` variant is disabled (`status: False`) so each agent exposes
    exactly one unambiguous tool rather than two near-identical ones.
    """
    return {
        "_input_type": "ToolsInput",
        "advanced": False,
        "api_editable": False,
        "display_name": "Actions",
        "dynamic": False,
        "info": "Modify tool names and descriptions to help agents understand when to use each tool.",
        "is_list": True,
        "list_add_label": "Add More",
        "name": "tools_metadata",
        "override_skip": False,
        "placeholder": "",
        "real_time_refresh": True,
        "required": False,
        "show": True,
        "title_case": False,
        "tool_mode": False,
        "trace_as_metadata": True,
        "track_in_telemetry": False,
        "type": "tools",
        "value": [
            {
                "name": tool_name,
                "description": description,
                "tags": ["Call_Agent_message_response"],
                "status": True,
                "approval_actions": [],
                "display_name": "message_response",
                "display_description": description,
                "readonly": False,
                "args": {
                    "input_value": {
                        "default": "",
                        "description": "The task for this agent, including any context it needs.",
                        "title": "Input Value",
                        "type": "string",
                    }
                },
            },
            {
                # Disabled: a second, near-identical tool per agent only gives
                # the orchestrator a way to pick wrong.
                "name": f"{tool_name}_json",
                "description": "Disabled.",
                "tags": ["Call_Agent_json_response"],
                "status": False,
                "approval_actions": [],
                "display_name": "json_response",
                "display_description": "Disabled.",
                "readonly": False,
                "args": {},
            },
        ],
    }


TOOL_OUTPUT = {
    "name": "component_as_tool",
    "display_name": "Toolset",
    "method": "to_toolkit",
    "types": ["Tool"],
    "selected": "Tool",
    "hidden": None,
    "cache": True,
    "value": "__UNDEFINED__",
    "allows_loop": False,
}


# --------------------------------------------------------------------------- #
# Langflow API helpers
# --------------------------------------------------------------------------- #


def _decode(raw: bytes) -> bytes:
    """Langflow gzips responses regardless of Accept-Encoding; urllib does not
    transparently decompress, so detect the gzip magic number and inflate."""
    if raw[:2] == b"\x1f\x8b":
        import gzip

        return gzip.decompress(raw)
    return raw


def api(method: str, path: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{LANGFLOW_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = _decode(resp.read())
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read()).decode(errors="replace")[:900]


def get_token() -> str:
    with urllib.request.urlopen(f"{LANGFLOW_URL}/api/v1/auto_login", timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def load_catalog(token: str) -> dict:
    """Fetch the component catalog.

    Langflow gzips this response regardless of Accept-Encoding, and urllib does
    not transparently decompress, so handle it here.
    """
    req = urllib.request.Request(
        f"{LANGFLOW_URL}/api/v1/all",
        headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
    if raw[:2] == b"\x1f\x8b":  # gzip magic number
        import gzip

        raw = gzip.decompress(raw)
    return json.loads(raw)


def find_component(catalog: dict, wanted: str) -> tuple[str, dict]:
    """Locate a component definition by its type name across all categories."""
    for category, entries in catalog.items():
        if category == "component_display_names":
            continue
        for name, definition in entries.items():
            if not isinstance(definition, dict) or "template" not in definition:
                continue
            if name == wanted or name.split(":")[-1].split("@")[0] == wanted:
                return name, definition
    msg = f"Component {wanted!r} not found in the Langflow catalog"
    raise SystemExit(msg)


# --------------------------------------------------------------------------- #
# Node and edge construction
# --------------------------------------------------------------------------- #


def make_node(
    node_id: str,
    type_name: str,
    definition: dict,
    position: tuple[int, int],
    values: dict | None = None,
    from_db: set[str] | None = None,
    tool_mode: bool = False,
    display_name: str | None = None,
) -> dict:
    """Instantiate a catalog component as a flow node."""
    node = json.loads(json.dumps(definition))  # deep copy
    node["display_name"] = display_name or node.get("display_name", type_name)

    for field, value in (values or {}).items():
        if field not in node["template"]:
            msg = f"{type_name}: no template field {field!r}"
            raise SystemExit(msg)
        node["template"][field]["value"] = value

    # A field marked load_from_db holds the *name* of a Global Variable; Langflow
    # resolves it at runtime, so the secret never enters the flow JSON.
    for field in from_db or set():
        node["template"][field]["load_from_db"] = True

    if tool_mode:
        node["tool_mode"] = True
        node["outputs"] = [dict(TOOL_OUTPUT)]

    return {
        "id": node_id,
        "type": "genericNode",
        "position": {"x": position[0], "y": position[1]},
        "data": {"id": node_id, "type": type_name, "node": node},
    }


def encode_handle(handle: dict) -> str:
    """Encode a handle dict the way the Langflow frontend does."""
    return json.dumps(handle, separators=(",", ":")).replace('"', QUOTE_SENTINEL)


def make_edge(source: dict, target: dict, output_name: str, field_name: str) -> dict:
    """Wire one node output to another node's input field."""
    src_id, tgt_id = source["id"], target["id"]
    src_type = source["data"]["type"]

    output = next(
        o for o in source["data"]["node"]["outputs"] if o["name"] == output_name
    )
    target_field = target["data"]["node"]["template"][field_name]

    source_handle = {
        "dataType": src_type,
        "id": src_id,
        "name": output_name,
        "output_types": list(output["types"]),
    }
    target_handle = {
        "fieldName": field_name,
        "id": tgt_id,
        "inputTypes": list(target_field.get("input_types") or []),
        "type": target_field.get("type", "str"),
    }

    src_str = encode_handle(source_handle)
    tgt_str = encode_handle(target_handle)

    return {
        "id": f"xy-edge__{src_id}{src_str}-{tgt_id}{tgt_str}",
        "source": src_id,
        "target": tgt_id,
        "sourceHandle": src_str,
        "targetHandle": tgt_str,
        "data": {"sourceHandle": source_handle, "targetHandle": target_handle},
        "animated": False,
        "className": "",
        "selected": False,
    }


def gemini_node(
    catalog: dict, node_id: str, position: tuple[int, int], label: str, model: str
) -> dict:
    type_name, definition = find_component(catalog, "GoogleGenerativeAIComponent")
    return make_node(
        node_id,
        type_name,
        definition,
        position,
        values={
            "model_name": model,
            "api_key": GEMINI_KEY_VAR,
            "temperature": 0.1,
            "tool_model_enabled": True,
        },
        from_db={"api_key"},
        display_name=label,
    )


# --------------------------------------------------------------------------- #
# The flow
# --------------------------------------------------------------------------- #


def build(catalog: dict) -> dict:
    agent_type, agent_def = find_component(catalog, "Agent")
    sql_type, sql_def = find_component(catalog, "SQLComponent")
    gmail_type, gmail_def = find_component(catalog, "GmailSenderComponent")
    chat_in_type, chat_in_def = find_component(catalog, "ChatInput")
    chat_out_type, chat_out_def = find_component(catalog, "ChatOutput")

    # --- tools ------------------------------------------------------------- #
    sql_tool = make_node(
        "SQLComponent-sqltool", sql_type, sql_def, (-1750, -520),
        values={"database_url": POSTGRES_URL_VAR, "include_columns": True, "add_error": True},
        from_db={"database_url"},
        tool_mode=True,
        display_name="SQL Database (support_requests)",
    )
    gmail_tool = make_node(
        "GmailSender-mailtool", gmail_type, gmail_def, (-1750, 520),
        values={"sender_email": GMAIL_ADDRESS_VAR, "app_password": GMAIL_PASSWORD_VAR},
        from_db={"sender_email", "app_password"},
        tool_mode=True,
        display_name="Gmail Sender",
    )

    # --- specialist agents, exposed to the orchestrator as tools ------------ #
    llm_analysis = gemini_node(
        catalog, "GoogleGenerativeAI-llmA", (-1750, -160),
        f"Gemini Analysis ({ANALYSIS_MODEL})", ANALYSIS_MODEL,
    )
    llm_response = gemini_node(
        catalog, "GoogleGenerativeAI-llmR", (-1750, 880),
        f"Gemini Response ({RESPONSE_MODEL})", RESPONSE_MODEL,
    )
    llm_orch = gemini_node(
        catalog, "GoogleGenerativeAI-llmO", (-1150, 380),
        f"Gemini Orchestrator ({ORCHESTRATOR_MODEL})", ORCHESTRATOR_MODEL,
    )

    analysis = make_node(
        "Agent-analysis", agent_type, agent_def, (-1150, -520),
        values={
            "system_prompt": ANALYSIS_PROMPT,
            "add_calculator_tool": False,
            "add_current_date_tool": True,
            "max_iterations": 8,
            "handle_parsing_errors": True,
        },
        tool_mode=True,
        display_name="Analysis Agent",
    )
    analysis["data"]["node"]["template"]["tools_metadata"] = agent_tools_metadata(
        "query_support_database",
        "Look up support requests in the company database. Use for ANY question about "
        "tickets, customers, statuses, priorities, categories or counts. Returns the "
        "records found, a classification, an urgency level, and what information is "
        "missing. Pass the user's question verbatim plus any customer name or email.",
    )
    response = make_node(
        "Agent-response", agent_type, agent_def, (-1150, 520),
        values={
            "system_prompt": RESPONSE_PROMPT,
            "add_calculator_tool": False,
            "add_current_date_tool": True,
            "max_iterations": 8,
            "handle_parsing_errors": True,
        },
        tool_mode=True,
        display_name="Response Agent",
    )
    response["data"]["node"]["template"]["tools_metadata"] = agent_tools_metadata(
        "compose_customer_response",
        "Compose the final reply to the customer and send email when required. Use ONLY "
        "when the user asked for an email/notification, or after the database lookup "
        "when a reply must be sent. Pass the findings and the recipient address. Do NOT "
        "use for plain questions that need no email.",
    )

    # --- orchestrator and chat I/O ------------------------------------------ #
    orchestrator = make_node(
        "Agent-orchestrator", agent_type, agent_def, (-560, 0),
        values={
            "system_prompt": ORCHESTRATOR_PROMPT,
            "add_calculator_tool": False,
            "add_current_date_tool": True,
            "max_iterations": 10,
            "handle_parsing_errors": True,
        },
        display_name="Orchestrator Agent",
    )
    chat_input = make_node(
        "ChatInput-in", chat_in_type, chat_in_def, (-1150, 100), display_name="Chat Input"
    )
    chat_output = make_node(
        "ChatOutput-out", chat_out_type, chat_out_def, (0, 0), display_name="Chat Output"
    )

    nodes = [
        sql_tool, gmail_tool, llm_analysis, llm_response, llm_orch,
        analysis, response, orchestrator, chat_input, chat_output,
    ]

    edges = [
        # tools into their owning specialist agent
        make_edge(sql_tool, analysis, "component_as_tool", "tools"),
        make_edge(gmail_tool, response, "component_as_tool", "tools"),
        # a model for each agent
        make_edge(llm_analysis, analysis, "model_output", "model"),
        make_edge(llm_response, response, "model_output", "model"),
        make_edge(llm_orch, orchestrator, "model_output", "model"),
        # specialist agents attached to the orchestrator AS TOOLS -> dynamic routing
        make_edge(analysis, orchestrator, "component_as_tool", "tools"),
        make_edge(response, orchestrator, "component_as_tool", "tools"),
        # conversation in and out
        make_edge(chat_input, orchestrator, "message", "input_value"),
        make_edge(orchestrator, chat_output, "response", "input_value"),
    ]

    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.5}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", help="Also write the flow JSON to this path.")
    args = parser.parse_args()

    token = get_token()
    catalog = load_catalog(token)
    graph = build(catalog)

    status, existing = api("GET", "/api/v1/flows/?get_all=true", token)
    if status != 200:
        print(f"Could not list flows: {status} {existing}", file=sys.stderr)
        return 1

    match = next((f for f in existing if f.get("name") == FLOW_NAME), None)
    payload = {
        "name": FLOW_NAME,
        "description": (
            "Three-agent customer support workflow. The Orchestrator classifies "
            "intent and dynamically routes to the Analysis Agent (SQL Tool) and/or "
            "the Response Agent (Gmail Tool). Simple messages invoke neither."
        ),
        "data": graph,
        "endpoint_name": "support-flow",
        "is_component": False,
    }

    if match:
        status, body = api("PATCH", f"/api/v1/flows/{match['id']}", token, payload)
        action = "updated"
    else:
        payload["id"] = str(uuid.uuid4())
        status, body = api("POST", "/api/v1/flows/", token, payload)
        action = "created"

    if status not in (200, 201):
        print(f"Flow {action} failed: {status}\n{body}", file=sys.stderr)
        return 1

    flow_id = body.get("id")
    print(f"Flow {action}: {FLOW_NAME}")
    print(f"  id            : {flow_id}")
    print(f"  endpoint      : {LANGFLOW_URL}/api/v1/run/support-flow")
    print(f"  nodes / edges : {len(graph['nodes'])} / {len(graph['edges'])}")

    if args.export:
        status, full = api("GET", f"/api/v1/flows/{flow_id}", token)
        with open(args.export, "w", encoding="utf-8") as handle:
            json.dump(full, handle, indent=2, ensure_ascii=False)
        print(f"  exported      : {args.export}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
