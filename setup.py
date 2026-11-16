"""The packages: everything under src/, and kicad2ems.examples from examples/
(pyproject.toml has the rest)."""
from setuptools import find_packages, setup

setup(packages=find_packages('src') + ['kicad2ems.examples'],
      package_dir={'': 'src', 'kicad2ems.examples': 'examples'})
