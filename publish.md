# 1. Build with uv
uv build

# 2. Install twine if not already installed
uv add --dev twine
# or
pip install twine

# 3. Upload to TestPyPI first
python -m twine upload --repository testpypi dist/*

# 4. If successful, upload to PyPI
python -m twine upload dist/*

# With token directly
python -m twine upload --repository testpypi dist/* --username __token__ --password pypi-your_testpypi_token

python -m twine upload dist/* --username __token__ --password pypi-your_pypi_token