"""The provider sign-in's configuration rules, and the key exchange's address check."""

from __future__ import annotations

import httpx
import pytest

from cloud_agent import settings
from cloud_agent.application import model_sign_in as flow
from cloud_agent.domain.errors import Unavailable
from cloud_agent.domain.model_sign_in import (
    MODEL_SIGN_IN_SETTINGS,
    SignInProvider,
    configured_provider,
    split_models,
)

_VALUES = {
    "model_sign_in.name": "  Example   Inference ",
    "model_sign_in.authorize_url": "https://console.provider.example/authorize/key",
    "model_sign_in.key_url": "https://console.provider.example/v1/auth/keys",
    "model_sign_in.base_url": "https://API.provider.example/v1/",
    "model_sign_in.models": "model-a,model-b\nmodel-a  org/model-c",
}


def test_a_complete_configuration_is_read_as_a_connection_would_save_it():
    assert configured_provider(_VALUES) == SignInProvider(
        name="Example Inference",
        authorize_url="https://console.provider.example/authorize/key",
        key_url="https://console.provider.example/v1/auth/keys",
        base_url="https://api.provider.example/v1",
        models=("model-a", "model-b", "org/model-c"),
    )


@pytest.mark.parametrize("key", sorted(_VALUES))
def test_any_empty_setting_turns_the_feature_off(key):
    assert configured_provider({**_VALUES, key: "  "}) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model_sign_in.authorize_url", "http://console.provider.example/authorize"),
        ("model_sign_in.authorize_url", "https://console.provider.example/a?client=x"),
        ("model_sign_in.key_url", "https://console.provider.example/keys#top"),
        ("model_sign_in.base_url", "https://api.provider.example:8443/v1"),
        ("model_sign_in.models", "not a model id!"),
    ],
)
def test_an_unusable_value_turns_the_feature_off_rather_than_failing_later(key, value):
    assert configured_provider({**_VALUES, key: value}) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model_sign_in.name", "x" * 65),
        ("model_sign_in.authorize_url", "https://console.provider.example/authorize#f"),
        ("model_sign_in.authorize_url", "https://console.provider.example/authorize?x=1"),
        ("model_sign_in.key_url", "http://console.provider.example/v1/auth/keys"),
        ("model_sign_in.base_url", "https://api.provider.example/v1?x=1"),
        ("model_sign_in.base_url", "https://api.provider.example:8443/v1"),
        ("model_sign_in.models", ",".join(f"model-{n}" for n in range(17))),
    ],
)
def test_the_settings_refuse_what_the_flow_would_refuse(key, value):
    with pytest.raises(ValueError):
        settings.SETTINGS_BY_KEY[key].validate(value)


def test_every_setting_may_be_left_empty_and_none_reads_the_environment():
    for definition in MODEL_SIGN_IN_SETTINGS:
        assert definition.validate("") == ""
        assert definition.env is None
        assert definition.category == "Model sign-in"


def test_model_ids_are_split_on_commas_and_whitespace():
    assert split_models(" a, b\nc  d,,") == ["a", "b", "c", "d"]


async def test_the_key_exchange_never_dials_an_address_a_sandbox_could_not_reach():
    requests: list[httpx.Request] = []
    provider = configured_provider(_VALUES)
    assert provider is not None

    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.5"]

    with pytest.raises(Unavailable) as refused:
        await flow.exchange_code(
            provider,
            code="code",
            verifier="verifier",
            resolve=private,
            transport=httpx.MockTransport(lambda request: requests.append(request)),
        )
    assert refused.value.type == "model_sign_in_unavailable_upstream"
    assert requests == []


async def test_the_key_exchange_posts_json_and_follows_no_redirect():
    seen: list[httpx.Request] = []
    provider = configured_provider(_VALUES)
    assert provider is not None

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://elsewhere.example/keys"})

    with pytest.raises(Unavailable):
        await flow.exchange_code(
            provider,
            code="code",
            verifier="verifier",
            resolve=public,
            transport=httpx.MockTransport(redirect),
        )
    [request] = seen
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert str(request.url) == "https://console.provider.example/v1/auth/keys"
