"""Static naming rules shared by the analyzer and its consumers."""


def is_test_path(path: str) -> bool:
    """pytest's default ``python_files = test_*.py *_test.py`` (static classification only)."""
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")
