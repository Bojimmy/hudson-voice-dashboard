import os
import sys
import json
import asyncio
import subprocess  # <--- NEW: Needed for installing packages
import re  # <--- NEW: Needed for regex parsing
from openai import OpenAI
import google.genai as genai

# --- CONFIG ---
# Ensure these are set in your terminal: export MOONSHOT_API_KEY=...
# --- CONFIG ---
# Try to load from environment first, then fallback to secrets.toml
import toml

def load_secrets():
    secrets_path = "../.streamlit/secrets.toml" # One level up from gemini-voice-widget-2
    if os.path.exists(secrets_path):
        return toml.load(secrets_path)
    return {}

secrets = load_secrets()

KIMI_API_KEY = os.getenv("MOONSHOT_API_KEY") or secrets.get("MOONSHOT_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or secrets.get("GEMINI_API_KEY")

if not KIMI_API_KEY:
    print("❌ Error: MOONSHOT_API_KEY not found in environment or secrets.toml")
    sys.exit(1)

# Updated to .ai for global access (checked locally via curl already)
kimi_client = OpenAI(api_key=KIMI_API_KEY, base_url="https://api.moonshot.ai/v1")
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# --- X-AGENT TOOLS INTEGRATION ---
sys.path.insert(0, os.path.abspath(".."))  # Add parent directory to path
sys.path.append(os.path.join(os.path.abspath(".."), "skill-scanner"))  # Add skill-scanner explicitly
try:
    from x_agent_guard import XAgentGuard
    import x_security_server
    import importlib
    importlib.reload(x_security_server)
    
    from x_security_server import web_search, analyze_screen, remember, recall, generate_image, upscale_image
    x_agent_guard = XAgentGuard()
    X_AGENT_AVAILABLE = True
    print("✅ X-Agent Tools Loaded")
except ImportError as e:
    print(f"⚠️ X-Agent Tools not available: {e}")
    X_AGENT_AVAILABLE = False
    x_agent_guard = None

# --- 1. THE AUTO-MAGIC INSTALLER FUNCTION ---
def install_package(package_name):
    """Installs a pip package automatically."""
    print(f"📦 AUTO-MAGIC: Installing {package_name}...")
    try:
        # This runs 'pip install <package>' in your terminal for you
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        return "Success"
    except Exception as e:
        print(f"❌ Install Failed: {e}")
        return f"Failed: {e}"


def parse_markdown_plan(text):
    """
    Universal Parser: Tries multiple strategies to find tasks.
    """
    tasks = []
    print("   (Parsing Plan...)")
    
    # --- STRATEGY 1: The "Header" Format (#### Task 1: Title) ---
    header_matches = list(re.finditer(r"#{2,}\s*Task\s*(\d+)[:\.]?\s*(.*?)(?=\n#{2,}|\Z)", text, re.IGNORECASE | re.DOTALL))
    if header_matches:
        for match in header_matches:
            body = match.group(2)
            owner_match = re.search(r"\*\*Owner:?\*\*\s*(.*)", body, re.IGNORECASE)
            owner = owner_match.group(1).strip().strip('`') if owner_match else "builder-kimi"
            tasks.append({
                "id": match.group(1),
                "task": match.group(2).split('\n')[0].strip(),
                "assigned": owner
            })
        return tasks

    # --- STRATEGY 2: The "Bold List" Format (1. **Title**) ---
    list_matches = list(re.finditer(r"(\d+)\.\s+\*\*(.*?)\*\*(.*?)(?=\n\d+\.|\Z)", text, re.DOTALL))
    if list_matches:
        for match in list_matches:
            title = match.group(2).strip()
            owner_match = re.search(r"Owner:?\*?\*?\s*(.*)", match.group(3), re.IGNORECASE)
            owner = owner_match.group(1).strip().strip('`').strip('*') if owner_match else "builder-kimi"
            tasks.append({
                "id": match.group(1),
                "task": title,
                "assigned": owner
            })
        return tasks

    # --- STRATEGY 3: Fallback (Any numbered list in the Plan section) ---
    simple_matches = re.findall(r"^\d+\.\s+(.*)", text, re.MULTILINE)
    valid_matches = [m for m in simple_matches if len(m) > 10]

    if valid_matches:
        for i, line in enumerate(valid_matches):
            tasks.append({
                "id": str(i+1),
                "task": line.strip(),
                "assigned": "builder-kimi" 
            })
            
    return tasks

# --- 2. AI & VOICE FUNCTIONS ---
def run_kimi(prompt, system_prompt):
    """Sends a request to Kimi 2.5 with robust retry logic"""
    print(f"🤖 Kimi is thinking...")
    import time
    
    max_retries = 5
    base_delay = 2
    
    for attempt in range(max_retries):
        try:
            response = kimi_client.chat.completions.create(
                model="moonshot-v1-128k",
                messages=[
                    {"role": "system", "content": system_prompt + get_memory_context()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content
            
        except Exception as e:
            # Check for Rate Limit (429) or overloaded engine errors
            if "429" in str(e) or "overloaded" in str(e).lower():
                delay = base_delay * (2 ** attempt)  # Exponential backoff: 2, 4, 8, 16...
                print(f"⚠️ API Overloaded (429). Retrying in {delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
            else:
                # If it's another error, raise it immediately
                print(f"❌ Kimi API Error: {e}")
                raise e
                
    raise Exception("Max retries exceeded for Kimi API")

def get_memory_context():
    """Reads the persistent memory file and formats it for context injection."""
    memory_file = ".studio_memory.json"
    if os.path.exists(memory_file):
        try:
            with open(memory_file, "r") as f:
                memories = json.load(f)
            
            # Format the last 10 memories for context
            recent_memories = sorted(memories, key=lambda x: x['timestamp'], reverse=True)[:10]
            context_str = "\n\n=== PERSISTENT MEMORY CONTEXT ===\n"
            for mem in recent_memories:
                context_str += f"- [{mem['category']}] {mem['text']}\n"
            return context_str
        except Exception as e:
            print(f"⚠️ Memory Read Error: {e}")
            return ""
    return ""

async def speak_update(text):
    """Sends text to Gemini Live for voice output"""
    print(f"🎤 GEMINI SPEAKING: {text}")
    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"You are a Text-to-Speech engine. Repeat the following text EXACTLY as written, with no intro or pleasantries: {text}"
        )
        # Note: In a real app, you would stream audio bytes here.
        # For now, it prints what it WOULD say.
        print(f"🔊 (Audio Output): {response.text}") 
    except Exception as e:
        print(f"⚠️ Voice Error: {e}")

# --- 3. MAIN MISSION LOOP ---
async def main():
    user_request = sys.argv[1] if len(sys.argv) > 1 else "Status Check"
    
    # A. ORCHESTRATION
    print("🚀 Mission Control Launched.")
    try:
        orch_sys = open(".mission_control/plans/plan_w_kimi.md").read()
    except FileNotFoundError:
        print("❌ Error: Could not find .mission_control/plans/plan_w_kimi.md")
        return

    plan_json = run_kimi(user_request, orch_sys)
    
    try:
        # Clean up json markdown if Kimi adds backticks
        clean_json = plan_json.replace("```json", "").replace("```", "").strip()
        tasks = json.loads(clean_json)
    except:
        # Fallback to markdown parser
        print("⚠️ JSON parse failed, attempting Markdown Parse...")
        tasks = parse_markdown_plan(plan_json)

    if not tasks:
        print("❌ Could not parse tasks. Here is what Kimi said:")
        print(plan_json)
        return

    # B. EXECUTION LOOP
    try:
        builder_sys = open(".mission_control/team/builder-kimi.md").read()
        validator_sys = open(".mission_control/team/validator-kimi.md").read()
        sanitizer_sys = open(".mission_control/team/sanitizer-kimi.md").read()
        researcher_sys = open(".mission_control/team/researcher-kimi.md").read()
    except FileNotFoundError as e:
        print(f"❌ Error: Could not find agent files in .mission_control/team/: {e}")
        return

    for task in tasks:
        print(f"\nExample Task: {task['task']}")
        assigned_agent = task.get('assigned', 'builder-kimi')
        task_lower = task['task'].lower()
        
        # Check for tool-related keywords
        tool_keywords = ['web_search', 'analyze_screen', 'remember', 'recall', 'validate_command', 
                        'screen', 'vision', 'memory', 'search the web', 'look at the screen']
        uses_tools = any(keyword in task_lower for keyword in tool_keywords)
        
        # --- SMART ROUTING SYSTEM ---
        # Priority 1: Check for explicit Agent Names in the task
        if 'builder' in task_lower:
            print("🔨 Builder Working (Explicitly Requested)...")
            build_result = run_kimi(f"Do this task: {task['task']}", builder_sys)
        elif 'sanitizer' in task_lower:
            print("🛡️ Sanitizer Working (Explicitly Requested)...")
            build_result = run_kimi(f"Do this task: {task['task']}", sanitizer_sys)
        elif 'researcher' in task_lower:
            print("🔍 Researcher Working (Explicitly Requested)...")
            build_result = run_kimi(f"Do this task: {task['task']}", researcher_sys)
        elif 'validator' in task_lower:
            print("✅ Validator Working (Explicitly Requested)...")
            build_result = run_kimi(f"Do this task: {task['task']}", validator_sys)
            
        # Priority 2: Keyword & Capability Matching
        elif 'audit' in task_lower or 'sanitize' in task_lower or 'security' in task_lower:
            print("🛡️ Sanitizer Working (Keyword Match)...")
            build_result = run_kimi(f"Do this task: {task['task']}", sanitizer_sys)
        elif 'research' in task_lower or 'search' in task_lower or uses_tools:
            print("🔍 Researcher Working (Keyword Match)...")
            build_result = run_kimi(f"Do this task: {task['task']}", researcher_sys)
        elif 'test' in task_lower or 'validate' in task_lower or 'verify' in task_lower:
             print("✅ Validator Working (Keyword Match)...")
             build_result = run_kimi(f"Do this task: {task['task']}", validator_sys)
        else:
            print("🔨 Builder Working (Default)...")
            build_result = run_kimi(f"Do this task: {task['task']}", builder_sys)
        
        # >>>> THIS IS THE AUTO-MAGIC SECTION <<<<
        # It looks for lines like "INSTALL_DEPENDENCY: requests" inside Kimi's answer
        if "INSTALL_DEPENDENCY:" in build_result:
            for line in build_result.split('\n'):
                if "INSTALL_DEPENDENCY:" in line:
                    pkg = line.split(":")[1].strip()
                    install_package(pkg)
        
        # >>>> X-AGENT TOOL EXECUTION <<<<
        if X_AGENT_AVAILABLE and "TOOL:" in build_result:
            lines = build_result.split('\n')
            i = 0
            while i < len(lines):
                if lines[i].strip().startswith("TOOL:"):
                    tool_name = lines[i].split("TOOL:")[1].strip()
                    print(f"🔧 Executing Tool: {tool_name}")
                    
                    # Extract parameters from next lines
                    params = {}
                    i += 1
                    while i < len(lines) and not lines[i].strip().startswith("TOOL:"):
                        if ":" in lines[i] and lines[i].strip() and lines[i].strip()[0].isupper():
                            key, value = lines[i].split(":", 1)
                            params[key.strip().lower()] = value.strip()
                        i += 1
                    
                    # Execute the tool
                    try:
                        if tool_name == "web_search":
                            result = web_search(params.get('query', ''))
                            print(f"📡 Search Result:\n{result[:200]}...")
                        elif tool_name == "analyze_screen":
                            result = analyze_screen(params.get('question', ''))
                            print(f"👁️ Screen Analysis:\n{result[:200]}...")
                        elif tool_name == "remember":
                            result = remember(params.get('text', ''), params.get('category', 'General'))
                            print(f"💾 {result}")
                        elif tool_name == "recall":
                            result = recall(params.get('query', ''))
                            print(f"🧠 {result[:200]}...")
                        elif tool_name == "validate_command":
                            cmd = params.get('command', '')
                            authorized, message = x_agent_guard.validate(cmd)
                            status = "✅ SAFE" if authorized else "🛡️ BLOCKED"
                            print(f"{status}: {cmd}\n   {message}")
                        elif tool_name == "generate_image":
                            prompt = params.get('prompt', '')
                            filename = params.get('filename', f"image_{int(time.time())}.png")
                            result = generate_image(prompt, filename)
                            print(f"{result}")
                    except Exception as e:
                        print(f"❌ Tool Error: {e}")
                    continue
                i += 1
        
        # >>>> NEW: AUTO-FILE WRITER <<<<
        # Looks for:
        # FILE: script.py
        # ```python
        # ...
        # ```
        if "FILE:" in build_result:
            lines = build_result.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith("FILE:"):
                    filename = line.split("FILE:")[1].strip()
                    print(f"📄 Found File Target: {filename}")
                    
                    # Look for next code block
                    code_content = []
                    in_block = False
                    for j in range(i+1, len(lines)):
                        if "```" in lines[j]:
                            if in_block:
                                break # End of block
                            else:
                                in_block = True # Start of block
                                continue
                        if in_block:
                            code_content.append(lines[j])
                    
                    if code_content:
                        full_path = os.path.abspath(filename)
                        with open(full_path, "w") as f:
                            f.write("\n".join(code_content))
                        print(f"💾 Saved {len(code_content)} lines to {filename}")
        # >>>> END AUTO-FILE WRITER <<<<

        print(f"✅ Built: {build_result[:50]}...")

        # --- VALIDATOR STEP ---
        print("WT Validator Checking...")
        valid_result = run_kimi(f"Verify this work: {task['task']}\nBuilder Output: {build_result}", validator_sys)
        
        # --- VOICE BRIDGE ---
        if "VOICE_UPDATE:" in valid_result:
            voice_text = valid_result.split("VOICE_UPDATE:")[1].strip()
            await speak_update(voice_text)
        elif "FAILURE" in valid_result:
            await speak_update(f"Alert. Task failed validation. {valid_result}")
            break

if __name__ == "__main__":
    asyncio.run(main())