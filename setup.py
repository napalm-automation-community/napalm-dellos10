"""setup.py file."""

import uuid

from setuptools import setup, find_packages
from pip.req import parse_requirements

__author__ = 'Mohamed Javeed <javeedf.dev@gmail.com>'

install_reqs = parse_requirements('requirements.txt', session=uuid.uuid1())
reqs = [str(ir.req) for ir in install_reqs]

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
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS',
    ],
    url="https://github.com/skg-net/napalm-dellos10",
    include_package_data=True,
    install_requires=reqs,
)