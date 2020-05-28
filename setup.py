"""setup.py file."""

from setuptools import setup, find_packages

with open("requirements.txt", "r") as fs:
    reqs = [r for r in fs.read().splitlines()
            if (len(r) > 0 and not r.startswith("#"))]

__author__ = 'Mohamed Javeed <javeedf.dev@gmail.com>'

setup(
    name="napalm-dellos10",
    version="0.1.0",
    packages=find_packages(),
    author="Senthil Kumar Ganesan, Mohamed Javeed",
    author_email="skg.dev.net@gmail.com, javeedf.dev@gmail.com",
    description="NAPALM driver for Dell EMC Networking OS10 Operating System.",
    classifiers=[
        'Topic :: Utilities',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS',
    ],
    url="https://github.com/napalm-automation-community/napalm-dellos10",
    include_package_data=True,
    install_requires=reqs,
)
