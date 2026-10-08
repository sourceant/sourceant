import argparse
import io
import json
import subprocess
import tarfile
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


def scan_layer(layer, failures):
    binaries = 0
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
            with zipfile.ZipFile(io.BytesIO(layer.extractfile(member).read())) as wheel:
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
    return binaries


def audit(image):
    failures = []
    binaries = 0
    scanned = set()
    manifests = None
    process = subprocess.Popen(
        ["docker", "image", "save", image], stdout=subprocess.PIPE
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|*") as saved:
            for member in saved:
                if not member.isfile():
                    continue
                if member.name == "manifest.json":
                    manifests = json.load(saved.extractfile(member))
                    continue
                if not (
                    member.name.endswith("/layer.tar")
                    or member.name.startswith("blobs/sha256/")
                ):
                    continue
                with io.BufferedReader(saved.extractfile(member)) as content:
                    header = content.peek(512)[:512]
                    if not (
                        header[257:262] == b"ustar"
                        or header.startswith(b"\x1f\x8b")
                        or header == bytes(512)
                    ):
                        continue
                    with tarfile.open(fileobj=content, mode="r|*") as layer:
                        binaries += scan_layer(layer, failures)
                    scanned.add(member.name)
        process.stdout.close()
        returncode = process.wait()
        if returncode:
            raise subprocess.CalledProcessError(returncode, process.args)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()
    if not manifests:
        raise SystemExit("Image archive has no manifest")
    expected = {name for manifest in manifests for name in manifest["Layers"]}
    if not expected or not expected.issubset(scanned):
        raise SystemExit("Image archive contains unaudited layers")
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
