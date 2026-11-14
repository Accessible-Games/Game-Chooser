#!/usr/bin/env python3
# Game Chooser - A desktop game library manager
# Copyright (C) 2025 Alec Olson
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import sys


def main():
    compiled = "__compiled__" in globals()
    try:
        import tracing.cli as tracing_cli
    except ImportError:
        tracing_cli = None
    if tracing_cli is not None:
        code = tracing_cli.run_command(sys.argv[1:], compiled)
        if code is not None:
            sys.exit(code)

    import wx
    from main_window import MainFrame

    app = wx.App()
    frame = MainFrame()
    frame.Show()
    if tracing_cli is not None and tracing_cli.tracing_wanted(sys.argv[1:], compiled):
        tracing_cli.attach_app(frame, compiled, sys.argv[1:])
    app.MainLoop()


if __name__ == "__main__":
    main()
