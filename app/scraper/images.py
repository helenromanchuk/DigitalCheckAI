"""Downloads item images into the local data/images folder."""
import logging
import re

import httpx

from app.config import IMAGES_DIR
from app.database import Database

logger = logging.getLogger(__name__)

IMAGE_TIMEOUT_SECONDS = 15.0


def _image_filename(item_number: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", item_number) + ".jpg"


def download_missing_images(db: Database) -> int:
    """
    Downloads images for catalog items that have an image_url but no local file.
    A failed image doesn't stop the sync; those items are retried on the next run.
    Returns the number of images downloaded.
    """
    pending = db.get_items_missing_images()
    if not pending:
        return 0

    downloaded = 0
    with httpx.Client(timeout=IMAGE_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for item in pending:
            try:
                resp = client.get(item["image_url"])
                if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
                    logger.info("Нет картинки для %s (HTTP %s)", item["item_number"], resp.status_code)
                    continue
                filename = _image_filename(item["item_number"])
                (IMAGES_DIR / filename).write_bytes(resp.content)
                db.set_item_image_file(item["item_number"], filename)
                downloaded += 1
            except httpx.HTTPError as exc:
                logger.warning("Не удалось скачать картинку %s: %s", item["item_number"], exc)
    return downloaded
