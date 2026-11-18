"""Remove a user's Supabase login record. Needs the server-only secret key."""

import httpx

from app.core.config import get_settings


def delete_auth_user(user_id) -> bool:
    """True if the login record is gone (or was already gone). False if it could not be
    removed, including when no secret key is configured."""
    settings = get_settings()
    if not settings.supabase_url or settings.supabase_secret_key is None:
        return False
    key = settings.supabase_secret_key.get_secret_value()
    try:
        response = httpx.delete(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code in (200, 204, 404)


def get_auth_user_deleter():
    """FastAPI dependency. Tests replace it so no real call is made."""
    return delete_auth_user
