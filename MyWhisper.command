#!/bin/zsh
cd -- "$(dirname "$0")"
python3 -m whisperer "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  echo "Whisperer exited with an error."
  read -p "Press Enter to exit... "
fi
exit "$status"
