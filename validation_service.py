import re
from pathlib import Path

class ValidationService:
    """Centralized validation logic for game data."""

    @staticmethod
    def validate_title(title):
        """
        Validate a game title.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not title or not title.strip():
            return False, "Title cannot be empty"

        if len(title) > 255:
            return False, "Title too long (max 255 characters)"

        return True, None

    @staticmethod
    def validate_url(url):
        """
        Validate a URL for web games.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not url:
            return False, "URL cannot be empty"

        # Basic URL pattern
        url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?'  # domain
            r'(?:/?|[/?]\S+)$', re.IGNORECASE
        )

        if not url_pattern.match(url):
            return False, "Invalid URL format"

        return True, None

    @staticmethod
    def validate_path(path, must_exist=True):
        """
        Validate a file path.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not path:
            return False, "Path cannot be empty"

        try:
            p = Path(path)

            if must_exist and not p.exists():
                return False, f"Path does not exist: {path}"

            if must_exist and not p.is_file():
                return False, f"Path is not a file: {path}"

            return True, None

        except Exception as e:
            return False, f"Invalid path: {str(e)}"

    @staticmethod
    def validate_year(year):
        """
        Validate a year value.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not year:
            return True, None  # Year is optional

        try:
            year_int = int(year)
            if year_int < 1970 or year_int > 2030:
                return False, "Year must be between 1970 and 2030"
            return True, None
        except ValueError:
            return False, "Year must be a number"