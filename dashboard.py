import streamlit as st
import streamlit.components.v1 as components
import asyncio
import sys
import os
import subprocess
import json
import re
import glob
import csv
from datetime import date, datetime, timedelta

# Import your robust logic from run_mission.py
import run_mission as mission_control
import importlib
try:
    from kimi_bridge import ask_kimi
    KIMI_AVAILABLE = True
except ImportError:
    KIMI_AVAILABLE = False
    
importlib.reload(mission_control) # Force reload to pick up latest skill changes

VOICE_WIDGET_URL = os.getenv("VOICE_WIDGET_URL", "http://localhost:3000").strip()


def _clean_question_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[-*+]\s+", "", line)
    line = re.sub(r"^\d+[\.\)]\s+", "", line)
    return line.strip()


def _is_likely_section_line(line: str) -> bool:
    l = line.strip()
    if not l:
        return True
    if re.match(r"^section\s+\d+", l, re.IGNORECASE):
        return True
    if re.match(r"^[-\s]*section\b", l, re.IGNORECASE):
        return True
    if l.isupper() and len(l.split()) <= 10:
        return True
    # Short title-like lines without question signal are usually section labels.
    if len(l.split()) <= 7 and "?" not in l and ":" not in l and "|" not in l:
        if re.match(r"^[A-Z][A-Za-z0-9/&\-\s]+$", l):
            return True
    return False


def parse_markdown_questions(markdown_text: str):
    """
    Parse a checklist-like markdown file into form questions.
    Rules:
    - Bullet/numbered/plain lines become text questions.
    - Lines with "A | B | C" become multiple-choice questions.
    - "(optional)" marks the question optional.
    """
    questions = []
    seen = set()
    in_code_block = False
    for raw in markdown_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("|"):  # skip markdown tables
            continue
        if set(line) <= {"-", "=", "*"}:
            continue
        if line.startswith(">"):
            continue

        q = _clean_question_line(line)
        if not q:
            continue
        if _is_likely_section_line(q):
            continue
        if q.lower().startswith(("note:", "goal:", "summary:", "questions:", "form settings:", "underlying issue")):
            continue

        lower = q.lower()
        required = "(optional)" not in lower and "optional:" not in lower
        q = re.sub(r"\(optional\)", "", q, flags=re.IGNORECASE).strip()
        q = re.sub(r"^optional:\s*", "", q, flags=re.IGNORECASE).strip()
        if not q:
            continue

        options = None
        if " | " in q:
            parts = [p.strip() for p in q.split(" | ") if p.strip()]
            if len(parts) >= 3:
                q = parts[0]
                options = parts[1:]

        if len(q) < 3:
            continue

        dedupe_key = re.sub(r"\s+", " ", q.lower()).strip()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        questions.append({
            "text": q,
            "options": options,
            "required": required
        })

    return questions


def import_markdown_to_google_form(markdown_path: str):
    try:
        from google_forms_skill import create_google_form, add_question, move_file_to_folder
    except Exception as e:
        return {"ok": False, "error": f"Google Forms skill import failed: {e}"}

    full_path = markdown_path
    if not os.path.isabs(full_path):
        full_path = os.path.abspath(os.path.join(os.getcwd(), full_path))

    if not os.path.exists(full_path):
        return {"ok": False, "error": f"Markdown file not found: {full_path}"}

    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            md_text = f.read()
    except Exception as e:
        return {"ok": False, "error": f"Failed to read markdown: {e}"}

    questions = parse_markdown_questions(md_text)
    if not questions:
        return {"ok": False, "error": "No valid questions found in markdown."}

    base_name = os.path.splitext(os.path.basename(full_path))[0]
    title = base_name.replace("-", " ").replace("_", " ").title()
    description = f"Imported from {os.path.basename(full_path)}"

    form_result = create_google_form(title, description)
    if not form_result:
        return {"ok": False, "error": "create_google_form failed."}

    form_id = form_result.get("formId")
    responder_uri = form_result.get("responderUri", "")
    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"

    for q in questions:
        add_question(form_id, q["text"], options=q["options"], required=q["required"])

    moved = False
    move_note = "Skipped"
    try:
        moved = move_file_to_folder(form_id, "Robby_Colab")
        move_note = "Moved to Robby_Colab" if moved else "Folder move failed or folder missing"
    except Exception as e:
        move_note = f"Folder move error: {e}"

    return {
        "ok": True,
        "form_id": form_id,
        "edit_url": edit_url,
        "responder_url": responder_uri,
        "question_count": len(questions),
        "source_path": full_path,
        "moved_to_folder": moved,
        "move_note": move_note
    }


def _parse_bool(value: str, default=True):
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("true", "yes", "y", "1", "required"):
        return True
    if v in ("false", "no", "n", "0", "optional"):
        return False
    return default


def _save_last_form(form_id: str, edit_url: str, responder_url: str, title: str = ""):
    st.session_state.last_created_form = {
        "form_id": form_id or "",
        "edit_url": edit_url or "",
        "responder_url": responder_url or "",
        "title": title or "",
    }
    _save_last_result(
        kind="form",
        title=title or "Google Form",
        form_id=form_id,
        edit_url=edit_url,
        responder_url=responder_url,
    )


def _last_form_markdown(header: str = "### 🔗 Last Created Form"):
    last = st.session_state.get("last_created_form")
    if not last:
        return f"{header}\nNo form saved in this session yet."
    return (
        f"{header}\n"
        f"- **Title:** `{last.get('title', '')}`\n"
        f"- **Form ID:** `{last.get('form_id', '')}`\n"
        f"- **Edit URL:** {last.get('edit_url', '')}\n"
        f"- **Responder URL:** {last.get('responder_url', '')}"
    )


def _save_last_result(kind: str, title: str = "", path: str = "", url: str = "", **extra):
    payload = {
        "kind": (kind or "artifact").strip().lower(),
        "title": title or "",
        "path": path or "",
        "url": url or "",
    }
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    st.session_state.last_result = payload


def _last_result_markdown(header: str = "### 📌 Last Result"):
    last = st.session_state.get("last_result")
    if not last:
        return f"{header}\nNo result saved in this session yet."

    lines = [
        header,
        f"- **Type:** `{last.get('kind', 'artifact')}`",
    ]
    if last.get("title"):
        lines.append(f"- **Title:** `{last.get('title')}`")
    if last.get("path"):
        lines.append(f"- **Path:** `{last.get('path')}`")
    if last.get("url"):
        lines.append(f"- **URL:** {last.get('url')}")
    if last.get("form_id"):
        lines.append(f"- **Form ID:** `{last.get('form_id')}`")
    if last.get("edit_url"):
        lines.append(f"- **Edit URL:** {last.get('edit_url')}")
    if last.get("responder_url"):
        lines.append(f"- **Responder URL:** {last.get('responder_url')}")
    return "\n".join(lines)


def _push_live_chat(role: str, text: str):
    clean = (text or "").strip()
    if not clean:
        return
    entry = {"role": role, "text": clean}
    history = st.session_state.get("live_chat", [])
    history.append(entry)
    st.session_state.live_chat = history[-200:]


def _live_chat_markdown():
    history = st.session_state.get("live_chat", [])
    if not history:
        return "_No chat yet. Send a prompt to populate live chat history._"
    lines = []
    for item in history[-120:]:
        role = item.get("role", "system")
        label = "You" if role == "user" else ("Robby" if role == "assistant" else "System")
        lines.append(f"**{label}:** {item.get('text', '')}")
    return "\n\n".join(lines)


def _format_report_markdown(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    # Normalize whitespace but keep newlines for section parsing.
    text = re.sub(r"\r\n?", "\n", text)

    title = None
    m = re.search(r"(?im)^report\s*:\s*(.+)$", text)
    if m:
        title = m.group(1).strip()
    elif "report" in text.lower():
        title = "Generated Report"

    intro = ""
    findings: list[str] = []

    intro_match = re.search(r"(?is)introduction\s*:\s*(.*?)(?:\n\s*findings\s*:|\Z)", text)
    if intro_match:
        intro = intro_match.group(1).strip()

    findings_match = re.search(r"(?is)findings\s*:\s*(.*)", text)
    if findings_match:
        body = findings_match.group(1).strip()
        # Try numbered/bulleted extraction first.
        for line in body.splitlines():
            clean = line.strip()
            if not clean:
                continue
            if re.match(r"^(\d+[\.\)]\s+|[-*]\s+)", clean):
                clean = re.sub(r"^(\d+[\.\)]\s+|[-*]\s+)", "", clean).strip()
                findings.append(clean)
        # Fallback: split long paragraph on sentence boundaries.
        if not findings and body:
            parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", body)
            findings = [p.strip() for p in parts if len(p.strip()) > 30][:8]

    if not title and not findings and len(text) < 900:
        return text

    lines = []
    lines.append(f"### 📄 {title or 'Report'}")
    if intro:
        lines.append(f"**Introduction:** {intro}")
    if findings:
        lines.append("**Findings:**")
        for i, item in enumerate(findings[:10], start=1):
            lines.append(f"{i}. {item}")
    else:
        lines.append(text[:3000])

    return "\n\n".join(lines)


def run_google_forms_command(raw_prompt: str):
    """
    Google Forms creator commands:
    - FORM_LIST [limit]
    - FORM_CREATE <title> || <description>
    - FORM_ADD <form_id> || <question> || <opt1,opt2,...> || <required|optional>
    - FORM_GET <form_id>
    - FORM_RESPONSES <form_id>
    - FORM_MOVE <form_id> || <folder_name>
    - FORM_LAST
    """
    try:
        from google_forms_skill import (
            create_google_form,
            add_question,
            list_forms,
            get_form_details,
            get_form_responses,
            move_file_to_folder,
        )
    except Exception as e:
        return {"ok": False, "error": f"Google Forms skill import failed: {e}"}

    text = raw_prompt.strip()
    upper = text.upper()

    if upper.startswith("FORM_LAST"):
        last = st.session_state.get("last_created_form")
        if not last:
            # Fallback: recover newest form from Google Drive listing when
            # the form was created through the indirect agent/tool route.
            try:
                latest = list_forms(limit=1) or []
                if latest:
                    item = latest[0]
                    form_id = item.get("id", "")
                    edit_url = item.get("webViewLink", "") or (f"https://docs.google.com/forms/d/{form_id}/edit" if form_id else "")
                    recovered = {
                        "form_id": form_id,
                        "edit_url": edit_url,
                        "responder_url": "",
                        "title": item.get("name", "Recovered Latest Form"),
                    }
                    if form_id:
                        try:
                            details = get_form_details(form_id)
                            if isinstance(details, dict) and details.get("responderUri"):
                                recovered["responder_url"] = details.get("responderUri")
                        except Exception:
                            pass
                    st.session_state.last_created_form = recovered
                    _save_last_result(
                        kind="form",
                        title=recovered.get("title", "Recovered Latest Form"),
                        form_id=recovered.get("form_id"),
                        edit_url=recovered.get("edit_url"),
                        responder_url=recovered.get("responder_url"),
                    )
                    return {"ok": True, "mode": "last", "last_form": recovered, "recovered": True}
            except Exception:
                pass
            return {"ok": False, "error": "No last form in session yet. Create/import a form first, or run FORM_LIST 1."}
        if last.get("form_id") and not last.get("responder_url"):
            try:
                details = get_form_details(last.get("form_id"))
                if isinstance(details, dict) and details.get("responderUri"):
                    last["responder_url"] = details.get("responderUri")
                    st.session_state.last_created_form = last
            except Exception:
                pass
        _save_last_result(
            kind="form",
            title=last.get("title", "Last Form"),
            form_id=last.get("form_id"),
            edit_url=last.get("edit_url"),
            responder_url=last.get("responder_url"),
        )
        return {"ok": True, "mode": "last", "last_form": last}

    if upper.startswith("FORM_LIST"):
        parts = text.split()
        limit = 10
        if len(parts) > 1 and parts[1].isdigit():
            limit = int(parts[1])
        items = list_forms(limit=limit)
        return {"ok": True, "mode": "list", "items": items or [], "limit": limit}

    if upper.startswith("FORM_CREATE"):
        payload = text[len("FORM_CREATE"):].strip()
        if not payload:
            return {"ok": False, "error": "Usage: FORM_CREATE <title> || <description>"}
        chunks = [c.strip() for c in payload.split("||")]
        title = chunks[0] if chunks else ""
        description = chunks[1] if len(chunks) > 1 else ""
        if not title:
            return {"ok": False, "error": "FORM_CREATE requires a title."}
        result = create_google_form(title, description)
        if not result:
            return {"ok": False, "error": "create_google_form failed."}
        form_id = result.get("formId")
        return {
            "ok": True,
            "mode": "create",
            "form_id": form_id,
            "edit_url": f"https://docs.google.com/forms/d/{form_id}/edit",
            "responder_url": result.get("responderUri", ""),
            "title": title,
        }

    if upper.startswith("FORM_ADD"):
        payload = text[len("FORM_ADD"):].strip()
        chunks = [c.strip() for c in payload.split("||")]
        if len(chunks) < 2:
            return {
                "ok": False,
                "error": "Usage: FORM_ADD <form_id> || <question> || <opt1,opt2,...> || <required|optional>",
            }
        form_id = chunks[0]
        question = chunks[1]
        options = None
        if len(chunks) > 2 and chunks[2]:
            options = [o.strip() for o in chunks[2].split(",") if o.strip()]
            if len(options) < 2:
                options = None
        required = _parse_bool(chunks[3], default=True) if len(chunks) > 3 else True
        add_question(form_id, question, options=options, required=required)
        return {
            "ok": True,
            "mode": "add",
            "form_id": form_id,
            "question": question,
            "options": options,
            "required": required,
        }

    if upper.startswith("FORM_GET"):
        payload = text[len("FORM_GET"):].strip()
        if not payload:
            return {"ok": False, "error": "Usage: FORM_GET <form_id>"}
        details = get_form_details(payload)
        if not details:
            return {"ok": False, "error": "get_form_details failed."}
        return {"ok": True, "mode": "get", "details": details, "form_id": payload}

    if upper.startswith("FORM_RESPONSES"):
        payload = text[len("FORM_RESPONSES"):].strip()
        if not payload:
            return {"ok": False, "error": "Usage: FORM_RESPONSES <form_id>"}
        responses = get_form_responses(payload)
        if responses is None:
            return {"ok": False, "error": "get_form_responses failed."}
        return {
            "ok": True,
            "mode": "responses",
            "form_id": payload,
            "responses": responses,
            "count": len(responses),
        }

    if upper.startswith("FORM_MOVE"):
        payload = text[len("FORM_MOVE"):].strip()
        chunks = [c.strip() for c in payload.split("||")]
        if len(chunks) < 2:
            return {"ok": False, "error": "Usage: FORM_MOVE <form_id> || <folder_name>"}
        ok = move_file_to_folder(chunks[0], chunks[1])
        return {
            "ok": ok,
            "mode": "move",
            "form_id": chunks[0],
            "folder": chunks[1],
            "error": None if ok else "move_file_to_folder failed.",
        }

    return {"ok": False, "error": "Unknown FORM command."}

# 1. PAGE SETUP
st.set_page_config(page_title="Antigravity Console", layout="wide", page_icon="🚀")

# 2. CUSTOM CSS (Antigravity Theme)
st.markdown("""
    <style>
    .stApp { background-color: #050510; color: #e0f7fa; }
    .stCodeBlock { border: 1px solid #00e5ff; border-radius: 5px; }
    h1, h2, h3 { color: #00e5ff; font-family: 'Courier New', monospace; }
    
    /* Make the iframe blend in seamlessly */
    iframe { border: none !important; }
    </style>
    """, unsafe_allow_html=True)

# --- SECTION 1: THE VOICE ENGINE (Embedded React App) ---
voice_container = st.container()

with voice_container:
    st.markdown("<h3 style='text-align: center; margin-bottom: 0;'>ANTIGRAVITY VOICE ENGINE</h3>", unsafe_allow_html=True)
    # components.iframe("http://localhost:3000", height=600, scrolling=False)
    # UPDATED (Vision-Safe): Injecting iframe with 'display-capture' permission.
    # We use a fixed-height container to prevent the layout collapse issue.
    st.markdown(f"""
        <div style="width: 100%; height: 720px; overflow: hidden; margin-bottom: 10px;">
            <iframe 
                src="{VOICE_WIDGET_URL}" 
                width="100%" 
                height="720" 
                style="border: none;" 
                allow="microphone; camera; display-capture; autoplay; clipboard-write; encrypted-media"
                scrolling="no"
            ></iframe>
        </div>
    """, unsafe_allow_html=True)
    # UPDATED: Using st.markdown to inject iframe with explicit permissions [REVERTED by User Request]
    # st.markdown("""
    #     <iframe 
    #         src="http://localhost:3000" 
    #         height="600" 
    #         style="width: 100%; border: none;" 
    #         allow="microphone; camera; display-capture; autoplay; clipboard-write"
    #     ></iframe>
    # """, unsafe_allow_html=True)

# --- SECTION 2: MISSION CONTROL (The IDE Layer) ---
st.divider()

# Input for the "Brain"
col_input, col_switch = st.columns([5, 1])
with col_input:
    mission_prompt = st.chat_input("Command Antigravity... Quick recall: LAST_FORM, LAST_IMAGE, LAST_CODE, LAST_MD, KIMI_INTRO_BRIEF")

with col_switch:
    use_kimi = st.checkbox("🔮 Kimi", value=False, help="Route command to OpenClaw Kimi Agent")
    st.checkbox(
        "🎯 Strict",
        value=True,
        key="strict_task_mode",
        help="Block web/screen/memory tools unless explicitly requested in prompt.",
    )
    st.checkbox(
        "💾 Auto Save",
        value=False,
        key="auto_save_agent_files",
        help="When off, FILE: artifacts are previewed in logs but not written to disk.",
    )

# --- REFACTORED LAYOUT: 3-COLUMN IDE VIEW (SANDBOXED) ---

# Persistence file for project root
PROJECT_ROOT_FILE = ".last_project_root"

def load_last_project_root():
    """Load the last used project root from file, or default to cwd."""
    if os.path.exists(PROJECT_ROOT_FILE):
        try:
            with open(PROJECT_ROOT_FILE, "r") as f:
                path = f.read().strip()
                if os.path.isdir(path):
                    return path
        except:
            pass
    return os.getcwd()

def save_project_root(path):
    """Save the current project root to file for persistence."""
    try:
        with open(PROJECT_ROOT_FILE, "w") as f:
            f.write(path)
    except:
        pass


def get_quick_roots():
    candidates = [
        os.getcwd(),
        "/Users/bobdallavia/Desktop/OpenClaw_Workspace",
        "/Users/bobdallavia/.openclaw/workspace",
    ]
    roots = []
    seen_real = set()
    for candidate in candidates:
        resolved = os.path.abspath(os.path.expanduser(candidate))
        real = os.path.realpath(resolved)
        if os.path.isdir(resolved) and real not in seen_real:
            seen_real.add(real)
            roots.append(resolved)
    return roots


def _normalized_real_path(path: str) -> str:
    """Return normalized absolute real path for safe comparisons."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def _is_within_root(path: str, root: str) -> bool:
    """True when path is inside root (symlink-safe)."""
    try:
        path_real = _normalized_real_path(path)
        root_real = _normalized_real_path(root)
        return os.path.commonpath([path_real, root_real]) == root_real
    except Exception:
        return False


def _extract_path_from_text(text: str) -> str:
    """Extract first plausible filesystem path from freeform text."""
    if not text:
        return ""
    # Prefer absolute macOS-style paths used in this project.
    m = re.search(r"(/Users/[^\s'\"`]+)", text)
    if m:
        return m.group(1).rstrip(".,;:!?")
    # Fallback: ~/path style.
    m = re.search(r"(~/[^\s'\"`]+)", text)
    if m:
        return m.group(1).rstrip(".,;:!?")
    return ""


def _extract_requested_section(content: str, question: str) -> str:
    """
    If the prompt asks for a section that starts with a marker, extract it.
    Especially useful for markdown headings like:
    starts with "## 1. Ron Wyse (HDL011)" and ends before next section.
    """
    if not content or not question:
        return ""

    marker = ""
    for pattern in [
        r'extract\s+section\s+"([^"]+)"',
        r"extract\s+section\s+'([^']+)'",
        r'extract\s+section\s+([#][#][^\n]+)',
        r'starts?\s+with\s+"([^"]+)"',
        r"starts?\s+with\s+'([^']+)'",
        r'section titled\s+"([^"]+)"',
        r"section titled\s+'([^']+)'",
    ]:
        m = re.search(pattern, question, re.IGNORECASE)
        if m:
            marker = m.group(1).strip()
            break

    if not marker:
        return _extract_section_by_intent(content, question)

    idx = content.find(marker)
    if idx < 0:
        return _extract_section_by_intent(content, question)

    end_idx = len(content)
    if marker.lstrip().startswith("##"):
        after = content[idx + len(marker):]
        next_header = re.search(r"(?m)^\s*##\s+", after)
        if next_header:
            end_idx = idx + len(marker) + next_header.start()

    return content[idx:end_idx].strip()


def _extract_section_by_intent(content: str, question: str) -> str:
    """Heuristic section extractor for natural speech like 'show Ron draft'."""
    if not content or not question:
        return ""
    q = question.lower()

    headings = list(re.finditer(r"(?m)^##\s+(.+)$", content))
    if not headings:
        return ""

    sections = []
    for i, h in enumerate(headings):
        start = h.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        title = h.group(1).strip()
        sections.append((title, content[start:end].strip()))

    # Draft number intent: "draft 1", "first draft", etc.
    number = None
    m = re.search(r"\bdraft\s*#?\s*(\d+)\b", q)
    if m:
        number = int(m.group(1))
    if number is None:
        ordinal_map = {
            "first": 1,
            "second": 2,
            "third": 3,
            "fourth": 4,
            "fifth": 5,
        }
        for word, n in ordinal_map.items():
            if word in q:
                number = n
                break
    if number is not None:
        for title, body in sections:
            if re.search(rf"^\s*{number}\.", title):
                return body

    # HDL code intent.
    hdl = re.search(r"\bhdl\d+\b", q)
    if hdl:
        code = hdl.group(0).lower()
        for title, body in sections:
            if code in title.lower():
                return body

    # Name/token-based scoring.
    stopwords = {
        "show", "me", "the", "draft", "email", "for", "please", "section", "extract",
        "from", "and", "to", "of", "a", "an", "in", "chat", "only", "read", "mode",
        "this", "that", "with", "before", "next", "stop"
    }
    tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2 and t not in stopwords]
    if not tokens:
        return ""
    scored = []
    for title, body in sections:
        tl = title.lower()
        score = sum(1 for t in tokens if t in tl)
        scored.append((score, len(title), body))
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2]

    return ""


def _safe_read_local_file(raw_path: str, project_root: str, max_chars: int = 12000):
    """
    Read text file inside project root only.
    Returns dict: {ok, path, content, error}
    """
    if not raw_path:
        return {"ok": False, "error": "Missing file path.", "path": "", "content": ""}

    candidate = raw_path.strip().strip("\"'`").rstrip(".,;:!?")
    candidate = os.path.expanduser(candidate)
    if not os.path.isabs(candidate):
        candidate = os.path.join(project_root, candidate)
    candidate = os.path.abspath(candidate)

    if not _is_within_root(candidate, project_root):
        return {
            "ok": False,
            "error": f"Path outside workspace root: {candidate}",
            "path": candidate,
            "content": "",
        }
    if not os.path.exists(candidate):
        fixed = candidate

        # Common folder typo.
        if "/hudsonsmarketing/" in fixed:
            fixed = fixed.replace("/hudsonsmarketing/", "/hudsons-marketing/")

        # Common filename typo: YYYYMMDD -> YYYY-MM-DD
        fixed = re.sub(
            r"(p1_personalized_email_drafts_)(\d{4})(\d{2})(\d{2})(\.md)$",
            r"\1\2-\3-\4\5",
            fixed,
        )

        if os.path.exists(fixed):
            candidate = fixed

    # Last fallback: if this looks like a P1 draft file request, pick latest known draft file.
    if not os.path.exists(candidate):
        base = os.path.basename(candidate).lower()
        if base.startswith("p1_personalized_email_drafts_") and base.endswith(".md"):
            draft_glob = os.path.join(
                _normalized_real_path(project_root),
                "hudsons-marketing",
                "outreach",
                "p1_personalized_email_drafts_*.md",
            )
            matches = sorted(glob.glob(draft_glob))
            if matches:
                candidate = matches[-1]
    if not os.path.exists(candidate):
        return {"ok": False, "error": f"File not found: {candidate}", "path": candidate, "content": ""}
    if os.path.isdir(candidate):
        return {"ok": False, "error": f"Path is a directory: {candidate}", "path": candidate, "content": ""}

    try:
        with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(max_chars + 1)
    except Exception as e:
        return {"ok": False, "error": f"Read error: {e}", "path": candidate, "content": ""}

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
        content += "\n\n[TRUNCATED]"

    return {"ok": True, "error": "", "path": candidate, "content": content}


def _is_read_only_prompt(prompt_text: str) -> bool:
    p = (prompt_text or "").lower()
    signals = [
        "read-only",
        "read only",
        "chat only",
        "paste in chat",
        "do not create",
        "don't create",
        "do not write",
        "don't write",
        "no file write",
        "without creating files",
    ]
    return any(s in p for s in signals)


def _prompt_allows_tool(prompt_text: str, tool_name: str) -> bool:
    p = (prompt_text or "").lower()

    def has_any(terms):
        return any(t in p for t in terms)

    if tool_name == "web_search":
        return has_any(["web", "google", "search", "lookup", "find online", "research", "url", "source"])
    if tool_name == "analyze_screen":
        return has_any(["screen", "screenshot", "display", "vision", "analyze screen", "look at the screen"])
    if tool_name == "remember":
        return has_any(["remember", "save memory", "store memory", "add to memory"])
    if tool_name == "recall":
        return has_any(["recall", "memory", "what did we save", "retrieve memory"])
    return True


def _looks_like_natural_read_request(prompt_text: str) -> bool:
    p = (prompt_text or "").lower()
    if any(t in p for t in ["scheduled", "schedule", "tomorrow", "today", "yesterday", "due"]):
        return False
    intent_terms = ["show", "read", "extract", "paste", "pull", "open", "give me"]
    target_terms = [
        "draft", "email", "outreach", "hdl", "ron", "matthew", "buck", "diana", "carey",
        "section", "first", "second", "third", "fourth", "fifth"
    ]
    return any(t in p for t in intent_terms) and any(t in p for t in target_terms)


def _resolve_latest_p1_draft_path(project_root: str) -> str:
    pattern = os.path.join(
        _normalized_real_path(project_root),
        "hudsons-marketing",
        "outreach",
        "p1_personalized_email_drafts_*.md",
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else ""


def _looks_like_schedule_query(prompt_text: str) -> bool:
    p = (prompt_text or "").lower()
    intent_terms = ["show", "list", "what", "which", "who", "give me"]
    schedule_terms = [
        "scheduled",
        "schedule",
        "follow up",
        "follow-up",
        "due",
        "tomorrow",
        "today",
        "yesterday",
        "day after tomorrow",
    ]
    email_terms = ["email", "emails", "outreach", "follow up", "follow-up", "lead", "leads"]
    return (
        any(t in p for t in intent_terms)
        and any(t in p for t in schedule_terms)
        and any(t in p for t in email_terms)
    )


def _target_date_from_prompt(prompt_text: str):
    p = (prompt_text or "").lower()
    today = date.today()
    if "day after tomorrow" in p:
        return today + timedelta(days=2), "day after tomorrow"
    if "tomorrow" in p:
        return today + timedelta(days=1), "tomorrow"
    if "yesterday" in p:
        return today - timedelta(days=1), "yesterday"
    if "today" in p:
        return today, "today"
    return None, ""


def _include_internal_contacts(prompt_text: str) -> bool:
    p = (prompt_text or "").lower()
    include_terms = [
        "include internal",
        "including internal",
        "with internal",
        "show internal",
        "include internal contacts",
        "all contacts",
        "include all contacts",
    ]
    return any(t in p for t in include_terms)


def _resolve_tracking_csv_path(project_root: str) -> str:
    roots = [project_root] + get_quick_roots()
    seen = set()
    for root in roots:
        if not root:
            continue
        rr = _normalized_real_path(root)
        if rr in seen:
            continue
        seen.add(rr)
        candidates = [
            os.path.join(rr, "hudsons-marketing", "leads", "crm-outreach-tracking.csv"),
            os.path.join(rr, "leads", "crm-outreach-tracking.csv"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return ""


def _normalize_date_value(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = raw.split("T")[0].strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except Exception:
            continue
    return raw


def _load_scheduled_outreach(csv_path: str, target_iso_date: str):
    if not os.path.isfile(csv_path):
        return {"ok": False, "error": f"File not found: {csv_path}", "rows": []}
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            headers = reader.fieldnames or []
    except Exception as e:
        return {"ok": False, "error": f"Failed to read CSV: {e}", "rows": []}

    date_col = ""
    for col in [
        "Follow Up Date",
        "Follow-Up Date",
        "FollowUpDate",
        "Next Follow Up Date",
        "Scheduled Date",
        "Send Date",
        "Date",
    ]:
        if col in headers:
            date_col = col
            break
    if not date_col:
        return {"ok": False, "error": "No schedule date column found in outreach tracking CSV.", "rows": []}

    matches = []
    for row in rows:
        row_date = _normalize_date_value(row.get(date_col, ""))
        if row_date == target_iso_date:
            matches.append(row)

    priority_rank = {"very high": 0, "high": 1, "medium": 2, "low": 3}
    matches.sort(key=lambda r: priority_rank.get((r.get("Priority", "") or "").strip().lower(), 9))
    return {"ok": True, "rows": matches, "date_col": date_col}


def _normalize_name_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _load_lead_name_index(project_root: str):
    csv_path = _resolve_tracking_csv_path(project_root)
    if not csv_path:
        return []
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return []

    entries = []
    seen = set()
    for row in rows:
        lead_id = (row.get("Lead ID", "") or row.get("lead_id", "") or "").strip()
        name = (row.get("Agent Name", "") or row.get("name", "") or "").strip()
        if not lead_id or not name:
            continue
        norm_name = _normalize_name_token(name)
        key = (lead_id.upper(), norm_name)
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "lead_id": lead_id.upper(),
            "name": name,
            "norm_name": norm_name,
        })
    return entries


def _inject_lead_id_into_prompt(prompt_text: str, project_root: str):
    text = (prompt_text or "").strip()
    if not text:
        return text, None
    if re.search(r"\bHDL\d+\b", text, re.IGNORECASE):
        return text, None

    lower = text.lower()
    intent_terms = ["lead", "follow up", "follow-up", "draft", "email", "outreach"]
    if not any(t in lower for t in intent_terms):
        return text, None

    entries = _load_lead_name_index(project_root)
    if not entries:
        return text, None

    best = None
    best_len = -1
    for e in entries:
        if e["name"].lower() in lower and len(e["name"]) > best_len:
            best = e
            best_len = len(e["name"])

    if best is None:
        norm_prompt = _normalize_name_token(text)
        for e in entries:
            if e["norm_name"] and e["norm_name"] in norm_prompt and len(e["norm_name"]) > best_len:
                best = e
                best_len = len(e["norm_name"])

    if best is None:
        return text, None

    pattern = re.compile(re.escape(best["name"]), re.IGNORECASE)
    replacement = f"{best['name']} ({best['lead_id']})"
    rewritten, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        rewritten = f"{text} [Lead ID: {best['lead_id']}]"
    return rewritten, best


def _kimi_intro_brief_markdown(trigger_phrase: str = "Kimi give me the Hudson Brieaf overview") -> str:
    return f"""### Kimi Voice Introduction Brief
**Trigger phrase:** `{trigger_phrase}`

### One-Minute Intro Script
"Hi, I am Kimi, your Hudsons Agentic Operations Assistant. I help your team run lead follow-up, outreach drafting, Google Form intake, and daily prioritization so high-value opportunities never get missed.

I route urgent leads first, summarize tomorrow's scheduled outreach, and keep every action visible in one dashboard. If you want me to act, just tell me the task in plain language, and I will return clear next steps you can approve."

### Quick Use Prompts
- "Show me emails scheduled for tomorrow."
- "Draft follow-up email for Ron Wyse."
- "Create a client intake Google Form."
- "Show top priority leads needing immediate attention."

### Infographic: Hudson Agentic Workflow
| Stage | What Happens | Output |
|---|---|---|
| 1. Capture | New lead/form/chat enters workspace | Structured lead record |
| 2. Prioritize | AI scores by urgency, value, timing | P0/P1 task queue |
| 3. Draft | Robby/Kimi generates personalized outreach | Ready-to-send draft |
| 4. Review | Human approves, edits, or rejects | Safe final message |
| 5. Execute | Email/forms/follow-up actions run | Logged activity + status |
| 6. Monitor | Dashboard tracks due dates + responses | No-miss daily triage |

### Operator Notes
- Default mode is safety-first (no unnecessary web/screen tools).
- Internal contacts can be excluded or included on command.
- Use this brief for onboarding, demos, and live daughter walkthroughs.
"""

# Initialize Session State
if "selected_file" not in st.session_state:
    st.session_state.selected_file = None
if "expanded_folders" not in st.session_state:
    st.session_state.expanded_folders = set()
if "project_root" not in st.session_state:
    st.session_state.project_root = load_last_project_root()
if "last_created_form" not in st.session_state:
    st.session_state.last_created_form = None
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "live_chat" not in st.session_state:
    st.session_state.live_chat = []
if "last_web_search_result" not in st.session_state:
    st.session_state.last_web_search_result = ""
if "auto_save_agent_files" not in st.session_state:
    st.session_state.auto_save_agent_files = False

# Styling
st.markdown("""
<style>
    .tree-button {
        text-align: left !important;
        padding: 2px 5px !important;
        border: none !important;
        background: transparent !important;
        color: #a0a0a0 !important;
        font-family: monospace !important;
        font-size: 13px !important;
        width: 100% !important;
    }
    .tree-button:hover {
        color: #00e5ff !important;
        background-color: rgba(26, 26, 46, 0.5) !important;
    }
    .selected-item {
        color: #00e5ff !important;
        font-weight: bold !important;
        background-color: rgba(0, 229, 255, 0.1) !important;
    }
</style>
""", unsafe_allow_html=True)

# Flattening Algorithm (Robust Streamlit Tree)
def get_flat_tree(path, depth=0):
    flat_list = []
    try:
        items = os.listdir(path)
    except PermissionError:
        return []
    except FileNotFoundError:
        return []

    # Filter ignored
    ignore = ['.git', '__pycache__', 'node_modules', 'env', 'venv', '.DS_Store', 'package-lock.json', '.pytest_cache']
    items = [i for i in items if i not in ignore]
    
    # Sort: Folders first, then files
    items.sort(key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))

    for item in items:
        full_path = os.path.join(path, item)
        is_dir = os.path.isdir(full_path)
        is_expanded = full_path in st.session_state.expanded_folders
        
        # Add current item
        flat_list.append({
            "name": item,
            "path": full_path,
            "is_dir": is_dir,
            "depth": depth,
            "is_expanded": is_expanded
        })
        
        # Recurse if expanded folder
        if is_dir and is_expanded:
            flat_list.extend(get_flat_tree(full_path, depth + 1))
            
    return flat_list

# The Massive Real Estate: Split View
col_explorer, col_code, col_preview = st.columns([1.2, 3.8, 3])

# --- COLUMN 1: FILE EXPLORER (SANDBOXED) ---
with col_explorer:
    st.subheader("📂 Explorer")
    
    # 1. Project Root Selector
    new_root = st.text_input("Target Project Path", value=st.session_state.project_root)
    if new_root != st.session_state.project_root:
        if os.path.exists(new_root) and os.path.isdir(new_root):
            st.session_state.project_root = new_root
            st.session_state.expanded_folders = set() # Reset expansion
            save_project_root(new_root)  # Persist for next refresh
            st.rerun()
        else:
            st.error("Invalid Directory")

    # Quick root switches for common workspaces
    quick_roots = get_quick_roots()
    if quick_roots:
        st.caption("Quick Roots")
        for root in quick_roots:
            label = "OpenClaw Workspace" if "openclaw/workspace" in root.lower() else os.path.basename(root) or root
            if st.button(f"📍 {label}", key=f"quick_root_{root}", use_container_width=True):
                st.session_state.project_root = root
                st.session_state.expanded_folders = set()
                save_project_root(root)
                st.rerun()
            
    # 2. Manual File Creation
    with st.expander("➕ New File", expanded=False):
        new_filename = st.text_input("Filename (e.g. utils/test.py)", key="new_file_input")
        if st.button("Create File"):
            if new_filename:
                # Security: Ensure it's inside the sandbox
                safe_path = os.path.abspath(os.path.join(st.session_state.project_root, new_filename))
                if not _is_within_root(safe_path, st.session_state.project_root):
                    st.error("⚠️ Security Blocked: Cannot create files outside project root.")
                else:
                    try:
                        # Ensure dir exists
                        os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                        # Create empty file
                        with open(safe_path, "w") as f:
                            f.write("") 
                        st.session_state.selected_file = safe_path
                        st.success(f"Created {new_filename}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    # 3. Render Tree (Flattened Loop)
    st.divider()
    tree_items = get_flat_tree(st.session_state.project_root)
    
    # Scrollable container for the tree
    with st.container(height=550):
        for item in tree_items:
            # Visual Indentation
            indent = "\u00A0" * (item['depth'] * 3) # Non-breaking space
            
            # Icons
            if item['is_dir']:
                icon = "📂" if item['is_expanded'] else "📁"
            else:
                icon = "📄"
            
            # Label
            label = f"{indent}{icon} {item['name']}"
            
            # Styling for selection
            is_selected = (st.session_state.selected_file == item['path'])
            # We can't easily inject dynamic CSS classes per button in vanilla Streamlit, 
            # so we use a visual marker or emoji change logic instead.
            if is_selected:
                label = f"{indent}👉 {item['name']}"
            
            # Button (Unique Key is critical)
            if st.button(label, key=f"btn_{item['path']}", use_container_width=True):
                if item['is_dir']:
                    # Toggle Expansion
                    if item['is_expanded']:
                        st.session_state.expanded_folders.remove(item['path'])
                    else:
                        st.session_state.expanded_folders.add(item['path'])
                    st.rerun()
                else:
                    # Select File
                    st.session_state.selected_file = item['path']
                    st.rerun()

# --- COLUMN 2: AGENT VIEW (Tabs for Logs vs Code) ---
with col_code:
    st.subheader("📝 Agent & Editor")
    
    # Create Tabs
    tab_agent, tab_editor, tab_chat = st.tabs(["🤖 Live Agent Logs", "🛠️ Code Editor", "💬 Live Chat"])
    
    with tab_agent:
        st.caption("Monitoring Mission Control...")
        code_placeholder = st.empty()
        image_placeholder = st.empty() # Dedicated space for images
        code_placeholder.info("System Standby. Waiting for Mission Control...")
        
    with tab_editor:
        if st.session_state.selected_file:
            st.caption(f"Editing: {st.session_state.selected_file}")
            
            # Determine file type
            ext = st.session_state.selected_file.split(".")[-1].lower()
            binary_exts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'pdf', 'mp3', 'wav']
            
            try:
                # 1. Handle Images/Binary
                if ext in binary_exts:
                    if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                        st.image(st.session_state.selected_file)
                    else:
                        st.info(f"Binary file detected ({ext}). Preview not supported.")
                
                # 2. Handle Text Files
                else:
                    with open(st.session_state.selected_file, "r", encoding='utf-8', errors='ignore') as f:
                        file_content = f.read()

                    # Writable Editor
                    # Dynamic key ensures text area updates when file changes
                    new_content = st.text_area("Editor", value=file_content, height=600, key=f"editor_{st.session_state.selected_file}")
                    
                    col_save, col_status = st.columns([1, 4])
                    with col_save:
                        if st.button("💾 Save Changes", key=f"save_{st.session_state.selected_file}"):
                            try:
                                with open(st.session_state.selected_file, "w", encoding='utf-8') as f:
                                    f.write(new_content)
                                kind = "markdown" if st.session_state.selected_file.lower().endswith(".md") else "code"
                                _save_last_result(
                                    kind=kind,
                                    title=os.path.basename(st.session_state.selected_file),
                                    path=st.session_state.selected_file,
                                )
                                st.success("✅ Saved!")
                                # Rerun to ensure freshness
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error saving file: {e}")
            
            except Exception as e:
                st.error(f"Error reading file: {e}")
        else:
            st.info("Select a file from the Explorer to view code.")

    with tab_chat:
        st.caption("Conversation history (Dashboard)")
        chat_placeholder = st.empty()
        chat_placeholder.markdown(_live_chat_markdown())

# --- COLUMN 3: PREVIEW / TERMINAL ---
with col_preview:
    st.subheader("👁️ Terminal / Preview")
    preview_placeholder = st.empty()
    preview_placeholder.code(" >_ Waiting for Execution...", language="bash")

# --- SECTION 3: THE ENHANCED ENGINE LOGIC WITH X-AGENT TOOLS ---
async def run_visual_loop(prompt):
    # Initialize
    full_terminal_log = f" >_ INITIALIZING MISSION: {prompt}\n"
    preview_placeholder.code(full_terminal_log, language="bash")
    _push_live_chat("user", prompt)
    chat_placeholder.markdown(_live_chat_markdown())

    # Direct local preview/import path to bypass OpenClaw exec instability.
    # Usage:
    #   FORM_IMPORT_MD_PREVIEW /absolute/or/relative/path/to/checklist.md
    #   FORM_IMPORT_MD /absolute/or/relative/path/to/checklist.md
    normalized_prompt = prompt.strip()
    rewritten_prompt, matched_lead = _inject_lead_id_into_prompt(
        normalized_prompt,
        st.session_state.project_root,
    )
    if rewritten_prompt != normalized_prompt:
        normalized_prompt = rewritten_prompt
        prompt = rewritten_prompt
        if matched_lead:
            full_terminal_log += (
                f"\n >_ LEAD LOOKUP: {matched_lead.get('name')} -> {matched_lead.get('lead_id')}"
            )
            preview_placeholder.code(full_terminal_log, language="bash")
    lower_prompt = normalized_prompt.lower()
    strict_task_mode = st.session_state.get("strict_task_mode", True)
    read_only_mode = _is_read_only_prompt(normalized_prompt)
    natural_read_request = _looks_like_natural_read_request(normalized_prompt)
    if natural_read_request and not read_only_mode:
        read_only_mode = True
        full_terminal_log += "\n >_ NATURAL READ MODE ENABLED"
        preview_placeholder.code(full_terminal_log, language="bash")

    last_result_aliases = [
        "last result",
        "latest result",
        "last thing",
        "latest thing",
        "last output",
        "latest output",
        "last file",
        "latest file",
        "last image",
        "latest image",
        "where is the last",
        "where's the last",
        "show last result",
        "show me the last",
    ]
    if any(alias in lower_prompt for alias in last_result_aliases):
        normalized_prompt = "LAST_RESULT"
        full_terminal_log += "\n >_ NATURAL LANGUAGE ALIAS DETECTED: LAST_RESULT"
        preview_placeholder.code(full_terminal_log, language="bash")

    report_recall_aliases = [
        "put the report here",
        "paste it into this chat",
        "paste the report here",
        "show the report here",
        "show report here",
        "show report in chat",
        "show report here in the chat",
        "can you put the report here",
    ]
    looks_like_report_recall = any(alias in lower_prompt for alias in report_recall_aliases)
    if not looks_like_report_recall:
        has_report_word = "report" in lower_prompt
        has_show_or_paste = ("show" in lower_prompt) or ("paste" in lower_prompt) or ("put" in lower_prompt)
        has_here_or_chat = ("here" in lower_prompt) or ("chat" in lower_prompt)
        looks_like_report_recall = has_report_word and has_show_or_paste and has_here_or_chat

    if looks_like_report_recall:
        cached = st.session_state.get("last_web_search_result", "").strip()
        if cached:
            full_terminal_log += "\n >_ REPORT RECALL MODE (FROM LAST WEB SEARCH)"
            preview_placeholder.code(full_terminal_log, language="bash")
            reply = _format_report_markdown(cached[:7000])
            code_placeholder.markdown(reply)
            _push_live_chat("assistant", reply)
            chat_placeholder.markdown(_live_chat_markdown())
            return

    intro_aliases = [
        "launch kimi intro",
        "kimi intro",
        "intro brief",
        "voice introduction brief",
        "hudsons intro brief",
        "agentic intro brief",
        "show kimi intro brief",
        "run kimi intro brief",
        "kimi give me the hudson brieaf overview",
        "kimi give me the hudson brief overview",
    ]
    if any(alias in lower_prompt for alias in intro_aliases):
        normalized_prompt = "KIMI_INTRO_BRIEF"
        full_terminal_log += "\n >_ NATURAL LANGUAGE ALIAS DETECTED: KIMI_INTRO_BRIEF"
        preview_placeholder.code(full_terminal_log, language="bash")

    if _looks_like_schedule_query(normalized_prompt):
        target_date, label = _target_date_from_prompt(normalized_prompt)
        if target_date is None:
            target_date = date.today() + timedelta(days=1)
            label = "tomorrow (default)"
        target_iso = target_date.isoformat()
        csv_path = _resolve_tracking_csv_path(st.session_state.project_root)
        if not csv_path:
            full_terminal_log += (
                "\n >_ SCHEDULE LOOKUP MODE"
                f"\n >_ TARGET_DATE: {target_iso} ({label})"
                "\n >_ ❌ Could not find crm-outreach-tracking.csv under current workspace roots."
            )
            preview_placeholder.code(full_terminal_log, language="bash")
            msg = (
                "### 📅 Scheduled Outreach\n"
                f"- Target date: `{target_iso}` ({label})\n"
                "- Error: could not locate `crm-outreach-tracking.csv`."
            )
            code_placeholder.markdown(msg)
            _push_live_chat("assistant", msg)
            chat_placeholder.markdown(_live_chat_markdown())
            return

        loaded = _load_scheduled_outreach(csv_path, target_iso)
        if not loaded.get("ok"):
            err = loaded.get("error", "Unknown schedule lookup error.")
            full_terminal_log += (
                "\n >_ SCHEDULE LOOKUP MODE"
                f"\n >_ TARGET_DATE: {target_iso} ({label})"
                f"\n >_ SOURCE: {csv_path}"
                f"\n >_ ❌ {err}"
            )
            preview_placeholder.code(full_terminal_log, language="bash")
            msg = (
                "### 📅 Scheduled Outreach\n"
                f"- Target date: `{target_iso}` ({label})\n"
                f"- Source: `{csv_path}`\n"
                f"- Error: {err}"
            )
            code_placeholder.markdown(msg)
            _push_live_chat("assistant", msg)
            chat_placeholder.markdown(_live_chat_markdown())
            return

        rows = loaded.get("rows", [])
        include_internal = _include_internal_contacts(normalized_prompt)
        excluded_internal = 0
        if not include_internal:
            filtered_rows = []
            for row in rows:
                status_lower = (row.get("Status", "") or "").strip().lower()
                if status_lower in ("internal contact", "internal", "team"):
                    excluded_internal += 1
                    continue
                filtered_rows.append(row)
            rows = filtered_rows

        full_terminal_log += (
            "\n >_ SCHEDULE LOOKUP MODE"
            f"\n >_ TARGET_DATE: {target_iso} ({label})"
            f"\n >_ SOURCE: {csv_path}"
            f"\n >_ MATCHES: {len(rows)}"
        )
        if excluded_internal:
            full_terminal_log += f"\n >_ EXCLUDED_INTERNAL: {excluded_internal}"
        preview_placeholder.code(full_terminal_log, language="bash")

        if not rows:
            msg = (
                "### 📅 Scheduled Outreach\n"
                f"- Target date: `{target_iso}` ({label})\n"
                f"- Source: `{csv_path}`\n"
                "- Result: no scheduled outreach rows found."
            )
            if excluded_internal:
                msg += "\n- Note: internal contacts were excluded by default."
            code_placeholder.markdown(msg)
            _push_live_chat("assistant", msg)
            chat_placeholder.markdown(_live_chat_markdown())
            return

        lines = [
            "### 📅 Scheduled Outreach",
            f"- Target date: `{target_iso}` ({label})",
            f"- Source: `{csv_path}`",
            f"- Matches: `{len(rows)}`",
        ]
        if excluded_internal:
            lines.append(f"- Internal contacts excluded: `{excluded_internal}`")
            lines.append("- Tip: add `include internal contacts` to include them.")
        lines.extend([
            "",
            "| Lead ID | Agent | Email | Priority | Status |",
            "|---|---|---|---|---|",
        ])
        for row in rows[:25]:
            lead_id = (row.get("Lead ID", "") or "").strip()
            agent = (row.get("Agent Name", "") or "").strip()
            email = (row.get("Email", "") or "").strip()
            priority = (row.get("Priority", "") or "").strip()
            status = (row.get("Status", "") or "").strip()
            lines.append(f"| {lead_id} | {agent} | {email} | {priority} | {status} |")

        if len(rows) > 25:
            lines.append("")
            lines.append(f"_Showing first 25 of {len(rows)} rows._")

        msg = "\n".join(lines)
        code_placeholder.markdown(msg)
        _push_live_chat("assistant", msg[:3500])
        chat_placeholder.markdown(_live_chat_markdown())
        return

    upper_prompt = normalized_prompt.upper()

    if upper_prompt in ("LAST_RESULT", "RESULT_LAST", "LAST"):
        full_terminal_log += "\n >_ UNIVERSAL RESULT RECALL"
        preview_placeholder.code(full_terminal_log, language="bash")
        code_placeholder.markdown(_last_result_markdown())
        _push_live_chat("assistant", "Displayed LAST_RESULT summary.")
        chat_placeholder.markdown(_live_chat_markdown())
        return

    if upper_prompt in ("KIMI_INTRO_BRIEF", "KIMI_INTRO", "INTRO_BRIEF", "HUDSON_INTRO"):
        full_terminal_log += "\n >_ KIMI INTRO BRIEF MODE"
        preview_placeholder.code(full_terminal_log, language="bash")
        brief = _kimi_intro_brief_markdown(trigger_phrase="Kimi give me the Hudson Brieaf overview")
        code_placeholder.markdown(brief)
        _push_live_chat("assistant", brief[:3500])
        chat_placeholder.markdown(_live_chat_markdown())
        return

    if (
        upper_prompt.startswith("FORM_CREATE")
        or upper_prompt.startswith("FORM_ADD")
        or upper_prompt.startswith("FORM_LIST")
        or upper_prompt.startswith("FORM_GET")
        or upper_prompt.startswith("FORM_RESPONSES")
        or upper_prompt.startswith("FORM_MOVE")
        or upper_prompt.startswith("FORM_LAST")
    ):
        full_terminal_log += "\n >_ GOOGLE FORMS CREATOR MODE"
        preview_placeholder.code(full_terminal_log, language="bash")
        result = run_google_forms_command(normalized_prompt)
        if not result.get("ok"):
            full_terminal_log += f"\n >_ ❌ {result.get('error')}"
            preview_placeholder.code(full_terminal_log, language="bash")
            _push_live_chat("system", f"Form command error: {result.get('error')}")
            chat_placeholder.markdown(_live_chat_markdown())
            return

        mode = result.get("mode")
        if mode == "create":
            _save_last_form(
                form_id=result.get("form_id"),
                edit_url=result.get("edit_url"),
                responder_url=result.get("responder_url"),
                title=result.get("title", ""),
            )
            full_terminal_log += (
                f"\n >_ ✅ FORM CREATED"
                f"\n >_ TITLE: {result.get('title')}"
                f"\n >_ FORM_ID: {result.get('form_id')}"
                f"\n >_ EDIT_URL: {result.get('edit_url')}"
                f"\n >_ RESPONDER_URL: {result.get('responder_url')}"
                f"\n >_ LINK ALSO POSTED BELOW IN LAST CREATED FORM"
            )
            code_placeholder.markdown(
                "### ✅ Form Created\n"
                f"- **Form ID:** `{result.get('form_id')}`\n"
                f"- **Edit URL:** {result.get('edit_url')}\n"
                f"- **Responder URL:** {result.get('responder_url')}\n\n"
                f"{_last_form_markdown()}"
            )
            _push_live_chat("assistant", f"Created form '{result.get('title')}' ({result.get('form_id')}).")
        elif mode == "add":
            q_type = "MCQ" if result.get("options") else "TEXT"
            req = "required" if result.get("required") else "optional"
            full_terminal_log += (
                f"\n >_ ✅ QUESTION ADDED"
                f"\n >_ FORM_ID: {result.get('form_id')}"
                f"\n >_ TYPE: {q_type}"
                f"\n >_ MODE: {req}"
                f"\n >_ TEXT: {result.get('question')}"
            )
        elif mode == "list":
            items = result.get("items", [])
            full_terminal_log += f"\n >_ ✅ LISTED {len(items)} FORMS"
            for idx, item in enumerate(items[:20], start=1):
                full_terminal_log += (
                    f"\n   {idx:02d}. {item.get('name', 'Untitled')} ({item.get('id', '-')})"
                    f"\n       {item.get('webViewLink', '')}"
                )
        elif mode == "get":
            details = result.get("details", {})
            info = details.get("info", {})
            items = details.get("items", [])
            full_terminal_log += (
                f"\n >_ ✅ FORM DETAILS"
                f"\n >_ FORM_ID: {result.get('form_id')}"
                f"\n >_ TITLE: {info.get('title', '')}"
                f"\n >_ DESCRIPTION: {info.get('description', '')}"
                f"\n >_ ITEM_COUNT: {len(items)}"
            )
        elif mode == "responses":
            full_terminal_log += (
                f"\n >_ ✅ RESPONSES"
                f"\n >_ FORM_ID: {result.get('form_id')}"
                f"\n >_ COUNT: {result.get('count', 0)}"
            )
        elif mode == "move":
            full_terminal_log += (
                f"\n >_ {'✅' if result.get('ok') else '❌'} MOVE"
                f"\n >_ FORM_ID: {result.get('form_id')}"
                f"\n >_ FOLDER: {result.get('folder')}"
            )
        elif mode == "last":
            last = result.get("last_form", {})
            full_terminal_log += (
                f"\n >_ ✅ LAST FORM"
                f"\n >_ FORM_ID: {last.get('form_id', '')}"
                f"\n >_ EDIT_URL: {last.get('edit_url', '')}"
                f"\n >_ RESPONDER_URL: {last.get('responder_url', '')}"
            )
            code_placeholder.markdown(_last_form_markdown())
            _push_live_chat("assistant", f"Displayed last form: {last.get('form_id', '')}.")

        preview_placeholder.code(full_terminal_log, language="bash")
        chat_placeholder.markdown(_live_chat_markdown())
        return

    # Read-only direct file flow:
    # If user provides a file path in read-only mode, bypass agent planning/tools drift.
    direct_path = _extract_path_from_text(normalized_prompt)
    if read_only_mode and not direct_path and natural_read_request:
        inferred = _resolve_latest_p1_draft_path(st.session_state.project_root)
        if inferred:
            direct_path = inferred
            full_terminal_log += f"\n >_ INFERRED DRAFT FILE: {direct_path}"
            preview_placeholder.code(full_terminal_log, language="bash")
    if read_only_mode and direct_path:
        full_terminal_log += "\n >_ READ-ONLY DIRECT FILE MODE"
        read_result = _safe_read_local_file(direct_path, st.session_state.project_root)
        if read_result.get("ok"):
            content = read_result.get("content", "")
            extracted = _extract_requested_section(content, normalized_prompt)
            if extracted:
                content = extracted
            result = f"📄 File Read: {read_result.get('path')}\n\n{content}"
            full_terminal_log += "\n >_ 📄 Read complete (direct mode)."
            full_code_log = f"\n# --- TOOL: read_file (direct) ---\n# Path: {direct_path}\n# Result:\n{result[:4000]}\n\n"
            code_placeholder.code(full_code_log, language="markdown")
            _push_live_chat("assistant", f"[read_file-direct] {direct_path}\n{result[:1200]}")
        else:
            err = f"❌ File read failed: {read_result.get('error')}"
            full_terminal_log += f"\n >_ {err}"
            _push_live_chat("system", err)

        preview_placeholder.code(full_terminal_log, language="bash")
        chat_placeholder.markdown(_live_chat_markdown())
        return

    if upper_prompt.startswith("FORM_IMPORT_MD_PREVIEW"):
        raw = normalized_prompt[len("FORM_IMPORT_MD_PREVIEW"):].strip()
        if not raw:
            full_terminal_log += (
                "\n >_ ❌ Missing path.\n >_ Usage: FORM_IMPORT_MD_PREVIEW /path/to/file.md"
            )
            preview_placeholder.code(full_terminal_log, language="bash")
            return

        full_path = raw
        if not os.path.isabs(full_path):
            full_path = os.path.abspath(os.path.join(os.getcwd(), full_path))
        if not os.path.exists(full_path):
            full_terminal_log += f"\n >_ ❌ Markdown file not found: {full_path}"
            preview_placeholder.code(full_terminal_log, language="bash")
            return

        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            md_text = f.read()
        questions = parse_markdown_questions(md_text)
        if not questions:
            full_terminal_log += "\n >_ ❌ Preview found 0 questions after filtering."
            preview_placeholder.code(full_terminal_log, language="bash")
            return

        full_terminal_log += (
            f"\n >_ PREVIEW MODE"
            f"\n >_ SOURCE: {full_path}"
            f"\n >_ PARSED_QUESTIONS: {len(questions)}"
            "\n >_ Showing first 25:"
        )
        for idx, q in enumerate(questions[:25], start=1):
            q_type = "MCQ" if q["options"] else "TEXT"
            req = "required" if q["required"] else "optional"
            full_terminal_log += f"\n   {idx:02d}. [{q_type}/{req}] {q['text']}"
            if q["options"]:
                full_terminal_log += f"\n       options: {', '.join(q['options'])}"
        preview_placeholder.code(full_terminal_log, language="bash")
        return

    if upper_prompt.startswith("FORM_IMPORT_MD"):
        raw = normalized_prompt[len("FORM_IMPORT_MD"):].strip()
        if not raw:
            full_terminal_log += "\n >_ ❌ Missing path.\n >_ Usage: FORM_IMPORT_MD /path/to/file.md"
            preview_placeholder.code(full_terminal_log, language="bash")
            return

        full_terminal_log += f"\n >_ LOCAL FORM IMPORT MODE\n >_ SOURCE: {raw}"
        preview_placeholder.code(full_terminal_log, language="bash")

        result = import_markdown_to_google_form(raw)
        if not result.get("ok"):
            full_terminal_log += f"\n >_ ❌ Import failed: {result.get('error')}"
            preview_placeholder.code(full_terminal_log, language="bash")
            _push_live_chat("system", f"FORM_IMPORT_MD failed: {result.get('error')}")
            chat_placeholder.markdown(_live_chat_markdown())
            return

        full_terminal_log += (
            f"\n >_ ✅ FORM CREATED"
            f"\n >_ FORM_ID: {result['form_id']}"
            f"\n >_ QUESTIONS_ADDED: {result['question_count']}"
            f"\n >_ EDIT_URL: {result['edit_url']}"
            f"\n >_ RESPONDER_URL: {result['responder_url']}"
            f"\n >_ FOLDER_MOVE: {result['move_note']}"
            f"\n >_ SOURCE_FILE: {result['source_path']}"
            f"\n >_ LINK ALSO POSTED BELOW IN LAST CREATED FORM"
        )
        preview_placeholder.code(full_terminal_log, language="bash")
        _save_last_form(
            form_id=result.get("form_id"),
            edit_url=result.get("edit_url"),
            responder_url=result.get("responder_url"),
            title="Imported Markdown Form",
        )
        code_placeholder.markdown(
            "### ✅ Google Form Imported\n"
            f"- **Form ID:** `{result['form_id']}`\n"
            f"- **Questions Added:** `{result['question_count']}`\n"
            f"- **Edit URL:** {result['edit_url']}\n"
            f"- **Responder URL:** {result['responder_url']}\n"
            f"- **Folder Move:** `{result['move_note']}`\n"
            f"- **Source:** `{result['source_path']}`\n\n"
            f"{_last_form_markdown()}"
        )
        _push_live_chat("assistant", f"Imported markdown and created form {result['form_id']} with {result['question_count']} questions.")
        chat_placeholder.markdown(_live_chat_markdown())
        return
    
    tasks = []

    # --- DIRECT KIMI ROUTING (TOOL-ENABLED) ---
    if use_kimi and KIMI_AVAILABLE:
        full_terminal_log += " >_ ROUTING TO OPENCLAW KIMI AGENT (DIRECT TOOL MODE)...\n"
        preview_placeholder.code(full_terminal_log, language="bash")
        tasks = [{"id": "1", "task": prompt, "assigned": "researcher-kimi"}]
        full_terminal_log += "\n >_ DIRECT MODE: 1 TASK CREATED (RESEARCHER WITH TOOLS)."
        preview_placeholder.code(full_terminal_log, language="bash")
    else:
        # Standard Mission Control Logic
        full_terminal_log += " >_ ORCHESTRATING..."
        preview_placeholder.code(full_terminal_log, language="bash")
        
        # Get Plan from Kimi
        with open(".mission_control/plans/plan_w_kimi.md", "r") as f: orch_sys = f.read()
        raw_plan = mission_control.run_kimi(prompt, orch_sys)
        
        # Try JSON parsing first
        try:
            clean_json = raw_plan.replace("```json", "").replace("```", "").strip()
            tasks = json.loads(clean_json)
        except:
            full_terminal_log += "\n >_ JSON PARSE FAILED. USING UNIVERSAL REGEX PARSER..."
            regex_tasks = re.findall(r"^\d+\.\s+(.*)", raw_plan, re.MULTILINE)
            if regex_tasks:
                tasks = [{"id": str(i+1), "task": t.replace('*', '').strip(), "assigned": "builder-kimi"} for i, t in enumerate(regex_tasks)]
            else:
                tasks = mission_control.parse_markdown_plan(raw_plan)
        
        full_terminal_log += f"\n >_ PLAN LOADED: {len(tasks)} TASKS DETECTED."
        preview_placeholder.code(full_terminal_log, language="bash")
    
    # Load all agent systems
    try:
        with open(".mission_control/team/builder-kimi.md", "r") as f: builder_sys = f.read()
        with open(".mission_control/team/validator-kimi.md", "r") as f: validator_sys = f.read()
        with open(".mission_control/team/sanitizer-kimi.md", "r") as f: sanitizer_sys = f.read()
        with open(".mission_control/team/researcher-kimi.md", "r") as f: researcher_sys = f.read()
    except FileNotFoundError as e:
        preview_placeholder.error(f"❌ Error: Could not find agent files: {e}")
        return
    
    full_code_log = ""
    mission_hard_stop = False
    
    for task in tasks:
        task_hard_stop = False
        # Determine which agent to use
        assigned_agent = task.get('assigned', 'builder-kimi')
        task_lower = task['task'].lower()
        
        # Check for tool-related keywords
        tool_keywords = ['web_search', 'analyze_screen', 'remember', 'recall', 'validate_command', 'read_file',
                        'screen', 'vision', 'memory', 'search the web', 'look at the screen',
                        'read file', 'file content', 'extract section', 'draft #',
                        'generate_image', 'visualize', 'image', 'nano banana',
                        'safe_execute', 'run command', 'google forms', 'form', 'hudson onboarding']
        uses_tools = any(keyword in task_lower for keyword in tool_keywords)
        
        # --- SMART ROUTING SYSTEM ---
        # Priority 1: Check for explicit Agent Names
        if assigned_agent == 'researcher-kimi':
            agent_name = "🔮 Direct Robby (Researcher + Tools)"
            agent_sys = researcher_sys
        elif 'builder' in task_lower:
            agent_name = "🔨 Builder (Explicit)"
            agent_sys = builder_sys
        elif 'sanitizer' in task_lower:
            agent_name = "🛡️ Sanitizer (Explicit)"
            agent_sys = sanitizer_sys
        elif 'researcher' in task_lower:
            agent_name = "🔍 Researcher (Explicit)"
            agent_sys = researcher_sys
        elif 'validator' in task_lower:
            agent_name = "✅ Validator (Explicit)"
            agent_sys = validator_sys
            
        # Priority 2: Keyword & Capability Matching
        elif 'audit' in task_lower or 'sanitize' in task_lower or 'security' in task_lower:
            agent_name = "🛡️ Sanitizer (Keyword)"
            agent_sys = sanitizer_sys
        elif 'research' in task_lower or 'search' in task_lower or uses_tools:
            agent_name = "🔍 Researcher (Keyword)"
            agent_sys = researcher_sys
        elif 'test' in task_lower or 'validate' in task_lower or 'verify' in task_lower:
            agent_name = "✅ Validator (Keyword)"
            agent_sys = validator_sys
        else:
            agent_name = "🔨 Builder (Default)"
            agent_sys = builder_sys
        
        # Update Status
        full_terminal_log += f"\n >_ EXECUTING: {task['task']}...\n >_ AGENT: {agent_name}"
        preview_placeholder.code(full_terminal_log, language="bash")
        
        # Run Agent
        build_result = mission_control.run_kimi(f"Do this task: {task['task']}", agent_sys)

        # Always surface plain-text agent replies (especially for direct chat).
        # Previously only TOOL:/FILE: outputs were rendered, making normal chat
        # look like a no-op in the dashboard.
        if build_result and "TOOL:" not in build_result and "FILE:" not in build_result:
            if read_only_mode:
                full_terminal_log += "\n >_ 🛑 READ-ONLY: ignored freeform agent response."
                preview_placeholder.code(full_terminal_log, language="bash")
            else:
                full_terminal_log += "\n >_ 💬 AGENT RESPONSE RECEIVED"
                preview_placeholder.code(full_terminal_log, language="bash")
                code_placeholder.markdown(
                    "### 💬 Robby Reply\n"
                    f"{build_result}"
                )
                _push_live_chat("assistant", build_result)
                chat_placeholder.markdown(_live_chat_markdown())

        # DEBUG: Show what Kimi actually said
        # full_terminal_log += f"\n >_ [DEBUG RAW]: {build_result[:200]}..." # Increased len
        # preview_placeholder.code(full_terminal_log, language="bash")

        
        # --- X-AGENT TOOL EXECUTION ---
        # DEBUG Check
        if not mission_control.X_AGENT_AVAILABLE:
             full_terminal_log += "\n >_ [DEBUG]: X_AGENT_TOOLS NOT AVAILABLE."
             preview_placeholder.code(full_terminal_log, language="bash")

        if mission_control.X_AGENT_AVAILABLE and "TOOL:" in build_result:
            lines = build_result.split('\n')
            i = 0
            while i < len(lines):
                # Robust Parser: Remove markdown bullets, bolding, backticks
                clean_line = lines[i].strip().replace('*', '').replace('-', '').replace('>', '').replace('`', '').strip()
                
                if clean_line.startswith("TOOL:"):
                    tool_name = clean_line.split("TOOL:")[1].strip()
                    full_terminal_log += f"\n >_ 🔧 EXECUTING TOOL: {tool_name}"
                    preview_placeholder.code(full_terminal_log, language="bash")


                    
                    # Extract parameters
                    params = {}
                    i += 1
                    while i < len(lines):
                        # Check if next line is a new tool header
                        # Robust Parser: Remove markdown bullets, bolding, backticks
                        next_clean = lines[i].strip().replace('*', '').replace('-', '').replace('>', '').replace('`', '').strip()
                        if next_clean.startswith("TOOL:"):
                            i -= 1 # Backtrack so main loop catches it
                            break
                        
                        # Parse Parameters (Key: Value)
                        # We allow keys that are uppercase or capitalized
                        current_line = lines[i].strip().replace('*', '').replace('-', '').replace('>', '').replace('`', '').strip()
                        if ":" in current_line:
                            key_part, value_part = current_line.split(":", 1)
                            key_clean = key_part.strip().upper() # Normalize to UPPER
                            
                            # Simple heuristic: Valid keys usually default to UPPERCASE in our protocol
                            # or at least Title Case. Avoid capturing random sentences.
                            if len(key_clean) < 20 and " " not in key_clean.strip():
                                params[key_clean.lower()] = value_part.strip()
                        i += 1

                    # Policy gate: restrict tool usage to user intent.
                    screen_question = params.get('question', '')
                    screen_path = _extract_path_from_text(screen_question)
                    screen_is_file_request = any(
                        token in screen_question.lower()
                        for token in ["file", "content", "extract", "section", "draft", "starts with", "ends before", "paste"]
                    )

                    if read_only_mode and tool_name != "read_file":
                        # In read-only mode, allow analyze_screen only as a transparent conversion
                        # to local file read when it's clearly a file-content request.
                        if tool_name == "analyze_screen" and screen_path and screen_is_file_request:
                            full_terminal_log += "\n >_ 🔁 READ-ONLY: converting analyze_screen -> read_file"
                            tool_name = "read_file"
                            params["path"] = screen_path
                            params["question"] = screen_question
                        else:
                            block = f"🛑 READ-ONLY BLOCKED TOOL: {tool_name}"
                            full_terminal_log += f"\n >_ {block}"
                            full_code_log += f"\n# --- TOOL BLOCKED ---\n# Reason: read-only mode\n# Tool: {tool_name}\n\n"
                            _push_live_chat("system", block)
                            preview_placeholder.code(full_terminal_log, language="bash")
                            code_placeholder.code(full_code_log, language="python")
                            chat_placeholder.markdown(_live_chat_markdown())
                            continue

                    if strict_task_mode and tool_name in ("web_search", "analyze_screen", "remember", "recall"):
                        if not _prompt_allows_tool(prompt, tool_name):
                            # If analyze_screen is being misused for file extraction, convert to read_file.
                            if tool_name == "analyze_screen" and screen_path and screen_is_file_request:
                                full_terminal_log += "\n >_ 🔁 STRICT MODE: converting analyze_screen -> read_file"
                                tool_name = "read_file"
                                params["path"] = screen_path
                                params["question"] = screen_question
                            else:
                                block = f"🛡️ STRICT MODE BLOCKED TOOL: {tool_name} (not explicitly requested)"
                                full_terminal_log += f"\n >_ {block}"
                                full_code_log += f"\n# --- TOOL BLOCKED ---\n# Reason: strict mode\n# Tool: {tool_name}\n\n"
                                _push_live_chat("system", block)
                                preview_placeholder.code(full_terminal_log, language="bash")
                                code_placeholder.code(full_code_log, language="python")
                                chat_placeholder.markdown(_live_chat_markdown())
                                continue
                    
                    # Execute the tool
                    try:
                        if tool_name == "web_search":
                            query = params.get('query', '')
                            if not query:
                                # import re (Removed)
                                match = re.search(r'QUERY:\s*(.+)', build_result, re.IGNORECASE)
                                if match:
                                    query = match.group(1).strip()
                            
                            result = mission_control.web_search(query)
                            st.session_state.last_web_search_result = result or ""
                            full_terminal_log += f"\n >_ 📡 Search completed"
                            full_code_log += f"\n# --- TOOL: web_search ---\n# Query: {params.get('query', '')}\n# Result:\n{result[:2000]}...\n\n"
                            formatted = _format_report_markdown(result[:7000])
                            _push_live_chat("assistant", f"[web_search] {query}\n{formatted[:3500]}")
                        
                        elif tool_name == "read_file":
                            path_value = params.get('path', params.get('file', params.get('filename', '')))
                            question = params.get('question', '')
                            if not path_value:
                                match = re.search(r'PATH:\s*(.+)', build_result, re.IGNORECASE)
                                if match:
                                    path_value = match.group(1).strip()
                            if not path_value:
                                path_value = _extract_path_from_text(build_result)

                            read_result = _safe_read_local_file(path_value, st.session_state.project_root)
                            if read_result.get("ok"):
                                content = read_result.get("content", "")
                                extracted = _extract_requested_section(content, question)
                                if extracted:
                                    content = extracted
                                result = f"📄 File Read: {read_result.get('path')}\n\n{content}"
                                full_terminal_log += f"\n >_ 📄 Local file read completed"
                                if read_only_mode:
                                    full_terminal_log += "\n >_ 🛑 READ-ONLY HARD STOP: stopping after read result."
                                    task_hard_stop = True
                                    mission_hard_stop = True
                            else:
                                result = f"❌ File read failed: {read_result.get('error')}"
                                full_terminal_log += f"\n >_ {result}"

                            full_code_log += f"\n# --- TOOL: read_file ---\n# Path: {path_value}\n# Question: {question}\n# Result:\n{result[:4000]}\n\n"
                            _push_live_chat("assistant", f"[read_file] {path_value}\n{result[:1200]}")

                        elif tool_name == "analyze_screen":
                            question = params.get('question', '')
                            requested_path = _extract_path_from_text(question)
                            asks_file_content = any(
                                token in question.lower()
                                for token in ["file", "content", "extract", "section", "draft", "starts with", "ends before", "paste"]
                            )

                            # Smart fallback: if the prompt is clearly about local file content,
                            # do a safe local file read instead of OCR/screen analysis.
                            if requested_path and asks_file_content:
                                read_result = _safe_read_local_file(requested_path, st.session_state.project_root)
                                if read_result.get("ok"):
                                    content = read_result.get("content", "")
                                    extracted = _extract_requested_section(content, question)
                                    if extracted:
                                        content = extracted
                                    result = f"📄 File Read (analyze_screen fallback): {read_result.get('path')}\n\n{content}"
                                    full_terminal_log += f"\n >_ 📄 Local file read used instead of screen OCR"
                                else:
                                    result = mission_control.analyze_screen(question)
                                    full_terminal_log += f"\n >_ ⚠️ Local file read failed ({read_result.get('error')}); used screen analysis"
                            else:
                                result = mission_control.analyze_screen(question)
                                full_terminal_log += f"\n >_ 👁️ Screen analyzed"

                            full_code_log += f"\n# --- TOOL: analyze_screen ---\n# Question: {question}\n# Result:\n{result[:2000]}...\n\n"
                            _push_live_chat("assistant", f"[analyze_screen]\n{result[:500]}")
                        
                        elif tool_name == "remember":
                            result = mission_control.remember(params.get('text', ''), params.get('category', 'General'))
                            full_terminal_log += f"\n >_ 💾 {result}"
                            full_code_log += f"\n# --- TOOL: remember ---\n{result}\n\n"
                            _push_live_chat("assistant", f"[remember] {result}")
                        
                        elif tool_name == "recall":
                            result = mission_control.recall(params.get('query', ''))
                            full_terminal_log += f"\n >_ 🧠 Memory recalled"
                            full_code_log += f"\n# --- TOOL: recall ---\n{result[:2000]}...\n\n"
                            _push_live_chat("assistant", f"[recall]\n{result[:500]}")
                        
                        elif tool_name == "validate_command":
                            cmd = params.get('command', '')
                            authorized, message = mission_control.x_agent_guard.validate(cmd)
                            status = "✅ SAFE" if authorized else "🛡️ BLOCKED"
                            full_terminal_log += f"\n >_ {status}: {cmd}\n >_    {message}"
                            full_code_log += f"\n# --- TOOL: validate_command ---\n# Command: {cmd}\n# Status: {'AUTHORIZED' if authorized else 'BLOCKED'}\n# Message: {message}\n\n"
                        
                        elif tool_name == "safe_execute":
                            cmd = params.get('command', '')
                            if not cmd:
                                match = re.search(r'COMMAND:\s*(.+)', build_result, re.IGNORECASE)
                                if match:
                                    cmd = match.group(1).strip()
                            result = mission_control.safe_execute(cmd)
                            full_terminal_log += f"\n >_ 🖥️ Command Executed via Guard\n >_    {cmd}"
                            full_code_log += f"\n# --- TOOL: safe_execute ---\n# Command: {cmd}\n# Result:\n{result[:4000]}\n\n"
                            _push_live_chat("assistant", f"[safe_execute] {cmd}\n{result[:500]}")
                        
                        elif tool_name == "generate_image":
                            import time
                            # DEBUG: Log what we found
                            # full_terminal_log += f"\n >_ [DEBUG PARAMS]: {params}"
                            # preview_placeholder.code(full_terminal_log, language="bash")
                            
                            prompt = params.get('prompt', '')
                            
                            # Fallback: Regex extraction if parser failed
                            if not prompt:
                                # import re (Removed to fix UnboundLocalError)
                                match = re.search(r'PROMPT:\s*(.+)', build_result, re.IGNORECASE)
                                if match:
                                    prompt = match.group(1).strip()
                                    # full_terminal_log += f"\n >_ [REGEX RESCUE]: Found prompt: {prompt[:50]}..."

                            
                            filename = params.get('filename', f"image_{int(time.time())}.png")
                            
                            # Regex fallback for filename
                            if "image_" in filename: # Default was used
                                match_f = re.search(r'FILENAME:\s*(.+)', build_result, re.IGNORECASE)
                                if match_f:
                                    filename = match_f.group(1).strip()
                            
                            # Sanitize filename
                            filename = filename.replace('"', '').replace("'", "")
                            
                            aspect = params.get('aspect', params.get('aspect_ratio', '1:1')).strip()
                            
                            result = mission_control.generate_image(prompt, filename, aspect)
                            full_terminal_log += f"\n >_ 🎨 {result} ({aspect})"
                            full_code_log += f"\n# --- TOOL: generate_image ---\n# Prompt: {prompt}\n# File: {filename}\n# Aspect: {aspect}\n# Result: {result}\n\n"
                            _push_live_chat("assistant", f"[generate_image] {prompt}\n{result[:300]}")
                            
                            # Auto-display the image (using dedicated placeholder)
                            if "Success" in result:
                                # We now support both 2K and 4K
                                has_2k = "_2k" in result
                                has_4k = "_4k" in result
                                
                                # Use a container to hold everything
                                with image_placeholder.container():
                                    # 1. Show the highest quality one by default (or the original)
                                    display_file = filename
                                    if has_4k:
                                        # Extract 4k filename
                                        try: display_file = result.split("4K: Success: Created ")[1].split(" ")[0]
                                        except: pass
                                    elif has_2k:
                                        try: display_file = result.split("2K: Success: Created ")[1].split(" ")[0]
                                        except: pass
                                    
                                    st.image(display_file, caption=f"ULTRA-HD PREVIEW: {prompt}", use_container_width=True)
                                    _save_last_result(
                                        kind="image",
                                        title=prompt[:120] if prompt else os.path.basename(display_file),
                                        path=os.path.abspath(display_file),
                                    )
                                    
                                    # 2. Add Download Buttons side-by-side
                                    dl_cols = st.columns(3)
                                    
                                    # Original
                                    with dl_cols[0]:
                                        with open(filename, "rb") as f:
                                            st.download_button("📸 Original", f, filename, "image/png", key=f"orig_{filename}")
                                    
                                    # 2K Option
                                    if has_2k:
                                        try:
                                            file_2k = result.split("2K: Success: Created ")[1].split(" ")[0]
                                            size_2k = result.split("2K: Success: Created ")[1].split("(")[1].split(")")[0]
                                            with dl_cols[1]:
                                                with open(file_2k, "rb") as f:
                                                    st.download_button(f"🥈 2K Res ({size_2k})", f, file_2k, "image/png", key=f"2k_{file_2k}")
                                        except: pass
                                        
                                    # 4K Option
                                    if has_4k:
                                        try:
                                            file_4k = result.split("4K: Success: Created ")[1].split(" ")[0]
                                            size_4k = result.split("4K: Success: Created ")[1].split("(")[1].split(")")[0]
                                            with dl_cols[2]:
                                                with open(file_4k, "rb") as f:
                                                    st.download_button(f"🥇 4K Res ({size_4k})", f, file_4k, "image/png", key=f"4k_{file_4k}")
                                        except: pass

                                    full_terminal_log += f"\n >_ ✨ MULTI-HD GENERATED: Original, 2K, and 4K ready."

                        elif tool_name == "upscale_image":
                            filename = params.get('file', params.get('filename', '')).strip()
                            result = mission_control.upscale_image(filename)
                            full_terminal_log += f"\n >_ 🚀 Upscaled: {result}"
                            full_code_log += f"\n# --- TOOL: upscale_image ---\n# File: {filename}\n# Result: {result}\n\n"
                            _push_live_chat("assistant", f"[upscale_image] {filename}\n{result[:300]}")
                            
                            if "Success" in result and "Created " in result:
                                try:
                                    # Extract new filename: "Success: Created foo_upscaled.png (...)"
                                    new_file = result.split("Created ")[1].split(" ")[0]
                                    image_placeholder.image(new_file, caption=f"Upscaled x4: {filename}")
                                    _save_last_result(
                                        kind="image",
                                        title=f"Upscaled {os.path.basename(filename)}",
                                        path=os.path.abspath(new_file),
                                    )
                                except:
                                    pass
                    
                    except Exception as e:
                        full_terminal_log += f"\n >_ ❌ Tool Error: {e}"
                    
                    preview_placeholder.code(full_terminal_log, language="bash")
                    code_placeholder.code(full_code_log, language="python")
                    chat_placeholder.markdown(_live_chat_markdown())
                    if task_hard_stop:
                        break
                    continue
                i += 1

            if task_hard_stop:
                full_terminal_log += "\n >_ READ-ONLY TASK COMPLETE."
                preview_placeholder.code(full_terminal_log, language="bash")
                if mission_hard_stop:
                    break
                continue
        
        # --- FILE CREATION (For Builder tasks) ---
        if read_only_mode and "FILE:" in build_result:
            full_terminal_log += "\n >_ 🛑 READ-ONLY MODE: ignoring FILE artifacts."
            preview_placeholder.code(full_terminal_log, language="bash")
        elif "FILE:" in build_result:
            lines = build_result.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.strip().startswith("FILE:"):
                    filename = line.split("FILE:")[1].strip()
                    full_terminal_log += f"\n >_ 📄 Found File Target: {filename}"
                    preview_placeholder.code(full_terminal_log, language="bash")
                    
                    # --- IMPROVED CONTENT EXTRACTION ---
                    # Strategy: Collect everything after FILE: until we hit the NEXT FILE: or end
                    code_content = []
                    in_code_block = False
                    found_code_block = False
                    
                    for j in range(i + 1, len(lines)):
                        next_line = lines[j]
                        
                        # Stop if we hit another FILE: directive
                        if next_line.strip().startswith("FILE:"):
                            break
                        
                        # Handle code blocks specially
                        if "```" in next_line:
                            if not in_code_block:
                                in_code_block = True
                                found_code_block = True
                                continue  # Skip the opening ```
                            else:
                                in_code_block = False
                                continue  # Skip the closing ```
                        
                        # Collect content
                        if in_code_block:
                            code_content.append(next_line)
                        elif not found_code_block:
                            # No code block yet, collect raw content (but skip empty lines at start)
                            if code_content or next_line.strip():
                                code_content.append(next_line)
                    
                    extracted_code = "\n".join(code_content).strip()
                    
                    # DEBUG: Show what we extracted
                    full_terminal_log += f"\n >_ [DEBUG] Extracted {len(extracted_code)} chars"
                    preview_placeholder.code(full_terminal_log, language="bash")
                    
                    if extracted_code:
                        # Update UI with the code we found
                        full_code_log += f"\n# --- TASK: {task['id']} ({filename}) ---\n{extracted_code}\n\n"
                        code_placeholder.code(full_code_log, language="python")

                        # Respect autosave toggle: preview only unless explicitly enabled
                        if not st.session_state.get("auto_save_agent_files", False):
                            full_terminal_log += "\n >_ 📝 AUTO-SAVE OFF: Artifact previewed only (not written)."
                            preview_placeholder.code(full_terminal_log, language="bash")
                            i += 1
                            continue
                        
                        # --- SANDBOX ENFORCEMENT ---
                        # Treat all Agent filenames as relative to the Project Root
                        # If a user provides an absolute path, we strip it or block it?
                        # For maximum flexibility + security, we resolve it relative to root.
                        
                        try:
                            # 1. Normalize
                            target_root = os.path.abspath(st.session_state.project_root)
                            target_root_real = _normalized_real_path(target_root)
                            
                            # If filename is absolute, try to make it relative to root, or just take basename
                            if os.path.isabs(filename):
                                candidate_path = filename
                            else:
                                # It's relative. Join with root.
                                candidate_path = os.path.join(target_root, filename)
                            
                            # 2. Final Check
                            safe_path = os.path.abspath(candidate_path)
                            safe_path_real = _normalized_real_path(safe_path)

                            if not _is_within_root(safe_path_real, target_root_real):
                                block_msg = f"🛡️ BLOCKED: Agent tried to write to {filename} (Outside Sandbox)"
                                full_terminal_log += f"\n >_ {block_msg}"
                                preview_placeholder.code(full_terminal_log, language="bash")
                                continue

                            # 3. Write
                            os.makedirs(os.path.dirname(safe_path_real), exist_ok=True)
                            with open(safe_path_real, "w") as f:
                                f.write(extracted_code)
                            
                            full_terminal_log += f"\n >_ SAVED FILE: {safe_path_real}"
                            kind = "markdown" if safe_path_real.lower().endswith(".md") else "code"
                            _save_last_result(
                                kind=kind,
                                title=os.path.basename(safe_path_real),
                                path=safe_path_real,
                            )

                            # Execute ONLY if it's a python script AND inside the root
                            if safe_path_real.endswith(".py"):
                                full_terminal_log += "\n >_ EXECUTING AGENT ARTIFACT..."
                                preview_placeholder.code(full_terminal_log, language="bash")
                                
                                try:
                                    # Run inside the project root directory
                                    result = subprocess.run([sys.executable, safe_path_real], 
                                                          cwd=target_root, # Run relative to project
                                                          capture_output=True, text=True, timeout=15)
                                    output = f"\n$ python {os.path.basename(safe_path_real)}\n" + (result.stdout if result.stdout else "")
                                    if result.stderr:
                                        output += f"\n[STDERR]\n{result.stderr}"
                                    full_terminal_log += output
                                    preview_placeholder.code(full_terminal_log, language="bash")
                                except Exception as e:
                                    full_terminal_log += f"\n >_ ❌ Execution Error: {e}"
                                    preview_placeholder.code(full_terminal_log, language="bash")
                            else:
                                full_terminal_log += "\n >_ (Skipping execution for non-Python file)"
                                preview_placeholder.code(full_terminal_log, language="bash")

                        except Exception as e_save:
                             full_terminal_log += f"\n >_ ❌ Save Error: {e_save}"
                i += 1  # Move to next line in while loop
    
    full_terminal_log += "\n >_ MISSION COMPLETE. SYSTEM READY."
    preview_placeholder.code(full_terminal_log, language="bash")

# --- TRIGGER ---
if mission_prompt:
    asyncio.run(run_visual_loop(mission_prompt))
