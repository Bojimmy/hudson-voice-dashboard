import os
import re
import sys
import requests

# OpenClaw Gateway Config
OPENCLAW_URL = "http://127.0.0.1:18789/v1/chat/completions"
OPENCLAW_TOKEN = "748e284d1c1ad7065cc32525784fb6c3fe61ae63b0d79902"
MODEL = "moonshot/kimi-k2.5"

# Import tool runtime from parent project root
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from x_security_server import web_search, analyze_screen, remember, recall, generate_image, upscale_image, safe_execute  # noqa: E402
except Exception:
    web_search = None
    analyze_screen = None
    remember = None
    recall = None
    generate_image = None
    upscale_image = None
    safe_execute = None

try:
    from x_agent_guard import XAgentGuard  # noqa: E402
    GUARD = XAgentGuard(os.path.join(ROOT_DIR, "x-agent-security.xml"))
except Exception:
    XAgentGuard = None
    GUARD = None


def _call_openclaw(messages):
    headers = {
        "Authorization": f"Bearer {OPENCLAW_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    response = requests.post(OPENCLAW_URL, json=payload, headers=headers, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"Kimi Error {response.status_code}: {response.text}")
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _parse_tool_blocks(text):
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        clean = lines[i].strip().replace("*", "").replace("`", "").replace(">", "").replace("-", "").strip()
        if clean.startswith("TOOL:"):
            tool = clean.split("TOOL:", 1)[1].strip()
            params = {}
            i += 1
            while i < len(lines):
                nxt = lines[i].strip().replace("*", "").replace("`", "").replace(">", "").replace("-", "").strip()
                if nxt.startswith("TOOL:"):
                    i -= 1
                    break
                if ":" in nxt:
                    key, value = nxt.split(":", 1)
                    key = key.strip().lower()
                    if key:
                        params[key] = value.strip()
                i += 1
            blocks.append((tool, params))
        i += 1
    return blocks


def _execute_tool(tool_name, params):
    if tool_name == "web_search":
        if web_search is None:
            return "web_search unavailable: x_security_server dependency error."
        return web_search(params.get("query", ""), max_results=5)
    if tool_name == "analyze_screen":
        if analyze_screen is None:
            return "analyze_screen unavailable: x_security_server dependency error."
        return analyze_screen(params.get("question", ""))
    if tool_name == "remember":
        if remember is None:
            return "remember unavailable: x_security_server dependency error."
        return remember(params.get("text", ""), params.get("category", "General"))
    if tool_name == "recall":
        if recall is None:
            return "recall unavailable: x_security_server dependency error."
        return recall(params.get("query", ""))
    if tool_name == "validate_command":
        cmd = params.get("command", "")
        if GUARD is None:
            return "validate_command unavailable: x_agent_guard import error."
        authorized, message = GUARD.validate(cmd)
        return f"{'ALLOWED' if authorized else 'BLOCKED'}: {message}"
    if tool_name == "safe_execute":
        if safe_execute is None:
            return "safe_execute unavailable: x_security_server dependency error."
        cmd = params.get("command", "")
        if not cmd:
            return "No command provided."
        return safe_execute(cmd)
    if tool_name == "generate_image":
        if generate_image is None:
            return "generate_image unavailable: x_security_server dependency error."
        prompt = params.get("prompt", "")
        filename = params.get("filename", "generated_image.png")
        aspect = params.get("aspect", "1:1")
        return generate_image(prompt, filename, aspect)
    if tool_name == "upscale_image":
        if upscale_image is None:
            return "upscale_image unavailable: x_security_server dependency error."
        filename = params.get("file") or params.get("filename", "")
        return upscale_image(filename)
    return f"Unknown tool: {tool_name}"


def ask_kimi(prompt, max_tool_turns=2):
    """
    Send a prompt to Kimi with tool execution support for direct routes (Discord/CLI).
    """
    researcher_prompt_path = os.path.join(
        os.path.dirname(__file__),
        ".mission_control",
        "team",
        "researcher-kimi.md",
    )
    if os.path.exists(researcher_prompt_path):
        with open(researcher_prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    else:
        system_prompt = (
            "You are Kimi with tool access. "
            "When execution is needed, return TOOL blocks (TOOL: ...)."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    for _ in range(max_tool_turns + 1):
        reply = _call_openclaw(messages)
        tool_blocks = _parse_tool_blocks(reply)
        if not tool_blocks:
            return reply

        results = []
        for tool_name, params in tool_blocks:
            try:
                result = _execute_tool(tool_name, params)
            except Exception as e:
                result = f"Tool execution error: {e}"
            results.append(f"TOOL_RESULT: {tool_name}\nRESULT:\n{result}\n")

        messages.append({"role": "assistant", "content": reply})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Tool results are below. Use them to continue and respond normally.\n\n"
                    + "\n".join(results)
                ),
            }
        )

    return "Tool loop exceeded max turns. Please retry."


if __name__ == "__main__":
    print(ask_kimi("Hello! Who are you?"))
