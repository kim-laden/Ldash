#!/bin/bash
# Removes the app and the login item. Notes stay in Application Support.
PLIST="$HOME/Library/LaunchAgents/org.laden.OpsDash.plist"
launchctl bootout "gui/$(id -u)/org.laden.OpsDash" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "/Applications/Laden Ops.app"
echo "Laden Ops was removed. Notes are still in ~/Library/Application Support/laden-ops."
