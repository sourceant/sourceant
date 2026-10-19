import pytest

from src.core.review.calls import calls, definitions, enclosing

# One caller and one callee per language. The parser is ours, so these are
# inputs to our own code, not stand-ins for anything external.
SOURCES = {
    "a.py": "def callee():\n    return 1\n\ndef caller():\n    x = obj.callee(1)\n    return x\n",
    "a.go": "package m\nfunc callee() int { return 1 }\nfunc caller() int { return pkg.callee() }\n",
    "A.java": "class A {\n int callee(){return 1;}\n int caller(){ int x = callee(); return x; }\n}\n",
    "A.cs": "class A {\n int callee(){return 1;}\n int caller(){ return callee(); }\n}\n",
    "a.rb": "def callee\n 1\nend\ndef caller\n  callee(1)\nend\n",
    "a.rs": "fn callee() -> i32 {1}\nfn caller() -> i32 { m::callee() }\n",
    "a.sh": "callee() { echo; }\ncaller() { callee arg; }\n",
    "a.ex": "defmodule A do\n def callee, do: 1\n def caller, do: callee()\nend\n",
    "a.r": "callee <- function() 1\ncaller <- function() callee()\n",
    "a.ml": "let callee () = 1\nlet caller () = callee ()\n",
    "a.jl": "function callee() 1 end\nfunction caller() callee() end\n",
    "a.php": "<?php\nfunction callee(){return 1;}\nfunction caller(){ return callee(); }\n",
    "a.kt": "fun callee(): Int = 1\nfun caller(): Int = callee()\n",
    "a.swift": "func callee() -> Int { 1 }\nfunc caller() -> Int { return callee() }\n",
    "a.lua": "local function callee() return 1 end\nlocal function caller() return callee() end\n",
    "a.dart": "int callee() => 1;\nint caller() => callee();\n",
    "a.scala": "object A {\n def callee(): Int = 1\n def caller(): Int = callee() }\n",
    "a.sol": "contract A {\n function callee() public {}\n function caller() public { callee(); } }\n",
    "a.c": "int callee(void){return 1;}\nint caller(void){return callee();}\n",
    "a.cpp": "int callee(){return 1;}\nint caller(){return callee();}\n",
    "a.ts": "export function callee(){return 1}\nexport function caller(){const v = callee(); return v}\n",
    "a.js": "function callee(){return 1}\nfunction caller(){return callee()}\n",
    "a.tsx": "export function callee(){return 1}\nexport function caller(){return callee()}\n",
    "a.vue": '<template><div/></template>\n<script lang="ts">\nfunction callee(){return 1}\nfunction caller(){return callee()}\n</script>\n',
    "a.svelte": "<script>\nfunction callee(){return 1}\nfunction caller(){return callee()}\n</script>\n",
}


@pytest.mark.parametrize("path", sorted(SOURCES))
def test_every_language_attributes_the_call_to_its_caller(path):
    source = SOURCES[path]
    found = definitions(path, source)
    assert {"callee", "caller"} <= {one.name for one in found}
    sites = [one for one in calls(path, source) if one.name == "callee"]
    assert sites
    assert {enclosing(found, one.line).name for one in sites} == {"caller"}


def test_script_blocks_keep_their_line_numbers():
    source = SOURCES["a.vue"]
    caller = next(one for one in definitions("a.vue", source) if one.name == "caller")
    assert source.splitlines()[caller.start - 1].startswith("function caller")
