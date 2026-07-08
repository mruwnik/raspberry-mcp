"""Tests for nyaa search."""

import pytest

from local_mcp.lib import torrent

NYAA_ROW_HTML = """
<html><body><table><tbody>
<tr class="success">
  <td>cat</td>
  <td><a href="/view/1" title="[SubsPlease] Tenmaku no Jaadugar - 03 (1080p) [AAAA].mkv">[SubsPlease] Tenmaku no Jaadugar - 03 (1080p) [AAAA].mkv</a></td>
  <td><a href="/download/1.torrent"><i class="fa-download"></i></a><a href="magnet:?xt=urn:btih:abc"><i class="fa-magnet"></i></a></td>
  <td>1.4 GiB</td><td>2026-07-08</td><td>100</td><td>2</td><td>500</td>
</tr>
</tbody></table></body></html>
"""


@pytest.mark.asyncio
async def test_search_releases_builds_query_and_parses(monkeypatch):
    captured = {}

    class FakeResponse:
        text = NYAA_ROW_HTML

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, timeout=None):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setattr(torrent.httpx, "AsyncClient", FakeClient)
    results = await torrent.search_releases("jaadugar", group="SubsPlease", quality="1080")
    assert "f=2" in captured["url"] and "c=1_2" in captured["url"]
    assert "SubsPlease" in captured["url"] and "jaadugar" in captured["url"]
    assert len(results) == 1
    assert results[0]["title"] == "Tenmaku no Jaadugar"
    assert results[0]["episode"] == 3.0
    assert results[0]["torrent"] == "https://nyaa.si/download/1.torrent"
