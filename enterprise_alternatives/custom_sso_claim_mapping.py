"""
Self-built SSO claim -> role/team mapping for LiteLLM proxy
(open-source, no Enterprise license needed).

Wires into the ``custom_ui_sso_sign_in_handler`` extension point. After LiteLLM
retrieves the user from your IdP, this handler inspects the OIDC group/role claims
and returns an ``SSOUserDefinedValues`` with the mapped LiteLLM role, budget, and
model scope — the open-source equivalent of manual per-user role assignment.

IMPORTANT — scope of this feature:
    This does NOT remove or raise the built-in free-tier "5 SSO users" limit.
    That limit is a licensing boundary checked *before* this handler runs
    (litellm/proxy/management_endpoints/ui_sso.py). This handler only decides
    *what role* a user gets once they are allowed to sign in. To exceed 5 SSO
    users legitimately, set LITELLM_LICENSE (https://enterprise.litellm.ai/demo).

Group -> role mapping is provided via ``SSO_GROUP_ROLE_MAP`` as JSON, e.g.:

    SSO_GROUP_ROLE_MAP='{"litellm-admins":"proxy_admin","litellm-viewers":"proxy_admin_viewer"}'

The group claim is read from ``extra_fields`` (requires
``GENERIC_USER_EXTRA_ATTRIBUTES`` to include the group attribute, e.g.
``GENERIC_USER_EXTRA_ATTRIBUTES="groups"`` and ``SSO_GROUP_CLAIM="groups"``).

Other env:
* ``SSO_DEFAULT_ROLE``     (default "internal_user")
* ``SSO_DEFAULT_MAX_BUDGET`` (float, optional)
* ``SSO_DEFAULT_BUDGET_DURATION`` (e.g. "30d", optional)

Enable by pointing the proxy at this handler (see README).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from fastapi_sso.sso.base import OpenID

from litellm.proxy._types import LitellmUserRoles, SSOUserDefinedValues

_VALID_ROLES = {
    "proxy_admin": LitellmUserRoles.PROXY_ADMIN.value,
    "proxy_admin_viewer": LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY.value,
    "internal_user": LitellmUserRoles.INTERNAL_USER.value,
    "internal_user_viewer": LitellmUserRoles.INTERNAL_USER_VIEW_ONLY.value,
}


def _load_group_role_map() -> Dict[str, str]:
    raw = os.getenv("SSO_GROUP_ROLE_MAP", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        # normalise role values to canonical litellm role strings
        return {
            str(group): _VALID_ROLES.get(str(role).lower(), str(role))
            for group, role in parsed.items()
        }
    except json.JSONDecodeError:
        print("[custom_sso] SSO_GROUP_ROLE_MAP is not valid JSON; ignoring")  # noqa: T201
        return {}


def _user_groups(userIDPInfo: OpenID) -> List[str]:
    claim = os.getenv("SSO_GROUP_CLAIM", "groups")
    extra = getattr(userIDPInfo, "extra_fields", None) or {}
    value = extra.get(claim)
    if value is None:
        # some providers surface the claim directly on the OpenID object
        value = getattr(userIDPInfo, claim, None)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        # space- or comma-delimited group strings are both common
        sep = "," if "," in value else " "
        return [g.strip() for g in value.split(sep) if g.strip()]
    return []


def _default_role() -> str:
    role = os.getenv("SSO_DEFAULT_ROLE", "internal_user").lower()
    return _VALID_ROLES.get(role, LitellmUserRoles.INTERNAL_USER.value)


def _default_max_budget():
    raw = os.getenv("SSO_DEFAULT_MAX_BUDGET")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _resolve_role(groups: List[str], group_role_map: Dict[str, str]) -> str:
    """Highest-privilege matching group wins."""
    precedence = [
        LitellmUserRoles.PROXY_ADMIN.value,
        LitellmUserRoles.PROXY_ADMIN_VIEW_ONLY.value,
        LitellmUserRoles.INTERNAL_USER.value,
        LitellmUserRoles.INTERNAL_USER_VIEW_ONLY.value,
    ]
    matched = [group_role_map[g] for g in groups if g in group_role_map]
    for role in precedence:
        if role in matched:
            return role
    return _default_role()


async def custom_sso_handler(userIDPInfo: OpenID) -> SSOUserDefinedValues:
    if userIDPInfo.id is None:
        raise ValueError(f"No ID found for user. userIDPInfo={userIDPInfo}")

    group_role_map = _load_group_role_map()
    groups = _user_groups(userIDPInfo)
    role = _resolve_role(groups, group_role_map)

    return SSOUserDefinedValues(
        models=[],
        user_id=userIDPInfo.id,
        user_email=userIDPInfo.email,
        user_role=role,
        max_budget=_default_max_budget(),
        budget_duration=os.getenv("SSO_DEFAULT_BUDGET_DURATION"),
    )
