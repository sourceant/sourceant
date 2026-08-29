"""Finding the imports a language's own reader does not report.

A repository in one of those languages draws as one island per file however
good the resolver is, because the connections were never read.
"""

from src.core.code_index.imports import read


class TestGrammarsTheReaderSkips:
    def test_a_namespace_import_is_the_name_it_imports(self):
        source = (
            "<?php\nnamespace App\\Http;\n"
            "use App\\Models\\User;\n"
            "use Illuminate\\Http\\Request as Req;\n"
            "class C {}\n"
        )

        assert read("php", source) == ["App\\Models\\User", "Illuminate\\Http\\Request"]

    def test_a_required_file_is_an_import(self):
        assert read("ruby", "require 'json'\nrequire_relative 'charge'\n") == [
            "json",
            "charge",
        ]

    def test_a_using_directive_is_an_import(self):
        assert read("csharp", "using System;\nusing App.Models;\n") == [
            "System",
            "App.Models",
        ]

    def test_something_that_only_looks_like_a_require_is_left_alone(self):
        """Matching on the grammar rather than the text is what keeps this out."""
        assert read("ruby", "def required_thing\n  'require me'\nend\n") == []


class TestFilesThatAreMostlyNotCode:
    """A single file component is a template with a module inside it."""

    def test_the_script_block_is_read_as_the_module_it_is(self):
        source = (
            "<script setup>\n"
            "import { ref } from 'vue'\n"
            "import Card from './Card.vue'\n"
            "</script>\n"
            "<template><div /></template>\n"
        )

        assert read("vue", source) == [
            "import { ref } from 'vue'",
            "import Card from './Card.vue'",
        ]

    def test_a_component_with_no_script_imports_nothing(self):
        assert read("vue", "<template><div>Hello</div></template>\n") == []


class TestQuiet:
    def test_a_language_nobody_wrote_a_query_for_reports_nothing(self):
        assert read("cobol", "IDENTIFICATION DIVISION.\n") == []

    def test_something_that_will_not_parse_is_not_an_error(self):
        assert read("php", "<?php use use use") == []
