"""Find the Alquimia checkout by its contents, from any depth."""

from pathlib import Path

import pytest

from mcp_alquimia.utils.paths import BATCH_CHEM_DIR, find_repository


def test_repository_is_found_from_a_nested_file(tmp_path):
    """A module at any depth below the checkout finds it."""
    (tmp_path / BATCH_CHEM_DIR).mkdir(parents=True)
    module = tmp_path / "mcp_alquimia/src/mcp_alquimia/server/app.py"
    module.parent.mkdir(parents=True)
    module.touch()
    assert find_repository(module) == tmp_path.resolve()


def test_later_starts_are_tried_in_order(tmp_path):
    """An installed package outside any checkout falls back to the working directory."""
    checkout = tmp_path / "checkout"
    (checkout / BATCH_CHEM_DIR).mkdir(parents=True)
    site_packages = tmp_path / "venv/lib/site-packages/mcp_alquimia/server/app.py"
    site_packages.parent.mkdir(parents=True)
    site_packages.touch()
    assert find_repository(site_packages, checkout / "build") == checkout.resolve()


def test_missing_checkout_asks_for_the_option(tmp_path):
    """Outside every checkout the error names --repository."""
    with pytest.raises(FileNotFoundError, match="--repository"):
        find_repository(tmp_path)


def test_this_checkout_is_found(repository_root):
    """The test suite itself runs inside the checkout."""
    assert (repository_root / BATCH_CHEM_DIR).is_dir()
    assert find_repository(Path.cwd()) == repository_root
