"""
Self-built JWT / OAuth2 API authentication for LiteLLM proxy
(open-source, no Enterprise license needed).

Wires into the ``custom_auth`` extension point. Incoming ``Authorization: Bearer <token>``
values are treated as JWTs and validated locally; on success the claims are mapped
to a ``UserAPIKeyAuth`` object so the request runs as an authenticated user with the
budget / rate-limit / model scope carried in the token.

Two verification modes, selected by env:

* HS256 (shared secret) — set ``JWT_AUTH_HS256_SECRET``.
* RS256 / ES256 (asymmetric, JWKS) — set ``JWT_AUTH_JWKS_URL`` (an OIDC provider's
  ``jwks_uri``). Keys are fetched once and cached; unknown ``kid`` triggers a refetch.

Optional validation env:
* ``JWT_AUTH_ISSUER``   — required ``iss`` claim.
* ``JWT_AUTH_AUDIENCE`` — required ``aud`` claim.
* ``JWT_AUTH_ALGORITHMS`` — comma list, default derived from the mode.

Claim -> UserAPIKeyAuth mapping (claim names overridable by env):
* ``JWT_CLAIM_USER_ID``    (default "sub")   -> user_id
* ``JWT_CLAIM_USER_EMAIL`` (default "email") -> user_email
* ``JWT_CLAIM_TEAM_ID``    (default "team_id") -> team_id
* ``JWT_CLAIM_ROLE``       (default "role")  -> user_role (internal_user unless "proxy_admin")
* ``JWT_CLAIM_MODELS``     (default "models") -> allowed models (list or CSV)

Enable in config.yaml:

    general_settings:
      custom_auth: enterprise_alternatives.custom_jwt_auth.user_api_key_auth
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from fastapi import Request

from litellm.proxy._types import (
    LitellmUserRoles,
    ProxyException,
    UserAPIKeyAuth,
)

# jwt (PyJWT) and cryptography are runtime dependencies of litellm.
import jwt
from jwt import PyJWKClient


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name, default)
    return val.strip() if isinstance(val, str) else val


def _algorithms() -> List[str]:
    explicit = _env("JWT_AUTH_ALGORITHMS")
    if explicit:
        return [a.strip() for a in explicit.split(",") if a.strip()]
    if _env("JWT_AUTH_HS256_SECRET"):
        return ["HS256"]
    return ["RS256", "ES256"]


# Cache the JWKS client across requests; PyJWKClient does its own key caching.
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _decode(token: str) -> Dict[str, Any]:
    """Verify signature + standard claims, return the decoded payload."""
    algorithms = _algorithms()
    issuer = _env("JWT_AUTH_ISSUER")
    audience = _env("JWT_AUTH_AUDIENCE")
    options = {"require": ["exp"]}  # always require expiry

    decode_kwargs: Dict[str, Any] = {
        "algorithms": algorithms,
        "options": options,
    }
    if issuer:
        decode_kwargs["issuer"] = issuer
    if audience:
        decode_kwargs["audience"] = audience

    secret = _env("JWT_AUTH_HS256_SECRET")
    jwks_url = _env("JWT_AUTH_JWKS_URL")

    if secret:
        return jwt.decode(token, secret, **decode_kwargs)
    if jwks_url:
        signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, **decode_kwargs)

    raise RuntimeError(
        "custom_jwt_auth misconfigured: set JWT_AUTH_HS256_SECRET or JWT_AUTH_JWKS_URL"
    )


def _extract_models(claims: Dict[str, Any], claim_name: str) -> List[str]:
    raw = claims.get(claim_name)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(m) for m in raw]
    if isinstance(raw, str):
        return [m.strip() for m in raw.split(",") if m.strip()]
    return []


def _map_role(claims: Dict[str, Any], claim_name: str) -> LitellmUserRoles:
    role = str(claims.get(claim_name, "")).lower()
    if role in ("proxy_admin", "admin"):
        return LitellmUserRoles.PROXY_ADMIN
    if role in ("proxy_admin_viewer", "viewer"):
        return LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY
    return LitellmUserRoles.INTERNAL_USER


def _bad_token(detail: str) -> ProxyException:
    return ProxyException(
        message=f"Invalid JWT: {detail}",
        type="auth_error",
        param="authorization",
        code=401,
    )


async def user_api_key_auth(request: Request, api_key: str) -> Union[UserAPIKeyAuth, str]:
    """custom_auth entrypoint. ``api_key`` is the Bearer value with 'Bearer ' stripped."""
    token = (api_key or "").strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    if not token:
        raise _bad_token("empty token")

    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise _bad_token("token expired")
    except jwt.InvalidTokenError as exc:
        raise _bad_token(str(exc))
    except RuntimeError:
        raise
    except Exception as exc:  # unexpected -> reject, never allow through
        raise _bad_token(f"verification error: {exc}")

    user_id_claim = _env("JWT_CLAIM_USER_ID", "sub")
    email_claim = _env("JWT_CLAIM_USER_EMAIL", "email")
    team_claim = _env("JWT_CLAIM_TEAM_ID", "team_id")
    role_claim = _env("JWT_CLAIM_ROLE", "role")
    models_claim = _env("JWT_CLAIM_MODELS", "models")

    user_id = claims.get(user_id_claim)
    if not user_id:
        raise _bad_token(f"missing '{user_id_claim}' claim")

    models = _extract_models(claims, models_claim)

    return UserAPIKeyAuth(
        api_key=None,
        user_id=str(user_id),
        user_email=claims.get(email_claim),
        team_id=claims.get(team_claim),
        user_role=_map_role(claims, role_claim),
        models=models,
        jwt_claims=claims,
        metadata={"auth_source": "custom_jwt_auth"},
    )
