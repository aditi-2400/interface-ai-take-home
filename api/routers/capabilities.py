"""Capability API: list what's callable, invoke one by name with typed args.

An agent-facing surface over the same replay engine the CLI already uses -
invoking runs the real deterministic replay, no LLM in the loop. Escalation
is deliberately off by default here: a synchronous HTTP request blocking
indefinitely on a human resolving an intervention is a bad API contract.
The CLI/escalation.operator path is still how a live escalation gets
demoed; this endpoint's job is just correct success/business_outcome/
hard_failure results.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.config import target_config_for
from artifacts import storage
from artifacts.models import Capability
from replay.engine import replay_capability
from replay.result import ReplayResult
from safety.allowlist import Allowlist

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


class InvokeRequest(BaseModel):
    inputs: dict[str, str] = {}


def _load_capability_or_404(capability_id: str, version: int | None) -> Capability:
    try:
        if version is not None:
            return storage.load(capability_id, version)
        return storage.load_latest(capability_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No capability {capability_id!r} found") from None


async def invoke_capability(capability: Capability, inputs: dict) -> ReplayResult:
    """The actual invoke logic, as a plain function - shared by this
    router's own endpoint and the chatbot (api/routers/chat.py), so there's
    one place that knows how to run a capability, not two copies of it.
    Raises ValueError if the capability's target_app isn't configured.
    """
    target = target_config_for(capability.target_app)
    return await replay_capability(
        capability,
        inputs,
        base_url=target.base_url,
        allowlist=Allowlist.load(),
        load_storage_state_from=target.session_state_path,
        save_storage_state_to=target.session_state_path,
    )


@router.get("", response_model=list[Capability])
def list_capabilities() -> list[Capability]:
    return storage.list_latest_capabilities()


@router.get("/{capability_id}", response_model=Capability)
def get_capability(capability_id: str, version: int | None = None) -> Capability:
    return _load_capability_or_404(capability_id, version)


@router.post("/{capability_id}/invoke", response_model=ReplayResult)
async def invoke_capability_route(capability_id: str, request: InvokeRequest) -> ReplayResult:
    capability = _load_capability_or_404(capability_id, None)
    try:
        return await invoke_capability(capability, request.inputs)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
