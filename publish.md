# 1. Build with uv
uv build --clean

# 2. Install twine if not already installed
uv add --dev twine
# or
pip install twine

# 3. If successful, upload to PyPI
python -m twine upload dist/*

# With token directly
python -m twine upload dist/* --username __token__ --password pypi-your_pypi_token