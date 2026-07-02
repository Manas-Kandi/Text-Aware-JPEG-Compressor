from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from benchmark.runner import BenchmarkRunner
from benchmark.scenarios import DEFAULT_LENGTHS, DEFAULT_SEEDS, build_trajectory


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
IMAGES = DATA / "memory_images"
BENCHMARK_IMAGES = DATA / "benchmark_images"
BENCHMARK_RUNS = DATA / "benchmarks"
ARTIFACTS = DATA / "artifacts"
DB_PATH = DATA / "agent.db"
for directory in (DATA, IMAGES, BENCHMARK_IMAGES, BENCHMARK_RUNS, ARTIFACTS):
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

# Direct OpenAI. When a key is set, any openai/... model id skips the router
# and calls OpenAI itself.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

DEFAULT_MODEL = ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "nvidia/nemotron-3-ultra-550b-a55b"
MAIN_MODEL = os.getenv("MAIN_MODEL", DEFAULT_MODEL)
VISION_MODEL = os.getenv("VISION_MODEL", ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "moonshotai/kimi-k2.6")
MEMORY_MODEL = os.getenv("MEMORY_MODEL", ROUTER_MODEL if MODEL_PROVIDER == "openrouter" else "moonshotai/kimi-k2.6")
BENCHMARK_MODEL = os.getenv("BENCHMARK_MODEL", VISION_MODEL)
ENABLE_MEMORY_LLM = os.getenv("ENABLE_MEMORY_LLM", "true").lower() == "true"
ENABLE_VISION_RECALL = os.getenv("ENABLE_VISION_RECALL", "true").lower() == "true"
DECAY_HOURS = max(1, int(os.getenv("DECAY_INTERVAL_HOURS", "24")))
MAX_RETRIEVED = max(1, min(6, int(os.getenv("MAX_RETRIEVED_MEMORIES", "3"))))
MAX_VISION_RECALL_IMAGES = max(1, min(MAX_RETRIEVED, int(os.getenv("MAX_VISION_RECALL_IMAGES", "2"))))
VISION_RECALL_IMAGE_DETAIL = os.getenv("VISION_RECALL_IMAGE_DETAIL", "low").lower()

DB_LOCK = threading.Lock()
LOGGER = logging.getLogger("piper")


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
                  activation REAL NOT NULL DEFAULT 1.0,
                  retrieval_meta TEXT NOT NULL DEFAULT '{}'
                );
            CREATE TABLE IF NOT EXISTS edges (
                  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                  relation TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0.5,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(source_id) REFERENCES memories(id) ON DELETE CASCADE,
                  FOREIGN KEY(target_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS benchmark_v2_runs (
                  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  status TEXT NOT NULL, phase TEXT NOT NULL, config TEXT NOT NULL,
                  summary TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                  cancel_requested INTEGER NOT NULL DEFAULT 0,
                  total_observations INTEGER NOT NULL DEFAULT 0,
                  completed_observations INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS benchmark_v2_trajectories (
                  run_id TEXT NOT NULL, id TEXT NOT NULL, profile TEXT NOT NULL,
                  seed INTEGER NOT NULL, length INTEGER NOT NULL, arms_order TEXT NOT NULL,
                  created_at TEXT NOT NULL, PRIMARY KEY(run_id,id),
                  FOREIGN KEY(run_id) REFERENCES benchmark_v2_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS benchmark_v2_observations (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, trajectory_id TEXT NOT NULL,
                  profile TEXT NOT NULL, arm TEXT NOT NULL, trajectory_length INTEGER NOT NULL,
                  checkpoint INTEGER NOT NULL, probe_type TEXT NOT NULL, prompt TEXT NOT NULL,
                  expected TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
                  correct INTEGER NOT NULL DEFAULT 0, fields_correct INTEGER NOT NULL DEFAULT 0,
                  fields_total INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
                  context_hash TEXT NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0,
                  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
                  cost REAL NOT NULL DEFAULT 0, payload_bytes INTEGER NOT NULL DEFAULT 0,
                  page_count INTEGER NOT NULL DEFAULT 0, resolved_model TEXT NOT NULL DEFAULT '',
                  provider TEXT NOT NULL DEFAULT '', attempt_count INTEGER NOT NULL DEFAULT 0,
                  error_type TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '',
                  UNIQUE(run_id,trajectory_id,arm,checkpoint),
                  FOREIGN KEY(run_id) REFERENCES benchmark_v2_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS benchmark_v2_attempts (
                  id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                  status TEXT NOT NULL, latency_ms INTEGER NOT NULL, error TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(observation_id) REFERENCES benchmark_v2_observations(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS benchmark_v2_artifacts (
                  run_id TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
                  created_at TEXT NOT NULL, PRIMARY KEY(run_id,path),
                  FOREIGN KEY(run_id) REFERENCES benchmark_v2_runs(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
            if "retrieval_meta" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN retrieval_meta TEXT NOT NULL DEFAULT '{}'")
            conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
                memory_id UNINDEXED, label, summary, tags, entities, actions,
                outcomes, procedures, task_type, tokenize='porter unicode61'
                )"""
            )
            conn.commit()
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


class BenchmarkRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    lengths: list[int] = Field(default_factory=lambda: list(DEFAULT_LENGTHS))
    seeds: list[int] = Field(default_factory=lambda: list(DEFAULT_SEEDS))
    closed_loop: bool = True
    density_sweep: bool = False
    density_lengths: list[int] = Field(default_factory=list)

    def normalized(self) -> dict[str, Any]:
        lengths = sorted(set(self.lengths))
        seeds = list(dict.fromkeys(self.seeds))
        if not lengths or len(lengths) > 4 or any(length < 4 or length > 128 for length in lengths):
            raise ValueError("lengths must contain one to four values between 4 and 128")
        if not seeds or len(seeds) > 5:
            raise ValueError("seeds must contain one to five values")
        density_lengths = sorted(set(self.density_lengths or [16, 32, 64, 128, 256, 512]))
        if self.density_sweep and (len(density_lengths) > 6 or any(length < 8 or length > 512 for length in density_lengths)):
            raise ValueError("density_lengths must contain up to six values between 8 and 512")
        return {
            "model": self.model or default_benchmark_model(),
            "lengths": lengths,
            "seeds": seeds,
            "closed_loop": self.closed_loop,
            "density_sweep": self.density_sweep,
            "density_lengths": density_lengths if self.density_sweep else [],
        }


app = FastAPI(title="Piper Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/memory-images", StaticFiles(directory=IMAGES), name="memory-images")
app.mount("/benchmark-images", StaticFiles(directory=BENCHMARK_IMAGES), name="benchmark-images")
app.mount("/benchmark-runs", StaticFiles(directory=BENCHMARK_RUNS), name="benchmark-runs")
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
    item["retrieval_meta"] = json.loads(item.get("retrieval_meta") or "{}")
    item["image_url"] = f"/memory-images/{item.pop('image_name')}"
    return item


def active_api_key() -> str:
    return OPENROUTER_API_KEY if MODEL_PROVIDER == "openrouter" else NVIDIA_API_KEY


def resolve_route(model: str) -> tuple[str, str]:
    """Which API a model id goes to. openai/... and bare ids go straight to OpenAI when we have a key."""
    if OPENAI_API_KEY:
        if model.startswith("openai/"):
            return "openai", model.split("/", 1)[1]
        if "/" not in model:
            return "openai", model
    return MODEL_PROVIDER, model


def openai_reasoning_model(name: str) -> bool:
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


def qualify_model(model: str) -> str:
    """Bare ids like gpt-5-nano-2025-08-07 get the openai/ prefix so pinning stays explicit."""
    model = model.strip()
    if OPENAI_API_KEY and model and "/" not in model:
        return f"openai/{model}"
    return model


def has_key_for(model: str) -> bool:
    provider, _ = resolve_route(model)
    return bool(OPENAI_API_KEY if provider == "openai" else active_api_key())


def model_chat(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1800,
    thinking: bool = False,
    json_mode: bool = False,
    with_metadata: bool = False,
    temperature: float = 0.35,
) -> Any:
    provider, provider_model = resolve_route(model)
    api_key = OPENAI_API_KEY if provider == "openai" else active_api_key()
    if not api_key:
        raise RuntimeError(f"{provider.upper()} API key is not configured")
    payload: dict[str, Any] = {
        "model": provider_model,
        "messages": messages,
        "temperature": temperature,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if provider == "openai":
        # OpenAI wants max_completion_tokens. GPT-5 and o-series also refuse pinned
        # temperature/top_p and spend part of the budget on reasoning tokens, so give headroom.
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        if openai_reasoning_model(provider_model):
            payload.pop("temperature", None)
            payload.pop("top_p", None)
            payload["reasoning_effort"] = "medium" if thinking else "minimal"
            payload["max_completion_tokens"] += 512
        endpoint = f"{OPENAI_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    elif provider == "openrouter":
        # Reasoning models spend hidden tokens before answering; without headroom
        # a small max_tokens yields an empty answer.
        if openai_reasoning_model(provider_model.split("/", 1)[-1]):
            payload["max_tokens"] += 512
        if thinking and OPENROUTER_REASONING:
            payload["reasoning"] = {"effort": "medium"}
        endpoint = f"{OPENROUTER_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_HTTP_REFERER,
            "X-Title": OPENROUTER_APP_TITLE,
        }
    else:
        if thinking and provider_model.startswith("nvidia/nemotron"):
            payload["chat_template_kwargs"] = {"enable_thinking": True}
            payload["reasoning_budget"] = min(4096, max_tokens * 2)
        endpoint = f"{NVIDIA_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    attempts = 3 if provider == "openrouter" and model == "openrouter/free" else 1
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
            last_error = f"{provider.title()} API {response.status_code}: {error_message}"
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
                if with_metadata:
                    resolved_model = str(data.get("model") or provider_model)
                    if provider == "openai" and not resolved_model.startswith("openai/"):
                        resolved_model = f"openai/{resolved_model}"
                    return {
                        "content": content,
                        "model": resolved_model,
                        "provider": str(data.get("provider") or provider),
                        "usage": data.get("usage") or {},
                        "cost": (data.get("usage") or {}).get("cost") or data.get("cost") or 0,
                    }
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


def clean_list(values: Any, limit: int = 10, max_length: int = 60) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value)).strip().lower()[:max_length]
        if normalized and normalized not in result:
            result.append(normalized)
    return result[:limit]


def infer_task_type(text: str) -> str:
    lowered = text.lower()
    groups = {
        "coding": ("code", "debug", "implement", "function", "repository", "api"),
        "planning": ("plan", "roadmap", "design", "architecture", "strategy"),
        "research": ("research", "compare", "benchmark", "analyze", "evaluate"),
        "writing": ("write", "document", "brief", "report", "artifact"),
        "task-management": ("task", "complete", "todo", "remind"),
        "knowledge": ("note", "remember", "memory", "context"),
    }
    scores = {name: sum(term in lowered for term in terms) for name, terms in groups.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def deterministic_retrieval_meta(text: str) -> dict[str, Any]:
    entity_candidates = re.findall(r"\b(?:[A-Z][A-Za-z0-9_-]+(?:\s+[A-Z][A-Za-z0-9_-]+){0,2}|[A-Za-z0-9_.-]+\.(?:md|py|js|ts|json|jpg|pdf))\b", text)
    entities = [item.lower() for item in entity_candidates if item.upper() not in {"REQUEST", "OUTCOME", "DURABLE SUMMARY", "TASK CREATED", "TASK COMPLETED", "NOTE SAVED"}]
    outcome_block = text.split("OUTCOME", 1)[-1] if "OUTCOME" in text else text
    outcome_lines = [line.strip() for line in outcome_block.splitlines() if line.strip()][:4]
    action_terms = [word for word in keywords(text, 24) if word in {
        "create", "created", "complete", "completed", "save", "saved", "write", "wrote",
        "design", "designed", "implement", "implemented", "analyze", "analyzed", "review", "build", "built"
    }]
    procedures = [line.strip()[:180] for line in text.splitlines() if re.match(r"^(?:\d+[.)]|[-*]\s|step\s+\d+)", line.strip(), re.I)][:8]
    search_terms = keywords(text, 18)
    return {
        "entities": clean_list(entities, 12),
        "actions": clean_list(action_terms, 10),
        "outcomes": clean_list(outcome_lines, 6, 180),
        "procedures": clean_list(procedures, 8, 180),
        "task_type": infer_task_type(text),
        "temporal": clean_list(re.findall(r"\b(?:20\d{2}-\d{2}-\d{2}|today|tomorrow|yesterday|week\s+\d+)\b", text, re.I), 6),
        "search_terms": search_terms,
    }


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def label_memory(event_text: str) -> tuple[str, str, list[str], dict[str, Any]]:
    fallback_tags = keywords(event_text)
    fallback_summary = event_text[:3200]
    fallback_meta = deterministic_retrieval_meta(event_text)
    if not (active_api_key() and ENABLE_MEMORY_LLM):
        return safe_label(event_text), fallback_summary, fallback_tags, fallback_meta
    prompt = (
        "Create retrieval metadata for this completed agent event. Return only JSON with: "
        '"label" (2-5 discriminative words), "summary" (max 900 characters), "tags" (3-8 concepts), '
        '"entities" (people, projects, files, systems, identifiers), "actions" (operations performed), '
        '"outcomes" (concrete resulting states), "procedures" (reusable steps), "task_type" (one category), '
        '"temporal" (dates or phases), and "search_terms" (8-15 likely future query terms and aliases). '
        "Prefer specific nouns over generic words. Preserve exact identifiers.\n\nEVENT:\n" + event_text[:12000]
    )
    try:
        data = parse_json_object(model_chat(MEMORY_MODEL, [{"role": "user", "content": prompt}], 600, json_mode=True))
        label = str(data.get("label") or safe_label(event_text))[:80]
        summary = str(data.get("summary") or fallback_summary)[:3200]
        tags = [str(tag).lower()[:32] for tag in data.get("tags", fallback_tags)][:8]
        meta = {
            "entities": clean_list(data.get("entities"), 12) or fallback_meta["entities"],
            "actions": clean_list(data.get("actions"), 10) or fallback_meta["actions"],
            "outcomes": clean_list(data.get("outcomes"), 6, 180) or fallback_meta["outcomes"],
            "procedures": clean_list(data.get("procedures"), 8, 180) or fallback_meta["procedures"],
            "task_type": str(data.get("task_type") or fallback_meta["task_type"]).lower()[:40],
            "temporal": clean_list(data.get("temporal"), 6) or fallback_meta["temporal"],
            "search_terms": clean_list(data.get("search_terms"), 15) or fallback_meta["search_terms"],
        }
        return label, summary, tags, meta
    except Exception:
        return safe_label(event_text), fallback_summary, fallback_tags, fallback_meta


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
    exif = Image.Exif()
    exif[270] = json.dumps({"memory_id": memory_id, "label": label, "tags": tags[:8]}, ensure_ascii=True)
    canvas.save(IMAGES / name, "JPEG", quality=profile["quality"], optimize=True, progressive=True, exif=exif)
    return name


def create_memory(event_text: str) -> dict[str, Any]:
    label, summary, tags, retrieval_meta = label_memory(event_text)
    memory_id, timestamp = uuid.uuid4().hex, now_iso()
    image_name = render_memory_image(memory_id, label, summary, tags, 0)
    execute(
        """INSERT INTO memories
        (id,label,summary,tags,image_name,created_at,last_accessed,access_count,decay_stage,activation,retrieval_meta)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (memory_id, label, summary, json.dumps(tags), image_name, timestamp, timestamp, 0, 0, 1.0, json.dumps(retrieval_meta)),
    )
    index_memory(memory_id, label, summary, tags, retrieval_meta)
    connect_memory(memory_id, summary + " " + " ".join(tags + retrieval_meta.get("search_terms", [])))
    return public_memory(rows("SELECT * FROM memories WHERE id=?", (memory_id,))[0])


def create_memory_safely(event_text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Keep user-visible actions successful if the derived memory side effect fails."""
    try:
        return create_memory(event_text), None
    except Exception as error:
        LOGGER.exception("Memory formation failed after a successful action")
        return None, f"The action succeeded, but its memory node could not be formed: {error}"


def index_memory(memory_id: str, label: str, summary: str, tags: list[str], meta: dict[str, Any]) -> None:
    fields = (
        memory_id, label, summary, " ".join(tags),
        " ".join(meta.get("entities", [])), " ".join(meta.get("actions", [])),
        " ".join(meta.get("outcomes", [])), " ".join(meta.get("procedures", [])),
        str(meta.get("task_type", "general")),
    )
    execute("DELETE FROM memory_search WHERE memory_id=?", (memory_id,))
    execute("INSERT INTO memory_search VALUES (?,?,?,?,?,?,?,?,?)", fields)


def ensure_memory_index() -> None:
    indexed = {row["memory_id"] for row in rows("SELECT memory_id FROM memory_search")}
    for item in rows("SELECT * FROM memories"):
        if item["id"] in indexed:
            continue
        tags = json.loads(item["tags"] or "[]")
        meta = json.loads(item.get("retrieval_meta") or "{}") or deterministic_retrieval_meta(item["summary"])
        if not item.get("retrieval_meta") or item["retrieval_meta"] == "{}":
            execute("UPDATE memories SET retrieval_meta=? WHERE id=?", (json.dumps(meta), item["id"]))
        index_memory(item["id"], item["label"], item["summary"], tags, meta)


def connect_memory(memory_id: str, text: str) -> None:
    candidates = rows("SELECT id, summary, tags, retrieval_meta FROM memories WHERE id != ? ORDER BY created_at DESC LIMIT 18", (memory_id,))
    source_words = set(keywords(text, 20))
    connected = 0
    for candidate in candidates:
        target_words = set(keywords(candidate["summary"] + " " + candidate["tags"] + " " + candidate["retrieval_meta"], 30))
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
    ensure_memory_index()
    query_terms = keywords(query, 16)
    if not query_terms:
        return [public_memory(item) for item in rows("SELECT * FROM memories ORDER BY activation DESC, last_accessed DESC LIMIT ?", (MAX_RETRIEVED,))]
    match_query = " OR ".join(f'"{term}"*' for term in query_terms)
    try:
        candidates = rows(
            """SELECT m.*, bm25(memory_search,0.0,6.0,1.0,3.0,6.0,3.5,4.5,3.5,2.5) AS search_rank
            FROM memory_search JOIN memories m ON m.id=memory_search.memory_id
            WHERE memory_search MATCH ? ORDER BY search_rank LIMIT 24""",
            (match_query,),
        )
    except sqlite3.OperationalError:
        candidates = []
    if not candidates:
        candidates = rows("SELECT *, 0 AS search_rank FROM memories ORDER BY activation DESC, last_accessed DESC LIMIT 24")
    query_set = set(query_terms)
    scored: list[tuple[float, dict[str, Any]]] = []
    for position, candidate in enumerate(candidates):
        meta = json.loads(candidate.get("retrieval_meta") or "{}")
        entity_terms = set(keywords(" ".join(meta.get("entities", [])), 30))
        outcome_terms = set(keywords(" ".join(meta.get("outcomes", [])), 30))
        exact_boost = len(query_set & entity_terms) * 1.2 + len(query_set & outcome_terms) * 0.8
        rank_score = 1.0 / (position + 1)
        score = rank_score * 4 + exact_boost + float(candidate["activation"]) * 0.3 + math.log1p(candidate["access_count"]) * 0.06
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


def memory_recall_brief(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "No relevant prior memory was found."
    blocks: list[str] = []
    for index, memory in enumerate(memories, 1):
        meta = memory.get("retrieval_meta") or {}
        lines = [
            f"IMAGE {index}: {memory['label']}",
            f"summary: {memory['summary']}",
        ]
        if memory.get("tags"):
            lines.append("tags: " + ", ".join(memory["tags"][:8]))
        if meta.get("entities"):
            lines.append("entities: " + ", ".join(meta["entities"][:8]))
        if meta.get("outcomes"):
            lines.append("outcomes: " + " | ".join(meta["outcomes"][:4]))
        if meta.get("procedures"):
            lines.append("procedures: " + " | ".join(meta["procedures"][:3]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def recall_from_images(memories: list[dict[str, Any]], query: str) -> str:
    if not memories:
        return "No relevant prior memory was found."
    fallback = memory_recall_brief(memories)
    if not (active_api_key() and ENABLE_VISION_RECALL):
        return fallback
    visual_memories = memories[:MAX_VISION_RECALL_IMAGES]
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "Use the TEXT RETRIEVAL BRIEF first. Inspect the indexed memory-node images only to recover "
            "visual details, exact wording, or layout that the brief may omit. Preserve concrete steps and "
            "outcomes. Cite IMAGE numbers when grounding a fact.\n\nQUERY\n"
            + query + "\n\nTEXT RETRIEVAL BRIEF\n" + fallback
        ),
    }]
    for index, memory in enumerate(visual_memories, 1):
        content.append({"type": "text", "text": f"IMAGE {index}: {memory['label']}"})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(memory["image_url"]), "detail": VISION_RECALL_IMAGE_DETAIL}})
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
    reply = "I’m running in local demo mode. I can still manage the task and memory graph; add OPENROUTER_API_KEY to .env for model-generated replies."
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


BENCHMARK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper-research")
BENCHMARK_FUTURES: dict[str, Any] = {}
BENCHMARK_FUTURES_LOCK = threading.Lock()


ROUTER_ALIASES = {"openrouter/free", "openrouter/auto", "auto", "router", "nvidia/auto"}


def is_router_alias(model: str) -> bool:
    normalized = model.strip().lower()
    return not normalized or "/" not in normalized or normalized in ROUTER_ALIASES


def validate_benchmark_model(model: str) -> None:
    if is_router_alias(model):
        raise ValueError(
            "Pick one real vision model, like provider/model. A router alias can swap models "
            "between the two arms, and that breaks the comparison."
        )


def base_slug(model: str) -> str:
    # ":free" and similar variant suffixes route to the same weights; only the base slug must match.
    return model.strip().lower().split(":", 1)[0]


def same_pinned_model(requested: str, resolved: str) -> bool:
    # Providers may resolve a slug to a dated snapshot of the same model
    # (gemma-4-26b-a4b-it -> gemma-4-26b-a4b-it-20260403). Accept that; the
    # per-run comparable_model check still catches drift between the two arms.
    requested_base, resolved_base = base_slug(requested), base_slug(resolved)
    return resolved_base == requested_base or resolved_base.startswith(requested_base + "-")


def benchmark_model_call(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    result = model_chat(model, messages, max_tokens=100, thinking=False, with_metadata=True, temperature=0.0)
    result.setdefault("model", model)
    if not same_pinned_model(model, str(result["model"])):
        raise RuntimeError(f"Pinned-model violation: requested {model}, provider resolved {result['model']}")
    return result


VISION_MODEL_CACHE: dict[str, Any] = {"at": 0.0, "models": []}


def openrouter_vision_models() -> list[dict[str, Any]]:
    if MODEL_PROVIDER != "openrouter":
        return []
    response = requests.get(f"{OPENROUTER_BASE_URL}/models", timeout=20)
    response.raise_for_status()
    models: list[dict[str, Any]] = []
    for item in response.json().get("data") or []:
        architecture = item.get("architecture") or {}
        if "image" not in (architecture.get("input_modalities") or []):
            continue
        pricing = item.get("pricing") or {}
        try:
            free = float(pricing.get("prompt") or 0) == 0 and float(pricing.get("completion") or 0) == 0
        except (TypeError, ValueError):
            free = False
        models.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("id") or ""),
            "free": free,
            "context_length": int(item.get("context_length") or 0),
        })
    return [model for model in models if model["id"] and not is_router_alias(model["id"])]


def openai_direct_vision_models() -> list[dict[str, Any]]:
    """Vision-capable chat models on the user's own OpenAI account."""
    if not OPENAI_API_KEY:
        return []
    response = requests.get(f"{OPENAI_BASE_URL}/models", headers={"Authorization": f"Bearer {OPENAI_API_KEY}"}, timeout=20)
    response.raise_for_status()
    excluded = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "moderation", "search", "codex")
    models: list[dict[str, Any]] = []
    for item in response.json().get("data") or []:
        model_id = str(item.get("id") or "")
        if not model_id.startswith(("gpt-5", "gpt-4o", "gpt-4.1")) or any(term in model_id for term in excluded):
            continue
        # OpenAI's models endpoint does not report context windows; these are rough family sizes.
        context = 400000 if model_id.startswith("gpt-5") else 1000000 if model_id.startswith("gpt-4.1") else 128000
        models.append({"id": f"openai/{model_id}", "name": f"OpenAI {model_id} (direct)", "free": False, "context_length": context})
    return models


def list_vision_models() -> list[dict[str, Any]]:
    if VISION_MODEL_CACHE["models"] and time.time() - VISION_MODEL_CACHE["at"] < 600:
        return VISION_MODEL_CACHE["models"]
    merged: dict[str, dict[str, Any]] = {}
    errors: list[Exception] = []
    for source in (openrouter_vision_models, openai_direct_vision_models):
        try:
            for model in source():
                merged[model["id"]] = model
        except Exception as error:
            errors.append(error)
    if not merged and errors:
        raise errors[0]
    models = sorted(merged.values(), key=lambda model: (not model["free"], model["name"].lower()))
    VISION_MODEL_CACHE.update(at=time.time(), models=models)
    return models


def default_benchmark_model() -> str:
    """BENCHMARK_MODEL if pinned; otherwise the free vision model with the biggest context window.

    Preview/experimental slugs lose: providers pull them fast, which breaks resume and reruns.
    """
    if not is_router_alias(BENCHMARK_MODEL):
        return BENCHMARK_MODEL
    try:
        models = list_vision_models()
    except Exception:
        return ""
    pool = [model for model in models if model["free"]] or models
    if not pool:
        return ""
    def stable_then_big(model: dict[str, Any]) -> tuple[bool, int]:
        preview = any(term in model["id"].lower() for term in ("preview", "-exp", "beta"))
        return (not preview, model["context_length"])
    return max(pool, key=stable_then_big)["id"]


def benchmark_runner(model: str) -> BenchmarkRunner:
    return BenchmarkRunner(DB_PATH, BENCHMARK_RUNS, lambda messages: benchmark_model_call(model, messages))


def run_letters(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters or "A"


def run_folder_index(label: str) -> int | None:
    match = re.fullmatch(r"run ([A-Z]+)", label)
    if not match:
        return None
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def next_benchmark_run_folder() -> str:
    used: set[int] = set()
    for path in BENCHMARK_RUNS.iterdir():
        if path.is_dir():
            index = run_folder_index(path.name)
            if index:
                used.add(index)
    for row in rows("SELECT config FROM benchmark_v2_runs"):
        try:
            index = run_folder_index(str(json.loads(row["config"] or "{}").get("run_folder") or ""))
        except (json.JSONDecodeError, TypeError):
            index = None
        if index:
            used.add(index)
    index = 1
    while index in used:
        index += 1
    return f"run {run_letters(index)}"


def benchmark_run_folder(config: dict[str, Any], run_id: str) -> str:
    return str(config.get("run_folder") or run_id)


def benchmark_public(run: dict[str, Any]) -> dict[str, Any]:
    item = dict(run)
    item["config"] = json.loads(item.get("config") or "{}")
    item["summary"] = json.loads(item.get("summary") or "{}")
    item["run_folder"] = benchmark_run_folder(item["config"], item["id"])
    total = int(item.get("total_observations") or 0)
    completed = int(item.get("completed_observations") or 0)
    item["progress"] = {"completed": completed, "total": total, "percent": round(completed / max(1, total) * 100, 1)}
    artifacts = rows("SELECT path,size FROM benchmark_v2_artifacts WHERE run_id=? ORDER BY path", (item["id"],))
    folder = quote(item["run_folder"], safe="")
    item["artifacts"] = [{**artifact, "url": f"/benchmark-runs/{folder}/{quote(artifact['path'], safe='/')}"} for artifact in artifacts]
    item["warnings"] = [
        "This pilot estimates effects and variance; it does not establish model-independent superiority.",
        "Image token accounting may not be directly comparable to text token accounting.",
    ]
    return item


def list_research_benchmarks() -> list[dict[str, Any]]:
    return [benchmark_public(item) for item in rows("SELECT * FROM benchmark_v2_runs ORDER BY created_at DESC")]


def get_research_benchmark(run_id: str) -> dict[str, Any] | None:
    found = rows("SELECT * FROM benchmark_v2_runs WHERE id=?", (run_id,))
    return benchmark_public(found[0]) if found else None


def compact_text(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit - 1] + "..."


def percent(value: float) -> str:
    return f"{value:.1f}%"


def benchmark_diagnostic_log(run_id: str) -> str:
    found = rows("SELECT * FROM benchmark_v2_runs WHERE id=?", (run_id,))
    if not found:
        raise ValueError("Benchmark run not found")
    run = benchmark_public(found[0])
    observations = rows(
        """SELECT o.*, t.seed, t.length FROM benchmark_v2_observations o
        LEFT JOIN benchmark_v2_trajectories t ON t.run_id = o.run_id AND t.id = o.trajectory_id
        WHERE o.run_id=? ORDER BY o.profile, o.trajectory_id, o.checkpoint, o.arm""",
        (run_id,),
    )
    summary = run.get("summary") or {}
    primary = summary.get("profiles", {}).get("primary", {})
    arms = primary.get("arms", {})
    tradeoff = primary.get("tradeoff", {})
    lines = [
        "PIEDPIPER JPEG CONTEXT DIAGNOSTIC LOG",
        "Paste this into Codex when asking for help diagnosing JPEG-as-context behavior.",
        "",
        "RUN",
        f"- folder: {run.get('run_folder')}",
        f"- id: {run['id']}",
        f"- status: {run['status']} / {run['phase']}",
        f"- created_at: {run['created_at']}",
        f"- updated_at: {run['updated_at']}",
        f"- model: {run['config'].get('model')}",
        f"- lengths: {run['config'].get('lengths')}",
        f"- seeds: {run['config'].get('seeds')}",
        f"- closed_loop: {run['config'].get('closed_loop', True)}",
        f"- density_sweep: {run['config'].get('density_sweep', False)}",
        f"- density_lengths: {run['config'].get('density_lengths', [])}",
        f"- observations: {run['progress']['completed']} / {run['progress']['total']}",
        "",
        "PRIMARY SUMMARY",
    ]
    for arm_name in ("jpeg", "text"):
        arm = arms.get(arm_name)
        if not arm:
            continue
        lines.append(
            f"- {arm_name}: field_accuracy={arm.get('field_accuracy')}%, probe_accuracy={arm.get('probe_accuracy')}%, "
            f"input_tokens={arm.get('input_tokens')}, output_tokens={arm.get('output_tokens')}, "
            f"payload_bytes={arm.get('payload_bytes')}, median_latency_ms={arm.get('median_latency_ms')}, "
            f"cost={arm.get('cost')}, failures={arm.get('failures')}, ci95={arm.get('ci95')}"
        )
    if tradeoff:
        lines.extend([
            "",
            "TOKEN / QUALITY TRADEOFF",
            f"- input_tokens_saved_by_jpeg: {tradeoff.get('input_tokens_saved')}",
            f"- input_token_savings_percent: {tradeoff.get('input_token_savings_percent')}%",
            f"- accuracy_delta_points_jpeg_minus_text: {tradeoff.get('accuracy_delta_points')}",
            f"- latency_delta_ms_jpeg_minus_text: {tradeoff.get('latency_delta_ms')}",
            f"- payload_bytes_delta_percent_jpeg_minus_text: {tradeoff.get('payload_bytes_delta_percent')}%",
            f"- cost_delta_jpeg_minus_text: {tradeoff.get('cost_delta')}",
        ])
    if summary.get("token_crossover"):
        lines.extend(["", "TOKEN CROSSOVER"])
        for profile, crossover in summary["token_crossover"].items():
            lines.append(f"- {profile}: first_jpeg_token_win_length={crossover.get('first_jpeg_token_win_length')}")
            for point in crossover.get("points", [])[:8]:
                lines.append(
                    f"  - length={point.get('trajectory_length')} jpeg={point.get('jpeg_input_tokens')} "
                    f"text={point.get('text_input_tokens')} saved_by_jpeg={point.get('input_tokens_saved_by_jpeg')}"
                )
    probe_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    paired: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in observations:
        key = (row["profile"], row["arm"], row["probe_type"])
        bucket = probe_groups.setdefault(key, {"total": 0, "correct": 0, "tokens": 0, "payload": 0, "pages": 0, "latency": []})
        bucket["total"] += 1
        bucket["correct"] += int(row["correct"])
        bucket["tokens"] += int(row["input_tokens"])
        bucket["payload"] += int(row["payload_bytes"])
        bucket["pages"] += int(row["page_count"])
        bucket["latency"].append(int(row["latency_ms"]))
        paired.setdefault((row["profile"], row["trajectory_id"], row["checkpoint"]), {})[row["arm"]] = row
    if probe_groups:
        lines.extend(["", "ACCURACY BY PROFILE / ARM / PROBE"])
        for key in sorted(probe_groups):
            bucket = probe_groups[key]
            mean_pages = bucket["pages"] / max(1, bucket["total"])
            mean_latency = sum(bucket["latency"]) / max(1, len(bucket["latency"]))
            accuracy = bucket["correct"] / max(1, bucket["total"]) * 100
            lines.append(
                f"- {key[0]} / {key[1]} / {key[2]}: {bucket['correct']}/{bucket['total']} correct "
                f"({percent(accuracy)}), input_tokens={bucket['tokens']}, payload_bytes={bucket['payload']}, "
                f"avg_pages={mean_pages:.2f}, avg_latency_ms={mean_latency:.0f}"
            )
    pair_lines: list[str] = []
    for pair_key in sorted(paired):
        jpeg = paired[pair_key].get("jpeg")
        text = paired[pair_key].get("text")
        if not jpeg or not text or int(jpeg["correct"]) == int(text["correct"]):
            continue
        pair_lines.append(
            f"- {pair_key[0]} {pair_key[1]} c{pair_key[2]} {jpeg['probe_type']}: "
            f"jpeg_correct={jpeg['correct']} text_correct={text['correct']} expected={compact_text(jpeg['expected'])} "
            f"jpeg_answer={compact_text(jpeg['answer'])} text_answer={compact_text(text['answer'])}"
        )
    if pair_lines:
        lines.extend(["", "PAIRED DISAGREEMENTS (first 40)", *pair_lines[:40]])
    failure_lines: list[str] = []
    for row in observations:
        if row["arm"] != "jpeg" or int(row["correct"]):
            continue
        counterpart = paired.get((row["profile"], row["trajectory_id"], row["checkpoint"]), {}).get("text")
        failure_lines.append(
            f"- {row['profile']} {row['trajectory_id']} length={row['trajectory_length']} checkpoint={row['checkpoint']} "
            f"probe={row['probe_type']} pages={row['page_count']} input_tokens={row['input_tokens']} "
            f"payload_bytes={row['payload_bytes']} latency_ms={row['latency_ms']} text_correct={counterpart['correct'] if counterpart else '?'} "
            f"prompt={compact_text(row['prompt'])} expected={compact_text(row['expected'])} answer={compact_text(row['answer'])}"
        )
    if failure_lines:
        lines.extend(["", "JPEG FAILURES (first 40)", *failure_lines[:40]])
    error_lines = [
        f"- {row['profile']} {row['arm']} {row['trajectory_id']} c{row['checkpoint']}: {row['error_type']} {compact_text(row['error'], 240)}"
        for row in observations
        if row.get("error_type") or row.get("error")
    ]
    if error_lines:
        lines.extend(["", "ERRORS", *error_lines[:30]])
    artifact_paths = [item["path"] for item in run.get("artifacts", [])]
    if artifact_paths:
        lines.extend(["", "ARTIFACTS", *[f"- {path}" for path in artifact_paths]])
    return "\n".join(lines) + "\n"


def public_observation(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    try:
        item["expected"] = json.loads(item["expected"])
    except (json.JSONDecodeError, TypeError):
        pass
    return item


def rebuild_observation_context(run_id: str, observation: dict[str, Any]) -> tuple[str, bool]:
    """Rebuild the exact context an observation saw. Trajectories are deterministic, so we
    regenerate from the stored seed and verify against the recorded hash."""
    trajectory_rows = rows(
        "SELECT seed, length FROM benchmark_v2_trajectories WHERE run_id=? AND id=?",
        (run_id, observation["trajectory_id"]),
    )
    if not trajectory_rows:
        return "", False
    trajectory = build_trajectory(trajectory_rows[0]["seed"], trajectory_rows[0]["length"])
    probe = next((item for item in trajectory.probes if item.checkpoint == observation["checkpoint"]), None)
    if not probe:
        return "", False
    context = probe.context
    if observation["profile"] == "closed_loop":
        prior = rows(
            "SELECT checkpoint, answer FROM benchmark_v2_observations WHERE run_id=? AND trajectory_id=? AND arm=? AND checkpoint<? ORDER BY checkpoint",
            (run_id, observation["trajectory_id"], observation["arm"], observation["checkpoint"]),
        )
        feedback = [f"At checkpoint {row['checkpoint']}, the model answered: {row['answer'] or '[no answer]'}" for row in prior]
        if feedback:
            context += "\n\nMODEL FEEDBACK\n" + "\n".join(feedback)
    verified = hashlib.sha256(context.encode("utf-8")).hexdigest() == observation["context_hash"]
    return context, verified


def observation_page_urls(run_id: str, observation: dict[str, Any]) -> list[str]:
    run_rows = rows("SELECT config FROM benchmark_v2_runs WHERE id=?", (run_id,))
    config = json.loads(run_rows[0]["config"] or "{}") if run_rows else {}
    folder = benchmark_run_folder(config, run_id)
    pages_dir = BENCHMARK_RUNS / folder / "pages"
    if not pages_dir.exists():
        return []
    pattern = f"{observation['trajectory_id']}-c{observation['checkpoint']}-p*.jpg"
    url_folder = quote(folder, safe="")
    return [f"/benchmark-runs/{url_folder}/pages/{quote(path.name)}" for path in sorted(pages_dir.glob(pattern))]


def latest_research_benchmark() -> dict[str, Any] | None:
    found = rows("SELECT * FROM benchmark_v2_runs ORDER BY created_at DESC LIMIT 1")
    return benchmark_public(found[0]) if found else None


def submit_research_benchmark(config: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    model = qualify_model(str(config["model"]))
    config = {**config, "model": model}
    validate_benchmark_model(model)
    if not has_key_for(model):
        raise ValueError(f"No API key for {model}. Add the right key to .env, then run the test.")
    run_id = run_id or uuid.uuid4().hex
    timestamp = now_iso()
    existing = rows("SELECT id FROM benchmark_v2_runs WHERE id=?", (run_id,))
    if existing:
        execute("UPDATE benchmark_v2_runs SET status='queued',phase='queued',cancel_requested=0,error='',updated_at=? WHERE id=?", (timestamp, run_id))
    else:
        config = {**config, "run_folder": next_benchmark_run_folder()}
        execute(
            "INSERT INTO benchmark_v2_runs (id,created_at,updated_at,status,phase,config) VALUES (?,?,?,?,?,?)",
            (run_id, timestamp, timestamp, "queued", "queued", json.dumps(config, sort_keys=True)),
        )
    runner = benchmark_runner(model)
    with BENCHMARK_FUTURES_LOCK:
        current = BENCHMARK_FUTURES.get(run_id)
        if not current or current.done():
            BENCHMARK_FUTURES[run_id] = BENCHMARK_EXECUTOR.submit(runner.run, run_id)
    return get_research_benchmark(run_id) or {}


def resume_research_benchmark(run_id: str) -> dict[str, Any]:
    found = get_research_benchmark(run_id)
    if not found:
        raise ValueError("Benchmark run not found")
    if found["status"] == "complete":
        return found
    return submit_research_benchmark(found["config"], run_id)


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
        "latest_benchmark": latest_research_benchmark(),
        "config": {
            "main_model": MAIN_MODEL,
            "vision_model": VISION_MODEL,
            "demo_mode": not bool(active_api_key()),
            "provider": MODEL_PROVIDER,
            "benchmark_model": "" if is_router_alias(BENCHMARK_MODEL) else BENCHMARK_MODEL,
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
    memory_warning = None
    if action_results:
        memory_meta = plan.get("memory") if isinstance(plan.get("memory"), dict) else {}
        memory_summary = str(memory_meta.get("summary") or request.message)
        event = f"REQUEST\n{request.message}\n\nOUTCOME\n{reply}\n\nDURABLE SUMMARY\n{memory_summary}"
        new_memory, memory_warning = create_memory_safely(event)
    return {"message": assistant, "retrieved": [m["id"] for m in retrieved], "memory": new_memory, "warning": memory_warning, "state": current_state()}


@app.post("/api/tasks")
def create_task(request: TaskRequest) -> dict[str, Any]:
    task_id, timestamp = uuid.uuid4().hex, now_iso()
    execute("INSERT INTO tasks VALUES (?,?,?,?,?,?)", (task_id, request.title, request.details, "open", timestamp, timestamp))
    memory, warning = create_memory_safely(f"TASK CREATED\n{request.title}\n\nDETAILS\n{request.details or 'No details supplied.'}")
    return {"task": rows("SELECT * FROM tasks WHERE id=?", (task_id,))[0], "memory": memory, "warning": warning, "state": current_state()}


@app.patch("/api/tasks/{task_id}")
def patch_task(task_id: str, request: TaskPatch) -> dict[str, Any]:
    found = rows("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not found:
        raise HTTPException(404, "Task not found")
    status = request.status if request.status in {"open", "done"} else found[0]["status"]
    title = (request.title or found[0]["title"])[:200]
    execute("UPDATE tasks SET status=?, title=?, updated_at=? WHERE id=?", (status, title, now_iso(), task_id))
    if status == "done" and found[0]["status"] != "done":
        _, warning = create_memory_safely(f"TASK COMPLETED\n{title}\n\nDETAILS\n{found[0]['details']}")
    else:
        warning = None
    return {"warning": warning, "state": current_state()}


@app.post("/api/notes")
def create_note(request: NoteRequest) -> dict[str, Any]:
    note_id = uuid.uuid4().hex
    execute("INSERT INTO notes VALUES (?,?,?,?)", (note_id, request.title, request.content, now_iso()))
    memory, warning = create_memory_safely(f"NOTE SAVED\n{request.title}\n\n{request.content}")
    return {"memory": memory, "warning": warning, "state": current_state()}


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


@app.get("/api/benchmarks")
def get_benchmarks() -> dict[str, Any]:
    return {"runs": list_research_benchmarks()}


@app.get("/api/benchmarks/latest")
def get_latest_benchmark() -> dict[str, Any]:
    result = latest_research_benchmark()
    if not result:
        raise HTTPException(404, "No research benchmark has been run")
    return result


@app.get("/api/benchmarks/{run_id}")
def get_benchmark(run_id: str) -> dict[str, Any]:
    result = get_research_benchmark(run_id)
    if not result:
        raise HTTPException(404, "Benchmark run not found")
    return result


@app.get("/api/benchmarks/{run_id}/diagnostic-log")
def get_benchmark_diagnostic_log(run_id: str) -> dict[str, str]:
    try:
        return {"log": benchmark_diagnostic_log(run_id)}
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/api/benchmarks")
def create_benchmark(request: BenchmarkRequest) -> dict[str, Any]:
    try:
        return submit_research_benchmark(request.normalized())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/benchmarks/{run_id}/observations")
def get_benchmark_observations(run_id: str) -> dict[str, Any]:
    if not rows("SELECT id FROM benchmark_v2_runs WHERE id=?", (run_id,)):
        raise HTTPException(404, "Benchmark run not found")
    observations = rows(
        """SELECT o.*, t.seed, t.length FROM benchmark_v2_observations o
        LEFT JOIN benchmark_v2_trajectories t ON t.run_id = o.run_id AND t.id = o.trajectory_id
        WHERE o.run_id=? ORDER BY o.profile, o.trajectory_id, o.checkpoint, o.arm""",
        (run_id,),
    )
    return {"observations": [public_observation(item) for item in observations]}


@app.get("/api/benchmarks/{run_id}/observations/{observation_id}")
def get_benchmark_observation(run_id: str, observation_id: str) -> dict[str, Any]:
    found = rows("SELECT * FROM benchmark_v2_observations WHERE run_id=? AND id=?", (run_id, observation_id))
    if not found:
        raise HTTPException(404, "Observation not found")
    observation = found[0]
    attempts = rows(
        "SELECT attempt, status, latency_ms, error, created_at FROM benchmark_v2_attempts WHERE observation_id=? ORDER BY attempt",
        (observation_id,),
    )
    context, verified = rebuild_observation_context(run_id, observation)
    return {
        "observation": public_observation(observation),
        "attempts": attempts,
        "context": context,
        "context_verified": verified,
        "pages": observation_page_urls(run_id, observation),
    }


@app.get("/api/benchmark-models")
def get_benchmark_models() -> dict[str, Any]:
    try:
        return {"models": list_vision_models()}
    except Exception as error:
        return {"models": [], "error": str(error)[:200]}


@app.post("/api/benchmarks/{run_id}/resume")
def resume_benchmark(run_id: str) -> dict[str, Any]:
    try:
        return resume_research_benchmark(run_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/benchmarks/{run_id}/cancel")
def cancel_benchmark(run_id: str) -> dict[str, Any]:
    if not rows("SELECT id FROM benchmark_v2_runs WHERE id=?", (run_id,)):
        raise HTTPException(404, "Benchmark run not found")
    execute("UPDATE benchmark_v2_runs SET cancel_requested=1,updated_at=? WHERE id=?", (now_iso(), run_id))
    return get_research_benchmark(run_id) or {}


@app.get("/api/artifacts")
def list_artifacts() -> dict[str, Any]:
    files = []
    for path in sorted(ARTIFACTS.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        files.append({
            "name": path.name,
            "title": path.stem.replace("-", " ").title(),
            "url": f"/artifacts/{path.name}",
            "size": path.stat().st_size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        })
    return {"artifacts": files}


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
