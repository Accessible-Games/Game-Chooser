#!/usr/bin/env python3
"""
Game Chooser - A desktop application for organizing and launching games
Entry point for the application
"""

import wx
from main_window import MainFrame


class GameChooserApp(wx.App):
    """Main application class"""
    
    def OnInit(self):
        frame = MainFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = GameChooserApp()
    app.MainLoop()