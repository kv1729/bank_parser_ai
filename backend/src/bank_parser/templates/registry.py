"""
Append-only, versioned template registry on the filesystem.

Layout:  <root>/<bank_code>/<layout_id>/v<N>.json

- Reviewed templates live in a committed directory (read-only here).
- Templates learned at runtime are written to a local, gitignored directory;
  a human promotes them into the committed directory after review.
- A version file is created with exclusive-create, so an existing version can
  never be overwritten, even by two processes racing (DECISIONS.md D-009).
"""
import json
import os
import re
import threading
from pathlib import Path

from bank_parser.templates.model import Template, TemplateError, template_from_dict, template_to_dict

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERSION_FILE = re.compile(r"^v(\d+)\.json$")


class VersionConflict(Exception):
    """A template with this bank/layout/version already exists."""


class TemplateRegistry:
    def __init__(self, read_dirs=(), write_dir=None):
        self.read_dirs = tuple(Path(p) for p in read_dirs)
        self.write_dir = Path(write_dir) if write_dir else None
        self._lock = threading.Lock()
        self._templates = {}
        self.load_errors = []
        self.reload()

    def _dirs(self):
        dirs = list(self.read_dirs)
        if self.write_dir is not None:
            dirs.append(self.write_dir)
        return dirs

    def reload(self):
        templates, errors = {}, []
        for root in self._dirs():
            if not root.exists():
                continue
            for path in sorted(root.glob("*/*/v*.json")):
                if not _VERSION_FILE.match(path.name):
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        t = template_from_dict(json.load(f))
                    expected = (path.parent.parent.name, path.parent.name, f"v{t.version}.json")
                    if (t.bank_code, t.layout_id, path.name) != expected:
                        raise TemplateError("file location does not match template identity")
                    if t.template_id in templates:
                        raise TemplateError(f"duplicate template {t.template_id}")
                    templates[t.template_id] = t
                except (OSError, ValueError, KeyError, TemplateError) as e:
                    errors.append(f"{path}: {e}")
        with self._lock:
            self._templates = templates
            self.load_errors = errors

    def all(self, include_unapproved=False):
        with self._lock:
            ts = list(self._templates.values())
        if not include_unapproved:
            ts = [t for t in ts if t.status == "approved"]
        return sorted(ts, key=lambda t: (t.bank_code, t.layout_id, -t.version))

    def get(self, template_id):
        with self._lock:
            return self._templates.get(template_id)

    def versions(self, bank_code, layout_id):
        with self._lock:
            return sorted(t.version for t in self._templates.values()
                          if t.bank_code == bank_code and t.layout_id == layout_id)

    def next_version(self, bank_code, layout_id):
        existing = self.versions(bank_code, layout_id)
        return (max(existing) + 1) if existing else 1

    def save(self, template):
        """Write a new version. Never overwrites; raises VersionConflict instead."""
        if self.write_dir is None:
            raise TemplateError("registry has no writable directory")
        for part in (template.bank_code, template.layout_id):
            if not _SAFE.match(part):
                raise TemplateError(f"unsafe bank_code/layout_id {part!r}")
        if template.version in self.versions(template.bank_code, template.layout_id):
            raise VersionConflict(template.template_id)
        folder = self.write_dir / template.bank_code / template.layout_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"v{template.version}.json"
        data = json.dumps(template_to_dict(template), indent=2, ensure_ascii=False) + "\n"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise VersionConflict(template.template_id) from None
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        self.reload()
        return path
