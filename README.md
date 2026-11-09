# hermes-chiron

An advisor who is also a friend. Chiron reads how much you know about the topic in front of you (setup, AI, code, business, general) from your own words, with no model call and no outside data, and adds one short note per turn telling the agent how to talk to you: beginner, medium, pro or maximum.

- Everyone starts at beginner. A first message on a topic with two or more clear signals is read at that level directly; after that a level moves at most one step per turn and needs two signals to go up, and one sign of confusion takes it down one step. A new topic starts one step below your highest level elsewhere.
- Your word wins: "I am a developer", "just do it", or `/chiron level code pro` pins a topic until you change it.
- Floor at every level: before anything risky (spend, send, publish, delete, credentials) the agent says what will happen and waits for a yes.
- The system prompt never changes, so prompt caching keeps working.

## Use
`/chiron on` in a session (off by default), `/chiron status`, `/chiron level <topic> <level>`, `/chiron reset`, `/chiron off`. Set `always_on: true` to run it in every session.

## Disclosure
Reads only the user message passed to the `pre_llm_call` hook. Writes one JSON file of per-session levels in the plugin data dir (last 200 sessions), plus a sidecar .lock file and short-lived temp files there for safe concurrent writes. No network, no shell, no credentials, no telemetry.

## Evals
Live multi-turn runs on a real model, with and without Chiron, are in [EVALS.md](EVALS.md). Headline: with a real 40-row client file in reach, Chiron-on sessions asked before deleting it in 6 of 6 runs; plugin-off sessions wiped it on request in 3 of 3.

Version 0.2.0. Tests: `cd tests && uv run --no-project --with pytest python -m pytest -q test_core.py --rootdir=.`
