
VERSION=20220520

import setuptools
try:
    with open("README.md", "r", encoding="utf-8") as fh:
        long_description = fh.read()
except:
    long_description = ''
setuptools.setup(
    name="gitparent",
    version=VERSION,
    author="June Nguyen",
    author_email="june@dreambigsemi.com",
    description="Git Parent multirepo management utility",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/gitparent/gitparent",
    project_urls={
        "Bug Tracker": "https://github.com/gitparent/gitparent/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "."},
    packages=setuptools.find_packages(where="."),
    python_requires=">=3.9",
    install_requires = [
        "PyYAML==5.4.1",
    ],
    entry_points = {
        'console_scripts': [ 'gitp = gitparent.gitp:main' ]
    }
)