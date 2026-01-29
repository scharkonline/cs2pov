#!/usr/bin/env python3
"""Setup script for cs2pov."""

from pathlib import Path
from setuptools import setup, find_packages

# Read version from package
version = {}
exec(Path("cs2pov/__init__.py").read_text(), version)

# Read requirements
requirements = Path("requirements.txt").read_text().strip().split("\n")

setup(
    name="cs2pov",
    version=version["__version__"],
    description="Record player POV from CS2 demo files",
    author="",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "cs2pov=cs2pov.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Games/Entertainment :: First Person Shooters",
        "Topic :: Multimedia :: Video :: Capture",
    ],
)
