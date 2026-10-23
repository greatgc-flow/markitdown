"""Original image extraction. Names and diagnostics never use document text."""

import hashlib
import html
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import quote


def check_media_options(options):
    if options.get("extract_media") and options.get("keep_data_uris"):
        raise ValueError("extract_media and keep_data_uris are mutually exclusive")


def save_media(data, ext, **options):
    check_media_options(options)
    ext = ext.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,12}", ext):
        raise ValueError("Unsafe media extension")
    directory = Path(options["extract_media"]).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(data).hexdigest()[:12] + "." + ext
    prefix = name.split(".", 1)[0]
    # Equal content may have extension aliases (jpg/jpeg). Keep one evidence file.
    existing_files = sorted(directory.glob(prefix + ".*"))
    for existing in existing_files:
        if (
            existing.is_symlink()
            or not existing.is_file()
            or existing.read_bytes() != data
        ):
            raise ValueError("Media hash collision or unsafe destination")
    if existing_files:
        name = existing_files[0].name
    target = directory / name
    if target.is_symlink() or target.resolve().parent != directory.resolve():
        raise ValueError("Unsafe media destination")
    try:
        with target.open("xb") as output:
            output.write(data)
    except FileExistsError:
        if target.read_bytes() != data:
            raise ValueError("Media hash collision")
    base = options.get("media_base_dir") or os.getcwd()
    return quote(os.path.relpath(target, base).replace(os.sep, "/"), safe="/.-_")


def image_html(stream, info, **options):
    ext = info.extension or mimetypes.guess_extension(info.mimetype or "") or ".bin"
    path = save_media(stream.read(), ext, **options)
    return '<img src="' + html.escape(path, quote=True) + '" alt="Image">'


def validate_members(archive):
    seen = set()
    for name in archive.namelist():
        parts = name.replace("\\", "/").split("/")
        if (
            name.startswith(("/", "\\"))
            or ".." in parts
            or ":" in name
            or name.casefold() in seen
        ):
            raise ValueError("Unsafe or colliding package member")
        seen.add(name.casefold())
