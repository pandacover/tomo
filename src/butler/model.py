from __future__ import annotations

from langchain_xai import ChatXAI

from .config import settings
from .oauth import XAI_API_BASE_URL, get_valid_tokens


def make_model() -> ChatXAI:
    tokens = get_valid_tokens()
    return ChatXAI(
        model=settings.model,
        temperature=0.7,
        max_retries=2,
        api_key="oauth-token-provided-via-authorization-header",
        xai_api_base=XAI_API_BASE_URL,
        default_headers={"Authorization": f"Bearer {tokens.access_token}"},
    )
