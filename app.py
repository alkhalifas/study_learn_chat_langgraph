import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import streamlit as st
import yaml
from dotenv import load_dotenv
from openai import OpenAI

# LangGraph (minimal usage to orchestrate lesson state & tools)
from langgraph.graph import StateGraph, START, END

# Local tool for slide export
from tools.slide_export import export_dmaic_to_pptx

# ----------------------
# Environment & Clients
# ----------------------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    st.warning("OPENAI_API_KEY not found in environment. Set it in a .env file.")
client = OpenAI(api_key=OPENAI_API_KEY)

# ----------------------
# UI Config
# ----------------------
st.set_page_config(page_title="Study & Learn Chat (LangGraph)", page_icon="🎓", layout="wide")

# ----------------------
# Constants & Helpers
# ----------------------
LESSONS_DIR = "lessons"
DEFAULT_MODEL = "gpt-4o-mini"  # closest available to "gpt 5 mini" in current SDKs
SYSTEM_PROMPT_BASE = (
    "You are a helpful, expert chat assistant. Keep answers practical and concise, "
    "and ask clarifying questions when needed. If the user requests learning a lesson, "
    "activate step-by-step tutoring. Avoid performing all steps at once; coach the user "
    "through each step with feedback and encouragement."
)

def ensure_session_defaults():
    if "model_name" not in st.session_state:
        st.session_state.model_name = DEFAULT_MODEL
    if "app_state" not in st.session_state:
        st.session_state.app_state = AppState(lessons_bank=load_lessons())

def get_current_model() -> str:
    return st.session_state.get("model_name", DEFAULT_MODEL)

def load_lessons() -> Dict[str, Dict[str, Any]]:
    lessons = {}
    if not os.path.isdir(LESSONS_DIR):
        return lessons
    for fname in os.listdir(LESSONS_DIR):
        if not fname.lower().endswith(('.yaml', '.yml')):
            continue
        path = os.path.join(LESSONS_DIR, fname)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and 'id' in data:
                    lessons[data['id']] = data
        except Exception as e:
            print(f"Failed to load {fname}: {e}")
    return lessons

def nlu_detect_lesson_request(text: str, lesson_titles: List[str]) -> Optional[str]:
    lowered = text.lower()
    triggers = ["teach me", "learn", "study"]
    if any(t in lowered for t in triggers):
        for lt in lesson_titles:
            if lt.lower() in lowered:
                return lt
    return None

# ----------------------
# Lesson State Dataclass
# ----------------------
@dataclass
class LessonState:
    active: bool = False
    lesson_id: Optional[str] = None
    current_step_idx: int = 0
    improvement_suggested: bool = False
    user_entries: Dict[int, str] = field(default_factory=dict)
    guidance: Dict[int, str] = field(default_factory=dict)
    completed: bool = False

# ----------------------
# LangGraph Orchestration
# ----------------------
@dataclass
class AppState:
    messages: List[Dict[str, str]] = field(default_factory=list)
    lesson_state: LessonState = field(default_factory=LessonState)
    lessons_bank: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    requested_tool: Optional[str] = None  # e.g., "export_dmaic"

def get_system_prompt(app_state: AppState) -> str:
    if app_state.lesson_state.active and app_state.lesson_state.lesson_id:
        lesson = app_state.lessons_bank.get(app_state.lesson_state.lesson_id, {})
        title = lesson.get("title", app_state.lesson_state.lesson_id)
        study_modifier = (
            f"Study & Learn mode is ACTIVE for lesson '{title}'. "
            f"Teach strictly step-by-step using the YAML steps below. "
            f"For each step: 1) ask user for their attempt/input, "
            f"2) provide targeted feedback and suggest one improvement, "
            f"3) if the user does not improve after that suggestion, proceed to the next step. "
            f"Do NOT reveal future steps early. When the final step completes, stop and await tool execution."
        )
        lesson_summary = []
        for i, step in enumerate(lesson.get("steps", []), start=1):
            step_name = step.get("name", f"Step {i}")
            goals = ", ".join(step.get("goals", [])[:3])
            lesson_summary.append(f"{i}. {step_name} — goals: {goals}")
        lesson_outline = "\n".join(lesson_summary)

        return (
            SYSTEM_PROMPT_BASE + "\n\n" +
            study_modifier + "\n\n" +
            f"Lesson outline (do not reveal more than current step):\n{lesson_outline}"
        )
    return SYSTEM_PROMPT_BASE

# ------------- LLM wrappers -------------
def model_stream_response(system_prompt: str, messages: List[Dict[str, str]], model: str):
    stream = client.chat.completions.create(
        model=model,
        stream=True,
        messages=[{"role": "system", "content": system_prompt}] + messages
    )
    for chunk in stream:
        if hasattr(chunk, "choices") and chunk.choices:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

# ------------- Graph Nodes -------------
def router_node(app_state: AppState) -> AppState:
    if not app_state.lessons_bank:
        app_state.lessons_bank = load_lessons()

    if app_state.messages and app_state.messages[-1]["role"] == "user":
        user_text = app_state.messages[-1]["content"]
        titles = [v.get("title", k) for k, v in app_state.lessons_bank.items()]
        hit = nlu_detect_lesson_request(user_text, titles)
        if hit:
            for lid, meta in app_state.lessons_bank.items():
                if meta.get("title", lid).lower() == hit.lower():
                    app_state.lesson_state = LessonState(active=True, lesson_id=lid, current_step_idx=0)
                    break
    return app_state

def lesson_node(app_state: AppState) -> AppState:
    ls = app_state.lesson_state
    if not ls.active or not ls.lesson_id:
        return app_state

    lesson = app_state.lessons_bank.get(ls.lesson_id, {})
    steps = lesson.get("steps", [])
    if ls.current_step_idx >= len(steps):
        ls.completed = True
        return app_state

    current_step = steps[ls.current_step_idx]
    step_name = current_step.get("name", f"Step {ls.current_step_idx+1}")
    goals = current_step.get("goals", [])
    best_practices = current_step.get("best_practices", [])
    prompts_for_user = current_step.get("prompts_for_user", [])

    system_prompt = get_system_prompt(app_state)
    coach_preamble = (
        f"You are coaching the user through step '{step_name}'.\n"
        f"Goals: {goals}\n"
        f"Best Practices: {best_practices}\n"
        f"Prompts to ask the user: {prompts_for_user}\n"
        f"If the user provided an attempt, give precise feedback and ONE suggested improvement.\n"
        f"If you already suggested an improvement and they didn't improve, proceed to the next step.\n"
        f"Keep messages concise and focused on this step only."
    )

    if app_state.messages and app_state.messages[-1]["role"] == "user":
        with st.chat_message("assistant"):
            placeholder = st.empty()
            accum = ""
            for token in model_stream_response(
                system_prompt,
                app_state.messages + [{"role": "assistant", "content": coach_preamble}],
                model=get_current_model()
            ):
                accum += token
                placeholder.markdown(accum)
        if accum.strip():
            app_state.messages.append({"role": "assistant", "content": accum})

        # step progression (one improvement cycle)
        user_input = app_state.messages[-2]["content"] if len(app_state.messages) >= 2 else ""
        prior_entry = ls.user_entries.get(ls.current_step_idx, "")
        if not ls.improvement_suggested:
            ls.user_entries[ls.current_step_idx] = user_input
            ls.improvement_suggested = True
        else:
            changed = len(user_input.strip()) > 0 and user_input.strip() != prior_entry.strip()
            ls.current_step_idx += 1
            ls.improvement_suggested = False

        if ls.current_step_idx >= len(steps):
            ls.completed = True

    return app_state

def normal_chat_node(app_state: AppState) -> AppState:
    if app_state.lesson_state.active and not app_state.lesson_state.completed:
        return app_state

    system_prompt = get_system_prompt(app_state)
    if app_state.messages and app_state.messages[-1]["role"] == "user":
        with st.chat_message("assistant"):
            placeholder = st.empty()
            accum = ""
            for token in model_stream_response(system_prompt, app_state.messages, model=get_current_model()):
                accum += token
                placeholder.markdown(accum)
        if accum.strip():
            app_state.messages.append({"role": "assistant", "content": accum})
    return app_state

def completion_node(app_state: AppState) -> AppState:
    ls = app_state.lesson_state
    if not ls.active or not ls.completed:
        return app_state

    lesson = app_state.lessons_bank.get(ls.lesson_id, {})
    lesson_id = lesson.get("id", ls.lesson_id)

    with st.chat_message("assistant"):
        if lesson_id == "dmaic":
            st.markdown("Lesson complete. Generating your DMAIC summary slides...")
            steps = lesson.get("steps", [])
            filled = []
            for idx, step in enumerate(steps):
                filled.append({
                    "step": step.get("name", f"Step {idx+1}"),
                    "user_input": ls.user_entries.get(idx, ""),
                    "goals": step.get("goals", []),
                    "best_practices": step.get("best_practices", []),
                })
            out_path = export_dmaic_to_pptx(filled)
            st.success("Slides ready.")
            with open(out_path, "rb") as f:
                st.download_button(
                    "Download DMAIC Slides (.pptx)",
                    data=f,
                    file_name=os.path.basename(out_path),
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            app_state.messages.append({"role": "assistant", "content": "✔️ DMAIC lesson complete. Slides generated and ready to download."})
        else:
            st.markdown("Lesson complete. No post-lesson artifact for this lesson.")
            app_state.messages.append({"role": "assistant", "content": "✔️ Lesson complete."})

    app_state.lesson_state = LessonState(active=False, lesson_id=None, current_step_idx=0, completed=False)
    return app_state

# Build the graph
graph = StateGraph(AppState)
graph.add_node("router", router_node)
graph.add_node("lesson", lesson_node)
graph.add_node("normal_chat", normal_chat_node)
graph.add_node("completion", completion_node)

graph.add_edge(START, "router")

def should_go_lesson(state: AppState) -> str:
    if state.lesson_state.active and not state.lesson_state.completed:
        return "lesson"
    elif state.lesson_state.completed:
        return "completion"
    else:
        return "normal_chat"

graph.add_conditional_edges("router", should_go_lesson, {
    "lesson": "lesson",
    "normal_chat": "normal_chat",
    "completion": "completion",
})

def after_lesson(state: AppState) -> str:
    if state.lesson_state.completed:
        return "completion"
    return END

graph.add_conditional_edges("lesson", after_lesson, {"completion": "completion", END: END})
graph.add_edge("normal_chat", END)
graph.add_edge("completion", END)

compiled = graph.compile()

# ----------------------
# Streamlit UI
# ----------------------
ensure_session_defaults()
app_state: AppState = st.session_state.app_state

# --- Helper: kickoff message when a sidebar lesson is started ---
def build_lesson_kickoff(lesson_meta: Dict[str, Any]) -> str:
    title = lesson_meta.get("title", "Lesson")
    desc = lesson_meta.get("description", "")
    steps = lesson_meta.get("steps", [])
    if steps:
        first = steps[0]
        step_name = first.get("name", "Step 1")
        goals = first.get("goals", [])
        short_goals = "; ".join(goals[:2]) if goals else ""
        prompts = first.get("prompts_for_user", [])
        first_prompt = prompts[0] if prompts else "Share your initial attempt for this step."
        kickoff = (
            f"**Starting lesson: {title}**\n\n"
            f"{desc}\n\n"
            f"**Step 1 — {step_name}**\n"
            + (f"_Goal(s):_ {short_goals}\n\n" if short_goals else "") +
            f"{first_prompt}\n\n"
            "Go ahead and give it a try!"
        )
    else:
        kickoff = f"**Starting lesson: {title}**\n\n{desc}\n\n_This lesson has no steps defined._"
    return kickoff

# Sidebar: lessons list
with st.sidebar:
    st.header("📚 Lessons")
    if not app_state.lessons_bank:
        st.caption("No YAML lessons found in ./lessons")
    else:
        for lid, meta in sorted(app_state.lessons_bank.items(), key=lambda x: x[1].get("title", x[0])):
            title = meta.get("title", lid)
            if st.button(f"Start: {title}", key=f"start_{lid}"):
                app_state.lesson_state = LessonState(active=True, lesson_id=lid, current_step_idx=0)
                kickoff_msg = build_lesson_kickoff(meta)
                app_state.messages.append({"role": "assistant", "content": kickoff_msg})
                st.session_state.app_state = app_state

    st.divider()
    st.caption("Model")
    model_choice = st.selectbox("OpenAI model", [DEFAULT_MODEL, "gpt-4o", "gpt-4.1-mini"], index=0)
    st.session_state.model_name = model_choice

# Header
st.markdown("## 🎓 Study & Learn Chat (LangGraph)")

# Render history
for msg in app_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input
user_input = st.chat_input("Ask anything, or say 'Teach me about DMAIC/5S/5 Whys' to start a lesson…")
if user_input:
    with st.chat_message("user"):
        st.markdown(user_input)

    banned = ["build a bomb", "self harm", "suicide", "harm others"]
    if any(term in user_input.lower() for term in banned):
        with st.chat_message("assistant"):
            st.markdown("I can’t help with that. If you’re in immediate danger, please contact local emergency services.")
    else:
        app_state.messages.append({"role": "user", "content": user_input})
        result = compiled.invoke(app_state)
        try:
            app_state = AppState(**result)
        except TypeError:
            app_state = AppState(**dict(result))
        st.session_state.app_state = app_state

# --- Fixed badge under the input bar (high z-index + bigger bottom offset) ---
def render_study_mode_badge():
    if app_state.lesson_state.active and not app_state.lesson_state.completed and app_state.lesson_state.lesson_id:
        active_title = app_state.lessons_bank.get(app_state.lesson_state.lesson_id, {}).get("title", app_state.lesson_state.lesson_id)
        st.markdown(
            f"""
            <style>
                .study-badge {{
                    position: fixed;
                    left: 16px;
                    bottom: 120px; /* larger offset ensures it clears the input on most setups */
                    z-index: 9999999;
                    background: #f0faf6;
                    color: #0a7f5a;
                    border: 1px solid #10a37f55;
                    border-radius: 999px;
                    padding: 3px 8px;
                    font-size: 12px;
                    font-weight: 500;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
                    pointer-events: none;
                }}
                @media (max-width: 640px) {{
                    .study-badge {{
                        left: 12px;
                        bottom: 140px; /* a bit higher on small screens */
                        font-size: 11px;
                    }}
                }}
            </style>
            <div class="study-badge">🟢 Study & Learn Mode — {active_title} (Step {app_state.lesson_state.current_step_idx+1})</div>
            """,
            unsafe_allow_html=True
        )
        # Inline fallback directly under the input (in case fixed overlay is blocked by theme/browser)
        st.markdown(
            f"""
            <div style="
                margin-top: 6px;
                font-size: 12px;
                display: inline-block;
                padding: 3px 8px;
                border: 1px solid #10a37f;
                border-radius: 12px;
                color: #10a37f;
                background-color: #f6fffa;
            ">
                🟢 Study Mode Active — {active_title} (Step {app_state.lesson_state.current_step_idx+1})
            </div>
            """,
            unsafe_allow_html=True
        )

render_study_mode_badge()
