import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chiron_core", ROOT / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules["chiron_core"] = core
spec.loader.exec_module(core)


def run(msgs, st=None):
    st = st or core.SessionState(on=True)
    for m in msgs:
        st = core.read_turn(st, m)
    return st


def test_everyone_starts_beginner():
    st = core.SessionState(on=True)
    st = core.read_turn(st, "can you help me with my clients")
    assert st.topic("business").level == "beginner"


def test_beginner_to_pro_over_four_turns_on_code():
    st = run(["run `git status` in the repo", "then `npm test` and fix the failing test.py",
              "push it with `git push --force-with-lease`", "add a cron for the build script"])
    assert st.topic("code").level == "pro"


def test_max_one_step_per_turn_after_cold_start():
    st = run(["fix the code", "git rebase --onto main with `git log` and the deploy script"])
    assert st.topic("code").level == "beginner"  # topic already open: first up-vote only


def test_cold_start_reads_strong_first_message():
    st = run(["set a weekly cron Sunday 6 PM that runs `axiom_sunday_progress.py`"])
    assert st.topic(st.last_topic).level == "pro"


def test_cold_start_needs_two_signals():
    st = run(["can you check my code, I use git"])
    assert st.topic("code").level == "beginner"


def test_new_topic_carries_one_step_below():
    st = run(["I'm a developer, fix the deploy script", "write a post for my clients"])
    assert st.topic("business").level == "medium"


def test_carry_still_drops_on_confusion():
    st = run(["I'm a developer, fix the deploy script", "what is a lead in sales for my clients"])
    assert st.topic("business").level == "beginner"


def test_override_sticks_and_beats_confusion():
    st = run(["I'm a developer, the deploy script is failing", "what does this error mean in the code"])
    assert st.topic("code").level == "pro" and st.topic("code").pinned


def test_confusion_drops_one_step():
    st = run(["run `git status`", "now `npm test` on the code"])
    before = core.LEVELS.index(st.topic("code").level)
    st = core.read_turn(st, "what does repo mean, I don't understand the code")
    assert core.LEVELS.index(st.topic("code").level) == before - 1


def test_frustration_does_not_change_level_but_adds_calm():
    st = run(["run `git status` in the code", "`npm test` the code"])
    lv = st.topic("code").level
    st = core.read_turn(st, "why won't this code work, it's broken")
    assert st.topic("code").level == lv and st.frustrated
    assert "not them" in core.build_note(st)


def test_floor_always_present():
    for lv in core.LEVELS:
        st = core.SessionState(on=True)
        st.topic("general").level = lv
        assert "wait for a clear yes" in core.build_note(st)


def test_notes_under_80_words():
    for v in core.NOTES.values():
        assert len(v.split()) < 80


def test_per_topic_independent():
    st = run(["I'm a developer working on this code", "what is a lead in sales for my clients"])
    assert st.topic("code").level == "pro" and st.topic("business").level == "beginner"


def test_command_on_off_level(tmp_path):
    s = core.Store(tmp_path / "s.json")
    assert "off" in core.command(s, "x", "status")
    core.command(s, "x", "on")
    assert s.get("x").on
    assert "stays" in core.command(s, "x", "level business pro")
    assert s.get("x").topic("business").level == "pro"
    core.command(s, "x", "off")
    assert not s.get("x").on


def test_confusion_on_fresh_topic_never_promotes():
    st = run(["I don't understand the code in `foo.py`; run `git status`"])
    assert st.topic("code").level == "beginner"


def test_store_survives_wrong_shapes(tmp_path):
    s = core.Store(tmp_path / "s.json")
    for bad in ["[]", '{"x": []}', '{"x": {"topics": {"code": {"level": "wizard"}}, "last_topic": 5}}',
                '{"x": {"topics": [], "turns": "a", "on": "yes"}}', "not json"]:
        (tmp_path / "s.json").write_text(bad)
        st = s.get("x")
        assert st.topic("code").level == "beginner" and st.on is False
        assert "Chiron" in core.command(s, "x", "status")
        core.command(s, "x", "on")
        assert s.get("x").on


def test_concurrent_writers_lose_nothing(tmp_path):
    import multiprocessing as mp
    path = tmp_path / "s.json"
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_writer, args=(str(path), i)) for i in range(8)]
    [p.start() for p in procs]; [p.join() for p in procs]
    assert all(p.exitcode == 0 for p in procs)
    s = core.Store(path)
    for i in range(8):
        for j in range(15):
            assert s.get(f"s{i}-{j}").on
    assert not list(tmp_path.glob("*.tmp"))


def _writer(path, i):
    s = core.Store(Path(path))
    for j in range(15):
        core.command(s, f"s{i}-{j}", "on")


def test_plugin_wiring_hook_and_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pspec = importlib.util.spec_from_file_location("chiron_pkg", ROOT / "__init__.py",
                                                   submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(pspec)
    sys.modules["chiron_pkg"] = pkg
    pspec.loader.exec_module(pkg)

    class Ctx:
        hooks, cmds = {}, {}
        def register_hook(self, name, fn): self.hooks[name] = fn
        def register_command(self, name, fn, **kw): self.cmds[name] = fn
        def get_config(self, key, default=None): return default

    ctx = Ctx()
    pkg.register(ctx)
    hook, cmd = ctx.hooks["pre_llm_call"], ctx.cmds["chiron"]
    assert hook(session_id="s1", user_message="what does API key mean?") is None  # off by default
    assert "on" in cmd("on", session_id="s1")
    out = hook(session_id="s1", user_message="what does API key mean? i'm not technical")
    assert "level: beginner" in out["context"] and "wait for a clear yes" in out["context"]
    assert hook(session_id="s1", user_message="/chiron status") is None
    assert hook(session_id="s2", user_message="hi") is None  # other sessions untouched
