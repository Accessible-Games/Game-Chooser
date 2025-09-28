import os
import stat
import platform
from pathlib import Path

class PathManager:
    """Centralized path operations and normalization."""

    @staticmethod
    def normalize(path):
        """
        Normalize a path to use forward slashes.

        Args:
            path: Path string or Path object

        Returns:
            String with forward slashes
        """
        if isinstance(path, Path):
            path = str(path)
        return path.replace('\\', '/').strip()

    @staticmethod
    def to_library_relative(full_path, library_paths):
        """
        Convert an absolute path to library-relative.

        Args:
            full_path: Absolute path to convert
            library_paths: List of library path dictionaries

        Returns:
            Library-relative path string or None if not in any library
        """
        full_path = Path(full_path).resolve()

        for lib in library_paths:
            lib_path = Path(lib["path"]).resolve()
            try:
                rel_path = full_path.relative_to(lib_path)
                return PathManager.normalize(rel_path)
            except ValueError:
                continue

        return None

    @staticmethod
    def get_full_path(launch_path, library_paths, library_name):
        """
        Convert a library-relative path to absolute.

        Args:
            launch_path: Library-relative path
            library_paths: List of library path dictionaries
            library_name: Name of the library containing the game

        Returns:
            Absolute path as string or None if library not found
        """
        # Handle special cases
        if launch_path.startswith("http"):
            return launch_path

        if library_name == "manual":
            return launch_path

        # Find library
        for lib in library_paths:
            if lib["name"] == library_name:
                lib_path = Path(lib["path"])
                full_path = lib_path / launch_path
                return str(full_path)

        return None

    @staticmethod
    def is_executable(path):
        """
        Check if a path is an executable file.

        Args:
            path: Path object to check

        Returns:
            bool: True if file is executable
        """
        if not path.is_file():
            return False

        # Platform-specific checks
        system = platform.system()

        if system == "Windows":
            return path.suffix.lower() in ['.exe', '.bat']
        elif system == "Darwin":  # macOS
            # Check for .app bundles (directories)
            if path.suffix.lower() == '.app' and path.is_dir():
                return True

            # Check for executable files with common game extensions
            if path.suffix.lower() in ['.sh', '.command']:
                return True

            # Check if it's an executable file (has execute permission and is a regular file)
            try:
                if path.is_file() and os.access(str(path), os.X_OK):
                    # Additional check: skip obvious non-game executables
                    name = path.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False
        else:  # Linux/Unix
            # Check for executable files with common extensions
            if path.suffix.lower() in ['.sh', '.run']:
                return True

            # Check if it's an executable file
            try:
                if path.is_file() and os.access(str(path), os.X_OK):
                    # Skip obvious non-game executables
                    name = path.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False

        return False