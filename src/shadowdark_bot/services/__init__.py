"""Domain operations shared by the Discord cogs and (later) the web API.

Services take an open SQLAlchemy `Session` plus plain arguments, enforce the
invariants, and either return a small result or raise a domain error. They
never import discord.py — presentation (embeds, views, reply wording) stays in
the caller.
"""
