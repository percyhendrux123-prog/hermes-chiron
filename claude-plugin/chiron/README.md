# Chiron for Claude Code

An advisor who is also a friend.

Chiron is a small Claude Code plugin. It reads how much you know about the topic in front of you (setup, AI, code, business or general) from your own words, with no model call and no outside data. Then it adds one short note per turn telling Claude how to talk to you: beginner, medium, pro or maximum.

It is the Claude Code counterpart of [Chiron for Hermes Agent](https://github.com/percyhendrux123-prog/hermes-chiron), and it keeps the same reading rules and the same privacy limits.

## What it does

- Everyone starts at beginner. A first message on a topic with two or more clear signals is read at that level directly. After that a level moves at most one step per turn and needs two signals to go up, and one sign of confusion takes it down one step. A new topic starts one step below your highest level elsewhere.
- Your word wins. "I am a developer", "just do it", or `/chiron level code pro` pins a topic until you change it.
- Floor at every level. The note always says that before anything risky (spending money, sending or publishing, deleting, credentials), Claude should say what will happen and wait for a clear yes. This is a request in Claude's context, not a gate. Claude Code's own permission system is what actually blocks tool calls.
- Off by default. Chiron does nothing in a session until you type `/chiron on`, unless you turn on the `always_on` option.

## How it works

One hook on `UserPromptSubmit`, a single Python script using only the standard library.

- On an ordinary prompt it reads the text with fixed rules, updates a small level record for the session, and returns one `additionalContext` note. Claude Code adds it to the conversation next to your prompt. It does not appear in the chat.
- On a `/chiron ...` command it answers from the hook and blocks the prompt, so the command never reaches the model and costs nothing. The reply shows as `UserPromptSubmit operation blocked by hook:` followed by the answer. That wording is Claude Code's; the block is how a command gets a reply without a model turn.

## Commands

```
/chiron on
/chiron off
/chiron status
/chiron reset
/chiron level <setup|ai|code|business|general> <beginner|medium|pro|maximum>
```

`/axiom-chiron:chiron` works too. Each command applies to the current session only.

## Before you install

- **What it reads:** the text of the prompt you submit, passed to the hook on standard input. Pasted blocks (the `<pasted_content>` sections Claude Code marks) are left out, because pasted logs and code are not your words.
- **What it stores:** one JSON file of per-session levels, which topics have been seen, an up-vote counter, an on/off flag and a timestamp, for the last 200 sessions. Session ids are the keys. **No prompt text is stored.** It lives in the plugin's data folder (`${CLAUDE_PLUGIN_DATA}`, under `~/.claude/plugins/data/`), which Claude Code deletes when you uninstall the plugin. Set `CHIRON_DATA_DIR` to use another folder. The folder is created 0700 and the files 0600, and an existing folder is tightened, never widened. A symlink where the state file should be is refused. A `.lock` file and short-lived temp files sit beside it for safe concurrent writes.
- **What it sends to Claude:** the note, which is fixed text chosen by topic and level, plus the topic and level labels. Your words are never copied into it.
- **Network at runtime, shell commands, background processes, stored credentials, telemetry:** none.
- **Requires:** `python3` on your PATH, and a Claude Code version with the `UserPromptSubmit` hook and plugin `userConfig` options with a boolean type.

## Install

```
claude plugin marketplace add percyhendrux123-prog/hermes-chiron
claude plugin install axiom-chiron@deploy-axiom-chiron
```

Claude Code may ask about the `always_on` option when you enable the plugin. Leave it `false` (the default) to turn Chiron on per session with `/chiron on`. To change it later: `claude plugin install axiom-chiron@deploy-axiom-chiron --config always_on=true`, or `/plugin configure`.

To try it for one session without installing:

```
claude --plugin-dir ./claude-plugin/chiron
```

## Differences from the Hermes plugin

- **The note is worded as statements about you, not commands to the model.** Claude Code's docs say text framed as out-of-band instructions can trigger the model's prompt-injection defenses, so the Claude Code note reads "the user wants..." instead of "Talk to this person...". The level, the topic label, the floor and the calm line carry the same meaning. This was checked on a live run (below), and the model followed it. It is not tested beyond that.
- **The system prompt is never edited.** As in Hermes, the note is added next to your prompt. Each note stays in the conversation history, so a long session carries one per turn. How that affects prompt caching was not measured.
- **Pasted blocks are ignored.** The Hermes plugin reads the whole message.
- **A subagent's prompts are skipped.** Only the prompts you submit are read.

## Known gaps

- **`UserPromptSubmit` also fires on prompts that are not typed by you.** Claude Code runs it when a scheduled task or `/loop` iteration fires, when a background subagent reports back, and when another session sends your session a message. The hook receives no flag that says which is which, so Chiron reads those texts as if you had written them. In a session with a lot of that traffic, levels may drift. Turn Chiron off for those sessions.
- **Command replies look like a block.** `/chiron` is also a skill, so it shows in the `/` menu, but its reply comes from the hook as a "blocked by hook" message, not as a normal chat line.
- **If hooks are disabled** (`disableAllHooks`, or an organization policy that blocks plugin hooks), the `/chiron` skill runs as a normal prompt instead. Its text tells Claude that nothing changed and to say so. The notes also stop.
- **Fail-open.** If the hook hits any error (bad input, unwritable folder, symlinked state file), it exits quietly and your prompt goes through with no note. A `/chiron` command in that case reaches the skill described above and does nothing.
- **Slash commands are not read.** A prompt that starts with `/` (other than `/chiron`) adds no note and does not change levels.
- **Timing.** A `UserPromptSubmit` hook that exceeds its timeout (set to 10 seconds here) has its output discarded and the prompt goes through without a note.
- **Hermes features not ported.** The Hermes plugin's evaluation harness (`evals/`, [EVALS.md](../../EVALS.md)) measured the Hermes plugin on a real model. Nothing equivalent has been run for this port, so the eval results do not apply to it.
- **Terminal UI.** The live check below used `claude -p`. The hook is the same in the interactive terminal, but this was not exercised there, so how the "blocked" reply looks in the interactive prompt is untested.
- **Directory listing.** The plugin ships a command hook and a Python script, so it installs from GitHub, not from the Anthropic plugin directory.

## What was checked on a real run

A headless Claude Code 2.1.325 run in a throwaway config, plugin installed from the local repo:

- `/chiron on` returned the reply with `num_turns: 0` and `total_cost_usd: 0`, so no model call was made.
- With Chiron on, a message about API keys that said "I'm not technical" produced a note labelled `topic: setup · level: beginner`, which the model quoted back when asked, and a beginner-level answer.
- After `/chiron level setup pro`, the next note was labelled `level: pro`.
- After `/chiron off`, the next message carried no note.
- With the `always_on` option set to true, a fresh session got a note without any `/chiron on`. With it set to false, a fresh session got none.
- With hooks disabled by settings, `/chiron status` ran as a plain prompt and the skill text told the model nothing had changed.

## Try it with no install

```
python3 -m unittest discover -s claude-plugin/tests
```

The tests run the real hook script with sample events and check the output and the state file. They cover the on/off and command paths, the level rules, that no prompt text is stored or printed back, folder and file modes, fail-open on bad input and unwritable or symlinked state, parallel writers, the manifest, and a parity check against the Hermes plugin's `core.py` (same signal tables, same level results on a set of sample conversations). The note wording is deliberately different and is not checked for parity. Tests use a temporary folder and touch nothing in `~/.claude`.

## Undo

Run `claude plugin uninstall axiom-chiron` and, if you added the marketplace, `claude plugin marketplace remove deploy-axiom-chiron`. Claude Code removes the plugin's data folder with it. If you set `CHIRON_DATA_DIR`, delete that folder yourself.

## How it was made

I designed it, set the rules for what it may read and keep, and tested it on my own system. AI agents wrote the code under my spec and review. I read Python; I don't claim to write it by hand.

Percy Keith, Deploy Axiom. MIT license.
