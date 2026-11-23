"""Stand-in for the standalone extraction app's database module.

The engine code asks `db._Session is not None` before touching storage that only the standalone app has (saved provider keys,
per-workspace reading plans). Inside this backend those come from the user's settings and the environment, so the answer is
always "no database here" and every such branch is skipped."""
_Session = None


def session():
    raise RuntimeError("the folded-in extraction engine has no database of its own; storage belongs to the host application")
