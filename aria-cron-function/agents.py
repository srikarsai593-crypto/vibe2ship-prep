import os
import json
import re
import datetime as dt
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

DEFAULT_HORIZON_HOURS = 48
DEFAULT_DENSITY_CRITICAL_THRESHOLD = 70
DEFAULT_TIMEZONE = "Asia/Kolkata"
WORK_SIGNAL_KEYWORDS = {
    "assignment",
    "deadline",
    "demo",
    "exam",
    "final",
    "hackathon",
    "interview",
    "lab",
    "midterm",
    "presentation",
    "project",
    "quiz",
    "review",
    "submission",
    "test",
    "viva",
}
TASK_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "in",
    "me",
    "my",
    "of",
    "on",
    "plan",
    "prep",
    "prepare",
    "study",
    "task",
    "the",
    "to",
    "with",
}


def _load_timezone(timezone_name: str = DEFAULT_TIMEZONE):
    """Resolve a timezone without making the calendar logic depend on Streamlit."""
    try:
        import pytz

        return pytz.timezone(timezone_name)
    except Exception:
        return dt.timezone.utc


def _localize_datetime(value: dt.datetime, timezone) -> dt.datetime:
    if value.tzinfo is not None:
        return value
    if hasattr(timezone, "localize"):
        return timezone.localize(value)
    return value.replace(tzinfo=timezone)


def _parse_calendar_datetime(value: str, timezone) -> dt.datetime | None:
    """Parse Google Calendar date/dateTime strings into timezone-aware UTC datetimes."""
    if not value:
        return None

    normalized_value = value.strip()
    try:
        if "T" in normalized_value:
            if normalized_value.endswith("Z"):
                normalized_value = normalized_value[:-1] + "+00:00"
            parsed = dt.datetime.fromisoformat(normalized_value)
        else:
            parsed_date = dt.date.fromisoformat(normalized_value)
            parsed = dt.datetime.combine(parsed_date, dt.time.min)
    except ValueError:
        return None

    return _localize_datetime(parsed, timezone).astimezone(dt.timezone.utc)


def _event_to_utc_interval(event: dict, default_timezone) -> tuple[dt.datetime, dt.datetime] | None:
    start_payload = event.get("start", {}) if isinstance(event, dict) else {}
    end_payload = event.get("end", {}) if isinstance(event, dict) else {}

    timezone = _load_timezone(
        start_payload.get("timeZone")
        or end_payload.get("timeZone")
        or DEFAULT_TIMEZONE
    )
    if timezone is dt.timezone.utc:
        timezone = default_timezone

    start_raw = start_payload.get("dateTime") or start_payload.get("date")
    end_raw = end_payload.get("dateTime") or end_payload.get("date")
    start_at = _parse_calendar_datetime(start_raw, timezone)
    end_at = _parse_calendar_datetime(end_raw, timezone)

    if start_at is None:
        return None

    if end_at is None:
        end_at = start_at + dt.timedelta(minutes=30)
    elif end_at <= start_at:
        end_at = start_at + dt.timedelta(minutes=30)

    return start_at, end_at


def _merge_intervals(intervals: list[tuple[dt.datetime, dt.datetime]]) -> list[tuple[dt.datetime, dt.datetime]]:
    if not intervals:
        return []

    ordered_intervals = sorted(intervals, key=lambda interval: interval[0])
    merged = [ordered_intervals[0]]

    for start_at, end_at in ordered_intervals[1:]:
        previous_start, previous_end = merged[-1]
        if start_at <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end_at))
        else:
            merged.append((start_at, end_at))

    return merged


def horizon_agent_intelligence_loop(
    calendar_events: list[dict] | None,
    *,
    now: dt.datetime | None = None,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    timezone_name: str = DEFAULT_TIMEZONE,
    critical_threshold: int = DEFAULT_DENSITY_CRITICAL_THRESHOLD,
) -> dict:
    """
    Calculate how packed the next horizon window is from Google Calendar events.

    The density score is an absolute 0-100 percentage of unique occupied minutes
    in the next `horizon_hours`. Overlapping events are merged before scoring.
    """
    timezone = _load_timezone(timezone_name)
    window_start = now or dt.datetime.now(dt.timezone.utc)
    window_start = _localize_datetime(window_start, timezone).astimezone(dt.timezone.utc)
    window_end = window_start + dt.timedelta(hours=horizon_hours)

    clipped_intervals = []
    skipped_event_count = 0

    for event in calendar_events or []:
        event_interval = _event_to_utc_interval(event, timezone)
        if event_interval is None:
            skipped_event_count += 1
            continue

        event_start, event_end = event_interval
        overlap_start = max(event_start, window_start)
        overlap_end = min(event_end, window_end)

        if overlap_start < overlap_end:
            clipped_intervals.append((overlap_start, overlap_end))

    merged_intervals = _merge_intervals(clipped_intervals)
    busy_seconds = sum(
        (end_at - start_at).total_seconds()
        for start_at, end_at in merged_intervals
    )
    horizon_seconds = max((window_end - window_start).total_seconds(), 1)
    density_percent = min((busy_seconds / horizon_seconds) * 100, 100)
    density_score = round(density_percent)

    if density_score >= critical_threshold:
        density_tier = "OVERCROWDED"
    elif density_score >= 55:
        density_tier = "PACKED"
    elif density_score >= 35:
        density_tier = "BUSY"
    elif density_score >= 15:
        density_tier = "LIGHT"
    else:
        density_tier = "CLEAR"

    busy_minutes = round(busy_seconds / 60)
    horizon_minutes = round(horizon_seconds / 60)

    return {
        "density_score": density_score,
        "density_percent": round(density_percent, 2),
        "density_tier": density_tier,
        "is_overcrowded": density_score >= critical_threshold,
        "critical_threshold": critical_threshold,
        "busy_minutes": busy_minutes,
        "free_minutes": max(horizon_minutes - busy_minutes, 0),
        "horizon_minutes": horizon_minutes,
        "horizon_hours": horizon_hours,
        "event_count": len(calendar_events or []),
        "counted_event_count": len(clipped_intervals),
        "merged_busy_block_count": len(merged_intervals),
        "skipped_event_count": skipped_event_count,
        "window_start": window_start.astimezone(timezone).isoformat(),
        "window_end": window_end.astimezone(timezone).isoformat(),
    }


def horizon_density_score(calendar_events: list[dict] | None, **kwargs) -> int:
    """Return only the absolute 0-100 density score for callers that need a scalar."""
    return horizon_agent_intelligence_loop(calendar_events, **kwargs)["density_score"]


def adjust_risk_for_horizon_density(base_risk: str, density_report: dict) -> str:
    """Escalate task risk when the real calendar has too little free space."""
    normalized_risk = (base_risk or "MEDIUM").strip().upper()
    if normalized_risk == "CRITICAL":
        return "CRITICAL"

    if density_report.get("is_overcrowded") and normalized_risk in {"MEDIUM", "HIGH"}:
        return "CRITICAL"

    return normalized_risk


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower()).strip()


def _meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) > 2 and token not in TASK_STOPWORDS
    }


def _calendar_event_summary(event: dict) -> str:
    return (event or {}).get("summary") or "Untitled calendar event"


def _task_matches_event(event_summary: str, task_name: str) -> bool:
    normalized_event = _normalize_text(event_summary)
    normalized_task = _normalize_text(task_name)

    if not normalized_event or not normalized_task:
        return False
    if normalized_event in normalized_task or normalized_task in normalized_event:
        return True

    event_tokens = _meaningful_tokens(event_summary)
    task_tokens = _meaningful_tokens(task_name)
    if not event_tokens or not task_tokens:
        return False

    overlap = event_tokens & task_tokens
    return len(overlap) >= 2 or len(overlap) / max(len(event_tokens), 1) >= 0.45


def _format_hours_until(hours_until: float | None) -> str:
    if hours_until is None:
        return "soon"
    if hours_until <= 1:
        return "within an hour"
    if hours_until < 24:
        return f"in {round(hours_until)} hours"
    return f"in {round(hours_until / 24, 1)} days"


def _suggested_task_for_event(summary: str) -> str:
    tokens = _meaningful_tokens(summary)
    if tokens & {"exam", "final", "midterm", "quiz", "test", "viva"}:
        return f"Create focused study plan for {summary}"
    if tokens & {"assignment", "deadline", "submission", "project", "lab"}:
        return f"Break down and finish preparation for {summary}"
    if tokens & {"presentation", "demo", "interview", "review"}:
        return f"Prepare talking points and materials for {summary}"
    return f"Prepare for {summary}"


def horizon_agent(
    events: list[dict] | None,
    existing_task_names: list[str] | None,
    *,
    now: dt.datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    max_suggestions: int = 5,
) -> list[dict]:
    """
    Detect calendar events that look like real work but have no matching task yet.

    This stays deterministic for the dashboard so the app does not block on an LLM
    call every time Streamlit reruns.
    """
    timezone = _load_timezone(timezone_name)
    reference_time = _localize_datetime(now or dt.datetime.now(dt.timezone.utc), timezone).astimezone(dt.timezone.utc)
    task_names = existing_task_names or []
    suggestions = []

    for event in events or []:
        summary = _calendar_event_summary(event)
        if any(_task_matches_event(summary, task_name) for task_name in task_names):
            continue

        interval = _event_to_utc_interval(event, timezone)
        start_at = interval[0] if interval else None
        hours_until = None
        if start_at:
            hours_until = max((start_at - reference_time).total_seconds() / 3600, 0)

        summary_tokens = _meaningful_tokens(summary)
        has_work_signal = bool(summary_tokens & WORK_SIGNAL_KEYWORDS)
        if not has_work_signal and hours_until is not None and hours_until > 72:
            continue

        if hours_until is not None and hours_until <= 6:
            urgency = "critical"
        elif has_work_signal or (hours_until is not None and hours_until <= 48):
            urgency = "high"
        else:
            urgency = "medium"

        suggestions.append(
            {
                "event": summary,
                "event_start": start_at.astimezone(timezone).isoformat() if start_at else "",
                "suggested_task": _suggested_task_for_event(summary),
                "urgency": urgency,
                "reason": f"Starts {_format_hours_until(hours_until)} and no matching task exists.",
            }
        )

    urgency_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    suggestions.sort(key=lambda item: (urgency_rank.get(item["urgency"], 9), item["event_start"]))
    return suggestions[:max_suggestions]


def intervention_letter(task_name: str, user_name: str, deadline_str: str, context: str = "") -> str:
    """
    Write a short letter from the user's future self for non-immediate tasks.
    Falls back to a deterministic letter when Gemini is unavailable.
    """
    first = (user_name or "there").split()[0]
    prompt = f"""Write a letter from {first}'s future self.
Written AFTER missing: {task_name} (deadline was: {deadline_str}).
Rules: sound like a real person writing to themselves, be SPECIFIC to this task,
mention one real consequence of missing it, give ONE concrete action for right now.
3 short paragraphs. Conversational and slightly emotional.
Context: {context}
Start with exactly: Hi {first},"""

    if client is not None:
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.45),
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as e:
            print(f"Intervention letter fallback triggered: {e}")

    consequence = "you lose the calm time that would have made this feel manageable"
    if context:
        consequence = f"the context you already know matters gets compressed into a rushed, lower-quality finish"

    return (
        f"Hi {first},\n\n"
        f"I am writing from the version of today where {task_name} slipped too far. "
        f"The real consequence was not just the missed deadline; {consequence}.\n\n"
        f"Do one thing right now: open the material for {task_name} and write the next three tiny moves. "
        f"Do not negotiate with the whole mountain. Touch the first stone."
    )


def crisis_agent(task_name: str, hours_left: float) -> list[dict]:
    """Create a ruthless emergency plan for tasks inside the six-hour danger zone."""
    if client is not None:
        prompt = (
            f"CRISIS: {hours_left:.1f} hours until deadline for: {task_name}\n"
            "Create a ruthless execution plan. Return JSON only:\n"
            "[{\"time_block\": \"0-30min\", \"action\": \"...\", \"output\": \"...\"}]\n"
            "Be brutal. Cut everything non-essential. Only what must exist to pass."
        )
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text or ""
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                # Try to strip common markdown fences and retry
                cleaned = raw_text.replace('```json', '').replace('```', '').strip()
                try:
                    parsed = json.loads(cleaned)
                except json.JSONDecodeError:
                    parsed = []
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception as e:
            print(f"Crisis agent fallback triggered: {e}")

    return [
        {
            "time_block": "0-20min",
            "action": f"Define the minimum passable version of {task_name}.",
            "output": "One-line scope and hard cutoff list.",
        },
        {
            "time_block": "20-90min",
            "action": "Build only the core deliverable. No polishing, no side quests.",
            "output": "Working draft or runnable core artifact.",
        },
        {
            "time_block": "90-150min",
            "action": "Patch the highest-risk gaps and remove anything unfinished.",
            "output": "Coherent submission-ready version.",
        },
        {
            "time_block": "Final 30min",
            "action": "Submit, export, or send. Do not keep tweaking after the cutoff.",
            "output": "Delivered artifact and confirmation.",
        },
    ]


def breakdown_agent(user_panic_text: str) -> str:
    """
    11:00 AM Blueprint Item: Extracts tasks and targets deadlines 
    forcing a structured JSON format response.
    """
    system_prompt = """
    You are 'The Last-Minute Life Saver' extraction engine. Take the user's messy text input and break it into individual actionable items.
    You MUST return your response as a valid JSON object matching this structure:
    {
       "tasks": [
          {"task_name": "Clear task details", "eta_minutes": 45}
       ]
    }
    """
    if client is None:
        return '{"tasks": []}'

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_panic_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        return (response.text or '{"tasks": []}')
    except Exception as e:
        print(f"Breakdown agent warning handler triggered: {e}")
        return '{"tasks": []}'

def priority_orchestrator(raw_tasks_json: str) -> str:
    """
    2:30 PM Blueprint Item: Takes extracted tasks and routes them 
    through an Eisenhower Urgency Matrix with color classifications.
    """
    system_prompt = """
    You are the Priority Orchestrator. Analyze the provided list of tasks and assign each one an explicit priority ranking.
    You MUST sort them from most urgent to least urgent and categorize them into: 'Critical', 'Important', or 'Flexible'.
    Return a clean JSON object structure matching this:
    {
       "prioritized_tasks": [
          {"task_name": "string", "eta_minutes": 45, "priority_tier": "Critical/Important/Flexible"}
       ]
    }
    """
    if client is None:
        return raw_tasks_json

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Prioritize these tasks: {raw_tasks_json}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        return (response.text or raw_tasks_json)
    except Exception as e:
        print(f"Priority orchestrator warning handler triggered: {e}")
        return raw_tasks_json
