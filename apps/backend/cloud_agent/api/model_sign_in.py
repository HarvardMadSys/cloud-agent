"""Owner routes for signing in at the deployment's model provider for a key.

``POST /v1/agent/model-sign-in`` issues a state and a PKCE challenge bound to
the owner and answers the provider's authorization page to send the browser
to. ``POST /v1/agent/model-sign-in/finish`` takes the code and the state the
provider sent back to the web app's callback page, and answers the connection
it saved, with the configured models it left off. The flow, and what is
checked before the code is exchanged, is ``application.model_sign_in``.

Both routes are the owner's own and ``no-store``; the code, the state and the
key are never answered or logged. A verification that refuses a save answers
as creating a connection does (``api.model_connections``), except that the
provider has by then issued a key, and the message says where it is.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from cloud_agent.api._owner_auth import require_agent_owner
from cloud_agent.api.deps import get_storage, require_store
from cloud_agent.api.model_connections import (
    _VERIFY_RESPONSES,
    ModelConnectionView,
    ModelStatusView,
    _ConnectionRoute,
    _refused,
    _view,
)
from cloud_agent.application.model_connection_save import (
    CheckLimitReached,
    VerificationRefused,
    VerificationTimedOut,
)
from cloud_agent.application.model_sign_in import (
    SignInStorage,
    finish_sign_in,
    sign_in_provider,
    start_sign_in,
)

router = APIRouter(
    prefix="/v1/agent/model-sign-in", tags=["model-connections"], route_class=_ConnectionRoute
)


class ModelSignInStart(BaseModel):
    """Where to send the browser: the provider's page, with this owner's challenge."""

    authorization_url: str


class ModelSignInFinish(BaseModel):
    """What the provider sent back to the web app's callback page."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=2048, repr=False)
    state: str = Field(..., min_length=1, max_length=512, repr=False)


class ModelSignInSaved(BaseModel):
    """The connection the sign-in saved, and the configured models it left off."""

    connection: ModelConnectionView
    #: Configured models already on another of the owner's connections, or
    #: that no agent can use with this key.
    skipped_models: list[str]


@router.post("", response_model=ModelSignInStart)
async def start_model_sign_in(
    user: dict[str, Any] = Depends(require_agent_owner),
    store: SignInStorage | None = Depends(get_storage),
) -> ModelSignInStart:
    """Start signing in at the provider; nothing is saved until it sends the owner back."""
    url = await start_sign_in(require_store(store), user_id=user["user_id"])
    return ModelSignInStart(authorization_url=url)


def _provider_name() -> str:
    provider = sign_in_provider()
    return provider.name if provider is not None else "The provider"


@router.post(
    "/finish", response_model=ModelSignInSaved, status_code=201, responses=_VERIFY_RESPONSES
)
async def finish_model_sign_in(
    body: ModelSignInFinish,
    user: dict[str, Any] = Depends(require_agent_owner),
    store: SignInStorage | None = Depends(get_storage),
) -> ModelSignInSaved:
    """Exchange the provider's code for the owner's key and save the verified connection.

    The state works once, for the owner it was issued to. Every refusal that
    does not need the key comes before the code is exchanged.
    """
    try:
        saved = await finish_sign_in(
            require_store(store), user_id=user["user_id"], code=body.code, state=body.state
        )
    except CheckLimitReached:
        raise _refused(
            429,
            "model_connection_check_limit",
            "Too many verifications in the last hour, so no key was requested. Try again later.",
        ) from None
    except VerificationTimedOut:
        raise _refused(
            504,
            "model_connection_verification_timeout",
            f"{_provider_name()} issued a key, but verifying it took too long. Nothing was "
            "saved here; the key can be deleted at the provider.",
        ) from None
    except VerificationRefused as refused:
        raise HTTPException(
            422,
            {
                "error": {
                    "type": "model_connection_verification_failed",
                    "message": f"{_provider_name()} issued a key, but no model could be verified "
                    "for any agent with it. Nothing was saved here; the key can be deleted at "
                    "the provider.",
                    "models": [
                        ModelStatusView(**status).model_dump() for status in refused.statuses
                    ],
                }
            },
        ) from None
    return ModelSignInSaved(connection=_view(saved.row), skipped_models=saved.skipped)


__all__ = ["ModelSignInFinish", "ModelSignInSaved", "ModelSignInStart", "router"]
