## Publishing to PyPI

1. Update pyproject.toml with your details:
authors = [
    {name = "Your Name", email = "you@example.com"},
]

2. Create accounts:
  - Register at https://pypi.org/account/register/
  - Enable 2FA on your account!
  - Create API token at https://pypi.org/manage/account/#api-tokens

3. Upload:
  - Install twine if needed: `uv pip install twine`
  - Upload to TestPyPI first (optional but recommended): `twine upload --repository testpypi dist/*`
  - Upload to PyPI: `twine upload dist/*`

4. Install and test:
`pip install vibecheck`
`vibecheck --help`

Publishing to GitHub Releases (Alternative)
1. Create a git tag: git tag v0.1.0 && git push origin v0.1.0
2. Go to GitHub Releases → Create new release
3. Attach the wheel/sdist files from dist/

## Quick Publish Commands

# Full workflow
```
uv pip install build twine

python -m build

twine upload dist/*
```