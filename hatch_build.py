"""Hatch build hook: compile the editor frontend and embed it in the package.

Runs `npm ci && npm run build`; Vite writes straight into
``src/agent_blueprint/editor/static/``, which pyproject lists under
``tool.hatch.build.artifacts`` so the VCS-ignored output still ships.

Skipped for editable installs (contributors run ``npm run build`` themselves
when they need the UI). When building from an sdist there is no ``frontend/``
directory — the sdist ships the prebuilt assets instead — so the hook is a
no-op there too, and installing from sdist never needs Node.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        root = Path(self.root)
        frontend = root / "frontend"
        static_index = root / "src" / "agent_blueprint" / "editor" / "static" / "index.html"
        if not frontend.is_dir():
            if static_index.is_file():
                return  # building from an sdist: prebuilt assets are bundled
            raise RuntimeError(
                "Cannot build agent-blueprint: frontend/ is missing and no prebuilt "
                "editor assets were found at src/agent_blueprint/editor/static/"
            )
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "npm is required to build the editor frontend (Node.js >= 20); "
                "install Node or build from a published sdist/wheel instead"
            )
        subprocess.run([npm, "ci"], cwd=frontend, check=True)
        subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
