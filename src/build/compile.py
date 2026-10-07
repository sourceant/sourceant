from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import base64
import re
import shutil
import tempfile
import zlib
from pathlib import Path


def model_compatibility(text):
    tree = ast.parse(text)
    model_names = {"BaseModel", "SQLModel"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"pydantic", "sqlmodel"}:
            model_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in {"BaseModel", "SQLModel"}
            )
    changed = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not any(
            isinstance(base, ast.Name) and base.id in model_names for base in node.bases
        ):
            continue
        declared = any(
            (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "model_config"
                    for target in statement.targets
                )
            )
            or (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "model_config"
            )
            for statement in node.body
        )
        if not declared:
            node.body.insert(0, ast.parse("model_config = {}").body[0])
        node.body.extend(
            ast.parse(
                'model_config["ignored_types"] = tuple(model_config.get("ignored_types", ())) + _compiled_function_types'
            ).body
        )
        relationships = [
            statement
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "Relationship"
        ]
        for relationship in relationships:
            node.body.append(
                ast.Assign(
                    targets=[
                        ast.Subscript(
                            value=ast.Name(id="__annotations__", ctx=ast.Load()),
                            slice=ast.Constant(value=relationship.target.id),
                            ctx=ast.Store(),
                        )
                    ],
                    value=relationship.annotation,
                )
            )
        changed = True
    if not changed:
        return text
    position = 0
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            position = index + 1
    tree.body.insert(
        position,
        ast.parse(
            "from src.utils.native_types import FUNCTION_TYPES as _compiled_function_types"
        ).body[0],
    )
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def compile_package(source: Path, output: Path, package: str, jobs: int = 2):
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension
    from setuptools.command.build_ext import build_ext

    source = source.resolve()
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Compilation output must be empty")
    assets = {}
    revisions = []
    modules = []
    with tempfile.TemporaryDirectory(prefix="sourceant-compile-") as temporary:
        stage = Path(temporary)
        for original in sorted(source.rglob("*")):
            relative = original.relative_to(source)
            if not original.is_file() or any(
                part.startswith(".") or part in {"tests", "__pycache__"}
                for part in relative.parts
            ):
                continue
            if original.suffix != ".py":
                if original.suffix in {
                    ".md",
                    ".json",
                    ".yaml",
                    ".yml",
                    ".sql",
                    ".mako",
                    ".ini",
                    ".txt",
                }:
                    assets[relative.as_posix()] = original.read_bytes().hex()
                continue
            text = model_compatibility(original.read_text(encoding="utf-8"))
            module_path = relative.with_suffix("")
            if not module_path.name.isidentifier():
                module_path = module_path.with_name(
                    "revision_" + re.sub(r"\W", "_", module_path.name)
                )
            is_package = module_path.name == "__init__"
            parts = module_path.parts
            name = ".".join((package, *parts))
            if "migrations" in relative.parts and not is_package:
                tree = ast.parse(text)
                if any(
                    (
                        isinstance(node, ast.Assign)
                        and any(
                            isinstance(target, ast.Name) and target.id == "revision"
                            for target in node.targets
                        )
                    )
                    or (
                        isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.target.id == "revision"
                    )
                    for node in tree.body
                ):
                    revisions.append(name)
                if relative.name == "env.py":
                    future = [
                        line
                        for line in text.splitlines()
                        if line.startswith("from __future__ import")
                    ]
                    body = [
                        line
                        for line in text.splitlines()
                        if not line.startswith("from __future__ import")
                    ]
                    text = (
                        "\n".join(future)
                        + "\n\ndef run_environment():\n"
                        + "\n".join("    " + line for line in body)
                        + "\n"
                    )
            destination = stage / package / module_path.with_suffix(".py")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
            modules.append((name, destination))
        packed = zlib.compress(json.dumps(assets, sort_keys=True).encode(), level=9)
        resources = stage / package / "_compiled_resources.py"
        resources.parent.mkdir(parents=True, exist_ok=True)
        resources.write_text(
            "import json\nimport zlib\n"
            + f"_PAYLOAD = {packed!r}\n"
            + "def resources():\n    return {name: bytes.fromhex(value) for name, value in json.loads(zlib.decompress(_PAYLOAD)).items()}\n"
            + f"MIGRATIONS = {tuple(revisions)!r}\n",
            encoding="utf-8",
        )
        modules.append((package + "._compiled_resources", resources))
        extensions = [
            Extension(
                name,
                [str(path)],
                extra_compile_args=["-Os", "-g0", f"-ffile-prefix-map={stage}=."],
            )
            for name, path in modules
        ]
        distribution = Distribution(
            {
                "ext_modules": cythonize(
                    extensions,
                    nthreads=jobs,
                    build_dir=str(stage / "c"),
                    compiler_directives={
                        "language_level": 3,
                        "binding": True,
                        "annotation_typing": False,
                        "infer_types": False,
                        "embedsignature": True,
                    },
                ),
            }
        )
        command = build_ext(distribution)
        command.build_lib = str(output)
        command.build_temp = str(stage / "objects")
        command.parallel = jobs
        command.ensure_finalized()
        command.run()
        for binary in output.rglob("*.so"):
            import subprocess

            subprocess.run(["strip", "--strip-unneeded", str(binary)], check=True)
        audit(output)
        return tuple(name for name, _ in modules)


def audit(root: Path):
    forbidden = {
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
        ".json",
        ".yaml",
        ".yml",
    }
    invalid = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in forbidden
        and not path.name.upper().startswith(
            ("LICENSE", "LICENCE", "COPYING", "NOTICE")
        )
    ]
    if invalid:
        raise ValueError("Source found in compiled package: " + ", ".join(invalid))


def copy_metadata(installed: Path, output: Path):
    for metadata in installed.glob("*.dist-info"):
        target = output / metadata.name
        target.mkdir(parents=True, exist_ok=True)
        for name in ("METADATA", "entry_points.txt", "top_level.txt"):
            if (metadata / name).is_file():
                shutil.copyfile(metadata / name, target / name)
        from packaging.tags import sys_tags

        tag = next(sys_tags())
        (target / "WHEEL").write_text(
            "Wheel-Version: 1.0\nRoot-Is-Purelib: false\n" + f"Tag: {tag}\n",
            encoding="utf-8",
        )
        if (metadata / "licenses").is_dir():
            shutil.copytree(metadata / "licenses", target / "licenses")
        with (target / "RECORD").open("w", newline="") as stream:
            writer = csv.writer(stream)
            for path in sorted(output.rglob("*")):
                if not path.is_file() or path == target / "RECORD":
                    continue
                content = path.read_bytes()
                digest = (
                    base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                    .rstrip(b"=")
                    .decode()
                )
                writer.writerow(
                    [
                        path.relative_to(output).as_posix(),
                        "sha256=" + digest,
                        len(content),
                    ]
                )
            writer.writerow(
                [(target / "RECORD").relative_to(output).as_posix(), "", ""]
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()
    if not all(part.isidentifier() for part in args.package.split(".")):
        parser.error("Package must be a Python module name")
    compile_package(args.source, args.output, args.package, args.jobs)
    if args.metadata is not None:
        copy_metadata(args.metadata, args.output)


if __name__ == "__main__":
    main()
