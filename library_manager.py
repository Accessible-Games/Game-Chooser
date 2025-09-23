#!/usr/bin/env python3
"""
Game library management for Game Chooser application
"""

import json
import os
import sys
import platform
import threading
import fnmatch
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from models import Game


class GameLibraryManager:
    """Handles all game library operations and data management"""
    
    # Constants for scanning behavior
    MAX_SCAN_DEPTH = 10
    VALID_GAME_NAMES = ["game", "launch", "play"]
    AUTO_EXCEPTION_KEYWORDS = [
        "setup", "install", "installer", "installshield", "unins", "uninstall",
        "update", "updater", "upgrade", "patch", "patcher", "patchnotes",
        "config", "configure", "configurator", "settings", "option", "options",
        "helper", "tool", "utility", "manager", "launcher", "launchpad",
        "register", "registration", "reg", "checkup", "checker", "checksum",
        "check", "diagnostic", "diagnose", "repair", "cleanup", "clean", "fix",
        "debug", "benchmark", "profile", "test", "tester", "sample", "demo",
        "tutorial", "manual", "readme", "license", "credit", "credits", "docs",
        "documentation", "keygen", "crack", "trainer", "cheat", "cheater", "editor",
        "configtool", "configtools", "unpack", "extract", "unzip", "zip", "rar",
        "lha", "7z", "tar", "gzip", "redis", "redist", "vcredist", "directx",
        "dxsetup", "runtime", "dotnet", "msvc", "prereq", "prerequisite",
        "bootstrap", "maintenance", "service", "agent",
        # System utilities
        "rebase", "scalar", "hostname", "productid", "productidmanager",
        # Compression tools
        "bzip", "bunzip", "bzcat", "lzma", "lzmadec", "lzmainfo", "funzip", "zipinfo",
        "brotli", "bspatch", "antiword",
        # Shell/system tools
        "bash", "sh", "cygwin", "mintty", "dash", "compat", "winpty", "console",
        # Development tools
        "perl", "python", "w9xpopen", "emake", "gettext", "envsubst", "gencat",
        "msgattrib", "msgcat", "msgcmp", "msgcomm", "msgconv", "msgen", "msgexec",
        "msgfilter", "msgfmt", "msggrep", "msginit", "msgmerge", "msgunfmt", "msguniq",
        "ngettext", "xgettext",
        # Security/crypto tools
        "gpg", "pinentry", "kbxutil", "dirmngr", "scdaemon", "gpgconf", "gpgparsemail",
        "gpgscm", "gpgsm", "gpgsplit", "gpgtar", "gpgv", "hmac", "nettle", "pbkdf2",
        "trust", "preset", "passphrase", "pkcs", "sexp",
        # Text processing tools
        "grep", "sed", "awk", "cut", "sort", "cat", "echo", "less", "lessecho", "lesskey",
        "head", "tail", "wc", "tr", "uniq", "comm", "join", "split", "csplit", "fold",
        "fmt", "pr", "nl", "od", "xxd", "hexdump",
        # File utilities
        "find", "locate", "ls", "cp", "mv", "rm", "mkdir", "rmdir", "chmod", "chown",
        "chgrp", "chroot", "touch", "ln", "link", "unlink", "stat", "file", "which",
        "dirname", "basename", "readlink", "realpath", "pathchk", "mkfifo", "mknod",
        "mount", "umount", "sync", "df", "du", "mktemp", "env", "printenv", "id",
        "who", "whoami", "users", "groups", "logname", "pinky", "hostname", "hostid",
        "uname", "arch", "nproc", "uptime", "date", "sleep", "timeout", "nice", "nohup",
        "kill", "killall", "pkill", "pgrep", "ps", "top", "jobs", "bg", "fg", "disown",
        # Network tools
        "ssh", "scp", "sftp", "curl", "wget", "ftp", "telnet", "ping", "netstat",
        "nslookup", "dig", "host", "whois",
        # Archive/compression
        "unrar", "p7zip", "lha", "cabextract", "rpm2cpio", "cpio",
        # System monitoring
        "iostat", "vmstat", "sar", "mpstat", "pidstat", "iotop", "htop",
        # Version control
        "svn", "hg", "mercurial", "bzr", "cvs", "rcs", "sccs",
        # Build tools
        "make", "cmake", "autoconf", "automake", "libtool", "pkg", "pkgconfig",
        # Scripting
        "ruby", "node", "npm", "yarn", "php", "lua", "tcl", "tclsh", "wish",
        # Database
        "mysql", "sqlite", "postgres", "redis", "mongo",
        # Multimedia
        "ffmpeg", "imagemagick", "convert", "identify", "mogrify", "composite",
        # Other common utilities
        "cron", "at", "batch", "mail", "mailx", "sendmail", "rsync", "screen",
        "tmux", "watch", "yes", "true", "false", "tee", "xargs", "parallel"
    ]
    AUTO_EXCEPTION_EXACT_STEMS = {
        "pcssb", "pcssbpc", "pcspc", "pcsend", "pcswsb", "pcspcv", "cwsdpmi",
        "w9xpopen", "ag_say", "zqds", "zquake", "zquake-gl", "zquake-vidnull",
        "bunzip2", "bzcat", "bzip2", "bzip2recover", "envsubst", "gettext",
        "openssl", "pkcs1-conv", "sexp-conv", "hostname", "curl", "perl", "less",
        "odt2txt", "pdftotext", "proxy-lookup", "xmlwf", "xz", "xzcat", "xzdec",
        "unxz", "lzmadec", "lzmainfo", "blocked-file-util", "create-shortcut",
        "github.ui", "gitlab.ui", "git", "git-bash", "git-cmd", "git-gui", "gitk",
        "git-lfs", "git-askpass", "git-askyesno", "git-credential",
        "git-credential-helper-selector", "git-credential-manager",
        "git-credential-manager-core", "git-credential-manager-ui",
        "git-credential-cache", "git-credential-cache--daemon",
        "git-credential-store", "git-credential-wincred", "git-receive-pack",
        "git-upload-pack", "git-upload-archive", "git-daemon", "git-http-backend",
        "git-http-fetch", "git-http-push", "gettext.sh", "tclsh", "tclsh86", "wish",
        "wish86",
        # Common uninstaller names
        "unins000", "unins001", "checkup",
        # System utilities
        "server", "sb", "id", "scalar", "rebase", "hostname", "productidmanager",
        # Shell and system tools
        "bash", "sh", "dash", "mintty", "cygwin-console-helper", "winpty-agent",
        "winpty-debugserver", "winpty", "headless-git", "edit-git-bash",
        # Text processing
        "sed", "awk", "grep", "less", "lessecho", "lesskey", "nano", "rnano",
        "vim", "view", "rview", "rvim", "vimdiff", "ex",
        # File utilities
        "ls", "cp", "mv", "rm", "mkdir", "rmdir", "chmod", "chown", "chgrp",
        "chroot", "touch", "ln", "link", "unlink", "stat", "file", "which",
        "dirname", "basename", "readlink", "realpath", "pathchk", "mkfifo",
        "mknod", "mount", "umount", "sync", "df", "du", "mktemp", "vdir", "dir",
        # Text utilities
        "cat", "head", "tail", "wc", "tr", "uniq", "comm", "join", "split",
        "csplit", "fold", "fmt", "pr", "nl", "od", "xxd", "cut", "sort", "shuf",
        "factor", "seq", "yes", "true", "false", "echo", "printf", "test",
        "expr", "tee", "timeout", "truncate",
        # Environment/process utilities
        "env", "printenv", "id", "who", "whoami", "users", "groups", "logname",
        "pinky", "hostname", "hostid", "uname", "arch", "nproc", "date", "sleep",
        "nice", "nohup", "kill", "ps", "stty", "tty", "strace", "pldd",
        # Archive utilities
        "tar", "unzip", "zip", "funzip", "unzipsfx", "zipinfo", "gzip", "gunzip",
        "zcat", "compress", "uncompress", "lha", "rar", "unrar", "7z", "p7zip",
        # Networking
        "ssh", "ssh-add", "ssh-agent", "ssh-keygen", "ssh-keyscan", "ssh-keysign",
        "ssh-pkcs11-helper", "ssh-sk-helper", "ssh-pageant", "scp", "sftp",
        "sftp-server", "sshd", "curl", "wget", "ftp", "nc", "netcat",
        # Checksums and hashing
        "md5sum", "sha1sum", "sha224sum", "sha256sum", "sha384sum", "sha512sum",
        "cksum", "sum", "b2sum", "base32", "base64", "basenc",
        # Locale and text conversion
        "iconv", "locale", "dos2unix", "unix2dos", "unix2mac", "mac2unix", "u2d", "d2u",
        "recode-sr-latin", "antiword", "odt2txt", "pdftotext",
        # System info and monitoring
        "ldd", "ldh", "cygcheck", "cygpath", "getconf", "getfacl", "setfacl",
        "lsattr", "chattr", "getopt", "getprocaddr32", "getprocaddr64",
        # Crypto and security
        "gpg", "gpg-agent", "gpg-check-pattern", "gpg-connect-agent", "gpg-error",
        "gpg-preset-passphrase", "gpg-protect-tool", "gpg-wks-client", "gpg-wks-server",
        "gpgconf", "gpgparsemail", "gpgscm", "gpgsm", "gpgsplit", "gpgtar", "gpgv",
        "pinentry", "pinentry-w32", "scdaemon", "dirmngr", "dirmngr-client",
        "kbxutil", "watchgnupg", "yat2m", "hmac256", "nettle-hash", "nettle-lfib-stream",
        "nettle-pbkdf2", "p11-kit", "p11-kit-remote", "p11-kit-server", "trust",
        # Development tools
        "make", "cmake", "autoconf", "automake", "libtool", "pkg-config",
        "perl", "perl5.36.0", "python", "python3", "node", "npm", "yarn",
        "ruby", "php", "lua", "tcl", "tclsh", "tclsh86", "wish", "wish86",
        # Git commands (all git subcommands)
        "git-add", "git-am", "git-annotate", "git-apply", "git-archive",
        "git-bisect--helper", "git-blame", "git-branch", "git-bugreport",
        "git-bundle", "git-cat-file", "git-check-attr", "git-check-ignore",
        "git-check-mailmap", "git-check-ref-format", "git-checkout--worker",
        "git-checkout-index", "git-checkout", "git-cherry-pick", "git-cherry",
        "git-clean", "git-clone", "git-column", "git-commit-graph",
        "git-commit-tree", "git-commit", "git-config", "git-count-objects",
        "git-describe", "git-diagnose", "git-diff-files", "git-diff-index",
        "git-diff-tree", "git-diff", "git-difftool", "git-env--helper",
        "git-fast-export", "git-fast-import", "git-fetch-pack", "git-fetch",
        "git-fmt-merge-msg", "git-for-each-ref", "git-for-each-repo",
        "git-format-patch", "git-fsck-objects", "git-fsck", "git-fsmonitor--daemon",
        "git-gc", "git-get-tar-commit-id", "git-grep", "git-hash-object",
        "git-help", "git-hook", "git-imap-send", "git-index-pack", "git-init-db",
        "git-init", "git-interpret-trailers", "git-log", "git-ls-files",
        "git-ls-remote", "git-ls-tree", "git-mailinfo", "git-mailsplit",
        "git-maintenance", "git-merge-base", "git-merge-file", "git-merge-index",
        "git-merge-ours", "git-merge-recursive", "git-merge-subtree",
        "git-merge-tree", "git-merge", "git-mktag", "git-mktree",
        "git-multi-pack-index", "git-mv", "git-name-rev", "git-notes",
        "git-pack-objects", "git-pack-redundant", "git-pack-refs", "git-patch-id",
        "git-prune-packed", "git-prune", "git-pull", "git-push", "git-range-diff",
        "git-read-tree", "git-rebase", "git-reflog", "git-remote-ext",
        "git-remote-fd", "git-remote-ftp", "git-remote-ftps", "git-remote-http",
        "git-remote-https", "git-remote", "git-repack", "git-replace", "git-rerere",
        "git-reset", "git-restore", "git-rev-list", "git-rev-parse", "git-revert",
        "git-rm", "git-send-pack", "git-sh-i18n--envsubst", "git-shortlog",
        "git-show-branch", "git-show-index", "git-show-ref", "git-show",
        "git-sparse-checkout", "git-stage", "git-stash", "git-status",
        "git-stripspace", "git-submodule--helper", "git-switch", "git-symbolic-ref",
        "git-tag", "git-unpack-file", "git-unpack-objects", "git-update-index",
        "git-update-ref", "git-update-server-info", "git-var", "git-verify-commit",
        "git-verify-pack", "git-verify-tag", "git-version", "git-whatchanged",
        "git-worktree", "git-wrapper", "git-write-tree",
        # Text processing tools
        "msgattrib", "msgcat", "msgcmp", "msgcomm", "msgconv", "msgen", "msgexec",
        "msgfilter", "msgfmt", "msggrep", "msginit", "msgmerge", "msgunfmt",
        "msguniq", "ngettext", "xgettext",
        # Terminal/display utilities
        "tic", "toe", "infocmp", "infotocap", "captoinfo", "tabs", "tput", "tset",
        "clear", "reset", "column", "expand", "unexpand", "runcon", "chcon",
        # System utilities
        "passwd", "pwcat", "mkgroup", "mkpasswd", "regtool", "setmetamode",
        "minidumper", "dumpsexp", "profiler", "gkill", "gmondump",
        # Package/module tools
        "pluginviewer", "glib-compile-schemas", "gobject-query", "gsettings",
        "gapplication", "gdbus", "gio-querymodules", "psl",
        # Other system tools
        "tig", "cldr-plurals", "frcode", "locate", "updatedb", "cmp", "diff", "diff3",
        "sdiff", "patch", "ptx", "tsort", "shred", "sync", "dircolors", "install",
        "ln", "mkfifo", "mknod", "pathchk", "rm", "rmdir", "shred", "sync", "touch",
        "unlink", "vdir", "chcon", "chgrp", "chmod", "chown", "chroot", "cp",
        "dd", "df", "dir", "du", "install", "link", "ls", "mkdir", "mv", "readlink",
        "realpath", "stat", "stty", "tac", "tzset", "uname", "uniq", "wc",
        # Additional utilities from the games.json analysis
        "adig", "ahost", "acountry", "arch", "awk", "gawk", "gawk-5.0.0",
        "brotli", "captoinfo", "column", "cmp", "diff", "diff3", "dircolors",
        "edit-git-bash", "edit_test", "edit_test_dll", "factor", "false",
        "find", "fmt", "fold", "frcode", "gapplication", "gencat", "getconf",
        "getfacl", "getopt", "getprocaddr32", "getprocaddr64", "gettext",
        "gio-querymodules", "glib-compile-schemas", "gmondump", "gobject-query",
        "gsettings", "gtc", "hostname", "infocmp", "infotocap", "install",
        "locale", "logname", "lsattr", "mac2unix", "minidumper", "mount",
        "mpicalc", "nano", "nettle-hash", "nettle-lfib-stream", "nettle-pbkdf2",
        "nice", "nohup", "numfmt", "od", "p11-kit", "p11-kit-remote", "p11-kit-server",
        "paste", "pathchk", "pinky", "pr", "printf", "profiler", "psl", "ptx",
        "pwd", "readlink", "realpath", "recode-sr-latin", "reset", "rm", "rmdir",
        "rnano", "runcon", "rview", "rvim", "scalar", "sdiff", "setfacl",
        "setmetamode", "shred", "shuf", "sleep", "split", "strace", "stty",
        "sum", "sync", "tabs", "tac", "timeout", "toe", "touch", "truncate",
        "tsort", "tty", "tzset", "umount", "unexpand", "unix2dos", "unix2mac",
        "unlink", "urlget", "users", "vdir", "view", "vim", "vimdiff", "watchgnupg",
        "which", "who", "whoami", "xxd", "yat2m", "yes"
    }
    AUTO_EXCEPTION_PREFIXES = [
        "git", "pcssb", "pcspc", "pcsend", "pcswsb", "pcspcv", "ag_say",
        "zqds", "zquake", "curl", "perl", "openssl", "gettext", "envsubst",
        "pkcs1", "sexp", "hostname", "tclsh", "wish", "blocked-file-util",
        "create-shortcut", "github", "gitlab", "odt2txt", "pdftotext", "proxy-lookup",
        "xmlwf"
    ]
    AUTO_EXCEPTION_BATCH_STEMS = {
        "help", "readme", "change", "site", "emake"
    }
    AUTO_EXCEPTION_SUFFIXES = [
        # Common utility suffixes
        "-setup", "-installer", "-install", "-uninstall", "-unins",
        "-update", "-updater", "-upgrade", "-patch", "-patcher",
        "-config", "-configure", "-configurator", "-register", "-registration",
        "-checkup", "-checker", "-diagnostic", "-repair", "-cleanup",
        "-tool", "-utility", "-helper", "-manager", "-launcher",
        "-editor", "-viewer", "-monitor", "-scanner", "-tester",
        # Version/build suffixes
        "-debug", "-release", "-beta", "-alpha", "-demo", "-trial",
        "-lite", "-full", "-pro", "-premium", "-server", "-client",
        # Platform/architecture suffixes
        "-32bit", "-64bit", "-x86", "-x64", "-win32", "-win64",
        "-console", "-gui", "-cli", "-ui", "-api",
        # File/data suffixes
        "-data", "-backup", "-temp", "-tmp", "-cache", "-log",
        "-crash", "-dump", "-report", "-export", "-import"
    ]
    
    def __init__(self):
        self.games = []
        self.config = {}
        self.app_dir = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.games_file = self.app_dir / "games.json"
        self.config_file = self.get_config_path()
        self.load_config()
        self.load_games()
        self._last_auto_exception_count = 0
    
    def get_config_path(self):
        """Get platform-specific config path"""
        system = platform.system()
        if system == "Windows":
            app_data = Path(os.environ.get('APPDATA', ''))
            config_dir = app_data / "GameChooser"
        elif system == "Darwin":  # macOS
            config_dir = Path.home() / "Library" / "Application Support" / "GameChooser"
        else:  # Linux/Unix
            config_dir = Path.home() / ".config" / "GameChooser"
        
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"
    
    def load_config(self):
        """Load configuration from JSON file"""
        default_config = {
            "libraries": [],
            "exceptions": [],
            "SavedState": {
                "window_size": None,
                "window_position": None,
                "splitter_position": None,
                "sort_column": 0,
                "sort_ascending": True,
                "last_selected": None,
                "last_search": "",
                "column_widths": None,
                "tree_expansion": {}
            }
        }
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key in default_config:
                        if key not in loaded:
                            loaded[key] = default_config[key]
                    if "SavedState" in loaded:
                        for state_key in default_config["SavedState"]:
                            if state_key not in loaded["SavedState"]:
                                loaded["SavedState"][state_key] = default_config["SavedState"][state_key]
                    self.config = loaded
            except:
                self.config = default_config
        else:
            self.config = default_config
            self.save_config()
    
    def save_config(self):
        """Save configuration to JSON file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def _normalize_exception_entry(self, entry: str) -> str:
        return entry.replace('\\', '/').strip()

    def _normalize_for_match(self, entry: str) -> str:
        return self._normalize_exception_entry(entry).lower()

    def _is_path_exception(self, rel_path: str) -> bool:
        rel_norm = self._normalize_for_match(rel_path)
        for exc in self.config["exceptions"]:
            pattern_norm = self._normalize_exception_entry(exc)
            pattern_lower = pattern_norm.lower()
            if any(ch in pattern_norm for ch in ['*', '?', '[', ']']):
                if fnmatch.fnmatch(rel_norm, pattern_lower):
                    return True
            elif rel_norm == pattern_lower:
                return True
        return False

    def _add_exception_entry(self, entry: str) -> bool:
        normalized = self._normalize_exception_entry(entry)
        candidate_lower = normalized.lower()

        for existing in self.config["exceptions"]:
            existing_norm = self._normalize_exception_entry(existing)
            existing_lower = existing_norm.lower()
            if existing_lower == candidate_lower:
                return False
            if fnmatch.fnmatch(candidate_lower, existing_lower):
                return False

        self.config["exceptions"].append(normalized)
        return True

    def _build_keyword_pattern(self, keyword: str, suffix: str) -> str:
        keyword = keyword.lower()
        suffix = suffix.lower()
        if suffix:
            return f"*{keyword}*{suffix}"
        return f"*{keyword}*"

    def _build_filename_pattern(self, name: str) -> str:
        return f"*{name.lower()}"

    def _generate_auto_exception_patterns(self, item: Path) -> List[str]:
        patterns: List[str] = []
        name = item.name
        suffix = item.suffix.lower()
        stem = item.stem.lower()

        def add_pattern(pattern: Optional[str]):
            if pattern and pattern not in patterns:
                patterns.append(pattern)

        # Check exact stem matches
        if stem in self.AUTO_EXCEPTION_EXACT_STEMS:
            add_pattern(self._build_filename_pattern(name))

        # Check batch file specific stems
        if suffix in {'.bat', '.cmd'} and stem in self.AUTO_EXCEPTION_BATCH_STEMS:
            add_pattern(self._build_filename_pattern(name))

        # Check keyword matches within the stem
        for keyword in self.AUTO_EXCEPTION_KEYWORDS:
            if keyword in stem:
                add_pattern(self._build_keyword_pattern(keyword, suffix))

        # Check prefix matches
        for prefix in self.AUTO_EXCEPTION_PREFIXES:
            if stem == prefix:
                add_pattern(self._build_keyword_pattern(prefix, suffix))
            elif stem.startswith(f"{prefix}-") or stem.startswith(f"{prefix}_"):
                add_pattern(self._build_keyword_pattern(prefix, suffix))
            elif stem.startswith(prefix) and prefix in {"git"}:
                add_pattern(self._build_keyword_pattern(prefix, suffix))

        # Check suffix patterns (e.g., filename ends with -setup, -installer, etc.)
        for exception_suffix in self.AUTO_EXCEPTION_SUFFIXES:
            if stem.endswith(exception_suffix):
                # Create pattern that matches the suffix
                add_pattern(f"*{exception_suffix}{suffix}")

        # Fallback for batch files that don't match valid game names
        if not patterns and suffix in {'.bat', '.cmd'} and stem not in self.VALID_GAME_NAMES:
            add_pattern(self._build_filename_pattern(name))

        return patterns

    def _should_auto_exclude(self, item: Path) -> bool:
        """Check if a file should be automatically excluded based on patterns.

        This method contains all the pattern-matching logic but doesn't modify
        the exceptions list. It's used internally to determine if a file should
        be excluded during scanning.

        Args:
            item: Path object representing the file to check

        Returns:
            bool: True if the file should be excluded, False otherwise
        """
        name = item.name
        suffix = item.suffix.lower()
        stem = item.stem.lower()

        # Check exact stem matches
        if stem in self.AUTO_EXCEPTION_EXACT_STEMS:
            return True

        # Check batch file specific stems
        if suffix in {'.bat', '.cmd'} and stem in self.AUTO_EXCEPTION_BATCH_STEMS:
            return True

        # Check keyword matches within the stem
        # Use word boundaries to avoid false positives (e.g., "rm" in "normal_game")
        for keyword in self.AUTO_EXCEPTION_KEYWORDS:
            if keyword in stem:
                # Check if it's a word boundary match to avoid false positives
                # For very short keywords (1-2 chars), require exact word match
                if len(keyword) <= 2:
                    # Require word boundaries for short keywords
                    import re
                    pattern = r'\b' + re.escape(keyword) + r'\b'
                    if re.search(pattern, stem):
                        return True
                else:
                    # For longer keywords, substring match is more reliable
                    return True

        # Check prefix matches
        for prefix in self.AUTO_EXCEPTION_PREFIXES:
            if stem == prefix:
                return True
            elif stem.startswith(f"{prefix}-") or stem.startswith(f"{prefix}_"):
                return True
            elif stem.startswith(prefix) and prefix in {"git"}:
                return True

        # Check suffix patterns (e.g., filename ends with -setup, -installer, etc.)
        for exception_suffix in self.AUTO_EXCEPTION_SUFFIXES:
            if stem.endswith(exception_suffix):
                return True

        # Fallback for batch files that don't match valid game names
        if suffix in {'.bat', '.cmd'} and stem not in self.VALID_GAME_NAMES:
            return True

        return False

    def add_to_exceptions(self, game):
        """Add a game's launch path to exceptions when user deletes it"""
        if game and game.launch_path:
            # Strip library prefix from launch_path to get library-relative path
            path_parts = game.launch_path.split('/')
            if len(path_parts) > 1:
                # Remove the first part (library name) to get library-relative path
                library_relative_path = '/'.join(path_parts[1:])
            else:
                # If no slash, use the path as-is (shouldn't happen for scanned games)
                library_relative_path = game.launch_path

            normalized_path = self._normalize_exception_entry(library_relative_path)
            if self._add_exception_entry(normalized_path):
                self.save_config()
    
    def load_games(self):
        """Load games from JSON file"""
        if self.games_file.exists():
            try:
                with open(self.games_file, 'r') as f:
                    data = json.load(f)
                    self.games = [Game.from_dict(g) for g in data]
            except:
                self.games = []
        else:
            self.games = []
    
    def save_games(self):
        """Save games to JSON file"""
        with open(self.games_file, 'w') as f:
            json.dump([g.to_dict() for g in self.games], f, indent=2)
    
    def get_library_by_name(self, name):
        """Get library path by name"""
        for lib in self.config["libraries"]:
            if lib["name"] == name:
                return lib["path"]
        return None
    
    def get_full_path(self, game):
        """Construct full path from relative path and library"""
        if game.launch_path.startswith("http"):
            return game.launch_path
        
        # Handle manual games - return the direct path
        if game.library_name == "manual":
            return game.launch_path
        
        parts = Path(game.launch_path).parts
        if not parts:
            return None
        
        lib_name = parts[0]
        for lib in self.config["libraries"]:
            if lib["name"] == lib_name:
                full_path = Path(lib["path"]) / Path(*parts[1:])
                return str(full_path)
        
        return None
    
    def is_executable(self, path):
        """Check if file is an executable based on platform and extension"""
        path_obj = Path(path)
        system = platform.system()
        
        if system == "Windows":
            return path_obj.suffix.lower() in ['.exe', '.bat']
        elif system == "Darwin":  # macOS
            # Check for .app bundles (directories)
            if path_obj.suffix.lower() == '.app' and path_obj.is_dir():
                return True
            
            # Check for executable files with common game extensions
            if path_obj.suffix.lower() in ['.sh', '.command']:
                return True
            
            # Check if it's an executable file (has execute permission and is a regular file)
            try:
                if path_obj.is_file() and os.access(path, os.X_OK):
                    # Additional check: skip obvious non-game executables
                    name = path_obj.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False
        else:  # Linux/Unix
            # Check for executable files with common extensions
            if path_obj.suffix.lower() in ['.sh', '.run']:
                return True
            
            # Check if it's an executable file
            try:
                if path_obj.is_file() and os.access(path, os.X_OK):
                    # Skip obvious non-game executables
                    name = path_obj.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False
        
        return False
    
    def is_valid_game_executable(self, path):
        """Check if executable matches valid game patterns"""
        name = Path(path).stem.lower()
        parent_name = Path(path).parent.name.lower()
        
        valid_names = self.VALID_GAME_NAMES + [parent_name]
        return name in valid_names
    
    def _validate_libraries(self):
        """Validate library paths and separate valid from missing ones
        
        Returns:
            tuple: (valid_libraries, missing_libraries)
        """
        valid_libraries = []
        missing_libraries = []
        
        for lib in self.config["libraries"]:
            if lib["name"] == "manual":
                valid_libraries.append(lib)  # Always keep manual library
                continue
                
            if not Path(lib["path"]).exists():
                missing_libraries.append(lib)
            else:
                valid_libraries.append(lib)
        
        return valid_libraries, missing_libraries
    
    def _remove_missing_libraries(self, missing_libraries):
        """Remove missing libraries from config and return list of removed libraries
        
        Args:
            missing_libraries: List of missing library configs
            
        Returns:
            list: List of removed library configs
        """
        if not missing_libraries:
            return []
            
        removed_libraries = []
        print(f"Removing {len(missing_libraries)} missing library path(s) from config:")
        for lib in missing_libraries:
            print(f"  - {lib['name']}: {lib['path']}")
            removed_libraries.append(lib)
        
        # Update config to only contain valid libraries
        valid_libraries, _ = self._validate_libraries()
        self.config["libraries"] = [lib for lib in self.config["libraries"] 
                                   if lib not in missing_libraries]
        self.save_config()
        
        return removed_libraries
    
    def _validate_existing_games(self, valid_library_names, cancel_check=None):
        """Validate existing games and return only those that still exist
        
        Args:
            valid_library_names: Set of valid library names
            cancel_check: Optional function to check for cancellation
            
        Returns:
            list: List of validated games that still exist
        """
        validated_games = []
        
        for game in self.games:
            if cancel_check and cancel_check():
                break
                
            # Always keep web games
            if game.launch_path.startswith("http"):
                validated_games.append(game)
                continue
            
            # Always keep manual games (they manage their own paths)
            if game.library_name == "manual":
                validated_games.append(game)
                continue
            
            # Only keep games from libraries that still exist
            if game.library_name in valid_library_names:
                full_path = self.get_full_path(game)
                if full_path and Path(full_path).exists():
                    validated_games.append(game)
        
        return validated_games
    
    def _build_known_game_dirs(self, validated_games):
        """Build set of known game directories from validated games
        
        Args:
            validated_games: List of validated games
            
        Returns:
            set: Set of directory paths that contain known games
        """
        known_game_dirs = set()
        
        for game in validated_games:
            if not game.launch_path.startswith("http") and game.library_name != "manual":
                try:
                    full_path = self.get_full_path(game)
                    if full_path:
                        game_dir = str(Path(full_path).parent)
                        known_game_dirs.add(game_dir)
                except:
                    pass  # Skip if path processing fails
        
        return known_game_dirs
    
    def scan_library(self, library_path, library_name, known_game_dirs=None, max_depth=None,
                     progress_callback=None, cancel_check=None):
        """Recursively scan a library path for games.

        Args:
            library_path: Path to library directory to scan
            library_name: Name of the library
            known_game_dirs: Optional set of directories to skip (for incremental scanning)
            max_depth: Maximum scan depth
            progress_callback: Optional progress callback function
            cancel_check: Optional cancellation check function

        Returns:
            tuple[list[Game], int]: Newly discovered games and number of auto exceptions added.
        """
        if max_depth is None:
            max_depth = self.MAX_SCAN_DEPTH
        if known_game_dirs is None:
            known_game_dirs = set()
            
        # Check if library path exists
        library_path_obj = Path(library_path)
        if not library_path_obj.exists():
            print(f"Warning: Library path '{library_path}' does not exist. Skipping scan.")
            return [], []
        
        if not library_path_obj.is_dir():
            print(f"Warning: Library path '{library_path}' is not a directory. Skipping scan.")
            return [], []

        found_games = []
        directories_to_scan = []
        auto_exceptions_added = 0

        # First pass: collect all directories to get total count for progress
        if progress_callback:
            def collect_directories(path, depth=0):
                if depth > max_depth or (cancel_check and cancel_check()):
                    return
                try:
                    for item in Path(path).iterdir():
                        if item.name.startswith('.') or item.is_symlink():
                            continue
                        if item.is_dir():
                            # Only add to scan list if not a known game directory (for incremental scanning)
                            if str(item) not in known_game_dirs:
                                directories_to_scan.append(str(item))
                                collect_directories(item, depth + 1)
                except (PermissionError, OSError):
                    pass
            collect_directories(library_path)

        directories_processed = 0

        def scan_recursive(path, depth=0):
            nonlocal directories_processed, auto_exceptions_added

            if depth > max_depth or (cancel_check and cancel_check()):
                return

            # SKIP if this directory already contains a known game (incremental scanning)
            if str(path) in known_game_dirs:
                return
            
            # Update progress
            if progress_callback and directories_to_scan:
                progress = (directories_processed / len(directories_to_scan)) * 100
                progress_callback(library_name, progress, len(found_games))
                directories_processed += 1
            
            try:
                # First, collect all executables in each directory to handle duplicates
                directory_exes = {}
                
                for item in Path(path).iterdir():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    # Skip hidden/system files and symlinks
                    if item.name.startswith('.'):
                        continue
                    if item.is_symlink():
                        continue
                    
                    if self.is_executable(str(item)):
                        # Build relative path (relative to library, not library parent)
                        rel_path = item.relative_to(Path(library_path))
                        rel_str = str(rel_path).replace(os.sep, '/')

                        # Check if in exceptions
                        if self._is_path_exception(rel_str):
                            continue

                        # Check if file should be auto-excluded
                        if self._should_auto_exclude(item):
                            # Add the exact path to exceptions (not a pattern)
                            if self._add_exception_entry(rel_str):
                                auto_exceptions_added += 1
                            continue

                        # Group by directory
                        dir_key = str(item.parent)
                        if dir_key not in directory_exes:
                            directory_exes[dir_key] = []
                        directory_exes[dir_key].append((item, rel_str))
                
                # Process each directory's executables - create a game for EACH executable
                for dir_path, exe_list in directory_exes.items():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    if not exe_list:
                        continue
                    
                    # Create a game for EVERY executable found (not just one per directory)
                    for exe_item, exe_rel_str in exe_list:
                        # Check for cancellation
                        if cancel_check and cancel_check():
                            return
                            
                        # Create descriptive title - use directory name, or add executable name if multiple exes
                        base_title = exe_item.parent.name
                        if len(exe_list) > 1:
                            # Multiple executables in directory - add exe name to distinguish
                            exe_name = exe_item.stem  # filename without extension
                            title = f"{base_title} ({exe_name})"
                        else:
                            # Single executable - just use directory name
                            title = base_title
                            
                        system = platform.system()
                        plat = "Windows" if system == "Windows" else "macOS"
                        
                        # Check if game already exists
                        existing = None
                        for g in found_games:
                            if g.launch_path == exe_rel_str:
                                existing = g
                                break
                        
                        if existing:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                        else:
                            game = Game(
                                title=title,
                                platforms=[plat],
                                launch_path=exe_rel_str,
                                library_name=library_name
                            )
                            found_games.append(game)
                
                # Continue recursing into subdirectories
                for item in Path(path).iterdir():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    if item.name.startswith('.'):
                        continue
                    if item.is_symlink():
                        continue
                    if item.is_dir():
                        # Only recurse if not a known game directory (for incremental scanning)
                        if str(item) not in known_game_dirs:
                            scan_recursive(item, depth + 1)
            
            except PermissionError:
                # Handle permission errors
                raise PermissionError(f"Permission denied: {path}")
        
        scan_recursive(library_path)
        return found_games, auto_exceptions_added
    
    def validate_and_scan_all(self, progress_callback=None, cancel_check=None):
        """Validate existing games and scan for new ones"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)

        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)

            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            self._last_auto_exception_count = 0
            return removed_libraries

        # SECOND: Validate existing games (only if no libraries were removed)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)

        if cancel_check and cancel_check():
            self._last_auto_exception_count = 0
            return []

        # THIRD: Scan all valid libraries for new games (exclude manual library)
        total_auto_exceptions = 0
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                self._last_auto_exception_count = total_auto_exceptions
                return []  # Return early if cancelled

            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery

            try:
                new_games, added_exceptions = self.scan_library(
                    lib["path"],
                    lib["name"],
                    progress_callback=progress_callback,
                    cancel_check=cancel_check
                )
                total_auto_exceptions += added_exceptions


                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        self._last_auto_exception_count = total_auto_exceptions
                        return []  # Return early if cancelled

                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()

        self._last_auto_exception_count = total_auto_exceptions

        return []  # No libraries removed in this case
    
    def validate_and_scan_incrementally(self, progress_callback=None, cancel_check=None):
        """Validate existing games and scan only new directories for faster startup"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)

        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)

            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            self._last_auto_exception_count = 0
            return removed_libraries

        # SECOND: Validate existing games (remove missing ones)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)

        if cancel_check and cancel_check():
            self._last_auto_exception_count = 0
            return []

        # THIRD: Build set of known game directories from validated games
        known_game_dirs = self._build_known_game_dirs(validated_games)

        # FOURTH: Scan for new games only (skip known directories)
        total_auto_exceptions = 0
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                self._last_auto_exception_count = total_auto_exceptions
                return []  # Return early if cancelled

            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery

            try:
                new_games, added_exceptions = self.scan_library(
                    lib["path"],
                    lib["name"],
                    known_game_dirs=known_game_dirs,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check
                )
                total_auto_exceptions += added_exceptions


                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        self._last_auto_exception_count = total_auto_exceptions
                        return []  # Return early if cancelled

                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()

        self._last_auto_exception_count = total_auto_exceptions

        return []  # No libraries removed in this case
    
    def validate_and_scan_all_with_dialog(self, parent_window):
        """Validate and scan all libraries with progress dialog"""
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_all()
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled

                removed_libraries = self.validate_and_scan_all(progress_callback, cancel_check)
                scan_result["exceptions_count"] = self._last_auto_exception_count
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled

            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]
    
    def validate_and_scan_targeted(self, new_library_names, progress_callback=None, cancel_check=None):
        """Validate existing games and scan only targeted libraries"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)
        
        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)

            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            self._last_auto_exception_count = 0
            return removed_libraries

        # SECOND: Validate existing games (remove missing ones)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)

        if cancel_check and cancel_check():
            self._last_auto_exception_count = 0
            return []

        # THIRD: Build set of known game directories from validated games
        known_game_dirs = self._build_known_game_dirs(validated_games)

        # FOURTH: Scan libraries (full scan for new ones, incremental for existing)
        total_auto_exceptions = 0
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                self._last_auto_exception_count = total_auto_exceptions
                return []  # Return early if cancelled

            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery

            try:
                if lib["name"] in new_library_names:
                    # New library - use full scan to discover all games
                    new_games, added_exceptions = self.scan_library(
                        lib["path"],
                        lib["name"],
                        progress_callback=progress_callback,
                        cancel_check=cancel_check
                    )
                else:
                    # Existing library - use incremental scan (skip known game directories)
                    new_games, added_exceptions = self.scan_library(
                        lib["path"],
                        lib["name"],
                        known_game_dirs=known_game_dirs,
                        progress_callback=progress_callback,
                        cancel_check=cancel_check
                    )
                total_auto_exceptions += added_exceptions


                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        self._last_auto_exception_count = total_auto_exceptions
                        return []  # Return early if cancelled

                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()

        self._last_auto_exception_count = total_auto_exceptions

        return []  # No libraries removed in this case
    
    def validate_and_scan_targeted_with_dialog(self, parent_window, new_library_names=None):
        """Validate existing games and scan targeted libraries with progress dialog"""
        if new_library_names is None:
            new_library_names = set()
        
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_targeted(new_library_names)
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Use standard scanning text
        progress_dialog.status_text.SetLabel("Scanning for games...")
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for targeted scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled

                removed_libraries = self.validate_and_scan_targeted(
                    new_library_names, progress_callback, cancel_check
                )
                scan_result["exceptions_count"] = self._last_auto_exception_count
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled

            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]
    
    def validate_and_scan_incrementally_with_dialog(self, parent_window):
        """Validate existing games and scan incrementally with progress dialog (faster startup)"""
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_incrementally()
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Use standard scanning text
        progress_dialog.status_text.SetLabel("Scanning for games...")
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for incremental scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled

                removed_libraries = self.validate_and_scan_incrementally(progress_callback, cancel_check)
                scan_result["exceptions_count"] = self._last_auto_exception_count
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled

            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]
