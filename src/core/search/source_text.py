from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

SMALL_SOURCE_BYTES = 256_000


class SourceTextSearch:
    def __init__(self, paths, read_content):
        self.paths = tuple(sorted(paths))
        self.read_content = read_content
        self._directory = None
        self._names = {}
        self._lines = {}
        self._lock = Lock()
        self._prepared = False
        self._folded = {}

    def matching_lines(self, terms):
        executable = shutil.which("rg")
        if executable is None:
            raise RuntimeError("ripgrep is required for source text search")
        self._prepare()
        if self._directory is None:
            wanted = tuple(term.casefold() for term in terms)
            return [
                (path, number, self._lines[path][number - 1])
                for path, lines in self._folded.items()
                for number, line in enumerate(lines, 1)
                if any(term in line for term in wanted)
            ]
        return self._ripgrep(executable, terms)

    def _prepare(self):
        with self._lock:
            if self._prepared:
                return
            contents = {}
            size = 0
            for path in self.paths:
                content = self.read_content(path)
                if content is not None:
                    contents[path] = content
                    size += len(content.encode("utf-8"))
            lines = {path: content.splitlines() for path, content in contents.items()}
            if size <= SMALL_SOURCE_BYTES:
                self._folded = {
                    path: tuple(line.casefold() for line in source)
                    for path, source in lines.items()
                }
            else:
                directory = TemporaryDirectory(prefix="sourceant-search-")
                names = {}
                try:
                    for index, (path, content) in enumerate(contents.items()):
                        name = str(index)
                        Path(directory.name, name).write_text(
                            "\n".join(content.casefold().splitlines()) + "\n",
                            encoding="utf-8",
                        )
                        names[name] = path
                except BaseException:
                    directory.cleanup()
                    raise
                self._directory, self._names = directory, names
            self._lines = lines
            self._prepared = True

    def _ripgrep(self, executable, terms):
        arguments = [
            executable,
            "--no-config",
            "--with-filename",
            "--null",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--no-ignore",
            "--hidden",
            "--text",
            "--fixed-strings",
        ]
        for term in terms:
            arguments.extend(("-e", term.casefold()))
        ran = subprocess.run(
            [*arguments, "--", self._directory.name],
            capture_output=True,
            timeout=20,
            check=False,
        )
        if ran.returncode not in (0, 1):
            raise ValueError("Source text search failed")
        found = []
        for entry in ran.stdout.splitlines():
            filename, content = entry.split(b"\0", 1)
            number, _ = content.split(b":", 1)
            path = self._names[Path(filename.decode("utf-8")).name]
            number = int(number)
            found.append((path, number, self._lines[path][number - 1]))
        return sorted(found, key=lambda match: (match[0], match[1]))

    def source_lines(self, path):
        return self._lines[path]
