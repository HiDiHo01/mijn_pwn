# setup.py

from setuptools import find_packages, setup

setup(
    name="pwn",
    version="0.1.0",
    description="Home Assistant integration for PWN",
    url="https://github.com/HiDiHo01/pwn",
    packages=find_packages(),
    install_requires=[
        "python_pwn",
    ],
)
