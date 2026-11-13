"""Locate the Alquimia checkout that supplies the benchmark inputs."""

from pathlib import Path

# Benchmark inputs, relative to the repository; also the discovery marker.
BATCH_CHEM_DIR = "benchmarks/batch_chem"


def find_repository(*starts: Path) -> Path:
    """Return the nearest ancestor of a start path that holds the benchmark inputs.

    The package may sit at any depth in a checkout, or in a virtualenv when it is
    installed non-editable, so the root is found by its contents rather than by
    counting parent directories.

    Args:
        starts: Paths searched in order, each from itself upward (e.g. the module
            file, then the working directory).

    Returns:
        The first directory found containing benchmarks/batch_chem.

    Raises:
        FileNotFoundError: No start lies inside a checkout.
    """
    for start in starts:
        path = Path(start).resolve()
        for directory in (path, *path.parents):
            if (directory / BATCH_CHEM_DIR).is_dir():
                return directory
    raise FileNotFoundError(
        f"No Alquimia checkout containing {BATCH_CHEM_DIR} above "
        f"{', '.join(str(start) for start in starts)}; pass --repository"
    )
