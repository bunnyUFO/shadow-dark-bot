# Issue tracker: Beads

Issues for this repo live in Beads (`bd`). See [CLAUDE.md](../../CLAUDE.md), Tracked project. The issue prefix is `sd`.

## When a skill says "publish to the issue tracker"

Run `bd create "<title>" -d "<body>" -l <labels>`. For a long body, use `--body-file <file>`.

## When a skill says "fetch the relevant ticket"

Run `bd show <id>`.

## Picking build work

Wayfinder's tickets are decisions, not build work, so build sessions leave them out:

```bash
bd ready --exclude-label wayfinder:map,wayfinder:research,wayfinder:prototype,wayfinder:grilling,wayfinder:task
```

## Wayfinding operations

Used by `/wayfinder`. The map is an epic, and each ticket is a child of it.

- **Map**: `bd create "<title>" -t epic -l wayfinder:map --body-file <file>`. The body (Destination, Notes, Decisions so far, Not yet specified, Out of scope) is the description. The Roadmap shows a map by this label.
- **Ticket**: `bd create "<title>" --parent <map> -l wayfinder:<type> --no-inherit-labels -d "## Question ..."`, where `<type>` is `research`, `prototype`, `grilling` or `task`. Without `--no-inherit-labels` a child copies the map's label.
- **Blocking**: `bd dep add <blocked> <blocker>`, in a second pass once every ticket has an id.
- **Frontier**: `bd ready --parent <map> --exclude-type epic --unassigned`. `bd ready` already leaves out blocked and in-progress issues.
- **Claim**: `bd update <id> --claim`, the session's first write. It sets the assignee and moves the ticket to in progress.
- **Resolve**: `bd comments add <id> "<answer>"`, then `bd close <id> --reason "resolved"`, then append a gist and the ticket id to the map's Decisions so far with `bd update <map> --body-file <file>`. That replaces the whole description, and other sessions may be editing the map, so read it with `bd show <map> --json` right before writing.
- **Undo a claim**: `bd update <id> -s open -a ""`.
