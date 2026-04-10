from __future__ import annotations

import json
import os

from app.main import merged_alias_config
from app.projects import discover_git_projects


def main() -> None:
    projects_root = os.getenv("PROJECTS_ROOT", "/opt/projects")
    discovered = discover_git_projects(projects_root)
    aliases = merged_alias_config()

    print("== Discovered Git Repositories ==")
    if not discovered:
        print("(none)")
    for project in discovered:
        print(
            f"- project_id={project.project_id} "
            f"repo_path={project.repo_path} "
            f"branch={project.branch}"
        )

    print("\n== Final Alias Map (merged) ==")
    print(json.dumps(aliases, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
