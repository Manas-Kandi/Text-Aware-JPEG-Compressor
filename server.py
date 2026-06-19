from __future__ import annotations

import ast
import base64
import json
import math
import os
import re
import sqlite3
import textwrap
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
IMAGES = DATA / "memory_images"
ARTIFACTS = DATA / "artifacts"
DB_PATH = DATA / "agent.db"
for directory in (DATA, IMAGES, ARTIFACTS):
    directory.mkdir(parents=True, exist_ok=True)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env(ROOT / ".env")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openrouter").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://127.0.0.1:8000")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Piper Agentic Memory")
OPENROUTER_REASONING = os.getenv("OPENROUTER_REASONING", "true").lower() == "true"
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "openrouter/free")

# Optional legacy provider. Set MODEL_PROVIDER=nvidia to use it.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")

DEFAULT_MODEL = ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "nvidia/nemotron-3-ultra-550b-a55b"
MAIN_MODEL = os.getenv("MAIN_MODEL", DEFAULT_MODEL)
VISION_MODEL = os.getenv("VISION_MODEL", ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "moonshotai/kimi-k2.6")
MEMORY_MODEL = os.getenv("MEMORY_MODEL", ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "moonshotai/kimi-k2.6")
ENABLE_MEMORY_LLM = os.getenv("ENABLE_MEMORY_LLM", "true").lower() == "true"
ENABLE_VISION_RECALL = os.getenv("ENABLE_VISION_RECALL", "true").lower() == "true"
DECAY_HOURS = max(1, int(os.getenv("DECAY_INTERVAL_HOURS", "24")))
MAX_RETRIEVED = max(1, min(6, int(os.getenv("MAX_RETRIEVED_MEMORIES", "3"))))

DB_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with DB_LOCK:
        conn = db()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL,
                  created_at TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                  id TEXT PRIMARY KEY, label TEXT NOT NULL, summary TEXT NOT NULL,
                  tags TEXT NOT NULL DEFAULT '[]', image_name TEXT NOT NULL,
                  created_at TEXT NOT NULL, last_accessed TEXT NOT NULL,
                  access_count INTEGER NOT NULL DEFAULT 0,
                  decay_stage INTEGER NOT NULL DEFAULT 0,
                  activation REAL NOT NULL DEFAULT 1.0
                );
                CREATE TABLE IF NOT EXISTS edges (
                  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                  relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0.5,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(source_id) REFERENCES memories(id) ON DELETE CASCADE,
                  FOREIGN KEY(target_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                """
            )
        finally:
            conn.close()


init_db()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000)


class TaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=4000)


class TaskPatch(BaseModel):
    status: str | None = None
    title: str | None = None


class NoteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)


app = FastAPI(title="Piper Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/memory-images", StaticFiles(directory=IMAGES), name="memory-images")
app.mount("/artifacts", StaticFiles(directory=ARTIFACTS), name="artifacts")


def rows(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with DB_LOCK:
        conn = db()
        try:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()


def execute(query: str, params: tuple = ()) -> None:
    with DB_LOCK:
        conn = db()
        try:
            conn.execute(query, params)
            conn.commit()
        finally:
            conn.close()


def public_memory(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["tags"] = json.loads(item.get("tags") or "[]")
    item["image_url"] = f"/memory-images/{item.pop('image_name')}"
    return item


def active_api_key() -> str:
    return OPENROUTER_API_KEY if MODEL_PROVIDER == "openrouter" else NVIDIA_API_KEY


def model_chat(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1800,
    thinking: bool = False,
    json_mode: bool = False,
) -> str:
    api_key = active_api_key()
    if not api_key:
        raise RuntimeError(f"{MODEL_PROVIDER.upper()} API key is not configured")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if MODEL_PROVIDER == "openrouter" and thinking and OPENROUTER_REASONING:
        payload["reasoning"] = {"effort": "medium"}
    elif thinking and model.startswith("nvidia/nemotron"):
        payload["chat_template_kwargs"] = {"enable_thinking": True}
        payload["reasoning_budget"] = min(4096, max_tokens * 2)

    if MODEL_PROVIDER == "openrouter":
        endpoint = f"{OPENROUTER_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_HTTP_REFERER,
            "X-Title": OPENROUTER_APP_TITLE,
        }
    else:
        endpoint = f"{NVIDIA_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    attempts = 3 if MODEL_PROVIDER == "openrouter" and model == "openrouter/free" else 1
    last_error = "The model returned no usable response."
    for attempt in range(attempts):
        attempt_payload = dict(payload)
        # Relax optional reasoning after the first failure so the free router has
        # a larger compatible pool to choose from.
        if attempt:
            attempt_payload.pop("reasoning", None)
        try:
            response = requests.post(endpoint, headers=headers, json=attempt_payload, timeout=180)
        except requests.RequestException as error:
            last_error = f"Connection error: {error}"
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(last_error) from error

        if not response.ok:
            try:
                error_data = response.json().get("error", {})
                error_message = str(error_data.get("message") or response.text)[:300]
            except (ValueError, AttributeError):
                error_message = response.text[:300]
            last_error = f"{MODEL_PROVIDER.title()} API {response.status_code}: {error_message}"
            if response.status_code in {408, 409, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                time.sleep(0.65 * (attempt + 1))
                continue
            raise RuntimeError(last_error)

        try:
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("response contained no choices")
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
            content = str(content).strip()
            if content:
                return content
            last_error = "The routed model returned an empty final answer."
        except (ValueError, TypeError, KeyError) as error:
            last_error = f"Malformed router response: {error}"

        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
            continue
    raise RuntimeError(last_error)


STOPWORDS = {
    "about", "after", "again", "agent", "also", "been", "being", "could", "does",
    "from", "have", "into", "just", "more", "that", "their", "them", "then", "there",
    "these", "they", "this", "through", "using", "very", "want", "when", "where", "which",
    "with", "would", "your", "task", "user", "assistant", "result"
}


def keywords(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    counts: dict[str, int] = {}
    for word in words:
        if word not in STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def safe_label(text: str) -> str:
    chosen = keywords(text, 4)
    return " ".join(word.title() for word in chosen) or "Agent Memory"


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def label_memory(event_text: str) -> tuple[str, str, list[str]]:
    fallback_tags = keywords(event_text)
    fallback_summary = event_text[:3200]
    if not (active_api_key() and ENABLE_MEMORY_LLM):
        return safe_label(event_text), fallback_summary, fallback_tags
    prompt = (
        "Compress this completed agent event into durable memory metadata. Return only JSON with "
        'keys "label" (2-5 words), "summary" (max 900 characters, preserve concrete procedures and outcomes), '
        'and "tags" (3-8 short strings).\n\nEVENT:\n' + event_text[:12000]
    )
    try:
        data = parse_json_object(model_chat(MEMORY_MODEL, [{"role": "user", "content": prompt}], 600, json_mode=True))
        label = str(data.get("label") or safe_label(event_text))[:80]
        summary = str(data.get("summary") or fallback_summary)[:3200]
        tags = [str(tag).lower()[:32] for tag in data.get("tags", fallback_tags)][:8]
        return label, summary, tags
    except Exception:
        return safe_label(event_text), fallback_summary, fallback_tags


DECAY_PROFILES = [
    {"quality": 38, "scale": 1.00, "ink": 24, "blur": 0.0},
    {"quality": 26, "scale": 0.92, "ink": 40, "blur": 0.0},
    {"quality": 17, "scale": 0.82, "ink": 58, "blur": 0.15},
    {"quality": 10, "scale": 0.70, "ink": 86, "blur": 0.35},
    {"quality": 6, "scale": 0.58, "ink": 118, "blur": 0.65},
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, chosen_font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=chosen_font)[2] <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_memory_image(memory_id: str, label: str, summary: str, tags: list[str], stage: int) -> str:
    profile = DECAY_PROFILES[max(0, min(4, stage))]
    canvas = Image.new("L", (750, 1000), 250)
    draw = ImageDraw.Draw(canvas)
    heading, body, mono = font(30, True), font(19), font(14)
    ink = profile["ink"]
    draw.rectangle((0, 0, 750, 118), fill=238)
    draw.text((42, 30), label[:48], font=heading, fill=ink)
    draw.text((44, 78), "MEMORY NODE  /  " + memory_id[:8].upper(), font=mono, fill=min(150, ink + 45))
    y = 154
    for line in wrap_pixels(draw, summary, body, 666):
        if y > 895:
            draw.text((42, y), "…", font=body, fill=ink)
            break
        draw.text((42, y), line, font=body, fill=ink)
        y += 29
    tag_line = "  ·  ".join(tags[:6]).upper()
    draw.line((42, 932, 708, 932), fill=190, width=1)
    draw.text((42, 950), tag_line[:82], font=mono, fill=min(160, ink + 50))
    if profile["blur"]:
        canvas = canvas.filter(ImageFilter.GaussianBlur(profile["blur"]))
    if profile["scale"] < 1:
        canvas = canvas.resize(
            (round(750 * profile["scale"]), round(1000 * profile["scale"])),
            Image.Resampling.LANCZOS,
        )
    name = f"{memory_id}.jpg"
    canvas.save(IMAGES / name, "JPEG", quality=profile["quality"], optimize=True, progressive=True)
    return name


def create_memory(event_text: str) -> dict[str, Any]:
    label, summary, tags = label_memory(event_text)
    memory_id, timestamp = uuid.uuid4().hex, now_iso()
    image_name = render_memory_image(memory_id, label, summary, tags, 0)
    execute(
        "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)",
        (memory_id, label, summary, json.dumps(tags), image_name, timestamp, timestamp, 0, 0, 1.0),
    )
    connect_memory(memory_id, summary + " " + " ".join(tags))
    return public_memory(rows("SELECT * FROM memories WHERE id=?", (memory_id,))[0])


def connect_memory(memory_id: str, text: str) -> None:
    candidates = rows("SELECT id, summary, tags FROM memories WHERE id != ? ORDER BY created_at DESC LIMIT 18", (memory_id,))
    source_words = set(keywords(text, 20))
    connected = 0
    for candidate in candidates:
        target_words = set(keywords(candidate["summary"] + " " + candidate["tags"], 20))
        overlap = len(source_words & target_words) / max(1, len(source_words | target_words))
        if overlap >= 0.08 or (connected == 0 and len(candidates) > 0):
            execute(
                "INSERT INTO edges VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex, candidate["id"], memory_id, "semantic" if overlap >= 0.08 else "temporal", max(0.2, overlap), now_iso()),
            )
            connected += 1
        if connected >= 3:
            break


def retrieve_memories(query: str) -> list[dict[str, Any]]:
    query_words = set(keywords(query, 20))
    candidates = rows("SELECT * FROM memories ORDER BY last_accessed DESC LIMIT 80")
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        words = set(keywords(candidate["label"] + " " + candidate["summary"] + " " + candidate["tags"], 30))
        lexical = len(query_words & words) / max(1, len(query_words))
        score = lexical * 4 + float(candidate["activation"]) * 0.35 + math.log1p(candidate["access_count"]) * 0.08
        if lexical > 0 or len(candidates) <= MAX_RETRIEVED:
            scored.append((score, candidate))
    return [public_memory(item) for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:MAX_RETRIEVED]]


def touch_memory(memory_id: str) -> None:
    found = rows("SELECT * FROM memories WHERE id=?", (memory_id,))
    if not found:
        return
    item = found[0]
    tags = json.loads(item["tags"] or "[]")
    render_memory_image(memory_id, item["label"], item["summary"], tags, 0)
    execute(
        "UPDATE memories SET last_accessed=?, access_count=access_count+1, decay_stage=0, activation=MIN(1.0, activation+0.25) WHERE id=?",
        (now_iso(), memory_id),
    )


def apply_decay(force: bool = False) -> int:
    changed = 0
    now = datetime.now(timezone.utc)
    for item in rows("SELECT * FROM memories"):
        last = datetime.fromisoformat(item["last_accessed"])
        elapsed = max(0, (now - last).total_seconds() / 3600)
        desired = min(4, int(elapsed // DECAY_HOURS))
        if force:
            desired = min(4, item["decay_stage"] + 1)
        if desired > item["decay_stage"]:
            tags = json.loads(item["tags"] or "[]")
            render_memory_image(item["id"], item["label"], item["summary"], tags, desired)
            execute(
                "UPDATE memories SET decay_stage=?, activation=MAX(0.08, activation*0.72) WHERE id=?",
                (desired, item["id"]),
            )
            changed += 1
    return changed


def image_data_url(image_url: str) -> str:
    data = (IMAGES / Path(image_url).name).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def recall_from_images(memories: list[dict[str, Any]], query: str) -> str:
    if not memories:
        return "No relevant prior memory was found."
    fallback = "\n\n".join(f"[{m['label']}] {m['summary']}" for m in memories)
    if not (active_api_key() and ENABLE_VISION_RECALL):
        return fallback
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": "Read these memory-node images. Extract only information relevant to the query, preserving concrete steps and outcomes. Query: " + query,
    }]
    for memory in memories:
        content.append({"type": "image_url", "image_url": {"url": image_data_url(memory["image_url"])}})
    try:
        return model_chat(VISION_MODEL, [{"role": "user", "content": content}], 900)
    except Exception:
        return fallback


AGENT_SYSTEM = """You are Piper, a concise and capable local-first agent. You can converse and execute a bounded set of tools.
Return ONLY valid JSON with this schema:
{
  "reply": "helpful user-facing response",
  "actions": [
    {"type":"create_task","title":"...","details":"..."},
    {"type":"complete_task","query":"task title or id"},
    {"type":"save_note","title":"...","content":"..."},
    {"type":"create_artifact","title":"...","content":"markdown content"}
  ],
  "memory": {"summary":"short durable account of what was decided or done"}
}
Use actions only when the user explicitly asks you to do or persist something. Never claim an action succeeded; execution results are appended by the system. Do not expose hidden reasoning."""


def mock_plan(message: str) -> dict[str, Any]:
    lowered = message.lower()
    actions: list[dict[str, str]] = []
    if any(phrase in lowered for phrase in ("create a task", "add a task", "remind me")):
        title = re.sub(r"(?i).*(?:create|add) a task(?: to)?|remind me to", "", message).strip(" .:") or message[:100]
        actions.append({"type": "create_task", "title": title, "details": "Created from chat"})
    if "save" in lowered and "note" in lowered:
        actions.append({"type": "save_note", "title": safe_label(message), "content": message})
    reply = "I’m running in local demo mode. I can still manage the task and memory graph; add NVIDIA_API_KEY to .env for model-generated replies."
    if actions:
        reply = "I prepared the requested local action."
    return {"reply": reply, "actions": actions, "memory": {"summary": message[:800]}}


def plan_response(message: str, memory_context: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    if not active_api_key():
        return mock_plan(message)
    context = f"RECALLED MEMORY (may be empty):\n{memory_context}\n\nCURRENT USER REQUEST:\n{message}"
    model_messages = [{"role": "system", "content": AGENT_SYSTEM}]
    model_messages.extend({"role": item["role"], "content": item["content"]} for item in history[-8:])
    model_messages.append({"role": "user", "content": context})
    try:
        raw_plan = model_chat(MAIN_MODEL, model_messages, 2200, thinking=True, json_mode=True)
        try:
            plan = parse_json_object(raw_plan)
        except (json.JSONDecodeError, ValueError):
            direct_messages = [{
                "role": "system",
                "content": "You are Piper, a concise helpful assistant. Respond directly to the user. Do not use tools or JSON in this fallback response.",
            }]
            direct_messages.extend({"role": item["role"], "content": item["content"]} for item in history[-6:])
            direct_messages.append({"role": "user", "content": message})
            direct_reply = model_chat(MAIN_MODEL, direct_messages, 1200, thinking=False, json_mode=False)
            return {"reply": direct_reply, "actions": [], "memory": {"summary": message}}
        plan.setdefault("reply", "Done.")
        plan.setdefault("actions", [])
        plan.setdefault("memory", {"summary": message})
        return plan
    except Exception as error:
        return {"reply": f"The model request failed, so no remote action ran. {str(error)[:180]}", "actions": [], "memory": {"summary": message}}


def slug(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return clean[:60] or "artifact"


def execute_actions(actions: list[dict[str, Any]]) -> list[str]:
    results: list[str] = []
    for action in actions[:6]:
        kind = action.get("type")
        if kind == "create_task":
            task_id, timestamp = uuid.uuid4().hex, now_iso()
            title = str(action.get("title") or "Untitled task")[:200]
            details = str(action.get("details") or "")[:4000]
            execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (task_id, title, details, "open", timestamp, timestamp))
            results.append(f"Created task “{title}” ({task_id[:8]}).")
        elif kind == "complete_task":
            query = str(action.get("query") or "").lower()
            candidates = rows("SELECT * FROM tasks WHERE status='open' ORDER BY created_at DESC")
            match = next((task for task in candidates if query in task["id"].lower() or query in task["title"].lower()), None)
            if match:
                execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (now_iso(), match["id"]))
                results.append(f"Completed task “{match['title']}”.")
            else:
                results.append(f"Could not find an open task matching “{query}”.")
        elif kind == "save_note":
            title = str(action.get("title") or "Agent note")[:200]
            content = str(action.get("content") or "")[:20000]
            execute("INSERT INTO notes VALUES (?,?,?,?)", (uuid.uuid4().hex, title, content, now_iso()))
            results.append(f"Saved note “{title}”.")
        elif kind == "create_artifact":
            title = str(action.get("title") or "Agent artifact")[:200]
            content = str(action.get("content") or "")[:60000]
            name = f"{slug(title)}-{uuid.uuid4().hex[:6]}.md"
            (ARTIFACTS / name).write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
            results.append(f"Created Markdown artifact “{title}” at /artifacts/{name}.")
        else:
            results.append(f"Skipped unsupported action “{kind}”.")
    return results


def insert_message(role: str, content: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {"id": uuid.uuid4().hex, "role": role, "content": content, "created_at": now_iso(), "meta": json.dumps(meta or {})}
    execute("INSERT INTO messages VALUES (?,?,?,?,?)", tuple(item.values()))
    item["meta"] = meta or {}
    return item


def current_state() -> dict[str, Any]:
    apply_decay()
    messages = rows("SELECT * FROM messages ORDER BY created_at ASC LIMIT 120")
    for message in messages:
        message["meta"] = json.loads(message["meta"] or "{}")
    return {
        "messages": messages,
        "tasks": rows("SELECT * FROM tasks ORDER BY status ASC, updated_at DESC"),
        "notes": rows("SELECT * FROM notes ORDER BY created_at DESC LIMIT 30"),
        "memories": [public_memory(item) for item in rows("SELECT * FROM memories ORDER BY created_at DESC LIMIT 100")],
        "edges": rows("SELECT * FROM edges ORDER BY created_at DESC LIMIT 240"),
        "config": {
            "main_model": MAIN_MODEL,
            "vision_model": VISION_MODEL,
            "demo_mode": not bool(active_api_key()),
            "provider": MODEL_PROVIDER,
            "decay_hours": DECAY_HOURS,
        },
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "demo_mode": not bool(active_api_key()), "provider": MODEL_PROVIDER, "model": MAIN_MODEL}


@app.get("/api/state")
def state() -> dict[str, Any]:
    return current_state()


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    insert_message("user", request.message)
    retrieved = retrieve_memories(request.message)
    for memory in retrieved:
        touch_memory(memory["id"])
    memory_context = recall_from_images(retrieved, request.message)
    history = rows("SELECT role, content FROM messages ORDER BY created_at DESC LIMIT 10")[::-1]
    if history and history[-1]["role"] == "user" and history[-1]["content"] == request.message:
        history.pop()
    plan = plan_response(request.message, memory_context, history)
    planned_actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
    action_results = execute_actions(planned_actions)
    reply = str(plan.get("reply") or "Done.")
    if action_results:
        reply += "\n\n" + "\n".join(f"✓ {result}" for result in action_results)
    assistant = insert_message("assistant", reply, {"retrieved": [m["id"] for m in retrieved], "actions": action_results})
    new_memory = None
    if action_results:
        memory_meta = plan.get("memory") if isinstance(plan.get("memory"), dict) else {}
        memory_summary = str(memory_meta.get("summary") or request.message)
        event = f"REQUEST\n{request.message}\n\nOUTCOME\n{reply}\n\nDURABLE SUMMARY\n{memory_summary}"
        new_memory = create_memory(event)
    return {"message": assistant, "retrieved": [m["id"] for m in retrieved], "memory": new_memory, "state": current_state()}


@app.post("/api/tasks")
def create_task(request: TaskRequest) -> dict[str, Any]:
    task_id, timestamp = uuid.uuid4().hex, now_iso()
    execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (task_id, request.title, request.details, "open", timestamp, timestamp))
    memory = create_memory(f"TASK CREATED\n{request.title}\n\nDETAILS\n{request.details or 'No details supplied.'}")
    return {"task": rows("SELECT * FROM tasks WHERE id=?", (task_id,))[0], "memory": memory, "state": current_state()}


@app.patch("/api/tasks/{task_id}")
def patch_task(task_id: str, request: TaskPatch) -> dict[str, Any]:
    found = rows("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not found:
        raise HTTPException(404, "Task not found")
    status = request.status if request.status in {"open", "done"} else found[0]["status"]
    title = (request.title or found[0]["title"])[:200]
    execute("UPDATE tasks SET status=?, title=?, updated_at=? WHERE id=?", (status, title, now_iso(), task_id))
    if status == "done" and found[0]["status"] != "done":
        create_memory(f"TASK COMPLETED\n{title}\n\nDETAILS\n{found[0]['details']}")
    return {"state": current_state()}


@app.post("/api/notes")
def create_note(request: NoteRequest) -> dict[str, Any]:
    note_id = uuid.uuid4().hex
    execute("INSERT INTO notes VALUES (?,?,?,?)", (note_id, request.title, request.content, now_iso()))
    memory = create_memory(f"NOTE SAVED\n{request.title}\n\n{request.content}")
    return {"memory": memory, "state": current_state()}


@app.post("/api/memories/{memory_id}/access")
def access_memory(memory_id: str) -> dict[str, Any]:
    if not rows("SELECT id FROM memories WHERE id=?", (memory_id,)):
        raise HTTPException(404, "Memory not found")
    touch_memory(memory_id)
    return {"state": current_state()}


@app.post("/api/decay")
def decay() -> dict[str, Any]:
    changed = apply_decay(force=True)
    return {"changed": changed, "state": current_state()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/app.js")
def javascript() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def stylesheet() -> FileResponse:
    return FileResponse(ROOT / "styles.css", media_type="text/css")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
