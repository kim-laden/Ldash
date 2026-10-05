#!/bin/sh
# Install Ldash 1.0 for the current Linux user.
# The menu entry and the ldash command point at this folder.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required."
  exit 1
fi

if ! python3 -c 'import gi, dbus
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, WebKit' >/dev/null 2>&1; then
  echo "Ldash needs GTK 4, WebKitGTK 6, and the Python D-Bus bindings."
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  case "${ID:-}${ID_LIKE:-}" in
    *arch*|*cachyos*)
      echo "sudo pacman -S --needed gtk4 webkitgtk-6.0 python-gobject python-dbus"
      ;;
    *debian*|*ubuntu*)
      echo "sudo apt install python3-gi python3-dbus gir1.2-gtk-4.0 gir1.2-webkit-6.0"
      ;;
    *fedora*|*rhel*)
      echo "sudo dnf install python3-gobject python3-dbus gtk4 webkitgtk6.0"
      ;;
    *)
      echo "Install GTK 4, WebKitGTK 6.0, python3-gobject, and python3-dbus for your distribution."
      ;;
  esac
  exit 1
fi

PY=$(command -v python3)
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.config/autostart"
printf '%s\n' "#!/bin/sh" "exec $PY \"$ROOT/main.py\" \"\$@\"" > "$HOME/.local/bin/ldash"
chmod 755 "$HOME/.local/bin/ldash"

desktop="[Desktop Entry]
Type=Application
Name=Laden Ops
Comment=Ldash personal dashboard by Laden AS
Exec=$PY $ROOT/main.py
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
StartupWMClass=laden-ops
"
printf '%s\n' "$desktop" > "$HOME/.local/share/applications/laden-ops.desktop"
printf '%s\nX-GNOME-Autostart-enabled=true\n' "$desktop" > "$HOME/.config/autostart/laden-ops.desktop"
mkdir -p "$HOME/.local/share/metainfo"
cp "$ROOT/linux/org.laden.OpsDash.metainfo.xml" "$HOME/.local/share/metainfo/org.laden.OpsDash.metainfo.xml"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "Ldash is installed for this user."
echo "Command: ldash"
echo "Menu: Laden Ops"
echo "A second start shows the window that is already running."
echo "Close hides it. Ctrl+\` shows it again. Quit from Control exits."
echo "Notes stay in ~/.config/laden-ops"
