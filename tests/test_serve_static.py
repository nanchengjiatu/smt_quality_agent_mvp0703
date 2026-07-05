import unittest

from serve import Handler


class StaticWhitelistTest(unittest.TestCase):
    """The public port must never serve .git, raw exports, or credentials."""

    def test_frontend_and_output_allowed(self) -> None:
        for path in ("/web/index.html", "/web/", "/web", "/output/drilldown.json", "/output"):
            self.assertTrue(Handler._static_allowed(path), path)

    def test_project_root_files_blocked(self) -> None:
        for path in (
            "/.git/HEAD",
            "/Over Volume0608.xlsx",
            "/config/datasource.json",
            "/serve.py",
            "/webby/x",  # prefix must not match /web/ loosely
        ):
            self.assertFalse(Handler._static_allowed(path), path)

    def test_traversal_cannot_escape_whitelist(self) -> None:
        for path in (
            "/web/../.git/HEAD",
            "/web/%2e%2e/config/datasource.json",
            "/output/../serve.py",
        ):
            self.assertFalse(Handler._static_allowed(path), path)


if __name__ == "__main__":
    unittest.main()
