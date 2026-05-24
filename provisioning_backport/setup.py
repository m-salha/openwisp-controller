#!/usr/bin/env python
from setuptools import find_packages, setup

from openwisp_provisioning import get_version

setup(
    name="openwisp-provisioning",
    version=get_version(),
    license="GPL3",
    author="Lullex WiFi Provisioning",
    description=(
        "SaaS device adoption/provisioning layer for OpenWISP "
        "(1.2.x-compatible backport)."
    ),
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    platforms=["Platform Independent"],
    keywords=["django", "openwrt", "openwisp", "provisioning", "adoption"],
    packages=find_packages(exclude=["tests*", "docs*"]),
    include_package_data=True,
    zip_safe=False,
    # Deliberately minimal. openwisp-users / openwisp-utils are expected to be
    # already present (1.2.x) and must NOT be upgraded to 1.3 by this package.
    install_requires=[
        "django>=4.2,<5.3",
        "djangorestframework>=3.14,<3.16",
        "swapper>=1.3,<2.0",
    ],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Framework :: Django",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
    ],
)
