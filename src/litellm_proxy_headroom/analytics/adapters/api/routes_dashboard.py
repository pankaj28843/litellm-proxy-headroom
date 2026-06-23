from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import HTMLResponse

from ..postgres.stats_queries import compression_stats
from .deps import SessionDep

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(session: SessionDep) -> str:
    snapshot = await compression_stats(session)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Headroom Analytics</title>
  </head>
  <body>
    <main>
      <h1>Headroom Analytics</h1>
      <dl>
        <dt>Requests</dt><dd>{snapshot.requests}</dd>
        <dt>Executions</dt><dd>{snapshot.executions}</dd>
        <dt>Chunks</dt><dd>{snapshot.chunks}</dd>
        <dt>Provider calls</dt><dd>{snapshot.provider_calls}</dd>
        <dt>Tokens saved</dt><dd>{snapshot.tokens_saved}</dd>
        <dt>Retrievals</dt><dd>{snapshot.retrievals}</dd>
      </dl>
    </main>
  </body>
</html>"""
