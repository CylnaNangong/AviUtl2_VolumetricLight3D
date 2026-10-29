"""Paths for the sandbox and the repository under test.

The sandbox itself can live in a different checkout from the code it tests.
``VL3DSB_REPO`` selects that checkout for the script, language packs and native
bridge; generated files always stay in this sandbox's ignored ``out/`` folder.
Set the environment variable before importing other ``vl3dsb`` modules.
"""
from __future__ import annotations

import os
from pathlib import Path

SANDBOX_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(os.environ.get("VL3DSB_REPO", SANDBOX_ROOT.parent)).expanduser().resolve()
