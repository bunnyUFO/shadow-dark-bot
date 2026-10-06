"""Web app for the character sheet (standalone site + Discord Activity).

`app.create_app` builds the FastAPI app from injected collaborators so tests
can run it without Discord; `server` wires it to the running bot and serves it
with uvicorn inside the bot's event loop.
"""
