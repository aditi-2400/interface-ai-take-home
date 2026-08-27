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

Two checks, both simple: a keyword on the button's own name, and whether
the previous click was labeled "Continue" (catches a real target where the
final confirm button uses its own wording per action - "Post Transfer",
"Apply Hold" - instead of a generic "confirm"/"submit").
"""

RISKY_KEYWORDS = ("confirm", "submit", "delete")

# A click right after a "Continue" click is the second half of a
# review-then-act flow - the real irreversible step, whatever this button
# happens to be called. Confirmed on a real target where each flow's final
# button uses different wording ("Post Transfer", "Open Share", "Apply
# Hold") - none of it matches RISKY_KEYWORDS, but all of them follow a
# "Continue" click. This catches those without needing to know the words
# in advance.
CONTINUE_LABEL = "continue"


def is_risky_action(action_type: str, target_name: str | None, prev_target_name: str | None = None) -> bool:
    if action_type != "click" or not target_name:
        return False
    text = target_name.lower()
    if any(kw in text for kw in RISKY_KEYWORDS):
        return True
    if prev_target_name and prev_target_name.strip().lower() == CONTINUE_LABEL:
        return True
    return False
