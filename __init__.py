# Copyright (C) 2008-2009 Adam Olsen
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
#
# The developers of the Exaile media player hereby grant permission
# for non-GPL compatible GStreamer and Exaile plugins to be used and
# distributed together with GStreamer and Exaile. This permission is
# above and beyond the permissions granted by the GPL license by which
# Exaile is covered. If you modify this code, you may extend this
# exception to your version of the code, but you are not obligated to
# do so. If you do not wish to do so, delete this exception statement
# from your version.

__all__ = ['main', 'panel', 'playlist']

import logging
import os
import urlparse

import gobject
import gtk

from xl.nls import gettext as _
logger = logging.getLogger(__name__)
from xl import xdg, common, event, metadata, settings, playlist as _xpl
try:
    import gtk.glade
    gtk.glade.textdomain('exaile')
    if xdg.local_hack:
        gtk.glade.bindtextdomain('exaile', os.path.join(xdg.exaile_dir, 'po'))
except ImportError:
    logger.warning(
        "Failed to import gtk.glade, interface "
        "will not be fully translated.")

from xlgui import commondialogs, cover
from xlgui import devices, guiutil, icons, prefs, queue


###
# Set up xl/event to work with the gtk event loop
logger.info("Setting up deferred idle manager function...")
event.IDLE_MANAGER._call_function = guiutil.idle_add()(
    event.IDLE_MANAGER._call_function)

def mainloop():
    gtk.main()

def controller():
    return Main._main

class Main(object):
    """
        This is the main gui controller for exaile
    """
    _main = None
    def __init__(self, exaile):
        """
            Initializes the GUI

            @param exaile: The Exaile instance
        """
        from xlgui import main, panel, tray, progress
        from xlgui.panel import collection, radio, playlists, files

        self.exaile = exaile
        self.first_removed = False
        self.tray_icon = None
        self.panels = {}
        self.builder = gtk.Builder()
        self.builder.add_from_file(xdg.get_data_path("ui/main.ui"))
        self.progress_box = self.builder.get_object('progress_box')
        self.progress_manager = progress.ProgressManager(self.progress_box)

        self.icons = icons.IconManager()
        self.icons.add_icon_name_from_directory('exaile',
            xdg.get_data_path('images'))
        gtk.window_set_default_icon_name('exaile')
        self.icons.add_icon_name_from_directory('exaile-pause',
            xdg.get_data_path('images'))
        self.icons.add_icon_name_from_directory('exaile-play',
            xdg.get_data_path('images'))

        for name in ('dynamic', 'repeat', 'shuffle'):
            icon_name = 'media-playlist-%s' % name
            self.icons.add_icon_name_from_directory(icon_name,
                xdg.get_data_path('images'))

        logger.info("Loading main window...")
        self.main = main.MainWindow(self, self.builder,
            exaile.collection, exaile.player, exaile.queue, exaile.covers)
        self.panel_notebook = self.builder.get_object('panel_notebook')
        self.play_toolbar = self.builder.get_object('play_toolbar')

        logger.info("Loading panels...")
        self.panels['collection'] = collection.CollectionPanel(self.main.window,
            exaile.collection, _show_collection_empty_message=True)
        self.panels['radio'] = radio.RadioPanel(self.main.window, exaile.collection,
            exaile.radio, exaile.stations)
        self.panels['playlists'] = playlists.PlaylistsPanel(self.main.window,
            exaile.playlists, exaile.smart_playlists, exaile.collection)
        self.panels['files'] = files.FilesPanel(self.main.window, exaile.collection)

        for panel in ('collection', 'radio', 'playlists', 'files'):
            self.add_panel(*self.panels[panel].get_panel())

        # add the device panels
        for device in self.exaile.devices.list_devices():
            if device.connected:
                self.add_device_panel(None, None, device)

        logger.info("Connecting panel events...")
        self.main._connect_panel_events()

        if settings.get_option('gui/use_tray', False):
            self.tray_icon = tray.TrayIcon(self.main)

        self.device_panels = {}
        event.add_callback(self.add_device_panel, 'device_connected')
        event.add_callback(self.remove_device_panel, 'device_disconnected')
        event.add_callback(self.on_gui_loaded, 'gui_loaded')

        logger.info("Done loading main window...")
        Main._main = self

    def export_current_playlist(self, *e):
        pl = self.main.get_current_playlist ().playlist
        name = pl.get_name() + ".m3u"

        dialog = commondialogs.FileOperationDialog(_("Export current playlist..."),
            None, gtk.FILE_CHOOSER_ACTION_SAVE,
            buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
            gtk.STOCK_SAVE, gtk.RESPONSE_OK))

        extensions = { 'm3u' : _('M3U Playlist'),
                       'pls' : _('PLS Playlist'),
                       'asx' : _('ASX Playlist'),
                       'xspf' : _('XSPF Playlist') }

        dialog.add_extensions(extensions)
        dialog.set_current_name (name)

        result = dialog.run()
        if result == gtk.RESPONSE_OK:
            path = unicode(dialog.get_filename(), 'utf-8')
            try:
                _xpl.export_playlist(pl, path)
            except _xpl.InvalidPlaylistTypeException:
                path = path + ".m3u"
                try:
                    _xpl.export_playlist(pl, path)
                except _xpl.InvalidPlaylistTypeException:
                    commondialogs.error(None, _('Invalid file extension, file not saved'))
        dialog.destroy()

    def open_url(self, *e):
        """
            Displays a dialog to open a url
        """
        dialog = commondialogs.TextEntryDialog(_('Enter the URL to open'),
        _('Open URL'))
        dialog.set_transient_for(self.main.window)
        dialog.set_position(gtk.WIN_POS_CENTER_ON_PARENT)

        result = dialog.run()
        dialog.hide()
        if result == gtk.RESPONSE_OK:
            url = dialog.get_value()
            self.open_uri(url, play=False)

    def open_dialog(self, *e):
        """
            Shows a dialog for opening playlists and tracks
        """
        dialog = gtk.FileChooserDialog(_("Choose a file to open"),
            self.main.window, buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_position(gtk.WIN_POS_CENTER_ON_PARENT)
        dialog.set_local_only(False) # enable gio
        dialog.set_select_multiple(True)

        supported_file_filter = gtk.FileFilter()
        supported_file_filter.set_name(_("Supported Files"))
        audio_file_filter = gtk.FileFilter()
        audio_file_filter.set_name(_("Music Files"))
        playlist_file_filter = gtk.FileFilter()
        playlist_file_filter.set_name(_("Playlist Files"))
        all_file_filter = gtk.FileFilter()
        all_file_filter.set_name(_("All Files"))

        for ext in metadata.formats.keys():
            supported_file_filter.add_pattern('*.' + ext)
            audio_file_filter.add_pattern('*.' + ext)

        playlist_file_types = ('m3u', 'pls', 'asx', 'xspf')
        for playlist_file_type in playlist_file_types:
            supported_file_filter.add_pattern('*.' + playlist_file_type)
            playlist_file_filter.add_pattern('*.' + playlist_file_type)

        all_file_filter.add_pattern('*')

        dialog.add_filter(supported_file_filter)
        dialog.add_filter(audio_file_filter)
        dialog.add_filter(playlist_file_filter)
        dialog.add_filter(all_file_filter)

        result = dialog.run()
        dialog.hide()
        if result == gtk.RESPONSE_OK:
            files = dialog.get_filenames()
            for f in files:
                self.open_uri(f, play=False)

    def open_dir(self, *e):
        """
            Shows a dialog for opening (multiple) directories
        """

        dialog = gtk.FileChooserDialog(_("Choose a file to open"),
            self.main.window, buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_position(gtk.WIN_POS_CENTER_ON_PARENT)
        dialog.set_action(gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER)
        dialog.set_select_multiple(True)

        result = dialog.run()
        dialog.hide()
        if result == gtk.RESPONSE_OK:
            files = dialog.get_filenames()
            for file in files:
                self.open_uri(file, play=False)

    def open_uri(self, uri, play=True):
        """
            Proxy for _open_uri
        """
        self._open_uri(uri, play)

    def _open_uri(self, uri, play=True):
        """
            Determines the type of a uri, imports it into a playlist, and
            starts playing it
        """
        from xl import playlist, trax
        if playlist.is_valid_playlist(uri):
            pl = playlist.import_playlist(uri)
            self.main.add_playlist(pl)
            if play:
                self.exaile.queue.play()
        else:
            pl = self.main.get_selected_playlist()
            column, descending = pl.get_sort_by()

            tracks = trax.get_tracks_from_uri(uri)
            tracks = trax.sort_tracks(pl.return_order_tags(column), tracks)

            try:
                pl.playlist.add_tracks(tracks)
                pl.playlist.set_current_pos(len(pl.playlist) - len(tracks))
                self.exaile.queue.play()
            # Catch empty directories
            except IndexError:
                pass

    def show_cover_manager(self, *e):
        """
            Shows the cover manager
        """
        window = cover.CoverManager(self.main.window, self.exaile.covers,
            self.exaile.collection)

    def show_preferences(self):
        """
            Shows the preferences dialog
        """
        dialog = prefs.PreferencesDialog(self.main.window, self)
        dialog.run()

    def show_devices(self):
        dialog = devices.ManagerDialog(self.main.window, self)
        dialog.run()

    def queue_manager(self, *e):
        dialog = queue.QueueManager(self.main.window, self.exaile.queue)
        dialog.run()

    def collection_manager(self, *e):
        """
            Invokes the collection manager dialog
        """
        from xl import collection
        from xlgui import collection as guicol
        dialog = guicol.CollectionManagerDialog(self.main.window,
            self, self.exaile.collection)
        result = dialog.run()
        dialog.dialog.hide()
        if result == gtk.RESPONSE_APPLY:
            items = dialog.get_items()
            dialog.destroy()

            coll = self.exaile.collection
            coll.freeze_libraries()

            for item in items:
                if not item in coll.libraries:
                    coll.add_library(collection.Library(item))

            remove = []
            for k, library in coll.libraries.iteritems():
                if not k in items:
                    remove.append(library)
            map(coll.remove_library, remove)

            coll.thaw_libraries()
            self.on_rescan_collection()

    def on_gui_loaded(self, event, object, nothing):
        """
            Allows plugins to be the last selected panel
        """
        try:
            last_selected_panel = settings.get_option(
                'gui/last_selected_panel', 'collection')
            panel = self.panels[last_selected_panel]._child
            panel_num = self.panel_notebook.page_num(panel)
            self.panel_notebook.set_current_page(panel_num)
            # Fix track info not displaying properly when resuming after a restart.
            self.main._update_track_information()
        except KeyError:
            pass

    def on_goto_playing_track(self, *e):
        track = self.exaile.queue.get_current()
        pl = self.main.get_current_playlist()
        if track in pl.playlist:
            index = pl.playlist.index(track)
            pl.list.scroll_to_cell(index)
            pl.list.set_cursor(index)
        #TODO implement a way to browse through all playlists and search for the track

    def on_rescan_collection(self, *e):
        """
            Called when the user wishes to rescan the collection
        """
        if not self.exaile.collection._scanning:
            from xlgui.collection import CollectionScanThread
            thread = CollectionScanThread(self, self.exaile.collection,
                    self.panels['collection'])
            self.progress_manager.add_monitor(thread,
                _("Scanning collection..."), 'gtk-refresh')

    def on_randomize_playlist(self, *e):
        pl = self.main.get_selected_playlist()
        pl.playlist.randomize()
        pl._set_tracks(pl.playlist.get_tracks())
        pl.reorder_songs()

    def on_track_properties(self, *e):
        pl = self.main.get_selected_playlist()
        if not pl.properties_dialog():
            if self.exaile.player.current:
                from xlgui import properties
                dialog = properties.TrackPropertiesDialog(self.main.window,
                        [self.exaile.player.current])
                dialog.hide()


    def add_panel(self, child, name):
        """
            Adds a panel to the panel notebook
        """
        label = gtk.Label(name)
        text = ''
        for i in unicode(label.get_text())[:-1]:
            text += (i + '\n')
        text += unicode(label.get_text())[-1]
        label.set_text(text)
        self.panel_notebook.append_page(child, label)

        if not self.first_removed:
            self.first_removed = True

            # the first tab in the panel is a stub that just stops libglade from
            # complaining
            # TODO: Check if this is valid for GtkBuilder
            self.panel_notebook.remove_page(0)

    def remove_panel(self, child):
        for n in range(self.panel_notebook.get_n_pages()):
            if child == self.panel_notebook.get_nth_page(n):
                self.panel_notebook.remove_page(n)
                return
        raise ValueError("No such panel")

    def on_panel_switch(self, notebook, page, pagenum):
        """
            Saves the currently selected panel
        """
        if self.exaile.loading:
            return

        page = notebook.get_nth_page(pagenum)
        for i, panel in self.panels.items():
            if panel._child == page:
                settings.set_option('gui/last_selected_panel', i)
                return

    def show_about_dialog(self, *e):
        """
            Displays the about dialog
        """
        import xl.main as xlmain
        builder = gtk.Builder()
        builder.add_from_file(xdg.get_data_path('ui/about_dialog.ui'))
        dialog = builder.get_object('AboutDialog')
        logo = gtk.gdk.pixbuf_new_from_file(
            xdg.get_data_path('images/exailelogo.png'))
        dialog.set_logo(logo)
        dialog.set_program_name('Exaile')
        dialog.set_version("\n" + str(xlmain.__version__))
        dialog.set_transient_for(self.main.window)
        dialog.connect('response', lambda d, r: d.destroy())
        dialog.show()

    def quit(self):
        """
            Quits the gui, saving anything that needs to be saved
        """

        # save open tabs
        self.main.save_current_tabs()

    @guiutil.idle_add()
    def add_device_panel(self, type, obj, device):
        from xlgui.panel.device import DevicePanel, FlatPlaylistDevicePanel
        import xlgui.panel

        paneltype = DevicePanel
        if hasattr(device, 'panel_type'):
            if device.panel_type == 'flatplaylist':
                paneltype = FlatPlaylistDevicePanel
            elif issubclass(device.panel_type, xlgui.panel.Panel):
                paneltype = device.panel_type

        panel = paneltype(self.main.window, self.main,
            device, device.get_name())

        sort = True
        panel.connect('append-items', lambda panel, items, sort=sort:
            self.main.on_append_items(items, sort=sort))
        panel.connect('queue-items', lambda panel, items, sort=sort:
            self.main.on_append_items(items, queue=True, sort=sort))

        self.device_panels[device.get_name()] = panel
        gobject.idle_add(self.add_panel, *panel.get_panel())
        thread = collection.CollectionScanThread(self.main,
                device.get_collection(), panel)
        self.progress_manager.add_monitor(thread,
                _("Scanning %s..."%device.name), 'gtk-refresh')

    @guiutil.idle_add()
    def remove_device_panel(self, type, obj, device):
        try:
            self.remove_panel(
                    self.device_panels[device.get_name()].get_panel()[0])
        except ValueError:
            logger.debug("Couldn't remove panel for %s"%device.get_name())
        del self.device_panels[device.get_name()]

def show_splash(show=True):
    """
        Show a splash screen

        @param show: [bool] show the splash screen
    """
    if not show: return
    image = gtk.Image()
    image.set_from_file(xdg.get_data_path("images/splash.png"))
    builder = gtk.Builder()
    builder.add_from_file(xdg.get_data_path("ui/splash.ui"))
    splash_screen = builder.get_object('SplashScreen')
    box = builder.get_object('splash_box')
    box.pack_start(image, True, True)
    splash_screen.set_transient_for(None)
    splash_screen.show_all()

    #ensure that the splash gets completely drawn before we move on
    while gtk.events_pending():
        gtk.main_iteration()

    return splash_screen
