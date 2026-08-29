"""One diff, as the piece of it belonging to each file."""

from src.core.change_context.git import split

DIFF = """diff --git a/app/charge.py b/app/charge.py
index 111..222 100644
--- a/app/charge.py
+++ b/app/charge.py
@@ -1,2 +1,2 @@
-old
+new
diff --git a/app/gone.py b/app/gone.py
deleted file mode 100644
index 333..000
--- a/app/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-everything
diff --git a/a/with space.py b/with space.py
new file mode 100644
--- /dev/null
+++ b/with space.py
@@ -0,0 +1 @@
+added
"""


class TestSplittingADiff:
    def test_each_file_gets_its_own_piece(self):
        pieces = split(DIFF)

        assert "app/charge.py" in pieces
        assert "+new" in pieces["app/charge.py"]
        assert "+new" not in pieces.get("app/gone.py", "")

    def test_a_deletion_is_keyed_by_what_was_there(self):
        pieces = split(DIFF)

        assert "app/gone.py" in pieces
        assert "-everything" in pieces["app/gone.py"]

    def test_a_path_with_a_space_survives(self):
        pieces = split(DIFF)

        assert "with space.py" in pieces

    def test_nothing_splits_into_nothing(self):
        assert split("") == {}
