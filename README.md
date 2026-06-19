# Piper — agentic visual memory MVP

Piper is a local-first chat and task agent using OpenRouter's capability-aware free-model router. Completed actions become compressed JPEG memory nodes in a SQLite relationship graph. Frequently retrieved memories are restored to full clarity; inactive memories decay through progressively lower resolution, contrast, and JPEG quality.

## What works

- Chat and bounded agent actions: create/complete tasks, save notes, and create Markdown artifacts.
- `openrouter/free` routing for executive reasoning, structured planning, labeling, and multimodal recall.
- Automated memory labeling and summarization with a deterministic fallback.
- SQLite memory graph with semantic and temporal edges.
- Five-stage synaptic decay and retrieval-based reconsolidation.
- Functional UI for conversation, tasks, graph inspection, manual decay, and memory recall.
- Demo mode when no API key is configured.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your OpenRouter API key to `.env`, then run:

```sh
uvicorn server:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. You can also keep opening `index.html` directly; it will call the backend at `127.0.0.1:8000`.

## Routing and model roles

- `MODEL_PROVIDER`: defaults to `openrouter`.
- `ROUTER_MODEL`: defaults to `openrouter/free`.
- `MAIN_MODEL`: executive conversation, structured planning, and action selection.
- `VISION_MODEL`: reads retrieved memory images.
- `MEMORY_MODEL`: labels and distills completed work.

All three roles default to `openrouter/free`. Each request advertises what it needs: JSON response format for planners/labelers and image content for visual recall. OpenRouter can then filter its free pool for compatible models. Pin individual roles to explicit OpenRouter model slugs if you need repeatability instead of free-pool variability.

The previous NVIDIA route remains available by setting `MODEL_PROVIDER=nvidia` and the NVIDIA values documented in `.env.example`.

## Memory lifecycle

1. A completed action is distilled into a label, summary, and tags.
2. The summary is typeset into a 750×1000 grayscale JPEG.
3. The node connects to recent semantically related nodes.
4. Query keywords retrieve the most relevant activated nodes.
5. The routed multimodal model reads their images and passes concise recalled context to the routed executive model.
6. Retrieval restores the node to stage 0. Every inactive decay interval advances it toward stage 4.

Canonical text remains in SQLite so retrieval can reconsolidate a faded memory. This makes decay reversible and avoids compounding JPEG corruption beyond recovery.

## Safety boundary

The MVP intentionally limits tools to local task, note, and Markdown artifact operations. It does not execute shell commands, send messages, or browse arbitrary URLs. Extend the action registry only with explicit validation and user confirmation for consequential operations.
