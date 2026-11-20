"""Load knowledge-base documents: Markdown with YAML front matter (see lab/knowledge/README.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DOC_TYPES = ("runbook", "postmortem")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_SECTION = re.compile(r"^## +(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    heading: str
    text: str


@dataclass(frozen=True)
class Document:
    id: str
    doc_type: str
    title: str
    body: str
    meta: dict = field(default_factory=dict)
    path: str = ""

    @property
    def preamble(self) -> str:
        """Text before the first `##` section (H1 title, status line)."""
        m = _SECTION.search(self.body)
        return (self.body[: m.start()] if m else self.body).strip()

    @property
    def sections(self) -> list[Section]:
        matches = list(_SECTION.finditer(self.body))
        out = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(self.body)
            out.append(Section(m.group(1).strip(), self.body[m.end() : end].strip()))
        return out


def parse(text: str, path: str = "") -> Document:
    m = _FRONT_MATTER.match(text)
    if not m:
        raise ValueError(f"{path or 'document'}: missing YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise TypeError(f"{path or 'document'}: front matter is not a mapping")
    return Document(
        id=str(meta.get("id", "")),
        doc_type=str(meta.get("doc_type", "")),
        title=str(meta.get("title", "")),
        body=m.group(2),
        meta=meta,
        path=path,
    )


def load_dir(root: Path) -> list[Document]:
    """All documents under `<root>/*/*.md`, sorted by path."""
    return [
        parse(p.read_text(), str(p.relative_to(root)))
        for p in sorted(root.glob("*/*.md"))
    ]
