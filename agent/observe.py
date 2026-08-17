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


class ObservedElement(BaseModel):
    role: str
    name: str


class Observation(BaseModel):
    url: str
    path: str
    elements: list[ObservedElement]

    def render(self) -> str:
        lines = [f"URL path: {self.path}", "Visible elements:"]
        for el in self.elements:
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

    def walk(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        node = nodes_by_id.get(node_id)
        if node is None:
            return
        if not node.get("ignored", False):
            role = node.get("role", {}).get("value")
            name = node.get("name", {}).get("value", "")
            if role in KEEP_ROLES and name:
                elements.append(ObservedElement(role=role, name=name))
        for child_id in node.get("childIds", []):
            walk(child_id)

    walk(root_id)

    parsed = urlparse(page.url)
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    return Observation(url=page.url, path=path, elements=elements)
