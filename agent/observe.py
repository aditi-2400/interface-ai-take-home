"""Observation capture: accessibility tree, not screenshots, as the primary signal.

Uses the CDP Accessibility.getFullAXTree call directly rather than Playwright's
higher-level aria_snapshot() YAML dump. The mock app's deliberately hostile
nested-table markup makes the YAML dump extremely noisy (the same text repeats
at every nesting level, since ARIA row/cell accessible names are computed from
all descendant text). Walking the raw CDP node tree and keeping only
interactive/content-bearing roles avoids that explosion entirely, which
matters because smaller local models are much more sensitive to long, noisy
context than frontier models.
"""

from urllib.parse import urlparse

from playwright.async_api import Page
from pydantic import BaseModel

KEEP_ROLES = {
    "textbox",
    "link",
    "button",
    "combobox",
    "checkbox",
    "radio",
    "option",
    "StaticText",
    "heading",
}

# Form fields that can have no accessible name at all - confirmed on
# MERIDIAN CORE, whose inputs/selects aren't linked to their labels via
# <label> or aria-label. We can't search for these by name, so we find them
# by position instead (see nth below). Buttons/links/options aren't included
# here since their name comes from their own visible text, not a label.
POSITIONAL_ROLES = {"textbox", "combobox", "checkbox", "radio"}


class ObservedElement(BaseModel):
    role: str
    name: str
    nth: int | None = None


class Observation(BaseModel):
    url: str
    path: str
    elements: list[ObservedElement]

    def render(self) -> str:
        lines = [f"URL path: {self.path}", "Visible elements:"]
        for el in self.elements:
            if el.nth is not None:
                lines.append(
                    f'- {el.role} "{el.name}" [no accessible name - address by '
                    f"position instead: set locator.nth={el.nth}]"
                )
            else:
                lines.append(f'- {el.role} "{el.name}"')
        return "\n".join(lines)


async def capture_observation(page: Page) -> Observation:
    cdp = await page.context.new_cdp_session(page)
    try:
        tree = await cdp.send("Accessibility.getFullAXTree")
    finally:
        await cdp.detach()

    nodes_by_id = {n["nodeId"]: n for n in tree["nodes"]}
    if not tree["nodes"]:
        return Observation(url=page.url, path=urlparse(page.url).path, elements=[])
    root_id = tree["nodes"][0]["nodeId"]

    elements: list[ObservedElement] = []
    visited: set[str] = set()
    role_counts: dict[str, int] = {}
    last_label = ""

    def walk(node_id: str) -> None:
        nonlocal last_label
        if node_id in visited:
            return
        visited.add(node_id)
        node = nodes_by_id.get(node_id)
        if node is None:
            return
        if not node.get("ignored", False):
            role = node.get("role", {}).get("value")
            name = node.get("name", {}).get("value", "")
            if role in POSITIONAL_ROLES:
                # Count every element of this role, named or not, so the
                # count matches what Playwright's .nth() will see later.
                idx = role_counts.get(role, 0)
                role_counts[role] = idx + 1
                if name:
                    elements.append(ObservedElement(role=role, name=name))
                else:
                    elements.append(
                        ObservedElement(role=role, name=last_label or f"unlabeled {role}", nth=idx)
                    )
            elif role in KEEP_ROLES and name:
                elements.append(ObservedElement(role=role, name=name))
            if role == "StaticText" and name.strip():
                last_label = name.strip().rstrip(":").strip()
        for child_id in node.get("childIds", []):
            walk(child_id)

    walk(root_id)

    parsed = urlparse(page.url)
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    return Observation(url=page.url, path=path, elements=elements)
