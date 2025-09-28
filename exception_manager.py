import re
import fnmatch
from pathlib import Path


class ExceptionManager:
    """Manages auto-exception patterns and user exceptions for game scanning."""

    # Auto-exception patterns moved from library_manager.py
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
        "mount", "umount", "sync", "df", "du", "mktemp", "env", "printenv",
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
        "server", "scalar", "rebase", "hostname", "productidmanager",
        # Game utilities that are not games
        "java", "oggenc2", "signtool", "joystick", "mapmaker", "myaccount",
        "leinstaller", "leupdater", "ssceruntime-enu", "updater_dt",
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
        "env", "printenv", "who", "whoami", "users", "groups", "logname",
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
        "gsettings", "hostname", "infocmp", "infotocap", "install",
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
        "setup", "-setup", "-installer", "-install", "-uninstall", "-unins",
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

    # Valid game names (from original library_manager.py)
    VALID_GAME_NAMES = ["game", "launch", "play", "start", "run"]

    def __init__(self):
        """Initialize exception manager with optimized lookup structures."""
        # Convert to sets for O(1) lookup
        self.keywords = set(self.AUTO_EXCEPTION_KEYWORDS)
        self.exact_stems = set(self.AUTO_EXCEPTION_EXACT_STEMS)
        self.suffixes = set(self.AUTO_EXCEPTION_SUFFIXES)
        self.prefixes = set(self.AUTO_EXCEPTION_PREFIXES)
        self.batch_stems = set(self.AUTO_EXCEPTION_BATCH_STEMS)
        self.valid_game_names = set(self.VALID_GAME_NAMES)

        # Compile regex patterns once for efficiency
        self.keyword_pattern = self._compile_keyword_pattern()

    def _compile_keyword_pattern(self):
        """Compile keyword patterns into a single regex for efficiency."""
        escaped = [re.escape(kw) for kw in self.keywords]
        pattern = r'\b(' + '|'.join(escaped) + r')\b'
        return re.compile(pattern, re.IGNORECASE)

    def should_auto_exclude(self, path):
        """
        Check if a path should be automatically excluded based on patterns.

        Args:
            path: Path object or string to check

        Returns:
            bool: True if path matches auto-exception patterns
        """
        if isinstance(path, str):
            path = Path(path)

        name = path.name
        suffix = path.suffix.lower()
        stem = path.stem.lower()

        # Check exact stem matches
        if stem in self.exact_stems:
            return True

        # Check stem without parentheses and numbers for variations like "oggenc2 (1)"
        stem_clean = re.sub(r'\s*\([^)]*\)', '', stem).strip()
        if stem_clean in self.exact_stems:
            return True

        # Check batch file specific stems
        if suffix in {'.bat', '.cmd'} and stem in self.batch_stems:
            return True

        # Check keyword matches within the stem (with word boundaries)
        if self.keyword_pattern.search(stem):
            return True

        # Check prefix matches
        for prefix in self.prefixes:
            if stem == prefix:
                return True
            elif stem.startswith(f"{prefix}-") or stem.startswith(f"{prefix}_"):
                return True
            elif stem.startswith(prefix) and prefix in {"git"}:
                return True

        # Check suffix patterns (e.g., filename ends with -setup, -installer, etc.)
        for exception_suffix in self.suffixes:
            if stem.endswith(exception_suffix):
                return True

        # Check additional specific patterns for utilities
        name_lower = name.lower()

        # Runtime libraries
        if stem.startswith('msvc') or stem.startswith('vbrun'):
            return True

        # Update/installer patterns
        if stem.startswith('update_'):
            return True

        # Uninstaller variations (catches "unins000 (1)", "uninst*" etc.)
        if stem.startswith('unins') or stem.startswith('uninst'):
            return True

        # Server pattern (e.g., "Server3000")
        if re.match(r'^server\d+$', stem):
            return True

        # Map maker pattern (with space)
        if 'map maker' in name_lower:
            return True

        # Additional installer/updater patterns as substrings for compound words
        if ('installer' in stem or 'updater' in stem or
            ('install' in stem and len(stem) > 7) or  # avoid matching "install" alone
            ('runtime' in stem)):
            return True

        # Fallback for batch files that don't match valid game names
        if suffix in {'.bat', '.cmd'}:
            # Allow if stem exactly matches valid game names
            if stem in self.valid_game_names:
                return False
            # Allow if stem starts with valid game name followed by space (compound launcher names)
            if any(stem.startswith(valid_name + ' ') for valid_name in self.valid_game_names):
                return False
            # Otherwise exclude batch file
            return True

        return False

    def is_user_exception(self, rel_path, exceptions_list):
        """
        Check if a path matches user-defined exceptions.

        Args:
            rel_path: Library-relative path to check
            exceptions_list: List of user exception patterns

        Returns:
            bool: True if path matches user exceptions
        """
        rel_str = str(rel_path).replace('\\', '/')

        for exception in exceptions_list:
            exception = exception.strip().replace('\\', '/')

            # Handle folder exceptions (ending with /)
            if exception.endswith('/'):
                # For folder exceptions, check if the path starts with the folder path
                folder_pattern = exception.rstrip('/')
                if rel_str.startswith(folder_pattern + '/') or rel_str == folder_pattern:
                    return True
            # Handle exact matches
            elif rel_str == exception:
                return True
            # Handle wildcard patterns
            elif '*' in exception:
                if fnmatch.fnmatch(rel_str, exception):
                    return True

        return False