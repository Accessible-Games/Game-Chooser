#!/usr/bin/env python3
"""
Data models for Game Chooser application
"""

from typing import List, Dict, Any


class Game:
    """Represents a game in the library"""
    def __init__(self, title="", genre="", developer="", year="unknown", 
                 platforms=None, launch_path="", library_name=""):
        self.title = title
        self.genre = genre
        self.developer = developer
        self.year = year
        self.platforms = platforms or []
        self.launch_path = launch_path
        self.library_name = library_name
    
    def to_dict(self):
        return {
            "title": self.title,
            "genre": self.genre,
            "developer": self.developer,
            "year": self.year,
            "platforms": self.platforms,
            "launch_path": self.launch_path,
            "library_name": self.library_name
        }
    
    @classmethod
    def from_dict(cls, data):
        return cls(
            title=data.get("title", ""),
            genre=data.get("genre", ""),
            developer=data.get("developer", ""),
            year=data.get("year", "unknown"),
            platforms=data.get("platforms", []),
            launch_path=data.get("launch_path", ""),
            library_name=data.get("library_name", "")
        )