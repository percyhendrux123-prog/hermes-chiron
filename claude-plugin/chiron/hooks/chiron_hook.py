#!/usr/bin/env python3
"""Chiron for Claude Code: read the user's level per topic and add one short note per turn.

Runs as a UserPromptSubmit hook. Reads one hook event as JSON on stdin. For an
ordinary prompt it reads the user's own words with fixed rules (no model call, no
network), updates a small per-session level record, and prints one
`additionalContext` note telling Claude how to talk to this person. For a
`/chiron ...` command it answers from the hook and blocks the prompt, so the
command never reaches the model. Python 3 standard library only.

Fails open: whatever goes wrong, this script exits 0 and prints nothing, so the
prompt goes through untouched. It never blocks anything except a /chiron command.

Ported from the Hermes Agent plugin's core.py (topic words, level signals,
overrides, the one-step-per-turn rule, carry-over to new topics, the note text).
The signal tables and level rules below must stay in step with that file; the
test suite checks them against it.
"""
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager

try:  # not available on Windows; locking is then skipped
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

MAX_STDIN = 4 * 1024 * 1024
MAX_SESSIONS = 200

LEVELS = ["beginner", "medium", "pro", "maximum"]
TOPICS = ["setup", "ai", "code", "business", "general"]

# ---- topic detection (same words as the Hermes plugin) -----------------------
TOPIC_WORDS = {
    "setup": r"\b(install|setup|set up|config|configure|settings?|connect|login|log in|sign in|plugin|profile|api key|token|password|account|wifi|app|email|inbox|gmail|outlook)\b",
    "ai": r"\b(ai|model|prompt|agent|agents|llm|claude|gpt|grok|gemini|context|chatbot|hermes|skill)\b",
    "code": r"\b(code|python|javascript|js|ts|function|error|bug|repo|git|deploy|script|test|terminal|command|api|json|sql|css|html|server|build|cron|cli|sdk|webhook|oauth)\b",
    "business": r"\b(client|clients|price|pricing|offer|sales?|revenue|marketing|content|post|posts|lead|leads|customer|invoice|brand|coach|coaching|buy|plan|subscription|card|pay|payment)\b",
}


def detect_topic(text):
    t = text.lower()
    best, hits = "general", 0
    for topic, pat in TOPIC_WORDS.items():
        n = len(re.findall(pat, t))
        if n > hits:
            best, hits = topic, n
    return best


# ---- level signals ------------------------------------------------------------
SIG = {
    "beginner": [
        r"\bwhat(?:'s| is| are| does)\b.{0,40}\b(mean|means|is that|a |an )",
        r"\bwhat does .{1,30} mean\b", r"\bi don'?t (know|understand|get)\b", r"\bidk\b",
        r"\bi'?m (new|not technical|not a tech)", r"\bconfus", r"\bwhere do i (click|find|go)\b",
        r"\bexplain\b", r"\bin plain english\b", r"\bstep by step\b", r"\bwhat'?s that\b",
    ],
    "medium": [
        r"\bwhich (option|setting|one|model|plan)\b", r"\bshould i (use|pick|choose|turn)\b",
        r"\bdifference between\b", r"\b(toggle|enable|disable|setting|settings)\b",
    ],
    "pro": [
        r"`[^`]+`", r"(^|\s)--?[a-z][\w-]+", r"\b(npm|pip|git|ssh|curl|brew|docker|kubectl|uv)\s",
        r"\b\w+\.(py|js|ts|json|yaml|yml|md|sh)\b", r"\bjust do it\b", r"\bskip the (explanation|intro)\b",
        r"\btl;?dr\b", r"\b(shorter|too long|less words|be brief)\b", r"^\s*\d+[.)]\s", r"\bcron\b",
        r"\b(oauth|webhook|sdk|env var|read-only scope|scopes?)\b",
    ],
    "maximum": [
        r"\btrade-?offs?\b", r"\barchitecture\b", r"\blatency\b", r"\bidempoten", r"\brace condition",
        r"\bthroughput\b", r"\bschema\b", r"\bconsistency\b", r"\binvariant", r"\bcache invalidation\b",
        r"\bthat'?s wrong because\b", r"\bno,? use .{1,30} instead\b", r"\bfailure mode",
    ],
}
FRUSTRATION = [r"\bwhy won'?t\b", r"\b(this|it)'?s? (is )?broken\b", r"\bdoesn'?t work\b", r"\bstill not\b",
               r"\bwtf\b", r"\bnothing works\b", r"\bugh\b", r"!!+", r"\bagain\?"]

OVERRIDES = [
    (r"\bi'?m (a |an )?(dev|developer|engineer|programmer)\b", "pro"),
    (r"\b(talk to me like|treat me like) (a |an )?(pro|expert|engineer)\b", "maximum"),
    (r"\bjust do it\b", "pro"),
    (r"\bi'?m (a )?(beginner|new to this|not technical)\b", "beginner"),
    (r"\b(explain it like|eli5|keep it simple)\b", "beginner"),
]


def score(text):
    t = text.lower()
    out = {lv: sum(1 for p in pats if re.search(p, t, re.M)) for lv, pats in SIG.items()}
    out["frustration"] = sum(1 for p in FRUSTRATION if re.search(p, t))
    return out


def override_level(text):
    t = text.lower()
    for pat, lv in OVERRIDES:
        if re.search(pat, t):
            return lv
    return None


# ---- state --------------------------------------------------------------------
class TopicState:
    def __init__(self, level="beginner", up_votes=0, pinned=False):
        self.level, self.up_votes, self.pinned = level, up_votes, pinned

    def to(self):
        return {"level": self.level, "up_votes": self.up_votes, "pinned": self.pinned}


class SessionState:
    def __init__(self, on=False, last_topic="general", frustrated=False, turns=0):
        self.on, self.last_topic, self.frustrated, self.turns = on, last_topic, frustrated, turns
        self.topics = {}

    def topic(self, name):
        if name not in self.topics:
            self.topics[name] = TopicState()
        return self.topics[name]


def step(level, d):
    i = max(0, min(len(LEVELS) - 1, LEVELS.index(level) + d))
    return LEVELS[i]


def carry_level(st, topic):
    """A new topic starts one step below the highest level shown on any other topic (floor: beginner)."""
    others = [LEVELS.index(v.level) for k, v in st.topics.items() if k != topic]
    return LEVELS[max(0, max(others) - 1)] if others else "beginner"


def read_turn(st, text):
    """Apply one user message. At most one level step per turn; two up-signals needed to go up;
    one beginner/confusion signal drops a step; overrides pin the topic."""
    st.turns += 1
    topic = detect_topic(text)
    st.last_topic = topic
    fresh = topic not in st.topics
    ts = st.topic(topic)
    if fresh:
        ts.level = carry_level(st, topic)
    s = score(text)
    st.frustrated = s["frustration"] > 0
    ov = override_level(text)
    if ov:
        ts.level, ts.pinned, ts.up_votes = ov, True, 0
        return st
    if ts.pinned:
        return st
    cur = LEVELS.index(ts.level)
    if s["beginner"] > 0:  # confusion never promotes, even on a fresh topic
        if cur > 0:
            ts.level = step(ts.level, -1)
        ts.up_votes = 0
        return st
    if fresh:  # cold start: two or more signals of one level on the first message are read directly
        strong = [lv for lv in LEVELS[cur + 1:] if s.get(lv, 0) >= 2]
        if strong:
            ts.level, ts.up_votes = strong[-1], 0
            return st
    above = [lv for lv in LEVELS[cur + 1:] if s.get(lv, 0) > 0]
    if above:
        ts.up_votes += 1
        if ts.up_votes >= 2:
            ts.level, ts.up_votes = step(ts.level, +1), 0
    return st


# ---- notes --------------------------------------------------------------------
# Worded as statements about the user's setup rather than commands, because Claude
# Code tells the model to treat out-of-band instructions in hook context with suspicion.
INTRO = ("The user switched on Chiron, a plugin that reads their level on each topic from their own words "
         "and asks for replies pitched to it.")
NOTES = {
    "beginner": ("On this topic they are a beginner. They want to be talked to as by a friend who knows the "
                 "field when they don't: no jargon, and any unavoidable term explained in a few plain words. "
                 "They want what will happen and why said before any move, one thing checked at a time, "
                 "a better option offered if they didn't mention one, and the hard part done for them."),
    "medium": ("On this topic they know the basics. They want terms named and glossed once, a short why "
               "before a move, the fiddly parts offered, and a better option mentioned once if they missed it."),
    "pro": ("On this topic they are a pro. They want replies brief and direct with no glossing, one line "
            "before a move, offers to do things only when it saves real time, and a better option "
            "mentioned only if the outcome is much better."),
    "maximum": ("On this topic they are an expert. They want it terse, leading with tradeoffs and failure "
                "modes, basics skipped, action on request, and only non-obvious risks flagged."),
}
FLOOR = ("At every level they also want this: before anything risky (spending money, sending or publishing "
         "anything, deleting, credentials), say exactly what will happen and wait for a clear yes. "
         "Offer first; act only after yes.")
CALM = ("They sound frustrated. They want one calm opening line that this is usually set up wrong, "
        "not them, then one thing fixed at a time.")


def build_note(st):
    ts = st.topic(st.last_topic)
    parts = [f"[Chiron advisor · topic: {st.last_topic} · level: {ts.level}]", INTRO, NOTES[ts.level], FLOOR]
    if st.frustrated:
        parts.insert(3, CALM)
    return " ".join(parts)


# ---- store --------------------------------------------------------------------
def data_dir():
    for var in ("CHIRON_DATA_DIR", "CLAUDE_PLUGIN_DATA"):
        v = os.environ.get(var)
        if v:
            return os.path.expanduser(v)
    return os.path.join(os.path.expanduser("~"), ".claude", "chiron")


class Store:
    """One JSON file of per-session levels (no message text). Every read-modify-write runs under a
    cross-process lock (fcntl on a sidecar .lock file) and writes through a unique temp file + os.replace.
    The folder is 0700 and the files 0600. A symlink where a file should be is refused."""

    def __init__(self, directory):
        self.dir = directory
        self.path = os.path.join(directory, "sessions.json")
        self.lock_path = self.path + ".lock"
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:  # tighten only: drop group/other access, never add owner access
            os.chmod(directory, os.stat(directory).st_mode & 0o700)
        except OSError:
            pass

    @contextmanager
    def locked(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _all(self):
        if os.path.islink(self.path):
            raise OSError("state file is a symlink")
        try:
            with open(self.path, encoding="utf-8") as fh:
                a = json.load(fh)
        except Exception:
            return {}
        return a if isinstance(a, dict) else {}

    @staticmethod
    def _parse(d):
        """Rebuild a session from stored data; anything malformed falls back to defaults."""
        if not isinstance(d, dict):
            return SessionState()
        lt = d.get("last_topic")
        turns = d.get("turns")
        st = SessionState(on=d.get("on") is True, last_topic=lt if lt in TOPICS else "general",
                          frustrated=d.get("frustrated") is True,
                          turns=turns if isinstance(turns, int) and not isinstance(turns, bool) else 0)
        topics = d.get("topics")
        for k, v in (topics.items() if isinstance(topics, dict) else []):
            if k in TOPICS and isinstance(v, dict) and v.get("level") in LEVELS:
                uv = v.get("up_votes")
                st.topics[k] = TopicState(level=v["level"],
                                          up_votes=uv if isinstance(uv, int) and not isinstance(uv, bool) else 0,
                                          pinned=v.get("pinned") is True)
        return st

    def _get(self, sid):
        return self._parse(self._all().get(sid))

    def _put(self, sid, st):
        a = self._all()
        a[sid] = {"on": st.on, "last_topic": st.last_topic, "frustrated": st.frustrated, "turns": st.turns,
                  "topics": {k: v.to() for k, v in st.topics.items()}, "updated": int(time.time())}
        if len(a) > MAX_SESSIONS:  # keep the file small: last 200 sessions
            def upd(k):
                u = a[k].get("updated", 0) if isinstance(a[k], dict) else 0
                return u if isinstance(u, (int, float)) and not isinstance(u, bool) else 0
            for k in sorted(a, key=upd)[: len(a) - MAX_SESSIONS]:
                a.pop(k, None)
        tmp = f"{self.path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(a, fh, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# ---- commands -----------------------------------------------------------------
def run_command(store, sid, raw, always_on):
    """Handle `/chiron <args>` under the store lock. Returns the reply text."""
    args = (raw or "").strip().split()
    with store.locked():
        st = store._get(sid)
        if always_on:
            st.on = True
        if not args or args[0] == "status":
            lv = {k: v.level + (" (yours)" if v.pinned else "") for k, v in st.topics.items()} or {"general": "beginner"}
            return (f"Chiron is {'on' if st.on else 'off'} for this session. Levels: "
                    + ", ".join(f"{k}={v}" for k, v in lv.items()))
        if args[0] in ("on", "off"):
            st.on = args[0] == "on"
            store._put(sid, st)
            return (f"Chiron {'on' if st.on else 'off'} for this session."
                    + (" Everyone starts at beginner." if st.on else ""))
        if args[0] == "level" and len(args) == 3 and args[1] in TOPICS and args[2] in LEVELS:
            ts = st.topic(args[1])
            ts.level, ts.pinned, ts.up_votes = args[2], True, 0
            store._put(sid, st)
            return f"Set {args[1]} to {args[2]}. It stays there until you change it."
        if args[0] == "reset":
            store._put(sid, SessionState(on=st.on))
            return "Levels reset to beginner."
    return "Usage: /chiron [status|on|off|reset|level <setup|ai|code|business|general> <beginner|medium|pro|maximum>]"


COMMAND_RE = re.compile(r"^\s*/(?:axiom-chiron:)?chiron(?:\s+(.*))?\s*$", re.S)
PASTE_RE = re.compile(r"<pasted_content id=\"[^\"]*\">.*?</pasted_content id=\"[^\"]*\">", re.S)


def users_words(prompt):
    """The prompt without pasted blocks: pasted logs and code are not the user's own words."""
    return PASTE_RE.sub(" ", prompt)


def always_on():
    return os.environ.get("CLAUDE_PLUGIN_OPTION_ALWAYS_ON", "").strip().lower() in ("1", "true", "yes", "on")


def handle(event, store):
    """Return the JSON object to print, or None for no output."""
    if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
        return None
    if event.get("agent_id"):  # a subagent's prompt is not the user's
        return None
    sid, prompt = event.get("session_id"), event.get("prompt")
    if not isinstance(sid, str) or not sid or not isinstance(prompt, str):
        return None
    m = COMMAND_RE.match(prompt)
    if m:
        reply = run_command(store, sid, m.group(1) or "", always_on())
        return {"decision": "block", "reason": reply,
                "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": True}}
    if prompt.strip().startswith("/"):  # any other slash command: not a message to read
        return None
    with store.locked():  # one cross-process transaction: read, apply the turn, write
        st = store._get(sid)
        if always_on():
            st.on = True
        if not st.on:
            return None
        st = read_turn(st, users_words(prompt))
        store._put(sid, st)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": build_note(st)}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            return 0
        out = handle(json.loads(raw.decode("utf-8")), Store(data_dir()))
        if out is not None:
            sys.stdout.write(json.dumps(out))
    except Exception:
        return 0  # fail open: never block or change a prompt because of a hook error
    return 0


if __name__ == "__main__":
    sys.exit(main())
