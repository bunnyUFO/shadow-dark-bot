# Discord Application Setup

One-time setup to register the bot with Discord and get it into your server. The only value you'll copy is the bot token — the bot auto-discovers everything else.

## 1. Create the application

1. Go to <https://discord.com/developers/applications>.
2. Click **New Application**, name it (e.g., "Shadow Dark Guild Bot"), accept terms, create.
3. In the left sidebar, click **Bot**.
4. Under **Privileged Gateway Intents**, leave them all **off** — this bot only uses slash commands and does not need to read message content.
5. Click **Reset Token** → **Yes, do it!**. Copy the token immediately. This goes in your `.env` as `DISCORD_TOKEN`. (You cannot view it again; if lost, reset and update `.env`.)

> Never commit the token. It belongs only in `.env` (which is gitignored).

## 2. Generate the invite URL

1. In the sidebar, click **OAuth2** → **URL Generator**.
2. Under **Scopes**, check:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, check:
   - Send Messages
   - Embed Links
   - Read Message History
   - Use Slash Commands
4. Copy the generated URL at the bottom of the page.
5. Paste it into your browser, choose your Discord server, click **Authorize**.

The bot will appear in your member list (offline until you start it).

## 3. Fill in `.env`

In your bot directory:
```
cp .env.example .env
```
Open `.env` and set the token. No other values are required:
```
DISCORD_TOKEN=<the token from step 1.5>
```

That's it — no Server ID, no Role ID, no Developer Mode needed. The bot finds its guild automatically on first connect.

## 4. Start the bot

See [deploy-proxmox.md](./deploy-proxmox.md). Once running, the bot logs should include:
```
Logged in as Shadow Dark Guild Bot (id=…)
Synced N command(s) to guild <Server Name> (<id>)
```
Slash commands appear in your server instantly — the bot syncs them guild-scoped to whichever server it finds itself in.

## 5. Try it

In any channel where the bot can see messages:
```
/ping
```
You should see `pong` (ephemeral, visible only to you).

If `/ping` doesn't appear in the slash command list, the bot either isn't running or was invited without the `applications.commands` scope (re-invite with the URL from step 2).

## Notes on the single-guild assumption

This build assumes the bot lives in exactly one Discord server. If invited to a second server, the bot logs a warning and ignores it — commands are only synced to the first server it joined. To move the bot to a different server, kick it from the current one and re-invite to the new one.
