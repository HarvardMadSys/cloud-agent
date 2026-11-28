"""Signing in at a model provider for a key: what an operator configures.

A deployment may name one model provider where an owner can create an
account, or sign in to the one they already have, and come back with an API
key of their own. The browser goes to the provider's authorization page, which
runs the provider's own sign-up or sign-in and asks the owner to approve a key
for this service; the provider sends the browser back with a one-time code,
and the control plane exchanges that code, with the PKCE verifier only it
holds, for a key on the owner's provider account (OpenRouter's PKCE key flow
is the model). The key is saved as one of the owner's own model connections,
verified like any other (``application.model_sign_in``).

This is a way to *obtain* the owner's own key, nothing more. It signs no one in
to this service, decides no admission, grants nothing per turn and supplies no
model catalog (AGENTS.md § 5): the provider never learns who the owner is here,
and this service learns nothing about the owner there except the key.

The settings are all-or-nothing: with any of them empty there is no offer, and
the routes refuse. Each value is held to the rule the flow applies to it, so a
value that saves is one that works.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from cloud_agent.domain.model_connections import (
    ModelConnectionInvalid,
    clean_base_url,
    clean_models,
    clean_name,
)
from cloud_agent.setting_definition import Setting

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

CATEGORY = "Model sign-in"
#: The web app's page the provider sends the browser back to, under the web
#: app URL. The operator registers ``<web app URL>`` + this with the provider.
CALLBACK_PATH = "/settings/models/connected"

KEY_NAME = "model_sign_in.name"
KEY_AUTHORIZE_URL = "model_sign_in.authorize_url"
KEY_KEY_URL = "model_sign_in.key_url"
KEY_BASE_URL = "model_sign_in.base_url"
KEY_MODELS = "model_sign_in.models"


def split_models(value: str) -> list[str]:
    """The model ids a saved value lists: separated by commas, spaces or new lines."""
    return [item for item in value.replace(",", " ").split() if item]


def _empty_or(cleaner: Callable[[str], object]) -> Callable[[str], bool]:
    def check(value: str) -> bool:
        if not value.strip():
            return True
        try:
            cleaner(value)
        except ModelConnectionInvalid:
            return False
        return True

    return check


def _no_fragment(value: str) -> bool:
    """Parameters are appended to this URL, so it may not end in a fragment."""
    return "#" not in value


MODEL_SIGN_IN_SETTINGS: tuple[Setting, ...] = (
    Setting(
        KEY_NAME,
        "Provider name",
        CATEGORY,
        "",
        description=(
            "The model provider owners can sign in to for a key of their own. Owners without a "
            "connection to it see a button to create or link an account there, and the "
            "connection it saves takes this name. Provider sign-in is off until every setting "
            "in this section is filled in."
        ),
        check=_empty_or(clean_name),
        hint="1 to 64 characters.",
    ),
    Setting(
        KEY_AUTHORIZE_URL,
        "Authorization page URL",
        CATEGORY,
        "",
        url=True,
        https_only=True,
        check=_no_fragment,
        hint="An https:// address without a query or fragment.",
        description=(
            "The provider's page that signs the owner up or in and asks them to approve a key. "
            "The browser is sent there with callback_url, code_challenge and "
            "code_challenge_method=S256 added. Register <web app URL>"
            f"{CALLBACK_PATH} as the callback with the provider."
        ),
    ),
    Setting(
        KEY_KEY_URL,
        "Key exchange URL",
        CATEGORY,
        "",
        url=True,
        https_only=True,
        check=_no_fragment,
        hint="An https:// address without a query or fragment.",
        description=(
            "Where the control plane exchanges the provider's one-time code and its PKCE "
            "verifier for the owner's API key: a JSON POST of code, code_verifier and "
            "code_challenge_method, answered with the key. Called from the control plane "
            "only, never from a browser or a sandbox."
        ),
    ),
    Setting(
        KEY_BASE_URL,
        "Inference Base URL",
        CATEGORY,
        "",
        description=(
            "The Base URL the new connection uses, as an owner would type it on the Models "
            "page. An owner who already has a connection with this Base URL is not offered "
            "sign-in."
        ),
        check=_empty_or(clean_base_url),
        hint="An https:// address on port 443, without a query.",
    ),
    Setting(
        KEY_MODELS,
        "Models",
        CATEGORY,
        "",
        description=(
            "The model ids the new connection lists, separated by commas. Each is verified "
            "with the new key, which spends the owner's hourly checks; a model no agent can "
            "use is left off, and one already on another of the owner's connections is "
            "skipped."
        ),
        check=_empty_or(lambda value: clean_models(split_models(value))),
        hint="1 to 16 model ids.",
    ),
)


@dataclass(frozen=True)
class SignInProvider:
    """A provider fully configured for sign-in, its values already checked."""

    name: str
    authorize_url: str
    key_url: str
    base_url: str
    models: tuple[str, ...]


def configured_provider(values: Mapping[str, str]) -> SignInProvider | None:
    """The configured provider, or ``None`` while any setting is empty or unusable."""
    raw = {key: str(values.get(key) or "").strip() for key in _KEYS}
    if not all(raw.values()):
        return None
    for key in (KEY_AUTHORIZE_URL, KEY_KEY_URL):
        parts = urlsplit(raw[key])
        if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
            return None
    try:
        return SignInProvider(
            name=clean_name(raw[KEY_NAME]),
            authorize_url=raw[KEY_AUTHORIZE_URL],
            key_url=raw[KEY_KEY_URL],
            base_url=clean_base_url(raw[KEY_BASE_URL]),
            models=tuple(clean_models(split_models(raw[KEY_MODELS]))),
        )
    except ModelConnectionInvalid:
        return None


_KEYS = (KEY_NAME, KEY_AUTHORIZE_URL, KEY_KEY_URL, KEY_BASE_URL, KEY_MODELS)

__all__ = [
    "CALLBACK_PATH",
    "CATEGORY",
    "MODEL_SIGN_IN_SETTINGS",
    "SignInProvider",
    "configured_provider",
    "split_models",
]
