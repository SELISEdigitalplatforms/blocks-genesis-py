# Run Commonds

### Activate the Virtual Environment
- Windows Bash => `source .venv/Scripts/activate`
- linux/mac => `source .venv/bin/activate`

### Check the Virtual Environment is Active
- `which python`

### Upgrade pip
- `python -m pip install --upgrade pip`

### Add .gitignore
- `echo "*" > .venv/.gitignore`

### Install Packages
- `pip install "fastapi"` or `uv pip install "fastapi"`
- `uv pip install -r requirements.txt`

### Deactivate the Virtual Environment
- `deactivate`