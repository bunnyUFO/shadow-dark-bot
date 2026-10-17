from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DISCORD_TOKEN: str
    DATABASE_URL: str = "sqlite:///./data/shadowdark.db"

    # --- Local web app: home network only, no Discord login (pick a character). ---
    LOCAL_WEB_ENABLED: bool = False
    LOCAL_WEB_HOST: str = "0.0.0.0"
    LOCAL_WEB_PORT: int = 8081

    # --- Public web app / Discord Activity (Discord login). Off unless WEB_ENABLED=true. ---
    WEB_ENABLED: bool = False
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    # Public origin the app is served from, e.g. https://shadowdark.bunnyufo.net.
    # The standalone login redirects back to f"{PUBLIC_BASE_URL}/auth/callback".
    PUBLIC_BASE_URL: str = ""
    # Developer Portal → OAuth2 (same application as the bot).
    DISCORD_CLIENT_ID: str = ""
    DISCORD_CLIENT_SECRET: str = ""
    # Signs web session tokens. Any long random string; rotating it logs everyone out.
    SESSION_SECRET: str = ""
    SESSION_TTL_HOURS: int = 12
    # Comma-separated guild IDs. Only members of at least one may log in.
    ALLOWED_GUILD_IDS: str = ""
    # Reverse proxies trusted for X-Forwarded-For / -Proto (comma-separated IPs).
    FORWARDED_ALLOW_IPS: str = "127.0.0.1"

    # --- Dungeon Masters: may edit every character's sheet in the web apps. ---
    # Comma-separated Discord role IDs; whoever holds one is a DM (local and
    # public app, checked through the bot at login).
    DM_ROLE_IDS: str = ""

    @property
    def allowed_guild_ids(self) -> set[int]:
        return _numeric_ids(self.ALLOWED_GUILD_IDS)

    @property
    def dm_role_ids(self) -> set[int]:
        return _numeric_ids(self.DM_ROLE_IDS)

    def web_config_errors(self) -> list[str]:
        """Problems that stop the web apps from starting safely (empty if fine)."""
        errors = []
        if self.LOCAL_WEB_ENABLED and self.WEB_ENABLED and self.LOCAL_WEB_PORT == self.WEB_PORT:
            errors.append("LOCAL_WEB_PORT and WEB_PORT must differ")
        if self.LOCAL_WEB_ENABLED or self.WEB_ENABLED:
            try:
                _numeric_ids(self.DM_ROLE_IDS)
            except ValueError:
                errors.append("DM_ROLE_IDS must be comma-separated numeric role IDs")
        if not self.WEB_ENABLED:
            return errors
        for name in ("PUBLIC_BASE_URL", "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET"):
            if not getattr(self, name):
                errors.append(f"{name} is not set")
        if len(self.SESSION_SECRET) < 32:
            errors.append("SESSION_SECRET must be at least 32 characters")
        try:
            if not self.allowed_guild_ids:
                errors.append("ALLOWED_GUILD_IDS is empty — nobody could log in")
        except ValueError:
            errors.append("ALLOWED_GUILD_IDS must be comma-separated numeric guild IDs")
        if "*" in [ip.strip() for ip in self.FORWARDED_ALLOW_IPS.split(",")]:
            errors.append("FORWARDED_ALLOW_IPS must list the reverse proxy's IP, not *")
        return errors


def _numeric_ids(value: str) -> set[int]:
    """Comma-separated numeric IDs; ValueError on anything else."""
    return {int(part) for part in value.replace(" ", "").split(",") if part}


settings = Settings()
