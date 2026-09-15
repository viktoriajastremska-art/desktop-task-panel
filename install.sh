#!/usr/bin/env bash
# Install the Task Panel into ~/.local/share/task-panel, write a starter config
# and add an autostart entry so the widget appears at every login.
set -euo pipefail

APP_DIR="${HOME}/.local/share/task-panel"
CONFIG_DIR="${HOME}/.config/task-panel"
AUTOSTART_DIR="${HOME}/.config/autostart"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing Task Panel"
mkdir -p "${APP_DIR}" "${CONFIG_DIR}" "${AUTOSTART_DIR}"
cp "${HERE}/taskpanel.py" "${HERE}/backend.py" "${APP_DIR}/"

if [ ! -f "${CONFIG_DIR}/config.json" ]; then
    cp "${HERE}/config.example.json" "${CONFIG_DIR}/config.json"
    echo "    wrote ${CONFIG_DIR}/config.json"
fi

sed "s#%h#${HOME}#g" "${HERE}/autostart/desktop-task-panel.desktop" \
    > "${AUTOSTART_DIR}/desktop-task-panel.desktop"

echo
echo "Done."
echo "  1. Edit ${CONFIG_DIR}/config.json and fill in your Jira"
echo "     base_url / token / project / assignee, and an LLM endpoint."
echo "  2. Start it now with:"
echo "     python3 ${APP_DIR}/taskpanel.py &"
echo "  3. It will start automatically at your next login."
echo "  4. On Wayland, make it stay above other windows with the"
echo "     compositor: Alt+F3 on the card -> More Actions -> Keep Above."
