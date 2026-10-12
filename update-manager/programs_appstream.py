# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read standard AppStream metadata without downloads or cache writes.

Descriptions are plain text, converted by libappstream. Sizes remain unknown:
release artifact sizes do not identify the package/bundle being installed.
Cached icons resolve only within the catalog's own icon tree; image bytes are not read.
"""
import gzip
import io
import os
import re
import stat
from pathlib import Path
from xml.parsers import expat
from xml.etree import ElementTree
from urllib.parse import urlsplit

from programs_catalog import clean_display, clean_text


# Limits apply before the native parser sees bytes and again to its output.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 32
# Per component (XML elements / YAML events), independent of catalog size.
MAX_NODES = 500_000
# The installed Flathub catalog has 611,883 elements; allow more than five times that.
MAX_FILE_NODES = 3_100_000
MAX_COMPONENTS = 50_000
MAX_SCALAR_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 2048
MAX_DESCRIPTION_BYTES = 16 * 1024
MAX_LIST_ITEMS = 64
_UNSAFE_URL = re.compile(r"[\s\x00-\x1f\x7f\\]")


def _input_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("[PLACEHOLDER] AppStream input exceeds the file limit.")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("[PLACEHOLDER] AppStream input exceeds the file limit.")
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("[PLACEHOLDER] AppStream expanded input exceeds the file limit.")
    return data


def _validate_xml(data):
    parser = expat.ParserCreate()
    parser.buffer_text = True
    depth = nodes = component_nodes = component_depth = components = scalar_bytes = 0
    root_tag = None

    def forbidden(*args):
        raise ValueError("[PLACEHOLDER] AppStream DTDs and entities are not supported.")

    def start(name, attrs):
        nonlocal depth, nodes, component_nodes, component_depth, components, scalar_bytes, root_tag
        depth += 1
        nodes += 1
        if root_tag is None:
            root_tag = name
        if name == "component" and not component_depth:
            component_depth = depth
            component_nodes = 0
            components += 1
        if component_depth:
            component_nodes += 1
        scalar_bytes = 0
        if (depth > MAX_DEPTH or nodes > MAX_FILE_NODES or component_nodes > MAX_NODES
                or components > MAX_COMPONENTS):
            raise ValueError("[PLACEHOLDER] AppStream XML exceeds structural limits.")
        for value in attrs.values():
            if len(value.encode("utf-8")) > MAX_SCALAR_BYTES:
                raise ValueError("[PLACEHOLDER] AppStream XML exceeds the scalar limit.")

    def end(name):
        nonlocal depth, component_depth, scalar_bytes
        if depth == component_depth:
            component_depth = 0
        depth -= 1
        scalar_bytes = 0

    def text(value):
        nonlocal scalar_bytes
        scalar_bytes += len(value.encode("utf-8"))
        if scalar_bytes > MAX_SCALAR_BYTES:
            raise ValueError("[PLACEHOLDER] AppStream XML exceeds the scalar limit.")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.Parse(data, True)
    return root_tag


def _validate_yaml(data):
    # YAML is optional: without a safe event parser, reject it before libappstream.
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("[PLACEHOLDER] AppStream YAML parser unavailable.") from exc
    depth = nodes = component_nodes = documents = 0
    for event in yaml.parse(data, Loader=yaml.SafeLoader):
        nodes += 1
        component_nodes += 1
        if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None):
            raise ValueError("[PLACEHOLDER] AppStream YAML aliases are not supported.")
        if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
            depth += 1
        elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
            depth -= 1
        elif isinstance(event, yaml.events.DocumentStartEvent):
            documents += 1
            component_nodes = 1
        if (depth > MAX_DEPTH or nodes > MAX_FILE_NODES or component_nodes > MAX_NODES
                or documents > MAX_COMPONENTS + 1):
            raise ValueError("[PLACEHOLDER] AppStream YAML exceeds structural limits.")
        if isinstance(event, yaml.events.ScalarEvent) and len(event.value.encode("utf-8")) > MAX_SCALAR_BYTES:
            raise ValueError("[PLACEHOLDER] AppStream YAML exceeds the scalar limit.")


def _bounded(value, limit=MAX_TEXT_BYTES):
    if isinstance(value, str):
        return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    if isinstance(value, list):
        return [_bounded(item) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        return {key: _bounded(item, MAX_DESCRIPTION_BYTES if key == "description" else MAX_TEXT_BYTES)
                for key, item in value.items()}
    return value


def _library():
    import gi
    gi.require_version("AppStream", "1.0")
    from gi.repository import AppStream, Gio, GLib
    return AppStream, Gio, GLib


def _web_url(value):
    """Only absolute HTTP(S) media references may reach a client."""
    if (not value or value != clean_text(value) or len(value.encode("utf-8")) > MAX_TEXT_BYTES
            or _UNSAFE_URL.search(value)):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and parsed.port != 0)
    except ValueError:
        return False


def _cached_icon(path, origin, name, width, height, scale=1):
    """Resolve native and Flatpak icon layouts without trusting catalog paths.

    Native catalogs keep icons beside xml/xmls/yaml, scoped by origin. Flatpak
    keeps icons beside appstream.xml in its already remote-scoped checkout.
    A filename or symlink must never turn metadata into an arbitrary local read.
    """
    if (not name or name in ('.', '..') or any(char in name for char in '/\\:')
            or name != clean_text(name)):
        return None
    metadata = Path(path).resolve()
    if metadata.parent.name in ('xml', 'xmls', 'yaml'):
        if not re.fullmatch(r'[a-zA-Z0-9_+.-]+', origin or '') or origin in ('.', '..'):
            return None
        boundary = metadata.parent.parent
        directory = boundary / 'icons' / origin
    elif metadata.name in ('appstream.xml', 'appstream.xml.gz'):
        boundary = metadata.parent
        directory = boundary / 'icons'
    else:
        return None
    sizes = [(width, height)] if width and height else [(64, 64), (128, 128)]
    for icon_width, icon_height in sizes:
        size = f'{icon_width}x{icon_height}' + (f'@{scale}' if scale > 1 else '')
        try:
            candidate = (directory / size / name).resolve()
            if candidate.is_relative_to(directory) and candidate.is_file():
                return str(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _record(component, library, origin, path):
    if component.get_kind() not in (
            library.ComponentKind.DESKTOP_APP, library.ComponentKind.CONSOLE_APP):
        return None
    identity_values = [component.get_id(), origin or component.get_origin(), str(path), *component.get_pkgnames(),
                       *(bundle.get_id() for bundle in component.get_bundles())]
    # Truncating an identity could resolve to a different application.
    if not component.get_id() or any(value and (len(value.encode("utf-8")) > MAX_TEXT_BYTES
                                               or value != clean_text(value))
                                     for value in identity_values):
        return None
    icons = []
    for icon in component.get_icons():
        if len(icons) == MAX_LIST_ITEMS:
            break
        kind = icon.get_kind()
        if kind == library.IconKind.REMOTE:
            value = icon.get_url()
            if not _web_url(value):
                continue
        elif kind in (library.IconKind.STOCK, library.IconKind.CACHED):
            value = icon.get_name()
            if not value or ":" in value or "/" in value or "\\" in value:
                continue
            if kind == library.IconKind.CACHED:
                value = _cached_icon(path, origin or component.get_origin(), value,
                                     icon.get_width(), icon.get_height(), icon.get_scale())
                if not value:
                    continue
        elif kind == library.IconKind.LOCAL:
            value = icon.get_filename()
            if origin or not value or ":" in value or not Path(value).is_absolute():
                continue
            # Only metadata-local files are usable; resolve symlinks as well as '..'.
            try:
                resolved = Path(value).resolve()
                if not resolved.is_relative_to(Path(path).resolve().parent):
                    continue
                value = str(resolved)
            except (OSError, RuntimeError, ValueError):
                continue
        else:
            continue
        if value != clean_text(value) or len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            continue
        icons.append({"kind": kind.value_nick, "value": value,
                      "width": icon.get_width(), "height": icon.get_height()})
    screenshots = []
    seen = set()
    for screenshot in component.get_screenshots_all():
        if len(screenshots) == MAX_LIST_ITEMS:
            break
        images = [image for image in screenshot.get_images() if _web_url(image.get_url())]
        if not images:
            continue
        # One screenshot can carry several thumbnails of the same scene. Prefer
        # its source image, then the largest available resolution, once per scene.
        image = max(images, key=lambda candidate: (
            candidate.get_kind() == library.ImageKind.SOURCE,
            candidate.get_width() * candidate.get_height(),
            candidate.get_width(), candidate.get_height()))
        url = image.get_url()
        if url not in seen:
            seen.add(url)
            screenshots.append(url)
    description = component.get_description() or ""
    if description:
        try:
            description = library.markup_convert(description, library.MarkupKind.TEXT) or ""
        except Exception:
            # Never fall back to passing unconverted markup to a client.
            description = ""
    return clean_display(_bounded({
        "id": component.get_id(),
        "name": component.get_name() or "",
        "summary": component.get_summary() or "",
        "description": description,
        "icons": icons,
        "categories": list(component.get_categories()),
        "screenshots": screenshots,
        "licence": component.get_project_license() or "",
        "packages": list(component.get_pkgnames()),
        "bundles": [{"kind": bundle.get_kind().value_nick, "id": bundle.get_id()}
                    for bundle in component.get_bundles() if bundle.get_id()],
        "keywords": list(component.get_keywords()),
        "downloadSize": None,
        "installedSize": None,
        "origin": origin or component.get_origin() or "",
        "sourcePath": str(path),
    }))


def _xml_input(data, library, locale, root_tag):
    """Keep exactly one language in installed metainfo descriptions."""
    if root_tag != "component":
        return data, library.FormatStyle.CATALOG
    root = ElementTree.fromstring(data)
    language_attr = "{http://www.w3.org/XML/1998/namespace}lang"
    for description in root.findall("description"):
        languages = {node.get(language_attr, "C") for node in description.iter()}
        selected = next((name for name in (locale, locale.split("_")[0], "en", "C")
                         if name in languages), "C")

        def prune(node, inherited="C"):
            language = node.get(language_attr, inherited)
            children = list(node)
            for child in children:
                if not prune(child, language):
                    node.remove(child)
            node.attrib.pop(language_attr, None)
            return bool(len(node)) if children else language == selected

        prune(description)
    return ElementTree.tostring(root), library.FormatStyle.METAINFO


def read_file(path, origin=""):
    """Read XML, YAML, or gzip catalog/metainfo; raise on unreadable input.

    Explicit origin is the caller's repository provenance, overriding the
    file's origin. No package identity or display metadata is synthesized.
    """
    library, _, glib = _library()
    path = Path(path)
    data = _input_bytes(path)
    is_xml = data.lstrip().startswith(b"<")
    root_tag = _validate_xml(data) if is_xml else _validate_yaml(data)
    metadata = library.Metadata()
    # GLib supplies the session language preferences, including regional fallback.
    locale = glib.get_language_names()[0].split(".")[0]
    metadata.set_locale(locale)
    if is_xml:
        data, style = _xml_input(data, library, locale, root_tag)
    else:
        style = library.FormatStyle.CATALOG
    metadata.set_format_style(style)
    kind = library.FormatKind.XML if is_xml else library.FormatKind.YAML
    source = glib.Bytes.new(data)
    del data
    if not metadata.parse_bytes(source, kind):
        raise ValueError("[PLACEHOLDER] AppStream metadata could not be read.")
    # The native parser owns its metadata now; release the expanded catalog before
    # building records so both copies do not remain live for the whole conversion.
    del source
    records = []
    parsed = metadata.get_components().as_array()
    if len(parsed) > MAX_COMPONENTS:
        raise ValueError("[PLACEHOLDER] AppStream catalog exceeds the component limit.")
    for component in parsed:
        record = _record(component, library, origin, path)
        if record is not None:
            records.append(record)
    return records


def _standard_paths():
    _, _, glib = _library()
    data_dirs = [Path(directory) for directory in glib.get_system_data_dirs()]
    # Flatpak exports can occur in XDG_DATA_DIRS; read only native OS metadata.
    data_dirs = [directory for directory in data_dirs if "flatpak" not in directory.parts]
    prefixes = data_dirs + [Path("/var/cache"), Path("/var/lib")]
    found = set()
    for prefix in prefixes:
        for catalog in ("swcatalog", "app-info"):
            for subdir in ("xml", "xmls", "yaml"):
                directory = prefix / catalog / subdir
                for pattern in ("*.xml", "*.xml.gz", "*.yml", "*.yml.gz", "*.yaml", "*.yaml.gz"):
                    found.update(directory.glob(pattern))
    for directory in data_dirs:
        for subdir in ("metainfo", "appdata"):
            found.update((directory / subdir).glob("*.xml"))
    # Resolve legacy aliases (app-info commonly links to swcatalog) once.
    unique = {}
    for path in sorted(found):
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


def components(paths=None):
    """Return (records, warnings), keeping valid files when another fails.

    Explicit paths replace discovery, including an empty list. Discovery is
    read-only and never includes Flatpak's remote catalogs automatically.
    """
    records, warnings = [], []
    try:
        files = _standard_paths() if paths is None else paths
        for path in files:
            try:
                records.extend(read_file(path))
            except Exception as exc:
                # GI file errors have their own GLib exception type.
                if isinstance(exc, OSError) or getattr(exc, "domain", "") == "g-io-error-quark":
                    reason = "unreadable"
                elif isinstance(exc, ImportError):
                    reason = "parser unavailable"
                else:
                    reason = "invalid"
                warnings.append(f"[PLACEHOLDER] AppStream metadata {reason}: {path}: {exc}")
    except Exception as exc:
        warnings.append(f"[PLACEHOLDER] AppStream discovery unavailable: {exc}")
    return records, clean_display(warnings)
