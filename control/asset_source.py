"""Where library assets may come from, and the local cache they land in.

A scene description names assets by catalog id; Framo resolves each id to a
signed URL in its own storage before the command is sent. The bridge still
refuses any URL that is not Framo storage (Convex, https only), and refuses a
redirect that leaves it, because the URL arrives over a port any local process
or page could reach -- a bridge that downloads whatever it is told is a
download-anything service.

Pure Python, no ``bpy``, no imports from the add-on: the tests load it by path.
"""

import hashlib
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOST_SUFFIXES = (".convex.cloud", ".convex.site")

# A normalized preview asset is a few MB; this bounds a hostile or broken file.
MAX_ASSET_BYTES = 200 * 1024 * 1024
DOWNLOAD_TIMEOUT_SEC = 30

REFUSED_URL = "refused: asset URLs must be https on Framo storage (*.convex.cloud / *.convex.site)"

_SAFE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}")


def is_allowed_asset_url(url):
    """True only for an https URL on Framo's Convex storage."""
    if not isinstance(url, str):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if parts.port not in (None, 443):
        return False
    return any(host.endswith(suffix) and len(host) > len(suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def cache_dir():
    return os.path.join(tempfile.gettempdir(), "framo_bridge_assets")


def cache_path(asset_id):
    """``<tmp>/framo_bridge_assets/<id>.glb``.

    A catalog id is a Convex id (letters and digits) in practice, but the scene
    format only promises a non-empty string, so an id that is not a plain
    filename is hashed instead of being trusted as a path.
    """
    if _SAFE_ID.fullmatch(asset_id) and asset_id not in (".", ".."):
        name = asset_id
    else:
        name = "id-" + hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:32]
    return os.path.join(cache_dir(), name + ".glb")


class AssetDownloadError(Exception):
    pass


class _AllowlistRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_asset_url(newurl):
            raise AssetDownloadError("the asset URL redirected outside Framo storage")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_asset(asset_id, url, opener=None):
    """Return a local path to the asset's GLB, downloading it once per catalog id.

    Raises AssetDownloadError with a message phrased for the model.
    """
    # Checked before the cache too, so a bad URL is refused the same way
    # whether or not the asset happens to be on disk already.
    if not is_allowed_asset_url(url):
        raise AssetDownloadError(REFUSED_URL)
    path = cache_path(asset_id)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path

    os.makedirs(cache_dir(), exist_ok=True)
    opener = opener or urllib.request.build_opener(_AllowlistRedirects())
    partial = path + ".part"
    try:
        with opener.open(url, timeout=DOWNLOAD_TIMEOUT_SEC) as response:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_ASSET_BYTES:
                raise AssetDownloadError("the asset file is too large")
            total = 0
            with open(partial, "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise AssetDownloadError("the asset file is too large")
                    out.write(chunk)
        with open(partial, "rb") as f:
            if f.read(4) != b"glTF":
                raise AssetDownloadError("the downloaded asset is not a GLB file")
        os.replace(partial, path)
        return path
    except AssetDownloadError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AssetDownloadError(f"could not download the asset: {e}") from e
    finally:
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
