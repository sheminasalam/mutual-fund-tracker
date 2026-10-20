"""Authenticated download endpoint for exported portfolio PNGs."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import EXPORT_SUBDIR, EXPORT_URL_BASE
from .export_utils import UnsafeExportError, is_valid_export_name, read_validated_export


class MutualFundExportView(HomeAssistantView):
    """Serve exports only to authenticated users or valid signed paths."""

    url = EXPORT_URL_BASE + "/{filename}"
    name = "api:mutual_fund_tracker:export"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        if not is_valid_export_name(filename):
            raise web.HTTPNotFound()
        root = Path(self._hass.config.path(*EXPORT_SUBDIR))
        try:
            # Same checks as the copy step: must live directly in the export
            # folder, no symlinks, regular file, PNG signature. The bytes are
            # read through the already-verified descriptor, so nothing can be
            # swapped in between the check and the read.
            data = await self._hass.async_add_executor_job(
                read_validated_export, str(root / filename), root, filename
            )
        except UnsafeExportError:
            raise web.HTTPNotFound() from None
        return web.Response(
            body=data,
            content_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
