"""Packaging entry point.

Works with both modern pip (PEP 660 editable wheels) and the legacy pip 21.2
bundled with Python 3.9. Legacy pip performs an editable install by invoking
``setup.py develop`` inside a PEP 517 build-isolation environment; setuptools'
``develop`` implementation then re-spawns ``<interpreter> -m pip install -e .``
while inheriting the isolation environment's ``PYTHONPATH``, which can shadow
the target interpreter's own pip module. We restore the target environment's
site-packages at the front of ``PYTHONPATH`` so that recursive call resolves
pip correctly.
"""
import os
import site
import sys


def _restore_target_pythonpath() -> None:
    try:
        candidate_paths = list(site.getsitepackages())
    except AttributeError:
        candidate_paths = []
    candidate_paths.append(site.getusersitepackages())
    existing = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    merged: list = []
    for path in candidate_paths + existing:
        if path and os.path.isdir(path) and path not in merged:
            merged.append(path)
    if merged:
        os.environ["PYTHONPATH"] = os.pathsep.join(merged)


_restore_target_pythonpath()

from setuptools import find_packages, setup  # noqa: E402

setup(
    name="cycleaccord",
    version="0.1.0",
    description="Dependency cycle negotiator: SCC analysis, break-change candidates, versioned approvals",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    package_data={"cycleaccord": ["web/*.html", "web/*.js", "web/*.css"]},
    include_package_data=True,
    extras_require={"test": ["pytest>=7.0"]},
    entry_points={"console_scripts": ["cycleaccord=cycleaccord.__main__:main"]},
)
