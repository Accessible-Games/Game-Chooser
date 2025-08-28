#!/usr/bin/env python3
"""
Dialog classes for Game Chooser application
"""

import wx
import json
import os
import platform
from models import Game


class ScanProgressDialog(wx.Dialog):
    """Dialog showing scanning progress with cancel button"""
    
    def __init__(self, parent):
        super().__init__(parent, title="Scanning for Games", 
                        style=wx.CAPTION | wx.SYSTEM_MENU)
        
        self.cancelled = False
        self.current_library = ""
        self.libraries_processed = 0
        self.total_libraries = 0
        self.games_found = 0
        
        self.init_ui()
        self.CenterOnParent()
        
    def init_ui(self):
        """Initialize the dialog UI"""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Status text
        self.status_text = wx.StaticText(panel, label="Initializing scan...")
        main_sizer.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 10)
        
        # Current library text
        self.library_text = wx.StaticText(panel, label="")
        main_sizer.Add(self.library_text, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        
        # Progress bar
        self.progress_bar = wx.Gauge(panel, range=100)
        main_sizer.Add(self.progress_bar, 0, wx.ALL | wx.EXPAND, 10)
        
        # Games found counter
        self.games_text = wx.StaticText(panel, label="Games found: 0")
        main_sizer.Add(self.games_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        
        # Cancel button
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        main_sizer.Add(cancel_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        
        panel.SetSizer(main_sizer)
        
        # Size the dialog
        main_sizer.Fit(panel)
        self.SetClientSize(panel.GetSize())
        self.SetMinSize((300, -1))
        
    def on_cancel(self, event):
        """Handle cancel button click"""
        self.cancelled = True
        self.status_text.SetLabel("Cancelling scan...")
        
    def update_progress(self, library_name, progress, games_found):
        """Update the progress display"""
        if self.cancelled:
            return
            
        # Use CallAfter to ensure UI updates happen on main thread
        wx.CallAfter(self._update_progress_ui, library_name, progress, games_found)
    
    def _update_progress_ui(self, library_name, progress, games_found):
        """Update progress UI on main thread"""
        if library_name != self.current_library:
            self.current_library = library_name
            self.library_text.SetLabel(f"Scanning: {library_name}")
            
        self.progress_bar.SetValue(int(progress))
        self.games_found = games_found
        self.games_text.SetLabel(f"Games found: {games_found}")
        
    def set_library_count(self, total):
        """Set total number of libraries to process"""
        self.total_libraries = total
        if total > 0:
            self.status_text.SetLabel(f"Scanning {total} game libraries...")
    
    def finish_scan(self, games_found, exceptions_added):
        """Called when scan is complete"""
        wx.CallAfter(self._finish_scan_ui, games_found, exceptions_added)
        
    def _finish_scan_ui(self, games_found, exceptions_added):
        """Finish scan UI on main thread"""
        if self.cancelled:
            self.status_text.SetLabel("Scan cancelled")
        else:
            self.status_text.SetLabel(f"Scan complete! Found {games_found} games")
            if exceptions_added > 0:
                self.games_text.SetLabel(f"Games found: {games_found} (+{exceptions_added} exceptions added)")
        
        self.progress_bar.SetValue(100)
        
        # Auto-close after 1 second if not cancelled
        if not self.cancelled:
            wx.CallLater(1000, self.EndModal, wx.ID_OK)


class EditManualGameDialog(wx.Dialog):
    """Dialog for editing manual game information"""
    
    def __init__(self, parent, game, library_manager):
        super().__init__(parent, title="Add Manual Game", size=(400, 350))
        
        self.game = game
        self.library_manager = library_manager
        
        # Get existing genres and developers for combo boxes
        genres = sorted(set(g.genre for g in library_manager.games if g.genre))
        developers = sorted(set(g.developer for g in library_manager.games if g.developer))
        
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(5, 5)
        
        # Title
        sizer.Add(wx.StaticText(panel, label="Title:"), 
                 pos=(0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.title_ctrl = wx.TextCtrl(panel, value=game.title)
        sizer.Add(self.title_ctrl, pos=(0, 1), flag=wx.EXPAND)
        
        # Launch Path
        sizer.Add(wx.StaticText(panel, label="Launch Path:"), 
                 pos=(1, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        
        path_panel = wx.Panel(panel)
        path_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.path_ctrl = wx.TextCtrl(path_panel, value="")
        browse_btn = wx.Button(path_panel, label="Browse...")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        
        path_sizer.Add(self.path_ctrl, 1, wx.EXPAND | wx.RIGHT, 5)
        path_sizer.Add(browse_btn, 0)
        path_panel.SetSizer(path_sizer)
        
        sizer.Add(path_panel, pos=(1, 1), flag=wx.EXPAND)
        
        # Platform
        sizer.Add(wx.StaticText(panel, label="Platform:"), 
                 pos=(2, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        platforms = ["Windows", "macOS", "Linux"]
        self.platform_ctrl = wx.ComboBox(panel, choices=platforms, style=wx.CB_DROPDOWN)
        if game.platforms:
            self.platform_ctrl.SetValue(game.platforms[0])
        else:
            system = platform.system()
            default_plat = "Windows" if system == "Windows" else "macOS"
            self.platform_ctrl.SetValue(default_plat)
        sizer.Add(self.platform_ctrl, pos=(2, 1), flag=wx.EXPAND)
        
        # Genre
        sizer.Add(wx.StaticText(panel, label="Genre:"), 
                 pos=(3, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.genre_ctrl = wx.ComboBox(panel, value=game.genre, choices=genres)
        sizer.Add(self.genre_ctrl, pos=(3, 1), flag=wx.EXPAND)
        
        # Developer
        sizer.Add(wx.StaticText(panel, label="Developer:"), 
                 pos=(4, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.developer_ctrl = wx.ComboBox(panel, value=game.developer, 
                                         choices=developers)
        sizer.Add(self.developer_ctrl, pos=(4, 1), flag=wx.EXPAND)
        
        # Year
        sizer.Add(wx.StaticText(panel, label="Year:"), 
                 pos=(5, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        year_val = 2000 if game.year == "unknown" else int(game.year)
        self.year_ctrl = wx.SpinCtrl(panel, value=str(year_val), 
                                    min=1000, max=9999, initial=year_val)
        sizer.Add(self.year_ctrl, pos=(5, 1), flag=wx.EXPAND)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK, "Add")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        
        sizer.Add(btn_sizer, pos=(6, 0), span=(1, 2), 
                 flag=wx.ALIGN_CENTER | wx.TOP, border=10)
        
        sizer.AddGrowableCol(1)
        panel.SetSizer(sizer)
        
        # Validation
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
    
    def on_browse(self, event):
        """Browse for executable file"""
        dlg = wx.FileDialog(self, "Select Game Executable", 
                           wildcard="Executable files (*.exe)|*.exe|All files (*.*)|*.*")
        if dlg.ShowModal() == wx.ID_OK:
            self.path_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()
    
    def on_ok(self, event):
        """Validate and save changes"""
        if not self.title_ctrl.GetValue().strip():
            wx.MessageBox("Title is required", "Error", wx.OK | wx.ICON_ERROR)
            return
        
        if not self.path_ctrl.GetValue().strip():
            wx.MessageBox("Launch path is required", "Error", wx.OK | wx.ICON_ERROR)
            return
        
        if not self.platform_ctrl.GetValue().strip():
            wx.MessageBox("Platform is required", "Error", wx.OK | wx.ICON_ERROR)
            return
        
        self.game.title = self.title_ctrl.GetValue().strip()
        self.game.launch_path = self.path_ctrl.GetValue().strip()
        self.game.platforms = [self.platform_ctrl.GetValue().strip()]
        self.game.genre = self.genre_ctrl.GetValue().strip()
        self.game.developer = self.developer_ctrl.GetValue().strip()
        self.game.library_name = "manual"
        
        year_val = self.year_ctrl.GetValue()
        self.game.year = str(year_val) if year_val != 2000 else "unknown"
        
        self.EndModal(wx.ID_OK)


class EditGameDialog(wx.Dialog):
    """Dialog for editing game information"""
    
    def __init__(self, parent, game, library_manager, is_web=False):
        title = "Edit Web Game" if is_web else "Edit Game"
        super().__init__(parent, title=title, size=(400, 300))
        
        self.game = game
        self.library_manager = library_manager
        self.is_web = is_web
        
        # Get existing genres and developers for combo boxes
        genres = sorted(set(g.genre for g in library_manager.games if g.genre))
        developers = sorted(set(g.developer for g in library_manager.games if g.developer))
        
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(5, 5)
        
        # Title
        sizer.Add(wx.StaticText(panel, label="Title:"), 
                 pos=(0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.title_ctrl = wx.TextCtrl(panel, value=game.title)
        sizer.Add(self.title_ctrl, pos=(0, 1), flag=wx.EXPAND)
        
        # URL or Platform (read-only)
        if is_web:
            sizer.Add(wx.StaticText(panel, label="URL:"), 
                     pos=(1, 0), flag=wx.ALIGN_CENTER_VERTICAL)
            self.url_ctrl = wx.TextCtrl(panel, value=game.launch_path)
            sizer.Add(self.url_ctrl, pos=(1, 1), flag=wx.EXPAND)
        
        sizer.Add(wx.StaticText(panel, label="Platform:"), 
                 pos=(2, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        platform_text = "Web" if is_web else ", ".join(game.platforms)
        self.platform_ctrl = wx.TextCtrl(panel, value=platform_text, 
                                        style=wx.TE_READONLY)
        sizer.Add(self.platform_ctrl, pos=(2, 1), flag=wx.EXPAND)
        
        # Genre
        sizer.Add(wx.StaticText(panel, label="Genre:"), 
                 pos=(3, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.genre_ctrl = wx.ComboBox(panel, value=game.genre, choices=genres)
        sizer.Add(self.genre_ctrl, pos=(3, 1), flag=wx.EXPAND)
        
        # Developer
        sizer.Add(wx.StaticText(panel, label="Developer:"), 
                 pos=(4, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.developer_ctrl = wx.ComboBox(panel, value=game.developer, 
                                         choices=developers)
        sizer.Add(self.developer_ctrl, pos=(4, 1), flag=wx.EXPAND)
        
        # Year
        sizer.Add(wx.StaticText(panel, label="Year:"), 
                 pos=(5, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        year_val = 2000 if game.year == "unknown" else int(game.year)
        self.year_ctrl = wx.SpinCtrl(panel, value=str(year_val), 
                                    min=1000, max=9999, initial=year_val)
        sizer.Add(self.year_ctrl, pos=(5, 1), flag=wx.EXPAND)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        
        sizer.Add(btn_sizer, pos=(6, 0), span=(1, 2), 
                 flag=wx.ALIGN_CENTER | wx.TOP, border=10)
        
        sizer.AddGrowableCol(1)
        panel.SetSizer(sizer)
        
        # Validation
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
    
    def on_ok(self, event):
        """Validate and save changes"""
        if not self.title_ctrl.GetValue().strip():
            wx.MessageBox("Title is required", "Error", wx.OK | wx.ICON_ERROR)
            return
        
        if self.is_web:
            url = self.url_ctrl.GetValue().strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                wx.MessageBox("URL must start with http:// or https://", 
                            "Error", wx.OK | wx.ICON_ERROR)
                return
            self.game.launch_path = url
            self.game.platforms = ["Web"]
        
        self.game.title = self.title_ctrl.GetValue().strip()
        self.game.genre = self.genre_ctrl.GetValue().strip()
        self.game.developer = self.developer_ctrl.GetValue().strip()
        
        year_val = self.year_ctrl.GetValue()
        self.game.year = str(year_val) if year_val != 2000 else "unknown"
        
        self.EndModal(wx.ID_OK)


class PreferencesDialog(wx.Dialog):
    """Preferences dialog with dual-panel layout"""
    
    def __init__(self, parent, library_manager):
        super().__init__(parent, title="Preferences", size=(700, 500))
        
        self.library_manager = library_manager
        
        # Create main splitter
        splitter = wx.SplitterWindow(self)
        
        # Left panel - navigation
        left_panel = wx.Panel(splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.nav_list = wx.ListCtrl(left_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.nav_list.AppendColumn("Settings", width=150)
        self.nav_list.InsertItem(0, "Path Management")
        self.nav_list.Select(0)
        
        left_sizer.Add(self.nav_list, 1, wx.EXPAND | wx.ALL, 5)
        left_panel.SetSizer(left_sizer)
        
        # Right panel - content
        self.right_panel = wx.Panel(splitter)
        self.right_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Path Management content
        self.create_path_management()
        
        self.right_panel.SetSizer(self.right_sizer)
        
        # Set up splitter
        splitter.SplitVertically(left_panel, self.right_panel, 200)
        splitter.SetMinimumPaneSize(150)
        
        # Main sizer with buttons
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(splitter, 1, wx.EXPAND)
        
        # Buttons
        btn_panel = wx.Panel(self)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.AddStretchSpacer()
        
        ok_btn = wx.Button(btn_panel, wx.ID_OK, "OK")
        cancel_btn = wx.Button(btn_panel, wx.ID_CANCEL, "Cancel")
        apply_btn = wx.Button(btn_panel, wx.ID_APPLY, "Apply")
        
        btn_sizer.Add(ok_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(cancel_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(apply_btn, 0, wx.RIGHT, 5)
        
        btn_panel.SetSizer(btn_sizer)
        main_sizer.Add(btn_panel, 0, wx.EXPAND | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        
        # Bind events
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
        apply_btn.Bind(wx.EVT_BUTTON, self.on_apply)
        
        # Store original config for cancel
        self.original_config = json.loads(json.dumps(library_manager.config))
    
    def create_path_management(self):
        """Create path management panel"""
        # Library paths section
        lib_label = wx.StaticText(self.right_panel, label="Game Library Paths:")
        self.right_sizer.Add(lib_label, 0, wx.ALL, 5)
        
        lib_panel = wx.Panel(self.right_panel)
        lib_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.lib_list = wx.ListCtrl(lib_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lib_list.AppendColumn("Name", width=100)
        self.lib_list.AppendColumn("Path", width=300)
        
        # Populate library list
        for lib in self.library_manager.config["libraries"]:
            index = self.lib_list.InsertItem(self.lib_list.GetItemCount(), lib["name"])
            self.lib_list.SetItem(index, 1, lib["path"])
        
        lib_sizer.Add(self.lib_list, 1, wx.EXPAND | wx.RIGHT, 5)
        
        # Library buttons
        lib_btn_panel = wx.Panel(lib_panel)
        lib_btn_sizer = wx.BoxSizer(wx.VERTICAL)
        
        add_lib_btn = wx.Button(lib_btn_panel, label="Add")
        remove_lib_btn = wx.Button(lib_btn_panel, label="Remove")
        
        lib_btn_sizer.Add(add_lib_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        lib_btn_sizer.Add(remove_lib_btn, 0, wx.EXPAND)
        
        lib_btn_panel.SetSizer(lib_btn_sizer)
        lib_sizer.Add(lib_btn_panel, 0, wx.ALIGN_TOP)
        
        lib_panel.SetSizer(lib_sizer)
        self.right_sizer.Add(lib_panel, 1, wx.EXPAND | wx.ALL, 5)
        
        # Exceptions section
        exc_label = wx.StaticText(self.right_panel, label="Excluded Executables:")
        self.right_sizer.Add(exc_label, 0, wx.ALL, 5)
        
        exc_panel = wx.Panel(self.right_panel)
        exc_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.exc_list = wx.ListCtrl(exc_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.exc_list.AppendColumn("Path", width=400)
        
        # Populate exceptions list
        for exc in self.library_manager.config["exceptions"]:
            self.exc_list.InsertItem(self.exc_list.GetItemCount(), exc)
        
        exc_sizer.Add(self.exc_list, 1, wx.EXPAND | wx.RIGHT, 5)
        
        # Exception buttons
        exc_btn_panel = wx.Panel(exc_panel)
        exc_btn_sizer = wx.BoxSizer(wx.VERTICAL)
        
        add_exc_btn = wx.Button(exc_btn_panel, label="Add")
        remove_exc_btn = wx.Button(exc_btn_panel, label="Remove")
        
        exc_btn_sizer.Add(add_exc_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        exc_btn_sizer.Add(remove_exc_btn, 0, wx.EXPAND)
        
        exc_btn_panel.SetSizer(exc_btn_sizer)
        exc_sizer.Add(exc_btn_panel, 0, wx.ALIGN_TOP)
        
        exc_panel.SetSizer(exc_sizer)
        self.right_sizer.Add(exc_panel, 1, wx.EXPAND | wx.ALL, 5)
        
        # Bind events
        add_lib_btn.Bind(wx.EVT_BUTTON, self.on_add_library)
        remove_lib_btn.Bind(wx.EVT_BUTTON, self.on_remove_library)
        add_exc_btn.Bind(wx.EVT_BUTTON, self.on_add_exception)
        remove_exc_btn.Bind(wx.EVT_BUTTON, self.on_remove_exception)
    
    def on_add_library(self, event):
        """Add a new library path"""
        dlg = wx.DirDialog(self, "Choose Game Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            name = os.path.basename(path)
            
            # Add to list control
            index = self.lib_list.InsertItem(self.lib_list.GetItemCount(), name)
            self.lib_list.SetItem(index, 1, path)
            
            # Add to config
            self.library_manager.config["libraries"].append({
                "name": name,
                "path": path
            })
        dlg.Destroy()
    
    def on_remove_library(self, event):
        """Remove selected library path"""
        selected = self.lib_list.GetFirstSelected()
        if selected >= 0:
            # Remove from config
            del self.library_manager.config["libraries"][selected]
            # Remove from list
            self.lib_list.DeleteItem(selected)
    
    def on_add_exception(self, event):
        """Add a new exception"""
        dlg = wx.TextEntryDialog(self, "Enter exception path (e.g., games\\tool\\updater.exe):", 
                                "Add Exception")
        if dlg.ShowModal() == wx.ID_OK:
            exc_path = dlg.GetValue()
            if exc_path:
                self.exc_list.InsertItem(self.exc_list.GetItemCount(), exc_path)
                self.library_manager.config["exceptions"].append(exc_path)
        dlg.Destroy()
    
    def on_remove_exception(self, event):
        """Remove selected exception"""
        selected = self.exc_list.GetFirstSelected()
        if selected >= 0:
            exc_path = self.exc_list.GetItemText(selected)
            self.library_manager.config["exceptions"].remove(exc_path)
            self.exc_list.DeleteItem(selected)
    
    def on_apply(self, event):
        """Apply changes and rescan if needed"""
        old_libs = self.original_config["libraries"]
        new_libs = self.library_manager.config["libraries"]
        
        # Check if libraries changed
        libs_changed = json.dumps(old_libs) != json.dumps(new_libs)
        
        self.library_manager.save_config()
        
        if libs_changed:
            # Rescan libraries
            try:
                exc_count = self.library_manager.validate_and_scan_all_with_dialog(self.GetParent())
                if exc_count > 0:
                    if wx.MessageBox(f"Added {exc_count} executables to exceptions. Open preferences?",
                                    "Exceptions Added", 
                                    wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                        # Already in preferences, just refresh the exceptions list
                        self.exc_list.DeleteAllItems()
                        for exc in self.library_manager.config["exceptions"]:
                            self.exc_list.InsertItem(self.exc_list.GetItemCount(), exc)
            except PermissionError as e:
                wx.MessageBox(str(e), "Permission Denied", wx.OK | wx.ICON_ERROR)
        
        # Update original config
        self.original_config = json.loads(json.dumps(self.library_manager.config))
    
    def on_ok(self, event):
        """Save and close"""
        self.on_apply(event)
        self.EndModal(wx.ID_OK)