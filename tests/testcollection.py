"""Verify collection installation, namespaced execution, and plugin docs."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CollectionTests(unittest.TestCase):
    def test_installed_collection(self):
        with tempfile.TemporaryDirectory(prefix="escapace-utilities-") as directory:
            collections = Path(directory) / "collections"
            environment = dict(os.environ)
            for key in ("ANSIBLE_COLLECTIONS_PATHS", "ANSIBLE_FILTER_PLUGINS"):
                environment.pop(key, None)
            environment.update(
                ANSIBLE_COLLECTIONS_PATH=str(collections),
                ANSIBLE_HOME=str(Path(directory) / ".ansible"),
                ANSIBLE_NOCOLOR="1",
            )

            def run(tool, *arguments):
                command = [str(Path(sys.executable).parent / tool), *arguments]
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertNotIn("[WARNING]", output)
                self.assertNotIn("[DEPRECATION WARNING]", output)
                return result.stdout

            run("ansible-galaxy", "collection", "install", str(ROOT), "--no-deps")
            installed = collections / "ansible_collections/escapace/utilities"
            manifest = json.loads((installed / "MANIFEST.json").read_text())
            self.assertEqual(manifest["collection_info"]["namespace"], "escapace")
            self.assertEqual(manifest["collection_info"]["name"], "utilities")
            for excluded in ("tests", ".github", ".venv", "uv.lock", "pyproject.toml"):
                self.assertFalse((installed / excluded).exists(), excluded)

            for name in ("github_metadata_retry", "github_archive_retry"):
                fqcn = "escapace.utilities." + name
                docs = json.loads(run("ansible-doc", "-t", "filter", "--json", fqcn))
                self.assertEqual(docs[fqcn]["doc"]["name"], name)
                self.assertEqual(docs[fqcn]["doc"]["options"]["policy"]["type"], "dict")
                self.assertTrue(docs[fqcn]["doc"]["filename"].endswith(".yaml"))

            arguments = (
                "-i",
                "localhost,",
                "-e",
                "ansible_python_interpreter=" + sys.executable,
                str(ROOT / "tests/integration/filters.yaml"),
            )
            run("ansible-playbook", *arguments)
            check_output = run("ansible-playbook", *arguments, "--check")
            self.assertIn("changed=0", check_output)


if __name__ == "__main__":
    unittest.main()
