"""Discord OAuth2 code exchange and our own signed session tokens.

Login (standalone site or Discord Activity) ends with the client holding a
one-time OAuth `code`. The backend exchanges it for a Discord access token with
the client secret, asks Discord who the user is, and — if they belong to an
allowed guild — issues a session token signed with SESSION_SECRET. The client
sends that back as `Authorization: Bearer …` (not a cookie: third-party iframe
cookies are often blocked inside Discord).
"""

from dataclasses import dataclass

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

DISCORD_API = "https://discord.com/api/v10"
SCOPES = ("identify",)


class OAuthError(Exception):
    """Discord rejected the code or token, or couldn't be reached."""


@dataclass(frozen=True)
class DiscordUser:
    id: str
    username: str
    global_name: str | None = None
    avatar: str | None = None

    @property
    def display_name(self) -> str:
        return self.global_name or self.username

    @property
    def avatar_url(self) -> str | None:
        if not self.avatar:
            return None
        return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar}.png"


class DiscordOAuth:
    """Talks to Discord's OAuth2 endpoints for one application."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=DISCORD_API, timeout=10.0, transport=self._transport
        )

    async def exchange_code(self, code: str, redirect_uri: str | None) -> str:
        """Trade an authorization code for an access token. The Activity flow
        (Embedded App SDK `authorize`) sends no redirect URI; the standalone
        redirect flow must send the one it authorized with."""
        data = {
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            "grant_type": "authorization_code",
            "code": code,
        }
        if redirect_uri is not None:
            data["redirect_uri"] = redirect_uri
        try:
            async with self._client() as client:
                resp = await client.post("/oauth2/token", data=data)
        except httpx.HTTPError as err:
            raise OAuthError("Couldn't reach Discord.") from err
        if resp.status_code != 200:
            raise OAuthError("Discord rejected the login code.")
        token = resp.json().get("access_token")
        if not token:
            raise OAuthError("Discord returned no access token.")
        return token

    async def fetch_user(self, access_token: str) -> DiscordUser:
        try:
            async with self._client() as client:
                resp = await client.get(
                    "/users/@me", headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.HTTPError as err:
            raise OAuthError("Couldn't reach Discord.") from err
        if resp.status_code != 200:
            raise OAuthError("Discord rejected the access token.")
        body = resp.json()
        return DiscordUser(
            id=str(body["id"]),
            username=body.get("username", ""),
            global_name=body.get("global_name"),
            avatar=body.get("avatar"),
        )


@dataclass(frozen=True)
class SessionUser:
    """Who a verified session token belongs to."""

    user_id: str
    display_name: str


class SessionSigner:
    """Issues and verifies timestamped, HMAC-signed session tokens."""

    _SALT = "shadowdark-web-session"

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=self._SALT)
        self.ttl_seconds = ttl_seconds

    def issue(self, user: DiscordUser) -> str:
        return self._serializer.dumps({"uid": user.id, "name": user.display_name})

    def verify(self, token: str) -> SessionUser | None:
        try:
            data = self._serializer.loads(token, max_age=self.ttl_seconds)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("uid"), str):
            return None
        return SessionUser(user_id=data["uid"], display_name=str(data.get("name", "")))
