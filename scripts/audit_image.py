import argparse
import io
import json
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import PurePosixPath


def first_party(name):
    name = name.lstrip("./")
    return (
        name.startswith("app/src/")
        or name.startswith("app/scripts/")
        or name.startswith(("build/memory/", "build/onboarding/", "build/entrypoint/"))
        or name.startswith(("app/reviewbench", "app/entrypoint"))
        or any(
            marker in name
            for marker in ("/site-packages/memory/", "/site-packages/onboarding/")
        )
    )


def forbidden(name):
    path = PurePosixPath(name)
    if path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
        return False
    return path.suffix in {
        ".py",
        ".pyc",
        ".pyo",
        ".pyx",
        ".pxd",
        ".c",
        ".cpp",
        ".md",
        ".sql",
        ".mako",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".txt",
    }


def audit(image):
    failures = []
    binaries = 0
    with tempfile.TemporaryFile() as archive:
        subprocess.run(["docker", "image", "save", image], stdout=archive, check=True)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as saved:
            manifests = json.load(saved.extractfile("manifest.json"))
            for manifest in manifests:
                for layer_name in manifest["Layers"]:
                    with tarfile.open(
                        fileobj=saved.extractfile(layer_name), mode="r|*"
                    ) as layer:
                        for member in layer:
                            if not member.isfile():
                                continue
                            name = member.name
                            if first_party(name):
                                if forbidden(name):
                                    failures.append(name)
                                elif name.endswith(".so"):
                                    binaries += 1
                            if name.endswith(".whl"):
                                with zipfile.ZipFile(
                                    io.BytesIO(layer.extractfile(member).read())
                                ) as wheel:
                                    for item in wheel.namelist():
                                        if item.startswith(
                                            (
                                                "src/",
                                                "memory/",
                                                "onboarding/",
                                                "reviewbench/",
                                                "entrypoint/",
                                            )
                                        ) and forbidden(item):
                                            failures.append(name + ":" + item)
    if failures:
        raise SystemExit(
            "First-party source in image layers:\n" + "\n".join(sorted(set(failures)))
        )
    if not binaries:
        raise SystemExit("No compiled first-party modules found")
    print(
        f"Image audit passed: {binaries} compiled modules, no first-party source in layers"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    audit(parser.parse_args().image)
