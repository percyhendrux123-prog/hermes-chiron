"""Tests for the Claude Code Chiron hook (stdlib unittest).

Most tests run the real hook script as a subprocess with a sample UserPromptSubmit
event on stdin, the way Claude Code does, and inspect what it prints and writes.
A parity test loads the Hermes plugin's core.py from the repo root and checks that
the level rules and signal tables behave the same.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "chiron")
HOOK = os.path.join(PLUGIN, "hooks", "chiron_hook.py")
REPO = os.path.dirname(os.path.dirname(HERE))
SCRATCH = os.path.realpath(os.path.expanduser("~/.hermes/cache/scratch"))
if not os.path.isdir(SCRATCH):
    SCRATCH = os.path.realpath(tempfile.gettempdir())

CANARY = "CANARY-5c3e91"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hook = load("chiron_hook_under_test", HOOK)


def event(prompt, session="s1", **over):
    payload = {"session_id": session, "transcript_path": "/home/example/.claude/projects/x/y.jsonl",
               "cwd": "/home/example/project", "permission_mode": "default",
               "hook_event_name": "UserPromptSubmit", "prompt": prompt}
    payload.update(over)
    return payload


class HookCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=SCRATCH, prefix="chiron-test-")
        self.dir = os.path.realpath(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.data = os.path.join(self.dir, "data")

    def run_hook(self, payload, env_extra=None, raw=None):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CHIRON_", "CLAUDE_PLUGIN_"))}
        env["CHIRON_DATA_DIR"] = self.data
        env.update(env_extra or {})
        stdin = raw if raw is not None else json.dumps(payload).encode()
        proc = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.decode()
        return json.loads(out) if out.strip() else None

    def say(self, prompt, session="s1", **kw):
        return self.run_hook(event(prompt, session), **kw)

    def note(self, prompt, session="s1"):
        out = self.say(prompt, session)
        self.assertIsNotNone(out, "expected a note")
        spec = out["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], "UserPromptSubmit")
        return spec["additionalContext"]

    def state(self):
        with open(os.path.join(self.data, "sessions.json"), encoding="utf-8") as fh:
            return json.load(fh)


class TestOnOff(HookCase):
    def test_off_by_default_prints_nothing_and_writes_nothing(self):
        self.assertIsNone(self.say("what does API key mean?"))
        self.assertFalse(os.path.exists(os.path.join(self.data, "sessions.json")))

    def test_on_command_blocks_with_reply_and_no_echo_of_prompt(self):
        out = self.say("/chiron on")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Chiron on for this session", out["reason"])
        self.assertTrue(out["hookSpecificOutput"]["suppressOriginalPrompt"])
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_namespaced_command_works(self):
        out = self.say("/axiom-chiron:chiron on")
        self.assertIn("on for this session", out["reason"])
        self.assertIn("level: beginner", self.note("what does API key mean? i'm not technical"))

    def test_note_after_on_and_other_sessions_untouched(self):
        self.say("/chiron on", "s1")
        n = self.note("what does API key mean? i'm not technical", "s1")
        self.assertIn("level: beginner", n)
        self.assertIn("wait for a clear yes", n)
        self.assertIsNone(self.say("hi", "s2"))

    def test_off_command_stops_notes(self):
        self.say("/chiron on")
        self.assertIsNotNone(self.say("hello there"))
        self.say("/chiron off")
        self.assertIsNone(self.say("hello there"))

    def test_always_on_option(self):
        out = self.say("what does API key mean?", env_extra={"CLAUDE_PLUGIN_OPTION_ALWAYS_ON": "true"})
        self.assertIn("level: beginner", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.say("hello", session="s9", env_extra={"CLAUDE_PLUGIN_OPTION_ALWAYS_ON": "false"}))

    def test_other_slash_commands_are_ignored_even_when_on(self):
        self.say("/chiron on")
        self.assertIsNone(self.say("/compact"))
        self.assertIsNone(self.say("/chironic status"))  # not our command

    def test_status_level_reset_usage(self):
        self.assertIn("off", self.say("/chiron status")["reason"])
        self.say("/chiron on")
        self.assertIn("stays", self.say("/chiron level business pro")["reason"])
        self.assertIn("business=pro (yours)", self.say("/chiron")["reason"])
        self.assertIn("Usage", self.say("/chiron level business wizard")["reason"])
        self.assertIn("reset", self.say("/chiron reset")["reason"])
        self.assertIn("general=beginner", self.say("/chiron status")["reason"])
        self.assertIn("on for this session", self.say("/chiron status")["reason"])  # reset keeps on/off


class TestReading(HookCase):
    def level(self, topic):
        return self.state()["s1"]["topics"][topic]["level"]

    def test_everyone_starts_beginner(self):
        self.say("/chiron on")
        self.say("can you help me with my clients")
        self.assertEqual(self.level("business"), "beginner")

    def test_beginner_to_pro_over_four_turns(self):
        self.say("/chiron on")
        for m in ["run `git status` in the repo", "then `npm test` and fix the failing test.py",
                  "push it with `git push --force-with-lease`", "add a cron for the build script"]:
            self.say(m)
        self.assertEqual(self.level("code"), "pro")

    def test_cold_start_reads_strong_first_message(self):
        self.say("/chiron on")
        n = self.note("set a weekly cron Sunday 6 PM that runs `axiom_sunday_progress.py`")
        self.assertIn("level: pro", n)

    def test_override_pins_and_beats_confusion(self):
        self.say("/chiron on")
        self.say("I'm a developer, the deploy script is failing")
        self.say("what does this error mean in the code")
        st = self.state()["s1"]["topics"]["code"]
        self.assertEqual((st["level"], st["pinned"]), ("pro", True))

    def test_frustration_adds_calm_line(self):
        self.say("/chiron on")
        n = self.note("why won't this code work, it's broken")
        self.assertIn("not them", n)

    def test_pasted_blocks_are_not_the_users_words(self):
        self.say("/chiron on")
        pasted = ('why is it slow? <pasted_content id="1">`git log --oneline` `npm test` build.py schema '
                  'architecture tradeoff latency</pasted_content id="1">')
        n = self.note(pasted)
        self.assertIn("level: beginner", n)

    def test_subagent_prompts_are_skipped(self):
        self.say("/chiron on")
        self.assertIsNone(self.run_hook(event("run `git status` now", agent_id="a1", agent_type="Explore")))
        self.assertEqual(self.state()["s1"]["turns"], 0)

    def test_note_is_short_and_statement_shaped(self):
        self.say("/chiron on")
        for lv in hook.LEVELS:
            self.assertLess(len(hook.NOTES[lv].split()), 80)
        n = self.note("hello")
        self.assertLess(len(n.split()), 160)
        self.assertNotIn("<system-reminder>", n)


class TestPrivacy(HookCase):
    def test_no_prompt_text_stored_or_printed_back(self):
        self.say("/chiron on")
        out = self.say(f"how do I configure the app {CANARY}")
        self.assertNotIn(CANARY, json.dumps(out))
        for root, _, files in os.walk(self.data):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    self.assertNotIn(CANARY.encode(), fh.read())

    def test_state_has_only_levels_and_counters(self):
        self.say("/chiron on")
        self.say("run `git status` in the repo")
        keys = set(self.state()["s1"])
        self.assertEqual(keys, {"on", "last_topic", "frustrated", "turns", "topics", "updated"})

    def test_modes(self):
        self.say("/chiron on")
        self.assertEqual(stat.S_IMODE(os.stat(self.data).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.data, "sessions.json")).st_mode), 0o600)

    def test_loose_existing_folder_is_tightened_not_widened(self):
        os.makedirs(self.data, mode=0o755)
        os.chmod(self.data, 0o755)
        self.say("/chiron on")
        self.assertEqual(stat.S_IMODE(os.stat(self.data).st_mode), 0o700)

    def test_symlinked_state_file_is_refused_and_target_untouched(self):
        os.makedirs(self.data, mode=0o700)
        target = os.path.join(self.dir, "elsewhere.json")
        with open(target, "w") as fh:
            fh.write("{}")
        os.symlink(target, os.path.join(self.data, "sessions.json"))
        out = self.say("/chiron on")
        self.assertIsNone(out)  # failed open: no block, no output
        with open(target) as fh:
            self.assertEqual(fh.read(), "{}")

    def test_prunes_to_200_sessions(self):
        for i in range(203):
            self.say("/chiron on", f"sess-{i}")
        self.assertEqual(len(self.state()), 200)

    def test_no_network_or_shell_imports(self):
        with open(HOOK, encoding="utf-8") as fh:
            src = fh.read()
        for banned in ("import socket", "urllib", "http.client", "subprocess", "os.system", "requests"):
            self.assertNotIn(banned, src)


class TestFailOpen(HookCase):
    def test_bad_input_exits_zero_with_no_output(self):
        for raw in (b"", b"not json", b"[]", b'{"hook_event_name": "UserPromptSubmit"}',
                    json.dumps(event(123)).encode(), json.dumps(event("hi", session="")).encode(),
                    json.dumps(event("hi", hook_event_name="PreToolUse")).encode(),
                    b"\xff\xfe"):
            self.assertIsNone(self.run_hook(None, raw=raw), raw)

    def test_unwritable_state_dir_fails_open(self):
        os.makedirs(self.data, mode=0o500)
        self.addCleanup(os.chmod, self.data, 0o700)
        if os.access(self.data, os.W_OK):
            self.skipTest("running as a user that ignores directory modes")
        self.assertIsNone(self.say("/chiron on"))

    def test_corrupt_state_recovers(self):
        os.makedirs(self.data, mode=0o700)
        for bad in ("[]", '{"s1": []}', '{"s1": {"topics": {"code": {"level": "wizard"}}, "last_topic": 5}}',
                    '{"s1": {"topics": [], "turns": "a", "on": "yes"}}', "not json"):
            with open(os.path.join(self.data, "sessions.json"), "w") as fh:
                fh.write(bad)
            self.assertIn("off", self.say("/chiron status")["reason"])
            self.assertIn("on for this session", self.say("/chiron on")["reason"])
            self.assertIn("level: beginner", self.note("hello"))


class TestConcurrency(HookCase):
    def test_parallel_writers_lose_nothing(self):
        env = dict(os.environ, CHIRON_DATA_DIR=self.data)
        batch = []
        for i in range(24):
            p = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
            p.stdin.write(json.dumps(event("/chiron on", f"par-{i}")).encode())
            p.stdin.close()
            batch.append(p)
        for p in batch:
            self.assertEqual(p.wait(timeout=30), 0)
            p.stdout.close()
        st = self.state()
        self.assertEqual(sorted(st), sorted(f"par-{i}" for i in range(24)))
        self.assertTrue(all(v["on"] for v in st.values()))
        self.assertEqual([f for f in os.listdir(self.data) if f.endswith(".tmp")], [])


class TestPackaging(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()

    def test_manifest_and_marketplace(self):
        plugin = json.loads(self.read(PLUGIN, ".claude-plugin", "plugin.json"))
        self.assertEqual((plugin["name"], plugin["displayName"], plugin["version"], plugin["license"]),
                         ("axiom-chiron", "Chiron", "0.1.0", "MIT"))
        self.assertEqual(plugin["author"], {"name": "Deploy Axiom"})
        self.assertEqual(plugin["homepage"], "https://github.com/percyhendrux123-prog/hermes-chiron")
        self.assertEqual(plugin["repository"], plugin["homepage"])
        self.assertFalse(plugin["userConfig"]["always_on"]["default"])
        market = json.loads(self.read(REPO, ".claude-plugin", "marketplace.json"))
        self.assertEqual(market["name"], "deploy-axiom-chiron")
        self.assertEqual(market["plugins"][0]["name"], "axiom-chiron")
        self.assertTrue(os.path.isdir(os.path.join(REPO, market["plugins"][0]["source"])))

    def test_hooks_json_wires_userpromptsubmit_only(self):
        hooks = json.loads(self.read(PLUGIN, "hooks", "hooks.json"))["hooks"]
        self.assertEqual(list(hooks), ["UserPromptSubmit"])
        entry = hooks["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual((entry["type"], entry["command"]), ("command", "python3"))
        self.assertEqual(entry["args"], ["${CLAUDE_PLUGIN_ROOT}/hooks/chiron_hook.py"])

    def test_no_personal_strings_in_plugin_or_tests(self):
        banned = ["/Us" + "ers/", "percy" + "keith", "@gm" + "ail", "sk-" + "ant", "xox" + "b-"]
        for base in (PLUGIN, HERE):
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".png") or f == "test_claude_hook.py":
                        continue
                    text = self.read(root, f)
                    for b in banned:
                        self.assertNotIn(b, text, os.path.join(root, f))


class TestParityWithHermes(unittest.TestCase):
    """The Hermes core.py is the reference: same signal tables, same level rules, same topics."""

    @classmethod
    def setUpClass(cls):
        cls.core = load("hermes_chiron_core", os.path.join(REPO, "core.py"))

    def test_tables_are_identical(self):
        c = self.core
        self.assertEqual(hook.LEVELS, c.LEVELS)
        self.assertEqual(hook.TOPICS, c.TOPICS)
        self.assertEqual(hook.TOPIC_WORDS, c.TOPIC_WORDS)
        self.assertEqual(hook.SIG, c.SIG)
        self.assertEqual(hook.FRUSTRATION, c.FRUSTRATION)
        self.assertEqual(hook.OVERRIDES, c.OVERRIDES)

    CONVERSATIONS = [
        ["can you help me with my clients"],
        ["run `git status` in the repo", "then `npm test` and fix the failing test.py",
         "push it with `git push --force-with-lease`", "add a cron for the build script"],
        ["fix the code", "git rebase --onto main with `git log` and the deploy script"],
        ["set a weekly cron Sunday 6 PM that runs `axiom_sunday_progress.py`"],
        ["can you check my code, I use git"],
        ["I'm a developer, fix the deploy script", "write a post for my clients"],
        ["I'm a developer, fix the deploy script", "what is a lead in sales for my clients"],
        ["I'm a developer, the deploy script is failing", "what does this error mean in the code"],
        ["run `git status`", "now `npm test` on the code", "what does repo mean, I don't understand the code"],
        ["run `git status` in the code", "`npm test` the code", "why won't this code work, it's broken"],
        ["I don't understand the code in `foo.py`; run `git status`"],
        ["I'm a developer working on this code", "what is a lead in sales for my clients"],
        ["talk to me like an expert: the schema has a race condition, tradeoffs in the architecture"],
        ["how do I install the app", "which setting should I toggle", "explain step by step", "ugh nothing works!!"],
        ["tl;dr the latency tradeoff", "1. do the cache invalidation", "that's wrong because of the schema"],
        ["keep it simple please", "just do it"],
        ["hello", "hi", "thanks"],
    ]

    def test_level_rules_match_on_a_corpus(self):
        for convo in self.CONVERSATIONS:
            a, b = self.core.SessionState(on=True), hook.SessionState(on=True)
            for msg in convo:
                a, b = self.core.read_turn(a, msg), hook.read_turn(b, msg)
                self.assertEqual(a.last_topic, b.last_topic, (convo, msg))
                self.assertEqual(a.frustrated, b.frustrated, (convo, msg))
                self.assertEqual({k: v.to() for k, v in a.topics.items()},
                                 {k: v.to() for k, v in b.topics.items()}, (convo, msg))

    def test_note_keeps_level_label_and_floor_but_is_reworded(self):
        a, b = self.core.SessionState(on=True), hook.SessionState(on=True)
        a, b = self.core.read_turn(a, "fix the code"), hook.read_turn(b, "fix the code")
        self.assertTrue(self.core.build_note(a).startswith("[Chiron advisor · topic: code · level: beginner]"))
        self.assertTrue(hook.build_note(b).startswith("[Chiron advisor · topic: code · level: beginner]"))
        self.assertNotEqual(self.core.build_note(a), hook.build_note(b))  # reworded for Claude Code


if __name__ == "__main__":
    unittest.main()
