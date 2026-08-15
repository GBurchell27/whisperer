#!/bin/zsh
set -u
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
REPO_DIR="$(cd "$APP_DIR/.." && pwd)"
cd "$REPO_DIR"
python3 -m whisperer "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  echo "Whisperer exited with an error."
  read -p "Press Enter to exit... "
fi
exit "$status"
