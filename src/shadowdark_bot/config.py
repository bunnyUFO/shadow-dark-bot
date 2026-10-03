from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DISCORD_TOKEN: str
    DATABASE_URL: str = "sqlite:///./data/shadowdark.db"

    # --- Web app / Discord Activity. Off unless WEB_ENABLED=true. ---
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

    @property
    def allowed_guild_ids(self) -> set[int]:
        return {int(part) for part in self.ALLOWED_GUILD_IDS.replace(" ", "").split(",") if part}

    def web_config_errors(self) -> list[str]:
        """Problems that stop the web app from starting safely (empty if fine)."""
        errors = []
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
        return errors


settings = Settings()
