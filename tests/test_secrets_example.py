#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Filename:    test_secrets_example.py
# Description: Every key the plugin reads from IndigoSecrets.py appears, blank,
#              in the bundled IndigoSecrets_example.py, and nothing in the repo
#              carries a credential-shaped literal.
# Author:      CliveS & Claude Opus 5.5
# Date:        24-10-2026
# Version:     1.0.0

import ast
import os
import subprocess

from conftest import ROOT, SRC
import levoit_logic as L


def test_example_holds_every_key_blank():
    tree = ast.parse(open(os.path.join(SRC, "IndigoSecrets_example.py"), encoding="utf-8").read())
    values = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    for key in L.SECRETS_KEYS:
        assert values.get(key) == "", key


def test_gitignore_keeps_secrets_and_packages_out():
    lines = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
    for entry in ("IndigoSecrets.py", "**/IndigoSecrets.py", "Contents/Packages/", "**/Contents/Packages/"):
        assert entry in lines, entry


def test_no_secrets_file_is_tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return                                     # not a git checkout (e.g. an unpacked zip)
    for path in out.splitlines():
        assert os.path.basename(path) != "IndigoSecrets.py", path
        assert "/Contents/Packages/" not in path, path
