import asyncio

import aiohttp
import pytest
from aiohttp import web

from streamrip.client.downloadable import BasicDownloadable


@pytest.fixture
async def download_server():
    started, finish = asyncio.Event(), asyncio.Event()
    payload = b"audio" * 50000

    async def ok(request):
        return web.Response(body=payload)

    async def missing(request):
        raise web.HTTPNotFound()

    async def slow(request):
        response = web.StreamResponse()
        await response.prepare(request)
        started.set()
        await finish.wait()
        return response

    app = web.Application()
    app.router.add_get("/ok", ok)
    app.router.add_get("/missing", missing)
    app.router.add_get("/slow", slow)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            yield session, f"http://127.0.0.1:{port}", payload, started
    finally:
        finish.set()
        await runner.cleanup()


async def test_direct_download_uses_async_stream_and_progress(
    download_server, tmp_path
):
    session, base, payload, _ = download_server
    item = BasicDownloadable(session, base + "/ok", "flac", source="qobuz")
    path = tmp_path / "download.flac"
    progress = []
    await item.download(str(path), progress.append)
    assert path.read_bytes() == payload
    assert sum(progress) == len(payload)


async def test_http_error_is_raised_before_writing(download_server, tmp_path):
    session, base, _, _ = download_server
    item = BasicDownloadable(session, base + "/missing", "flac", source="qobuz")
    path = tmp_path / "download.flac"
    with pytest.raises(aiohttp.ClientResponseError):
        await item.download(str(path), lambda count: None)
    assert not await asyncio.to_thread(path.exists)


async def test_stalled_download_is_cancellable(download_server, tmp_path):
    session, base, _, started = download_server
    item = BasicDownloadable(session, base + "/slow", "flac", source="qobuz")
    task = asyncio.create_task(
        item.download(str(tmp_path / "download.flac"), lambda count: None)
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
