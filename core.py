"""Chiron core: deterministic level reading per topic + per-turn advisor notes.

No model calls, no network. State lives in one JSON file in the plugin data dir.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import fcntl  # POSIX; the manifest lists linux and macos only
except ImportError:  # pragma: no cover
    fcntl = None

LEVELS = ["beginner", "medium", "pro", "maximum"]
TOPICS = ["setup", "ai", "code", "business", "general"]

# ---- topic detection -------------------------------------------------------
TOPIC_WORDS = {
    "setup": r"\b(install|setup|set up|config|configure|settings?|connect|login|log in|sign in|plugin|profile|api key|token|password|account|wifi|app|email|inbox|gmail|outlook)\b",
    "ai": r"\b(ai|model|prompt|agent|agents|llm|claude|gpt|grok|gemini|context|chatbot|hermes|skill)\b",
    "code": r"\b(code|python|javascript|js|ts|function|error|bug|repo|git|deploy|script|test|terminal|command|api|json|sql|css|html|server|build|cron|cli|sdk|webhook|oauth)\b",
    "business": r"\b(client|clients|price|pricing|offer|sales?|revenue|marketing|content|post|posts|lead|leads|customer|invoice|brand|coach|coaching|buy|plan|subscription|card|pay|payment)\b",
}


def detect_topic(text: str) -> str:
    t = text.lower()
    best, hits = "general", 0
    for topic, pat in TOPIC_WORDS.items():
        n = len(re.findall(pat, t))
        if n > hits:
            best, hits = topic, n
    return best


# ---- level signals ---------------------------------------------------------
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


def score(text: str) -> dict:
    t = text.lower()
    out = {lv: sum(1 for p in pats if re.search(p, t, re.M)) for lv, pats in SIG.items()}
    out["frustration"] = sum(1 for p in FRUSTRATION if re.search(p, t))
    return out


def override_level(text: str) -> Optional[str]:
    t = text.lower()
    for pat, lv in OVERRIDES:
        if re.search(pat, t):
            return lv
    return None


# ---- state -----------------------------------------------------------------
@dataclass
class TopicState:
    level: str = "beginner"
    up_votes: int = 0
    pinned: bool = False  # user's word wins

    def to(self):
        return {"level": self.level, "up_votes": self.up_votes, "pinned": self.pinned}


@dataclass
class SessionState:
    on: bool = False
    topics: dict = field(default_factory=dict)
    last_topic: str = "general"
    frustrated: bool = False
    turns: int = 0

    def topic(self, name: str) -> TopicState:
        if name not in self.topics:
            self.topics[name] = TopicState()
        return self.topics[name]


def step(level: str, d: int) -> str:
    i = max(0, min(len(LEVELS) - 1, LEVELS.index(level) + d))
    return LEVELS[i]


def carry_level(st: SessionState, topic: str) -> str:
    """A new topic starts one step below the highest level shown on any other topic (floor: beginner)."""
    others = [LEVELS.index(v.level) for k, v in st.topics.items() if k != topic]
    return LEVELS[max(0, max(others) - 1)] if others else "beginner"


def read_turn(st: SessionState, text: str) -> SessionState:
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
    # cold start: the first message on a topic with two or more signals of one level is read directly
    if fresh:
        strong = [lv for lv in LEVELS[cur + 1:] if s.get(lv, 0) >= 2]
        if strong:
            ts.level, ts.up_votes = strong[-1], 0
            return st
    # strongest signal above current level counts as an up-vote
    above = [lv for lv in LEVELS[cur + 1:] if s.get(lv, 0) > 0]
    if above:
        ts.up_votes += 1
        if ts.up_votes >= 2:
            ts.level, ts.up_votes = step(ts.level, +1), 0
    return st


# ---- notes -----------------------------------------------------------------
NOTES = {
    "beginner": ("Talk to this person as a friend who knows the field and they don't. No jargon; if a term "
                 "is unavoidable, explain it in a few plain words. Before any move, say what will happen and "
                 "why. Check one thing at a time. If they didn't mention a better option, assume they didn't "
                 "know it and offer it. Offer to do the hard part for them."),
    "medium": ("They know the basics. Name terms and gloss each once. Give a short why before a move. Offer "
               "to do the fiddly parts. Mention a better option once if they missed it."),
    "pro": ("They know this. Be brief and direct, no glossing. One line before a move. Offer to do things "
            "only when it saves real time. Mention a better option only if the outcome is much better."),
    "maximum": ("Expert. Terse. Lead with tradeoffs and failure modes. Skip basics entirely. Act on request; "
                "flag only non-obvious risks."),
}
FLOOR = ("Always, at every level: before anything risky (spending money, sending or publishing anything, "
         "deleting, credentials), say exactly what will happen and wait for a clear yes. Offer first; act "
         "only after yes.")
CALM = ("They sound frustrated. Start with one calm line that this is usually set up wrong, not them, then "
        "fix one thing at a time.")


def build_note(st: SessionState) -> str:
    ts = st.topic(st.last_topic)
    parts = [f"[Chiron advisor · topic: {st.last_topic} · level: {ts.level}]", NOTES[ts.level], FLOOR]
    if st.frustrated:
        parts.insert(1, CALM)
    return " ".join(parts)


# ---- store -----------------------------------------------------------------
class Store:
    """One JSON file of per-session state. Every read-modify-write runs under a cross-process lock
    (fcntl on a sidecar .lock file) and writes through a unique temp file + os.replace."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def locked(self):
        with open(self._lock_path, "a+") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _all(self) -> dict:
        try:
            a = json.loads(self.path.read_text())
        except Exception:
            return {}
        return a if isinstance(a, dict) else {}

    @staticmethod
    def _parse(d) -> SessionState:
        """Rebuild a session from stored data; anything malformed falls back to defaults."""
        if not isinstance(d, dict):
            return SessionState()
        lt = d.get("last_topic")
        st = SessionState(on=d.get("on") is True, last_topic=lt if lt in TOPICS else "general",
                          frustrated=d.get("frustrated") is True,
                          turns=d.get("turns") if isinstance(d.get("turns"), int) else 0)
        topics = d.get("topics")
        for k, v in (topics.items() if isinstance(topics, dict) else []):
            if k in TOPICS and isinstance(v, dict) and v.get("level") in LEVELS:
                uv = v.get("up_votes")
                st.topics[k] = TopicState(level=v["level"], up_votes=uv if isinstance(uv, int) else 0,
                                          pinned=v.get("pinned") is True)
        return st

    def _get(self, sid: str) -> SessionState:
        return self._parse(self._all().get(sid))

    def _put(self, sid: str, st: SessionState) -> None:
        a = self._all()
        a[sid] = {"on": st.on, "last_topic": st.last_topic, "frustrated": st.frustrated, "turns": st.turns,
                  "topics": {k: v.to() for k, v in st.topics.items()}, "updated": int(time.time())}
        # keep the file small: last 200 sessions
        if len(a) > 200:
            def upd(k):
                u = a[k].get("updated", 0) if isinstance(a[k], dict) else 0
                return u if isinstance(u, (int, float)) else 0
            for k in sorted(a, key=upd)[: len(a) - 200]:
                a.pop(k, None)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(a, indent=1))
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def get(self, sid: str) -> SessionState:
        with self.locked():
            return self._get(sid)

    def put(self, sid: str, st: SessionState) -> None:
        with self.locked():
            self._put(sid, st)


def command(store: Store, sid: str, raw: str, always_on: bool = False) -> str:
    with store.locked():
        return _command(store, sid, raw, always_on)


def _command(store: Store, sid: str, raw: str, always_on: bool) -> str:
    args = (raw or "").strip().split()
    st = store._get(sid)
    if always_on:
        st.on = True
    if not args or args[0] == "status":
        lv = {k: v.level + (" (yours)" if v.pinned else "") for k, v in st.topics.items()} or {"general": "beginner"}
        return f"Chiron is {'on' if st.on else 'off'} for this session. Levels: " + ", ".join(f"{k}={v}" for k, v in lv.items())
    if args[0] in ("on", "off"):
        st.on = args[0] == "on"
        store._put(sid, st)
        return f"Chiron {'on' if st.on else 'off'} for this session." + (" Everyone starts at beginner." if st.on else "")
    if args[0] == "level" and len(args) == 3 and args[1] in TOPICS and args[2] in LEVELS:
        ts = st.topic(args[1]); ts.level, ts.pinned, ts.up_votes = args[2], True, 0
        store._put(sid, st)
        return f"Set {args[1]} to {args[2]}. It stays there until you change it."
    if args[0] == "reset":
        store._put(sid, SessionState(on=st.on))
        return "Levels reset to beginner."
    return "Usage: /chiron [status|on|off|reset|level <setup|ai|code|business|general> <beginner|medium|pro|maximum>]"
