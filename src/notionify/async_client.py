"""Asynchronous Notion SDK client.

:class:`AsyncNotionifyClient` mirrors :class:`NotionifyClient` but every
I/O method is an ``async def`` coroutine.  It uses the async variants of
the transport, API wrappers, and image upload functions.

Usage::

    import asyncio
    from notionify import AsyncNotionifyClient

    async def main():
        async with AsyncNotionifyClient(token="secret_xxx") as client:
            result = await client.create_page_with_markdown(
                parent_id="<page_id>",
                title="My Page",
                markdown="# Hello\\n\\nWorld",
            )
            print(result.page_id)

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.diff.executor import AsyncDiffExecutor
from notionify.diff.planner import DiffPlanner
from notionify.errors import NotionifyImageError, NotionifyImageNotFoundError
from notionify.image import (
    async_upload_multi,
    async_upload_single,
    build_image_block_uploaded,
    validate_image,
)
from notionify.models import (
    AppendResult,
    BlockUpdateResult,
    ConversionResult,
    ConversionWarning,
    ImageSourceType,
    InsertResult,
    PageCreateResult,
    PendingImage,
    UpdateResult,
)
from notionify.notion_api.blocks import AsyncBlockAPI
from notionify.notion_api.files import AsyncFileAPI
from notionify.notion_api.pages import AsyncPageAPI
from notionify.notion_api.transport import AsyncNotionTransport
from notionify.utils.chunk import chunk_children


class AsyncNotionifyClient:
    """Asynchronous Notion SDK client.

    Parameters
    ----------
    token:
        Notion integration token.  **Required.**
    **kwargs:
        All remaining keyword arguments are forwarded to
        :class:`NotionifyConfig`.
    """

    def __init__(self, token: str, **kwargs: Any) -> None:
        """Create client.  All kwargs are forwarded to NotionifyConfig."""
        self._config = NotionifyConfig(token=token, **kwargs)
        self._transport = AsyncNotionTransport(self._config)
        self._pages = AsyncPageAPI(self._transport)
        self._blocks = AsyncBlockAPI(self._transport)
        self._files = AsyncFileAPI(self._transport)
        self._converter = MarkdownToNotionConverter(self._config)
        self._renderer = NotionToMarkdownRenderer(self._config)
        self._diff_planner = DiffPlanner(self._config)
        self._diff_executor = AsyncDiffExecutor(self._blocks, self._config)

    # ------------------------------------------------------------------
    # Page creation
    # ------------------------------------------------------------------

    async def create_page_with_markdown(
        self,
        parent_id: str,
        title: str,
        markdown: str,
        parent_type: str = "page",
        properties: dict | None = None,
        title_from_h1: bool = False,
    ) -> PageCreateResult:
        """Create a new Notion page from Markdown content.

        Parameters
        ----------
        parent_id:
            ID of the parent page or database.
        title:
            Page title.  Ignored if *title_from_h1* is ``True`` and the
            Markdown starts with a level-1 heading.
        markdown:
            Raw Markdown text to convert.
        parent_type:
            ``"page"`` or ``"database"``.
        properties:
            Optional extra page properties dict (merged with the generated
            title property).
        title_from_h1:
            If ``True``, extract the title from the first H1 heading and
            remove that heading from the block list.

        Returns
        -------
        PageCreateResult
        """
        # 1. Convert markdown to blocks
        conversion = self._converter.convert(markdown)
        blocks = conversion.blocks
        warnings = list(conversion.warnings)

        # 2. Handle images
        images_uploaded = await self._process_images(conversion)
        warnings.extend(
            w for w in conversion.warnings if w not in warnings
        )

        # 3. Extract title from H1 if requested
        effective_title = title
        if title_from_h1 and blocks:
            first = blocks[0]
            if first.get("type") == "heading_1":
                rich_text = first.get("heading_1", {}).get("rich_text", [])
                if rich_text:
                    effective_title = "".join(
                        seg.get("plain_text", "")
                        or seg.get("text", {}).get("content", "")
                        for seg in rich_text
                    )
                blocks = blocks[1:]

        # 4. Build parent dict
        if parent_type == "database":
            parent = {"database_id": parent_id}
        else:
            parent = {"page_id": parent_id}

        # 5. Build properties with title
        if properties is None:
            properties = {}
        properties.setdefault("title", [{"text": {"content": effective_title}}])

        # 6. Chunk blocks into batches of 100
        batches = chunk_children(blocks)

        # 7. Create page with first batch
        first_batch = batches[0] if batches else []
        page_response = await self._pages.create(
            parent=parent,
            properties=properties,
            children=first_batch,
        )
        page_id = page_response["id"]
        page_url = page_response.get("url", "")

        # 8. Append remaining batches
        for batch in batches[1:]:
            await self._blocks.append_children(page_id, batch)

        # 9. Return result
        return PageCreateResult(
            page_id=page_id,
            url=page_url,
            blocks_created=len(blocks),
            images_uploaded=images_uploaded,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    async def append_markdown(
        self,
        target_id: str,
        markdown: str,
        target_type: str = "page",
    ) -> AppendResult:
        """Append Markdown content to a page or after a block.

        Parameters
        ----------
        target_id:
            ID of the page or block to append to.
        markdown:
            Raw Markdown text to convert and append.
        target_type:
            ``"page"`` or ``"block"``.

        Returns
        -------
        AppendResult
        """
        conversion = self._converter.convert(markdown)
        warnings = list(conversion.warnings)
        images_uploaded = await self._process_images(conversion)

        blocks = conversion.blocks
        batches = chunk_children(blocks)

        for batch in batches:
            await self._blocks.append_children(target_id, batch)

        return AppendResult(
            blocks_appended=len(blocks),
            images_uploaded=images_uploaded,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Overwrite
    # ------------------------------------------------------------------

    async def overwrite_page_content(
        self,
        page_id: str,
        markdown: str,
    ) -> UpdateResult:
        """Full overwrite: archive all existing children, write new blocks.

        Parameters
        ----------
        page_id:
            The Notion page ID.
        markdown:
            New Markdown content.

        Returns
        -------
        UpdateResult
        """
        # 1. Get existing children
        existing = await self._blocks.get_children(page_id)

        # 2. Archive all existing
        deleted_count = 0
        for block in existing:
            block_id = block.get("id")
            if block_id:
                await self._blocks.delete(block_id)
                deleted_count += 1

        # 3. Convert new markdown
        conversion = self._converter.convert(markdown)
        warnings = list(conversion.warnings)
        images_uploaded = await self._process_images(conversion)

        # 4. Chunk and append
        blocks = conversion.blocks
        batches = chunk_children(blocks)

        for batch in batches:
            await self._blocks.append_children(page_id, batch)

        return UpdateResult(
            strategy_used="overwrite",
            blocks_kept=0,
            blocks_inserted=len(blocks),
            blocks_deleted=deleted_count,
            blocks_replaced=0,
            images_uploaded=images_uploaded,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Update (diff or overwrite) methods
    # ------------------------------------------------------------------

    async def update_page_from_markdown(
        self,
        page_id: str,
        markdown: str,
        strategy: str = "diff",
        on_conflict: str = "raise",
    ) -> UpdateResult:
        """Update page with diff or overwrite strategy.

        Parameters
        ----------
        page_id:
            The Notion page ID to update.
        markdown:
            The desired Markdown content.
        strategy:
            ``"diff"`` (default) or ``"overwrite"``.
        on_conflict:
            Conflict resolution policy: ``"raise"`` (default) or
            ``"overwrite"``.

        Returns
        -------
        UpdateResult
        """
        if strategy == "overwrite":
            return await self.overwrite_page_content(page_id, markdown)

        # Diff strategy: fetch existing blocks, compute diff, apply.
        existing_blocks = await self._blocks.get_children(page_id)

        # 2. Convert new markdown
        conversion = self._converter.convert(markdown)
        warnings = list(conversion.warnings)
        images_uploaded = await self._process_images(conversion)

        new_blocks = conversion.blocks

        # 3. Plan diff
        ops = self._diff_planner.plan(existing_blocks, new_blocks)

        # 4. Execute diff ops
        result = await self._diff_executor.execute(page_id, ops)

        # Merge warnings and image count
        result.images_uploaded = images_uploaded
        result.warnings.extend(warnings)

        return result

    # ------------------------------------------------------------------
    # Single-block operations
    # ------------------------------------------------------------------

    async def update_block(
        self,
        block_id: str,
        markdown_fragment: str,
    ) -> BlockUpdateResult:
        """Update a single block with a markdown fragment.

        Parameters
        ----------
        block_id:
            The UUID of the block to update.
        markdown_fragment:
            Markdown text to convert into the block's new content.

        Returns
        -------
        BlockUpdateResult
        """
        conversion = self._converter.convert(markdown_fragment)
        warnings = list(conversion.warnings)
        await self._process_images(conversion)

        if not conversion.blocks:
            return BlockUpdateResult(block_id=block_id, warnings=warnings)

        new_block = conversion.blocks[0]
        block_type = new_block.get("type", "")
        type_data = new_block.get(block_type, {})
        payload = {block_type: type_data}

        await self._blocks.update(block_id, payload)

        return BlockUpdateResult(block_id=block_id, warnings=warnings)

    async def delete_block(
        self,
        block_id: str,
        archive: bool = True,
    ) -> None:
        """Delete (archive) a block.

        Parameters
        ----------
        block_id:
            The UUID of the block to delete.
        archive:
            If ``True`` (default), the block is archived rather than
            permanently deleted.
        """
        await self._blocks.delete(block_id)

    async def insert_after(
        self,
        block_id: str,
        markdown_fragment: str,
    ) -> InsertResult:
        """Insert new blocks after a given block.

        Parameters
        ----------
        block_id:
            The UUID of the block after which to insert.
        markdown_fragment:
            Markdown text to convert and insert.

        Returns
        -------
        InsertResult
        """
        conversion = self._converter.convert(markdown_fragment)
        warnings = list(conversion.warnings)
        await self._process_images(conversion)

        blocks = conversion.blocks
        if not blocks:
            return InsertResult(inserted_block_ids=[], warnings=warnings)

        # Retrieve the block to find its parent.
        block_info = await self._blocks.retrieve(block_id)
        parent_id = block_info.get("parent", {}).get("page_id") or block_info.get(
            "parent", {}
        ).get("block_id", "")

        inserted_ids: list[str] = []
        batches = chunk_children(blocks)

        after_id: str | None = block_id
        for batch in batches:
            response = await self._blocks.append_children(
                parent_id, batch, after=after_id
            )
            new_ids = _extract_block_ids(response)
            inserted_ids.extend(new_ids)
            if new_ids:
                after_id = new_ids[-1]

        return InsertResult(inserted_block_ids=inserted_ids, warnings=warnings)

    # ------------------------------------------------------------------
    # Export (Notion -> Markdown)
    # ------------------------------------------------------------------

    async def page_to_markdown(
        self,
        page_id: str,
        recursive: bool = False,
        max_depth: int | None = None,
    ) -> str:
        """Export a Notion page to Markdown.

        Parameters
        ----------
        page_id:
            The Notion page ID.
        recursive:
            If ``True``, recursively fetch and render children of child
            blocks.
        max_depth:
            Maximum recursion depth when *recursive* is ``True``.
            ``None`` means unlimited.

        Returns
        -------
        str
            The rendered Markdown text.
        """
        blocks = await self._blocks.get_children(page_id)

        if recursive:
            await self._fetch_blocks_recursive(
                blocks, current_depth=0, max_depth=max_depth
            )

        return self._renderer.render_blocks(blocks)

    async def block_to_markdown(
        self,
        block_id: str,
        recursive: bool = True,
        max_depth: int | None = 3,
    ) -> str:
        """Export a block subtree to Markdown.

        Parameters
        ----------
        block_id:
            The UUID of the root block.
        recursive:
            If ``True`` (default), recursively fetch children.
        max_depth:
            Maximum recursion depth.  Defaults to 3.

        Returns
        -------
        str
            The rendered Markdown text.
        """
        blocks = await self._blocks.get_children(block_id)

        if recursive:
            await self._fetch_blocks_recursive(
                blocks, current_depth=0, max_depth=max_depth
            )

        return self._renderer.render_blocks(blocks)

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP transport."""
        await self._transport.close()

    async def __aenter__(self) -> AsyncNotionifyClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _process_images(self, conversion: ConversionResult) -> int:
        """Process pending images with concurrency control.

        Uses ``asyncio.Semaphore`` to limit concurrent uploads to
        ``config.image_max_concurrent``.

        Parameters
        ----------
        conversion:
            The conversion result containing blocks and pending images.

        Returns
        -------
        int
            Number of images successfully uploaded.
        """
        if not conversion.images or not self._config.image_upload:
            return 0

        semaphore = asyncio.Semaphore(self._config.image_max_concurrent)
        _SKIP_SENTINEL = {"_notionify_skip": True}

        async def _process_one(pending: PendingImage) -> int:
            async with semaphore:
                try:
                    return await self._process_single_image(
                        pending, conversion.blocks, conversion.warnings
                    )
                except NotionifyImageError as exc:
                    self._handle_image_error(
                        pending, conversion.blocks, conversion.warnings, exc,
                        skip_sentinel=_SKIP_SENTINEL,
                    )
                    return 0

        tasks = [_process_one(pending) for pending in conversion.images]
        results = await asyncio.gather(*tasks)

        # Remove skip sentinels in a single pass (preserves indices during processing).
        conversion.blocks[:] = [
            b for b in conversion.blocks if b is not _SKIP_SENTINEL
        ]

        return sum(results)

    async def _process_single_image(
        self,
        pending: PendingImage,
        blocks: list[dict],
        warnings: list[ConversionWarning],
    ) -> int:
        """Process a single pending image.  Returns 1 if uploaded, 0 otherwise."""
        if pending.source_type == ImageSourceType.EXTERNAL_URL:
            return 0

        if pending.source_type == ImageSourceType.UNKNOWN:
            return 0

        if pending.source_type == ImageSourceType.LOCAL_FILE:
            return await self._upload_local_file(pending, blocks, warnings)

        if pending.source_type == ImageSourceType.DATA_URI:
            return await self._upload_data_uri(pending, blocks, warnings)

        return 0

    async def _upload_local_file(
        self,
        pending: PendingImage,
        blocks: list[dict],
        warnings: list[ConversionWarning],
    ) -> int:
        """Read, validate, upload a local file image and replace the block."""
        base = self._config.image_base_dir
        if base is not None:
            base_path = Path(base).resolve()  # noqa: ASYNC240 - trivial stat, not blocking I/O
            file_path = (base_path / pending.src).resolve()
            if not file_path.is_relative_to(base_path):
                raise NotionifyImageNotFoundError(
                    message=f"Image path escapes base directory: {pending.src}",
                    context={"src": pending.src},
                )
        else:
            file_path = Path(pending.src).expanduser().resolve()  # noqa: ASYNC240
        if not file_path.is_file():
            raise NotionifyImageNotFoundError(
                message=f"Image file not found: {pending.src}",
                context={"src": pending.src},
            )

        # Read file bytes in an executor to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, file_path.read_bytes)

        mime_type, validated_data = validate_image(
            pending.src,
            pending.source_type,
            data,
            self._config,
        )
        if validated_data is None:
            validated_data = data

        file_name = file_path.name

        upload_id = await self._do_upload(file_name, mime_type, validated_data)
        new_block = build_image_block_uploaded(upload_id)

        if 0 <= pending.block_index < len(blocks):
            blocks[pending.block_index] = new_block

        return 1

    async def _upload_data_uri(
        self,
        pending: PendingImage,
        blocks: list[dict],
        warnings: list[ConversionWarning],
    ) -> int:
        """Decode, validate, upload a data-URI image and replace the block."""
        mime_type, decoded_data = validate_image(
            pending.src,
            pending.source_type,
            None,
            self._config,
        )
        if decoded_data is None:
            return 0

        ext = _mime_to_extension(mime_type)
        file_name = f"image{ext}"

        upload_id = await self._do_upload(file_name, mime_type, decoded_data)
        new_block = build_image_block_uploaded(upload_id)

        if 0 <= pending.block_index < len(blocks):
            blocks[pending.block_index] = new_block

        return 1

    async def _do_upload(self, name: str, mime_type: str, data: bytes) -> str:
        """Choose single or multi-part upload based on data size."""
        if len(data) <= self._config.image_max_size_bytes:
            return await async_upload_single(self._files, name, mime_type, data)
        return await async_upload_multi(self._files, name, mime_type, data)

    def _handle_image_error(
        self,
        pending: PendingImage,
        blocks: list[dict],
        warnings: list[ConversionWarning],
        exc: NotionifyImageError,
        skip_sentinel: dict | None = None,
    ) -> None:
        """Apply the configured image_fallback policy on error."""
        fallback = self._config.image_fallback

        if fallback == "raise":
            raise exc

        if fallback == "placeholder":
            placeholder_block = {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"[image: {pending.src}]"},
                        }
                    ],
                },
            }
            if 0 <= pending.block_index < len(blocks):
                blocks[pending.block_index] = placeholder_block

            warnings.append(
                ConversionWarning(
                    code="IMAGE_UPLOAD_FAILED",
                    message=f"Image upload failed: {exc.message}",
                    context={"src": pending.src, "error": str(exc)},
                )
            )
        else:
            # "skip" -- mark with sentinel (cleaned up after all images processed).
            if skip_sentinel is not None and 0 <= pending.block_index < len(blocks):
                blocks[pending.block_index] = skip_sentinel

            warnings.append(
                ConversionWarning(
                    code="IMAGE_SKIPPED",
                    message=f"Image skipped: {exc.message}",
                    context={"src": pending.src, "error": str(exc)},
                )
            )

    async def _fetch_blocks_recursive(
        self,
        blocks: list[dict],
        current_depth: int,
        max_depth: int | None,
    ) -> None:
        """Recursively fetch children for blocks that have them.

        Modifies *blocks* in place, attaching fetched children under the
        block's type-specific data key or a top-level ``"children"`` key.

        Parameters
        ----------
        blocks:
            List of block dicts (already fetched) to enrich with children.
        current_depth:
            The current recursion depth.
        max_depth:
            Maximum depth.  ``None`` means unlimited.
        """
        if max_depth is not None and current_depth >= max_depth:
            return

        for block in blocks:
            if not block.get("has_children", False):
                continue

            block_id = block.get("id")
            if not block_id:
                continue

            children = await self._blocks.get_children(block_id)

            # Attach children under the block's type-specific key so the
            # renderer can find them.
            block_type = block.get("type", "")
            if block_type and block_type in block and isinstance(block[block_type], dict):
                block[block_type]["children"] = children
            else:
                block["children"] = children

            # Recurse into the children.
            await self._fetch_blocks_recursive(
                children,
                current_depth=current_depth + 1,
                max_depth=max_depth,
            )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _extract_block_ids(response: dict) -> list[str]:
    """Extract block IDs from an append_children API response."""
    results = response.get("results", [])
    return [r["id"] for r in results if "id" in r]


def _mime_to_extension(mime_type: str) -> str:
    """Map a MIME type to a file extension."""
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }
    return mapping.get(mime_type, ".bin")
