# Study & Learn Chat (LangGraph) — README

An interactive Streamlit app that wraps the OpenAI API and uses **LangGraph** to orchestrate a “Study & Learn” mode with YAML-driven lessons. When a lesson is active (e.g., **DMAIC**), the assistant teaches step-by-step, provides feedback once per step, and—on completion—runs a lesson-specific tool to export a **PowerPoint** summary.

---

## ✨ Features

* **Chat + Lessons**: Normal chat plus YAML-configured lessons (DMAIC, 5S, 5 Whys, etc.).
* **Study & Learn Mode**: Triggered when a lesson is started (via sidebar or “teach me …” in chat).

  * Teaches **step-by-step** (no spoilers for later steps).
  * Gives **one targeted improvement** suggestion per step; proceeds even if user doesn’t improve.
  * Shows a small **Study Mode badge** near the input (fixed on screen).
* **LangGraph Orchestration**: Simple `StateGraph` manages routing between normal chat, lesson flow, and completion.
* **OpenAI Streaming**: Streams tokens for a responsive feel.
* **DMAIC Export Tool**: On lesson completion, generates a `.pptx` summary using `python-pptx` and offers a download button.
* **No Chat Persistence** (by design): Session-only history (no DB).

---

## 🧱 Project Structure (suggested)

```
.
├─ app.py                        # Streamlit app (this script)
├─ lessons/                      # YAML lessons (dmaic.yml, 5s.yml, 5whys.yml, etc.)
│  └─ dmaic.yml
├─ tools/
│  ├─ __init__.py
│  └─ slide_export.py            # DMAIC -> PPTX export tool
├─ .env                          # OPENAI_API_KEY=...
├─ requirements.txt              # (optional)
└─ README.md
```

---

## ⚙️ Requirements

* **Python**: 3.9–3.11 recommended
* **Dependencies**

  ```bash
  pip install streamlit python-dotenv pyyaml openai langgraph python-pptx pillow
  ```

> If you maintain a `requirements.txt`, include:
>
> ```
> streamlit
> python-dotenv
> pyyaml
> openai
> langgraph
> python-pptx
> pillow
> ```

---

## 🔐 Environment Variables

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
```

The app will surface a Streamlit warning if the key is not found.
The default model is **`gpt-4o-mini`** (closest to “gpt-5-mini” requested). You can switch models from the sidebar.

---

## ▶️ Running the App

```bash
streamlit run app.py
```

Then open the provided local URL in your browser.

---

## 🧠 How Lessons Work

### Start a Lesson

* From the **sidebar**: click `Start: DMAIC` (or any lesson listed), **or**
* In chat: type `Teach me about DMAIC` (also works with “learn …”, “study …”).

When a lesson starts:

* The app posts a **kickoff** assistant message describing the lesson and **Step 1** prompt.
* The **Study & Learn** badge appears near the input to indicate the mode is active.

### YAML Lesson Format

Each lesson is defined in `lessons/*.yml` with this structure:

```yaml
id: dmaic
title: DMAIC
description: Structured, step-by-step improvement method (Define, Measure, Analyze, Improve, Control).
steps:
  - name: Define
    goals: ["Clarify the problem", "Bound scope", "Identify stakeholders"]
    best_practices: ["Use SMART format", "State CTQs", "Align scope"]
    prompts_for_user: ["Write your problem statement in SMART format."]
  - name: Measure
    goals: ["Build baseline", "Agree on definitions", "Gather data"]
    best_practices: ["Operational definitions", "Data plan", "Check data quality"]
    prompts_for_user: ["Describe what you'll measure and how you'll collect it."]
  # ... Analyze, Improve, Control
```

> The app reads all `.yaml/.yml` files in `./lessons` at startup and lists them in the sidebar.

---

## 🧩 LangGraph Flow (High-Level)

Nodes:

* **router** → Determines whether to enter lesson flow (based on the last user message and YAML titles).
* **lesson** → Handles step coaching, one improvement suggestion per step, then advances.
* **completion** → Runs post-lesson actions (e.g., DMAIC slide export) and resets lesson state.
* **normal\_chat** → Default chat when no lesson is active.

State is held in:

* `AppState` → `messages` (chat history), `lesson_state`, `lessons_bank`, etc.
* `LessonState` → active flag, `lesson_id`, `current_step_idx`, user inputs per step, etc.

---

## 📤 Slide Export Tool (DMAIC)

The app calls the tool **automatically** when a DMAIC lesson completes.

* Code location: `tools/slide_export.py`
* Dependency: `python-pptx` (and `pillow`)
* Output: A `.pptx` saved to `./exports/` and exposed via a **Download** button in the UI.

Minimal interface:

```python
from tools.slide_export import export_dmaic_to_pptx

# steps_filled is constructed for you at the end of the lesson:
out_path = export_dmaic_to_pptx(steps_filled)
```

If you want to test the tool manually, you can pass a list like:

```python
steps_filled = [
  {"step": "Define", "user_input": "Problem ...", "goals": ["..."], "best_practices": ["..."]},
  # ...
]
```

---

## 🖥️ UI Notes

* **Streaming** replies: the assistant’s message is streamed and then **persisted** to history so it remains visible after subsequent turns.
* **Study Mode badge**: A tiny floating badge is rendered near the input with fixed positioning and an inline fallback to ensure visibility across themes/browsers.

---

## 🧪 Tips & Troubleshooting

* **Badge not visible**
  The app renders both a fixed-position badge and an inline fallback under the input. If you still can’t see it, check your Streamlit version/theme. You can adjust the CSS `bottom:` offset or `z-index` in the `render_study_mode_badge()` function.

* **“OPENAI\_API\_KEY not found”**
  Ensure your `.env` is in the project root and contains a valid key. Restart the app after adding it.

* **Assistant messages disappear**
  This is handled: streamed output is appended to `app_state.messages` after completion. If you customize, ensure you persist the assistant message after streaming.

* **No lessons appear**
  Make sure `./lessons` exists and contains valid `.yaml/.yml` files with an `id` field.

* **Different Model**
  Use the sidebar selector to change the model at runtime.

---

## ➕ Adding New Lessons & Tools

1. **Add a YAML** to `./lessons` using the schema above. The app will auto-discover it.
2. **Add a tool** (optional):

   * Implement a function in `tools/your_tool.py`.
   * Import and trigger it in `completion_node` when `lesson_id` matches your lesson.
   * Return a path and show a `st.download_button`.

---

## 🔒 Privacy & Data

* No persistent storage of chat history (session-only).
* No external logging beyond Streamlit/OpenAI client defaults.

---

## 📄 License

Add your preferred license here (e.g., MIT).

---

**Happy learning!**
