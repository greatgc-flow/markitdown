"""Release identity and user-facing installation guidance for the fork."""

import re
from pathlib import Path


def test_install_hints_use_fork_distribution():
    core = Path(__file__).resolve().parents[1]
    source = core / "src" / "markitdown"
    paths = [core.parents[1] / "README.md", core / "README.md"]
    paths.extend(source.rglob("*.py"))
    paths.append(source / "GUIDE.md")
    upstream_install = re.compile(r"pip install\s+['\"]?markitdown\[")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not upstream_install.search(text), path
    guide = (source / "GUIDE.md").read_text(encoding="utf-8")
    assert 'pip install "markitdown-gc[all]"' in guide
