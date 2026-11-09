# hermes-chiron

An advisor who is also a friend. Chiron reads how much you know about the topic in front of you (setup, AI, code, business, general) from your own words, with no model call and no outside data, and adds one short note per turn telling the agent how to talk to you: beginner, medium, pro or maximum.

- Everyone starts at beginner. A first message on a topic with two or more clear signals is read at that level directly; after that a level moves at most one step per turn and needs two signals to go up, and one sign of confusion takes it down one step. A new topic starts one step below your highest level elsewhere.
- Your word wins: "I am a developer", "just do it", or `/chiron level code pro` pins a topic until you change it.
- Floor at every level: before anything risky (spend, send, publish, delete, credentials) the agent says what will happen and waits for a yes.
- The system prompt never changes, so prompt caching keeps working.

## Watch
A 50-second walkthrough: [youtu.be/3DTyepZPk98](https://youtu.be/3DTyepZPk98)

<a href="https://youtu.be/3DTyepZPk98"><img src="docs/chiron-woodcut.jpg" alt="Chiron, woodcut still from the walkthrough" width="360"></a>

## Use
`/chiron on` in a session (off by default), `/chiron status`, `/chiron level <topic> <level>`, `/chiron reset`, `/chiron off`. Set `always_on: true` to run it in every session.

## Disclosure
Reads only the user message passed to the `pre_llm_call` hook. Writes one JSON file of per-session levels in the plugin data dir (last 200 sessions), plus a sidecar .lock file and short-lived temp files there for safe concurrent writes. No network, no shell, no credentials, no telemetry.

## Evals
Live multi-turn runs on a real model, with and without Chiron, are in [EVALS.md](EVALS.md). Headline: with a real 40-row client file in reach, Chiron-on sessions asked before deleting it in 6 of 6 runs; plugin-off sessions wiped it on request in 3 of 3.

Version 0.2.0. Tests: `cd tests && uv run --no-project --with pytest python -m pytest -q test_core.py --rootdir=.`

## Works with

Chiron is one part of an operator layer for Hermes Agent, built by Deploy Axiom. Each plugin installs on its own and none requires another. Together they cover a working agent from first reply to finished task:

- [Standing](https://github.com/percyhendrux123-prog/hermes-standing): earned trust. A worker's proven record sets what still needs your approval, and a task is not done until a check passes.
- [Axioma](https://github.com/percyhendrux123-prog/hermes-axioma): marks the few phrases in a reply that should change your decision, and shows an advisory card before a risky call.
- [Tripwire](https://github.com/percyhendrux123-prog/hermes-tripwire): logs every refusal, so you know when an agent was told no.
- [Handydandy](https://github.com/percyhendrux123-prog/hermes-handydandy): session-size notices and structured handoffs, so long sessions stop carrying the whole chat.
- [Gatekeeper](https://github.com/percyhendrux123-prog/hermes-gatekeeper): owner-configured policy checks on recognized outbound messaging calls.
- **Chiron** (this plugin): reads your level per topic from your own words and adjusts how the agent talks to you.
