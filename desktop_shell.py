"""Native Windows folder picker and IDM-style background notification-area lifecycle."""
from __future__ import annotations
from pathlib import Path
import threading

class FolderApi:
    def __init__(self):
        # Private: pywebview must not recurse through Window.native accessibility objects.
        self._window = None

    def choose_folder(self, current=""):
        """Exposed to the HTML UI through pywebview's local JS API."""
        import webview
        folder = Path(current) if current else Path.home() / "Downloads"
        if not folder.is_dir():
            folder = Path.home() / "Downloads"
        # This dialog is an operating-system folder picker; it does not require typing.
        selection = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(folder), allow_multiple=False
        )
        if selection:
            return str(selection[0]) if isinstance(selection, (list, tuple)) else str(selection)
        return None


class TrayController:
    def __init__(self, window):
        self.window=window
        self.quitting=False
        self.icon=None
        self.lock=threading.RLock()

    @staticmethod
    def create_image():
        from PIL import Image, ImageDraw
        img=Image.new("RGBA", (64,64), (0,0,0,0))
        draw=ImageDraw.Draw(img)
        draw.rounded_rectangle((3,3,60,60), radius=14, fill=(27,103,157,255))
        draw.line([(31,13),(31,39)], fill=(236,255,255,255), width=7)
        draw.polygon([(17,33),(31,49),(46,33)], fill=(237,255,255,255))
        return img

    def start(self):
        import pystray
        menu=pystray.Menu(
            pystray.MenuItem("فتح مدير تحميل هيثم",self.show,default=True),
            pystray.MenuItem("إنهاء البرنامج بالكامل",self.exit)
        )
        self.icon=pystray.Icon("HaithamDownloader",self.create_image(),
                              "Haitham Downloader Classic",menu)
        # The Windows notification-area icon lives in its own event thread.
        self.icon.run_detached()
        self.window.events.closing += self.on_closing

    def on_closing(self, *args):
        if self.quitting:
            return True
        # X minimizes to the system notification area; HTTP/aria2 keep running.
        self.window.hide()
        return False

    def show(self, *args):
        try:
            self.window.show()
            self.window.restore()
            self.window.on_top=True
            self.window.on_top=False
        except Exception as exc:
            print("TRAY_RESTORE_ERROR",repr(exc),flush=True)

    def exit(self, *args):
        with self.lock:
            self.quitting=True
        try:
            self.window.destroy()
        except Exception as exc:
            print("TRAY_EXIT_ERROR",repr(exc),flush=True)
        self.stop()

    def stop(self):
        if self.icon is not None:
            try:self.icon.stop()
            except Exception:pass
            self.icon=None
