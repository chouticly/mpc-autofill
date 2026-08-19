import io
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import ratelimit
import requests
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from oauth2client.service_account import ServiceAccountCredentials

import src.constants as constants
from src.logging import logger
from src.processing import (
    ImagePostProcessingConfig,
    embedded_file_dpi,
    image_meets_mpc_print_requirements,
    post_process_image,
    save_processed_image,
)

thread_local = threading.local()  # Should only be called once per thread


# region Google Drive API


def find_or_create_google_drive_service() -> Resource:
    if (service := getattr(thread_local, "google_drive_service", None)) is None:
        logger.debug("Getting Google Drive API credentials...")
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            str(Path(os.path.abspath(__file__)).parent.parent / constants.SERVICE_ACC_FILENAME), scopes=constants.SCOPES
        )
        service = build("drive", "v3", credentials=creds, static_discovery=False, cache_discovery=False)
        logger.debug("Finished getting Google Drive API credentials - saving to thread local storage.")
        thread_local.google_drive_service = service
    return service


@ratelimit.sleep_and_retry  # type: ignore  # `ratelimit` does not implement decorator typing correctly
@ratelimit.limits(calls=20_000, period=100)  # type: ignore  # `ratelimit` does not implement decorator typing correctly
def execute_google_drive_api_call(service: Resource) -> Optional[dict[str, Any]]:
    try:
        return service.execute()
    except HttpError:
        return None


# endregion

# region network IO


@ratelimit.sleep_and_retry  # type: ignore  # `ratelimit` does not implement decorator typing correctly
@ratelimit.limits(calls=1, period=0.1)  # type: ignore  # `ratelimit` does not implement decorator typing correctly
def rate_limit_api_call(
    url: str, method: str, data: dict[str, Any], params: dict[str, Any], timeout: Optional[int] = None
) -> requests.Response:
    with requests.request(url=url, method=method, data=data, params=params, timeout=timeout) as r_info:
        return r_info


def rate_limit_get_api_call(url: str, params: dict[str, Any], timeout: Optional[int] = None) -> requests.Response:
    return rate_limit_api_call(url=url, method="GET", data={}, params=params, timeout=timeout)


def rate_limit_post_api_call(url: str, data: dict[str, Any], timeout: Optional[int] = None) -> requests.Response:
    return rate_limit_api_call(url=url, method="POST", data=data, params={}, timeout=timeout)


def safe_get_api_call(
    url: str, params: dict[str, Any], max_tries: int = 3, timeout: Optional[int] = None
) -> Optional[str]:
    tries = 0
    while True:
        try:
            r_info = rate_limit_get_api_call(url=url, params=params, timeout=timeout)
            r_text = r_info.text
            # validate contents of response
            if r_info.status_code == 200 and len(r_text) > 0:
                return r_text
        except (requests.exceptions.RequestException, TimeoutError):
            pass

        tries += 1
        if tries >= max_tries:
            return None


def safe_post_api_call(
    url: str, data: dict[str, Any], expected_keys: set[str], max_tries: int = 3, timeout: Optional[int] = None
) -> Optional[dict[str, Any]]:
    tries = 0
    while True:
        try:
            r_info = rate_limit_post_api_call(url=url, data=data, timeout=timeout)
            r_json = r_info.json()
            # validate contents of response
            if (
                r_info.status_code == 200
                and len(expected_keys - r_json.keys()) == 0
                and not any([bool(r_json[x]) is False for x in expected_keys])
            ):
                return r_json
        except (requests.exceptions.RequestException, TimeoutError):
            pass

        tries += 1
        if tries >= max_tries:
            return None


def get_google_drive_file_name(drive_id: str) -> Optional[str]:
    """
    Retrieve the name for the Google Drive file identified by `drive_id`.
    """

    if not drive_id:
        return None
    service = find_or_create_google_drive_service()
    response = execute_google_drive_api_call(service.files().get(fileId=drive_id))
    return response["name"] if response is not None else None


# endregion

# region file IO


# TODO: migrate to Pathlib
DEFAULT_WORKING_DIRECTORY: str = (
    # nuitka magic
    os.path.dirname(sys.argv[0])
    if "__compiled__" in globals()
    # if running through a normal python interpreter
    else os.getcwd()
)


def get_image_directory(working_directory: str) -> str:
    return os.path.join(working_directory, "cards")


def create_image_directory_if_not_exists(working_directory: str) -> bool:
    if not os.path.exists(get_image_directory(working_directory=working_directory)):
        os.mkdir(get_image_directory(working_directory=working_directory))
        return True
    return False


def file_exists(file_path: Optional[str]) -> bool:
    return file_path is not None and file_path != "" and os.path.isfile(file_path) and os.path.getsize(file_path) > 0


def remove_directories(directory_list: list[str]) -> None:
    for directory in directory_list:
        try:
            os.rmdir(directory)
        except Exception:  # TODO: investigate which exceptions `os.rmdir` can raise and handle specifically them
            pass


def remove_files(file_list: list[str]) -> None:
    for file in file_list:
        try:
            os.remove(file)
        except Exception:  # TODO: investigate which exceptions `os.remove` can raise and handle specifically them
            pass


# endregion

# region mixed network and file IO


def _write_image_bytes(
    file_bytes: bytes, file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]
) -> None:
    if post_processing_config is not None:
        processed_image = post_process_image(raw_image=file_bytes, config=post_processing_config)
        save_processed_image(
            processed_image, file_path, embedded_file_dpi(post_processing_config.max_dpi)
        )
    else:
        with open(file_path, "wb") as f:
            f.write(file_bytes)


def reprocess_image_file(file_path: str, post_processing_config: ImagePostProcessingConfig) -> bool:
    if not file_exists(file_path):
        return False
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    _write_image_bytes(file_bytes=file_bytes, file_path=file_path, post_processing_config=post_processing_config)
    return True


def ensure_mpc_print_ready(file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]) -> bool:
    if image_meets_mpc_print_requirements(file_path, post_processing_config):
        return True
    fallback = post_processing_config or ImagePostProcessingConfig(
        max_dpi=constants.MIN_PRINT_DPI, downscale_alg=constants.ImageResizeMethods.LANCZOS
    )
    if not reprocess_image_file(file_path, fallback):
        return False
    return image_meets_mpc_print_requirements(file_path, fallback)


def _google_drive_public_urls(drive_id: str) -> list[str]:
    return [
        f"https://drive.google.com/uc?id={drive_id}&export=download",
        f"https://cdn.mpcautofill.com/images/google_drive/full/{drive_id}.jpg?dpi=1500",
        f"https://img.mpcautofill.com/{drive_id}-large-google_drive",
    ]


def download_http_image(
    url: str, file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]
) -> bool:
    logger.debug(f"Downloading image from {url}...")
    try:
        response = requests.get(
            url,
            headers={"User-Agent": constants.SCRYFALL_USER_AGENT, "Accept": "*/*"},
            timeout=60,
        )
    except requests.exceptions.RequestException:
        logger.exception(f"HTTP error while downloading {url}")
        return False

    content_type = (response.headers.get("content-type") or "").lower()
    if response.status_code != 200 or not response.content or "html" in content_type:
        return False

    if post_processing_config is not None:
        logger.debug(f"Post-processing {url}...")
    _write_image_bytes(
        file_bytes=response.content, file_path=file_path, post_processing_config=post_processing_config
    )
    return True


def download_google_drive_file(
    drive_id: str, file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]
) -> bool:
    """
    Download the Google Drive file identified by `drive_id` to the specified `file_path`.
    Returns whether the request was successful or not.
    """

    logger.debug(f"Downloading Google Drive image {drive_id}...")
    try:
        service = find_or_create_google_drive_service()
        request = service.files().get_media(fileId=drive_id)
        file = io.BytesIO()
        downloader = MediaIoBaseDownload(file, request)
        done = False
        while done is False:
            _, done = downloader.next_chunk()
        file_bytes = file.getvalue()
        if post_processing_config is not None:
            logger.debug(f"Post-processing {drive_id}...")
        _write_image_bytes(file_bytes=file_bytes, file_path=file_path, post_processing_config=post_processing_config)
        logger.debug(f"Finished downloading Google Drive image {drive_id}!")
        return True
    except Exception:
        logger.debug(f"Google Drive API download failed for {drive_id}; trying public URLs.")

    for url in _google_drive_public_urls(drive_id):
        if download_http_image(url=url, file_path=file_path, post_processing_config=post_processing_config):
            logger.debug(f"Finished downloading Google Drive image {drive_id} from public URL!")
            return True
    logger.exception(f"Failed to download Google Drive image {drive_id}")
    return False


def materialise_local_image(
    source_path: str, file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]
) -> bool:
    """
    Copy (and optionally post-process) a local image from `source_path` to `file_path`.
    When source and destination are the same path and post-processing is disabled, this is a no-op.
    """

    if not file_exists(source_path):
        return False

    if os.path.abspath(source_path) == os.path.abspath(file_path) and post_processing_config is None:
        return True

    with open(source_path, "rb") as f:
        file_bytes = f.read()
    _write_image_bytes(file_bytes=file_bytes, file_path=file_path, post_processing_config=post_processing_config)
    return True


def download_scryfall_file(
    png_url: str, file_path: str, post_processing_config: Optional[ImagePostProcessingConfig]
) -> bool:
    from src.scryfall import download_png

    try:
        file_bytes = download_png(png_url)
    except Exception:
        logger.exception(f"Failed to download Scryfall image {png_url}")
        return False

    if post_processing_config is not None:
        logger.debug(f"Post-processing Scryfall image {png_url}...")
    _write_image_bytes(file_bytes=file_bytes, file_path=file_path, post_processing_config=post_processing_config)
    return True


# endregion
