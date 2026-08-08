"""Isolated persistent Chromium runtime with a deliberately narrow HTTP API."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from digital_company.browser_policy import contains_human_checkpoint, normalize_domain, validate_browser_url


app = FastAPI(title="Digital Company Browser Runtime")
DATA_DIR = Path(os.getenv("BROWSER_DATA_DIR", "/browser-data")).resolve()
COMPANY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
sessions: dict[str, dict] = {}
playwright = None


class OpenSessionIn(BaseModel):
    url: str = Field(max_length=2000)
    allowed_domains: list[str] = Field(min_length=1, max_length=30)


class BrowserActionIn(BaseModel):
    kind: str
    x: float | None = Field(default=None, ge=0, le=1440)
    y: float | None = Field(default=None, ge=0, le=1000)
    text: str | None = Field(default=None, max_length=4000)
    key: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=2000)
    scroll_x: float | None = Field(default=None, ge=-2000, le=2000)
    scroll_y: float | None = Field(default=None, ge=-2000, le=2000)
    path: list[dict[str, float]] | None = Field(default=None, max_length=100)
    keys: list[str] = Field(default_factory=list, max_length=8)


def safe_company_id(company_id: str) -> str:
    if not COMPANY_RE.fullmatch(company_id):
        raise HTTPException(400, "Invalid company ID")
    return company_id


@app.on_event("startup")
async def startup() -> None:
    global playwright
    from playwright.async_api import async_playwright
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()


@app.on_event("shutdown")
async def shutdown() -> None:
    for session in list(sessions.values()):
        await session["context"].close()
    if playwright:
        await playwright.stop()


async def session_status(company_id: str) -> dict:
    session = sessions.get(company_id)
    if not session:
        return {"status": "closed", "company_id": company_id}
    page = session["page"]
    title = await page.title()
    text = await page.locator("body").inner_text(timeout=3000)
    return {
        "status": "open", "company_id": company_id, "url": page.url, "title": title,
        "allowed_domains": session["allowed_domains"],
        "human_checkpoint": contains_human_checkpoint(page.url, title, text),
        "viewport": {"width": 1440, "height": 1000},
    }


@app.get("/health")
def health():
    return {"status": "ok", "browser": "chromium"}


@app.put("/sessions/{company_id}")
async def open_session(company_id: str, payload: OpenSessionIn):
    company_id = safe_company_id(company_id)
    try:
        allowed = [normalize_domain(value) for value in payload.allowed_domains]
        url = validate_browser_url(payload.url, allowed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if company_id in sessions:
        await sessions.pop(company_id)["context"].close()
    profile = DATA_DIR / company_id
    context = await playwright.chromium.launch_persistent_context(
        str(profile), headless=True, viewport={"width": 1440, "height": 1000},
        env={},
        args=["--disable-dev-shm-usage", "--disable-extensions", "--disable-file-system"],
    )
    page = context.pages[0] if context.pages else await context.new_page()

    async def restrict(route):
        hostname = urlparse(route.request.url).hostname or ""
        permitted = any(hostname == domain or hostname.endswith("." + domain) for domain in allowed)
        await (route.continue_() if permitted else route.abort())

    await page.route("**/*", restrict)
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    sessions[company_id] = {"context": context, "page": page, "allowed_domains": allowed}
    return await session_status(company_id)


@app.get("/sessions/{company_id}")
async def get_session(company_id: str):
    return await session_status(safe_company_id(company_id))


@app.get("/sessions/{company_id}/screenshot")
async def screenshot(company_id: str):
    session = sessions.get(safe_company_id(company_id))
    if not session:
        raise HTTPException(404, "Browser session is closed")
    return Response(await session["page"].screenshot(type="png"), media_type="image/png")


@app.post("/sessions/{company_id}/actions")
async def action(company_id: str, payload: BrowserActionIn):
    session = sessions.get(safe_company_id(company_id))
    if not session:
        raise HTTPException(404, "Browser session is closed")
    page = session["page"]
    if payload.kind in {"click", "double_click"} and payload.x is not None and payload.y is not None:
        await page.mouse.click(payload.x, payload.y, click_count=2 if payload.kind == "double_click" else 1)
    elif payload.kind == "type" and payload.text is not None:
        await page.keyboard.type(payload.text)
    elif payload.kind == "key" and payload.key:
        await page.keyboard.press(payload.key)
    elif payload.kind == "keypress" and payload.keys:
        await page.keyboard.press("+".join(payload.keys))
    elif payload.kind == "scroll":
        await page.mouse.wheel(payload.scroll_x or 0, payload.scroll_y or 0)
    elif payload.kind == "move" and payload.x is not None and payload.y is not None:
        await page.mouse.move(payload.x, payload.y)
    elif payload.kind == "drag" and payload.path and len(payload.path) >= 2:
        await page.mouse.move(payload.path[0]["x"], payload.path[0]["y"])
        await page.mouse.down()
        for point in payload.path[1:]:
            await page.mouse.move(point["x"], point["y"])
        await page.mouse.up()
    elif payload.kind == "wait":
        await page.wait_for_timeout(1000)
    elif payload.kind == "screenshot":
        pass
    elif payload.kind == "navigate" and payload.url:
        try:
            url = validate_browser_url(payload.url, session["allowed_domains"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    else:
        raise HTTPException(400, "Invalid or incomplete browser action")
    await page.wait_for_timeout(350)
    return await session_status(company_id)


@app.delete("/sessions/{company_id}")
async def close_session(company_id: str):
    session = sessions.pop(safe_company_id(company_id), None)
    if session:
        await session["context"].close()
    return {"status": "closed", "company_id": company_id}


def main() -> None:
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8430")))
