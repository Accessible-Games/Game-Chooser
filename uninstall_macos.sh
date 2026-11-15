#!/bin/bash
# Removes Game Chooser from this Mac: the app, its settings, games list, install records, catalog cache, log,
# preferences file and saved window state.
#
# Usage: bash uninstall_macos.sh [--dry-run] [--yes] [--delete-games]
#   --dry-run       list what would be removed and remove nothing
#   --yes           don't ask before removing
#   --delete-games  also remove the library folders (the games installed from the catalog)
#
# Without --delete-games the library folders are kept and listed at the end. The list of library folders comes
# from config.json, so --delete-games must be given on the first run.

set -u

if [ "$(uname)" != "Darwin" ]; then
	echo "uninstall_macos.sh runs only on macOS."
	exit 1
fi

APP_NAME="Game Chooser"
APP_BUNDLE="$APP_NAME.app"
BUNDLE_ID="Game Chooser"
PROCESS_NAME="game-chooser"
CONFIG_DIR="$HOME/Library/Application Support/GameChooser"
PREFS_FILE="$HOME/Library/Preferences/$BUNDLE_ID.plist"

dry_run=0
assume_yes=0
delete_games=0
for arg in "$@"; do
	case "$arg" in
		--dry-run) dry_run=1 ;;
		--yes) assume_yes=1 ;;
		--delete-games) delete_games=1 ;;
		*) echo "Unknown option: $arg"; exit 1 ;;
	esac
done

if pgrep -x "$PROCESS_NAME" > /dev/null; then
	echo "$APP_NAME is running. Quit it, then run this script again."
	exit 1
fi

library_folders=()
if [ -f "$CONFIG_DIR/config.json" ]; then
	i=0
	while path=$(plutil -extract "libraries.$i.path" raw -o - "$CONFIG_DIR/config.json" 2>/dev/null); do
		[ -d "$path" ] && library_folders+=("$path")
		i=$((i + 1))
	done
fi

targets=()
if [ -e "./$APP_BUNDLE" ]; then
	targets+=("$PWD/$APP_BUNDLE")
elif [ -e "$HOME/Applications/$APP_BUNDLE" ]; then
	targets+=("$HOME/Applications/$APP_BUNDLE")
fi
for candidate in \
	"$CONFIG_DIR" \
	"$PREFS_FILE" \
	"$HOME/Library/Saved Application State/$BUNDLE_ID.savedState" \
	"$HOME/Library/Caches/$BUNDLE_ID" \
	"$HOME/Library/HTTPStorages/$BUNDLE_ID" \
	"$HOME/Library/WebKit/$BUNDLE_ID"
do
	[ -e "$candidate" ] && targets+=("$candidate")
done
if [ "$delete_games" -eq 1 ] && [ "${#library_folders[@]}" -gt 0 ]; then
	targets+=("${library_folders[@]}")
fi

if [ "${#targets[@]}" -eq 0 ]; then
	echo "Nothing of $APP_NAME was found."
	exit 0
fi

echo "These will be removed:"
printf '  %s\n' "${targets[@]}"

if [ "$dry_run" -eq 1 ]; then
	echo "Dry run: nothing was removed."
	exit 0
fi

if [ "$assume_yes" -eq 0 ]; then
	read -r -p "Remove them? [y/N] " answer
	case "$answer" in
		[yY]|[yY][eE][sS]) ;;
		*) echo "Nothing was removed."; exit 0 ;;
	esac
fi

# The preferences daemon keeps its own copy of the plist, so it must forget the domain or it writes the file back.
if [ -e "$PREFS_FILE" ]; then
	defaults delete "$BUNDLE_ID" 2>/dev/null
fi

failed=0
for target in "${targets[@]}"; do
	if rm -rf "$target" 2>/dev/null || [ ! -e "$target" ]; then
		echo "Removed $target"
	else
		echo "Couldn't remove $target"
		failed=1
	fi
done

if [ "$delete_games" -eq 0 ] && [ "${#library_folders[@]}" -gt 0 ]; then
	echo ""
	echo "These library folders were kept (they hold your games):"
	printf '  %s\n' "${library_folders[@]}"
	echo "Run again with --delete-games to remove them too."
fi

exit "$failed"
