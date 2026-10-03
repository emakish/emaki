#!/usr/bin/env python3
"""Read a bounded static wpaperd wallpaper into a private texture.

Sharp wallpaper crops have a private content/stat-keyed PNG cache. Only that synthetic
cache URL crosses the pipe, never the source path. Desktop captures never use this helper.
No shell or connection to wpaperd/Wayland.
The QML consumer imposes a process deadline in addition to these input bounds.
"""
import base64
import io
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import tomllib
import warnings

MAX_CONFIG = 65536
MAX_IMAGE = 32 * 1024 * 1024
# Published bytes are account-controlled input to the greeter. Restrict plugin
# selection itself, before Image.open can dispatch to EPS/Ghostscript or another
# external decoder. The ordinary C8/user reader keeps its existing format policy.
PUBLISHED_FORMATS = ('PNG', 'JPEG', 'WEBP')
# 6016×6016 (36 Mpx, the MacBook Neo wallpaper Emaki's glass was tuned on) must pass.
MAX_PIXELS = 48_000_000
MAX_OUTPUT = 12_000_000  # Includes 3840x2400 while bounding each output's allocation.
STALE_TEMP_SECONDS = 60  # Longer than WallpaperSource's eight-second helper deadline.


def open_no_symlinks(path, *, directory=False, root=None):
    """Walk from / with directory descriptors, never through a user-controlled link.

    A published image must be lexically inside root before opening any component.
    O_NOFOLLOW on each open closes the lstat/open race, including parent directories.
    The default desktop/lock reader deliberately does not use this stricter policy.
    """
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_path')
    if root is not None:
        root = Path(root)
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('unsafe_root')
        path.relative_to(root)
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    # Published users/ is root-owned 0711: accounts may traverse it but must not
    # list it. O_RDONLY would require read permission on every parent. O_PATH
    # holds a traversal-only descriptor, while O_DIRECTORY still rejects links.
    descriptor = os.open('/', os.O_PATH | flags | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            final = index == len(path.parts) - 2
            access = os.O_RDONLY if final else os.O_PATH
            child = os.open(part, access | flags | (os.O_DIRECTORY if directory or not final else 0),
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def bounded_descriptor(descriptor, maximum):
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError('input_limit')
    chunks, remaining = [], maximum + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if not remaining:
        raise ValueError('input_limit')
    return b''.join(chunks), info


def bounded_no_symlinks(path, maximum, *, root=None):
    descriptor = open_no_symlinks(path, root=root)
    try:
        return bounded_descriptor(descriptor, maximum)
    finally:
        os.close(descriptor)


def bounded_read(path, maximum):
    # Reject FIFOs/devices before open; O_NONBLOCK also closes the FIFO race.
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('not_regular')
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('input_limit')
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('input_limit')
        return data


def prune_sharp_temps(directory):
    """Reap abandoned atomic writes without racing workers still writing their crop."""
    cutoff = time.time() - STALE_TEMP_SECONDS
    for candidate in directory.glob('.sharp-*'):
        try:
            info = candidate.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_mtime < cutoff):
                candidate.unlink(missing_ok=True)
        except OSError:
            pass


def sharp_cache(path, pw, ph, mode, offset, source_info=None):
    """Cache wallpaper only; a new stat/geometry/config identity gets a new private name."""
    try:
        info = source_info if source_info is not None else path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE:
            return None
        identity = ('sharp-v1', str(path), info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns, pw, ph, mode, offset)
        key = hashlib.sha256(repr(identity).encode()).hexdigest()
        directory = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'emaki/wallpaper-sharp'
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory_info = directory.lstat()
        if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.getuid():
            return None
        directory.chmod(0o700)
        # A killed helper cannot run save_sharp's finally block. Prune on reads too,
        # so a warm cache does not retain abandoned writes until the next wallpaper.
        prune_sharp_temps(directory)
        return directory / (key + '.png')
    except OSError:
        return None


def cached_sharp(path, size):
    if path is None:
        return False
    from PIL import Image
    try:
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_mode & 0o077 == 0 and 8 < info.st_size <= MAX_IMAGE):
            return False
        # The cache is disposable. Decode before returning its URL so a truncated
        # or corrupt PNG is regenerated in this request, even when its key is unchanged.
        with Image.open(io.BytesIO(bounded_read(path, MAX_IMAGE))) as cached:
            if cached.format != 'PNG' or cached.size != size or cached.width * cached.height > MAX_OUTPUT:
                return False
            cached.load()
        return True
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return False


def save_sharp(path, image):
    """Atomic write; cache failures preserve the previous in-memory fallback."""
    if path is None:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.sharp-', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            image.save(stream, format='PNG', compress_level=1)
        temporary.replace(path)
        # Bound retained crops after geometry/wallpaper changes. Open Qt images remain valid.
        entries = sorted(path.parent.glob('*.png'), key=lambda item: item.stat().st_mtime_ns, reverse=True)
        for old in entries[8:]:
            old.unlink(missing_ok=True)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def texture(width, height, scale, output, variant='glass'):
    from PIL import Image, ImageEnhance, ImageFilter
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    if not (math.isfinite(scale) and .5 <= scale <= 4 and 1 <= width <= 8192 and 1 <= height <= 8192):
        return {'state': 'invalid_geometry'}
    pw, ph = round(width * scale), round(height * scale)
    if pw * ph > MAX_OUTPUT:
        return {'state': 'output_limit'}
    published_root = os.environ.get('EMAKI_GREETER_WALLPAPER_ROOT', '')
    config_root = Path(published_root or os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    try:
        if published_root:
            config_bytes, _ = bounded_no_symlinks(config_root / 'wpaperd/config.toml', MAX_CONFIG,
                                                root=config_root)
        else:
            config_bytes = bounded_read(config_root / 'wpaperd/config.toml', MAX_CONFIG)
        config = tomllib.loads(config_bytes.decode())
    except FileNotFoundError:
        return {'state': 'config_missing'}
    except (OSError, ValueError, UnicodeError):
        return {'state': 'config_invalid'}
    # Exact connector names supported. Description/glob matching is deliberately
    # not guessed: it could select a different picture than wpaperd.
    if published_root:
        # The publishing account owns these files and may edit them directly.
        # Recheck its selector contract here; unrelated exact outputs are safe
        # to ignore, while globs/descriptions must never be guessed.
        if len(config) > 32 or any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key) for key in config):
            return {'state': 'output_selection_unsupported'}
        if any(not isinstance(options, dict) for options in config.values()):
            return {'state': 'config_invalid'}
    elif any(key not in ('default', 'any', output) for key in config):
        return {'state': 'output_selection_unsupported'}
    options = config.get('default', {})
    specific = config.get(output, config.get('any', {}))
    if not isinstance(options, dict) or not isinstance(specific, dict):
        return {'state': 'config_invalid'}
    options = dict(options, **specific)
    mode = options.get('mode', 'center')
    if mode not in ('center', 'stretch', 'fit'):
        return {'state': 'mode_unsupported'}
    offset = options.get('offset', .5)
    if isinstance(offset, bool) or not isinstance(offset, (float, int)) or not math.isfinite(offset) or not 0 <= offset <= 1:
        return {'state': 'config_invalid'}
    path = options.get('path')
    if not isinstance(path, str) or not path:
        return {'state': 'config_invalid'}
    path = Path(path) if published_root else Path(path).expanduser()
    if not path.is_absolute():
        return {'state': 'relative_path_unsupported'}
    if not published_root and path.is_dir():
        return {'state': 'slideshow_unsupported'}
    source_bytes, source_info = None, None
    if published_root:
        try:
            source_bytes, source_info = bounded_no_symlinks(path, MAX_IMAGE, root=config_root)
        except FileNotFoundError:
            return {'state': 'image_missing'}
        except (OSError, ValueError):
            return {'state': 'image_invalid'}
    cache = sharp_cache(path, pw, ph, mode, offset, source_info) if variant == 'sharp' else None
    if cached_sharp(cache, (pw, ph)):
        return {'state': 'ready', 'mode': mode, 'texture': cache.as_uri()}
    try:
        data = io.BytesIO(source_bytes if source_bytes is not None else bounded_read(path, MAX_IMAGE))
        with Image.open(data, formats=PUBLISHED_FORMATS if published_root else None) as original:
            if original.width * original.height > MAX_PIXELS or getattr(original, 'n_frames', 1) != 1:
                return {'state': 'image_limit'}
            # Keep encoded orientation, matching wpaperd's image decoding.
            image = original.convert('RGB')
        ratio, image_ratio = pw / ph, image.width / image.height
        sx, sy = (1., 1.)
        if mode == 'center':
            sx, sy = min(ratio / image_ratio, 1), min(image_ratio / ratio, 1)
        elif mode == 'fit':
            sx, sy = max(ratio / image_ratio, 1), max(image_ratio / ratio, 1)
        # wpaperd 1.3.0 renderer.rs: (uv - offset) * textureScale + offset.
        # Top-origin image/Qt coordinates; physical output resolution is explicit.
        box = (offset * (1 - sx) * image.width, offset * (1 - sy) * image.height,
               (sx + offset * (1 - sx)) * image.width, (sy + offset * (1 - sy)) * image.height)
        mapped = image.transform((pw, ph), Image.Transform.EXTENT, box, Image.Resampling.BILINEAR)
        if variant == 'sharp':
            # The dock's liquid glass refracts the untouched wallpaper at device pixels and
            # blurs it itself; filtering here would erase thin lines before the lens.
            if save_sharp(cache, mapped):
                return {'state': 'ready', 'mode': mode, 'texture': cache.as_uri()}
            data = io.BytesIO()
            mapped.save(data, format='PNG')
            return {'state': 'ready', 'mode': mode, 'texture': 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()}
        mapped = ImageEnhance.Color(mapped.filter(ImageFilter.GaussianBlur(7 * scale))).enhance(1.55)
        mapped = ImageEnhance.Brightness(mapped).enhance(1.03)
        # Texture in logical coordinates: lens distance, blur and curvature stay
        # constant across output scales. Oversampling was applied before filtering.
        mapped = mapped.resize((width, height), Image.Resampling.BILINEAR)
        data = io.BytesIO()
        mapped.save(data, format='PNG')
        return {'state': 'ready', 'mode': mode, 'texture': 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()}
    except FileNotFoundError:
        return {'state': 'image_missing'}
    except Exception:
        # Never expose exception strings: decoder exceptions often contain paths.
        return {'state': 'image_invalid'}


def main():
    try:
        result = texture(int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4],
                         sys.argv[5] if len(sys.argv) > 5 else 'glass')
    except Exception:
        result = {'state': 'unavailable'}
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
