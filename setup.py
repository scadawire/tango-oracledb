import setuptools

setuptools.setup(
    name="OracleDb",
    version="0.1.0",
    author="Sebastian Jennen",
    author_email="sj@imagearts.de",
    description="OracleDb device driver",
    packages=setuptools.find_packages(),
    python_requires='>=3.6',
    install_requires=['requests'],
    scripts=['OracleDb.py']
)