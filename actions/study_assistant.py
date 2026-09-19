import json
import sys
from datetime import datetime
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
STUDY_DATA_PATH = BASE_DIR / "memory" / "study_data.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_study_data() -> dict:
    if not STUDY_DATA_PATH.exists():
        return {"subjects": {}, "sessions": [], "plan": {}}
    try:
        return json.loads(STUDY_DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"subjects": {}, "sessions": [], "plan": {}}


def _save_study_data(data: dict) -> None:
    STUDY_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    STUDY_DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _ask_ai(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel("gemini-2.5-flash")
    return model.generate_content(prompt).text.strip()


def _log_session(data: dict, subject: str, duration_min: int, score: int | None, notes: str) -> None:
    data["sessions"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "subject": subject,
        "duration_min": duration_min,
        "score": score,
        "notes": notes,
    })
    subj = data["subjects"].setdefault(subject, {"total_min": 0, "sessions": 0, "avg_score": None, "scores": []})
    subj["total_min"] += duration_min
    subj["sessions"]  += 1
    if score is not None:
        subj["scores"].append(score)
        subj["avg_score"] = round(sum(subj["scores"]) / len(subj["scores"]), 1)


def study_assistant(parameters: dict = None, player=None, speak=None) -> str:
    params  = parameters or {}
    action  = params.get("action", "plan").lower()
    subject = params.get("subject", "").strip()
    topic   = params.get("topic", "").strip()
    goal    = params.get("goal", "").strip()
    duration_min = int(params.get("duration_min", 0) or 0)
    score        = params.get("score")
    if score is not None:
        try: score = int(score)
        except Exception: score = None
    notes   = params.get("notes", "").strip()

    data = _load_study_data()

    # ── LOG SESSION ──────────────────────────────────────────────
    if action == "log":
        if not subject:
            return "Subject batao log karne ke liye."
        _log_session(data, subject, duration_min, score, notes)
        _save_study_data(data)
        msg = f"{subject} session logged — {duration_min} min"
        if score is not None:
            msg += f", score: {score}/100"
        return msg + "."

    # ── GENERATE STUDY PLAN ──────────────────────────────────────
    if action == "plan":
        subjects_str = ", ".join(data["subjects"].keys()) if data["subjects"] else (subject or "General studies")
        goal_str     = goal or "improve overall performance"
        progress_str = json.dumps(
            {s: {"sessions": v["sessions"], "avg_score": v["avg_score"], "total_min": v["total_min"]}
             for s, v in data["subjects"].items()}, ensure_ascii=False
        ) if data["subjects"] else "No prior data."

        prompt = (
            f"You are a personalized AI study coach. Based on the student's progress data below, "
            f"create a focused weekly study plan.\n\n"
            f"Subjects: {subjects_str}\n"
            f"Goal: {goal_str}\n"
            f"Progress so far: {progress_str}\n\n"
            f"Rules:\n"
            f"- Prioritize weak subjects (low avg_score or low total_min).\n"
            f"- Keep it practical: day-wise schedule with time slots.\n"
            f"- Max 200 words. Use bullet points.\n"
            f"- End with one motivational line."
        )
        plan = _ask_ai(prompt)
        data["plan"] = {"generated": datetime.now().strftime("%Y-%m-%d"), "content": plan}
        _save_study_data(data)
        return plan

    # ── SUGGEST TOPICS / MATERIALS ───────────────────────────────
    if action == "suggest":
        if not subject:
            return "Kaunsa subject hai? Subject batao."
        subj_data    = data["subjects"].get(subject, {})
        avg          = subj_data.get("avg_score")
        total        = subj_data.get("total_min", 0)
        topic_hint   = f" Focus on: {topic}." if topic else ""
        progress_ctx = f"Avg score: {avg}/100, Total study time: {total} min." if avg else "No prior data."

        prompt = (
            f"You are a study assistant. Suggest 5 specific topics and free resources "
            f"for a student studying {subject}.\n"
            f"Student progress: {progress_ctx}{topic_hint}\n"
            f"Rules:\n"
            f"- Suggest topics from weak/foundational areas if score is low.\n"
            f"- Include 1-2 free resource links (YouTube, Khan Academy, etc.).\n"
            f"- Max 150 words. Bullet points only."
        )
        return _ask_ai(prompt)

    # ── ANALYZE PROGRESS ─────────────────────────────────────────
    if action == "analyze":
        if not data["subjects"]:
            return "Abhi tak koi study session log nahi hua. Pehle 'log' action use karo."
        summary = {s: {"sessions": v["sessions"], "avg_score": v["avg_score"], "total_min": v["total_min"]}
                   for s, v in data["subjects"].items()}
        prompt = (
            f"Analyze this student's study progress and give honest, actionable feedback.\n\n"
            f"Data: {json.dumps(summary, ensure_ascii=False)}\n\n"
            f"Rules:\n"
            f"- Identify strongest and weakest subjects.\n"
            f"- Point out what needs urgent attention.\n"
            f"- Give 3 specific improvement tips.\n"
            f"- Max 180 words. Be direct."
        )
        return _ask_ai(prompt)

    # ── QUIZ ─────────────────────────────────────────────────────
    if action == "quiz":
        if not subject:
            return "Quiz ke liye subject batao."
        topic_str = f" on the topic: {topic}" if topic else ""
        prompt = (
            f"Generate a 5-question multiple-choice quiz for {subject}{topic_str}.\n"
            f"Format each question as:\nQ1. [question]\nA) ... B) ... C) ... D) ...\nAnswer: [letter]\n\n"
            f"Keep it educational and clear."
        )
        return _ask_ai(prompt)

    return f"Unknown action: {action}. Use: log, plan, suggest, analyze, quiz."
