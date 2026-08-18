"""Risk classification: mark irreversible/risky actions (submit, confirm, delete) distinctly.

The canonical classifier — formalizes what was a narrow placeholder heuristic
inline in agent/convert.py during Phase 3, explicitly deferred here per that
module's docstring. Used in three places:

- agent/convert.py, to set Step.risky when converting a discovery transcript
  into an artifact.
- agent/discovery.py, to gate a LIVE agent-decided action on explicit
  confirmation before it executes (discovery must confirm before running).
- replay/engine.py does NOT call this — it reads the already-classified
  step.risky straight from the saved artifact and gates on approval_state
  instead (replay must gate on approval_state, a property of the artifact,
  not re-derive risk from a locator string every run).

A single keyword heuristic on the target's accessible name is what's
available at decision time in both the live-action and conversion cases —
deliberately narrow, not layered with e.g. HTTP-method awareness.
"""

RISKY_KEYWORDS = ("confirm", "submit", "delete")


def is_risky_action(action_type: str, target_name: str | None) -> bool:
    if action_type != "click" or not target_name:
        return False
    text = target_name.lower()
    return any(kw in text for kw in RISKY_KEYWORDS)
