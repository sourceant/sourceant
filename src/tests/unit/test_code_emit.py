"""Reading an import out of the text it was written as."""

from src.core.code_index.emit import import_source


class TestQuoting:
    """A Go import arrives quoted, and stored that way it matches nothing."""

    def test_double_quotes_come_off(self):
        assert import_source('"example.com/who/internal/core"') == (
            "example.com/who/internal/core"
        )

    def test_single_quotes_come_off(self):
        assert import_source("'./charge'") == "./charge"

    def test_backticks_come_off(self):
        assert import_source("`./charge`") == "./charge"

    def test_a_stray_quote_comes_off_too(self):
        """A half-quoted name is still a name, and the quote is not part of it."""
        assert import_source('"unbalanced') == "unbalanced"


class TestRefusals:
    """A grouped import arrives as the whole block, which is not a module."""

    def test_a_block_is_not_one_name(self):
        block = 'import (\n\t"context"\n\t"fmt"\n)'

        assert import_source(block) is None

    def test_nothing_is_nothing(self):
        assert import_source("") is None
        assert import_source("   ") is None
        assert import_source(None) is None

    def test_quotes_around_nothing_are_nothing(self):
        assert import_source('""') is None


class TestStatements:
    """The parser answers with the whole statement, not the module in it."""

    def test_a_python_from_import_names_its_module(self):
        assert import_source("from app.charge import charge") == "app.charge"

    def test_a_plain_import_names_its_module(self):
        assert import_source("import decimal") == "decimal"

    def test_an_aliased_import_names_what_it_aliased(self):
        assert import_source("import numpy as np") == "numpy"

    def test_a_javascript_import_names_the_path_it_quoted(self):
        assert import_source("import { ref } from 'vue'") == "vue"

    def test_a_require_names_the_path_it_quoted(self):
        assert import_source("require('./charge')") == "./charge"

    def test_an_include_names_what_is_in_the_brackets(self):
        assert import_source("#include <stdio.h>") == "stdio.h"


class TestTidying:
    def test_surrounding_space_goes(self):
        assert import_source("  ./charge  ") == "./charge"

    def test_a_trailing_semicolon_goes(self):
        assert import_source("'./charge';") == "./charge"
