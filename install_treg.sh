#!/bin/bash
export PATH="/home/jksha/.local/bin:$PATH"
uv tool install --force --python ">=3.12,<3.14" "tools-registry[proxy]"
echo "INSTALL_EXIT=$?"
