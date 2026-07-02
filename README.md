# piedpiper

This is a research rig. It is not a product.

It asks one question. Can a model read its context from JPEG pages as well as from plain text?
If yes, and the JPEG arm uses fewer tokens, that is a cheap way to carry long context.

## The experiment

We build a fake project log. It is deterministic. Same seed, same log.
The log mutates state. Leads change. Budgets move. Old values get replaced.

At checkpoints we ask questions. Who leads now? What is the budget? What was the original access phrase?

One arm reads the log as plain text. The other arm reads JPEG pictures of the same text
(750×1000 grayscale pages, 16 px mono font, quality 75). Same pinned model. Temperature zero.
We flip which arm goes first, so order does not hide in the result.

Python scores the answers, bootstraps confidence intervals, and draws charts.

There is also a closed-loop profile. The model's past answers get fed back into the context,
so errors can compound. We report it apart from the main comparison.

One run is a pilot. It shows effect size and noise for one model and one rendering.
It does not prove a general law.

## Run it

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # put your OpenRouter key in it
uvicorn server:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. Press **Run test**. Read the result.

By default everything routes through `openrouter/free`. Chat, memory, and vision recall all
use it. The benchmark is the one exception. It refuses router aliases, because a router can
swap models between the two arms, and that breaks the comparison. So for the test, the app
picks one free vision model with a big context window and pins it. Pick a different one from
the list if you want.

You can also run it from the terminal:

```sh
python -m benchmark.cli start --model provider/model
python -m benchmark.cli start --model provider/model --skip-closed-loop
python -m benchmark.cli status RUN_ID
python -m benchmark.cli resume RUN_ID
python -m benchmark.cli analyze RUN_ID
```

Each run lives in `data/benchmarks/<run-id>/`. It holds the manifest, raw observations (JSONL),
summary (JSON and CSV), a report, the JPEG pages, and the charts. Runs are resumable.
Finished observations are never redone.

## The playground

The playground tab is a small live agent. It chats, makes tasks, saves notes, and writes
markdown files. When it finishes work, it distills the event into a JPEG memory node.

Nodes fade in five stages. Each stage re-encodes the JPEG smaller and blurrier, like forgetting.
Recall restores stage zero. The canonical text stays in SQLite, so decay is reversible and
a faded memory never rots past recovery.

The playground is a demo of the idea. The Experiment tab is the evidence.

## Model roles

- `MAIN_MODEL` — chat and planning. May use `openrouter/free`.
- `VISION_MODEL` — reads memory images. May use `openrouter/free`.
- `MEMORY_MODEL` — labels finished work. May use `openrouter/free`.
- `BENCHMARK_MODEL` — the pinned model for research runs. No aliases.

## Safety

The agent only touches local tasks, notes, and markdown files. It runs no shell commands,
sends no messages, and browses no URLs.

## Tests

```sh
.venv/bin/python -m unittest discover tests
```
