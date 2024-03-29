"""Another simple wxtrio example.  This one showcases the effect
   of navigating menus on cooperative multitasking: with a menu open and the
   mouse outside of the window frame, trio tasks do not run.
"""
import time
from typing import NoReturn

import wx

import wxtrio as wxt
from wxtrio import Bind, StartCoroutine


class TestFrame(wx.Frame):
    def __init__(self, parent: wx.Window | None = None):
        super().__init__(parent)

        # Widgets
        panel = wx.Panel(self)
        self.edit_timer = wx.StaticText(
            panel, style=wx.ALIGN_CENTRE_HORIZONTAL | wx.ST_NO_AUTORESIZE
        )

        # Menu, statusbar
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        file_menu.Append(wx.ID_EXIT, 'E&xit', 'Quit application')
        menubar.Append(file_menu, '&File')
        self.SetMenuBar(menubar)
        self.CreateStatusBar()

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(self.edit_timer, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(vbox)
        panel.Layout()

        sizer = wx.BoxSizer()
        sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.CenterOnScreen(wx.BOTH)

        # Events and long running tasks
        StartCoroutine(self, self.update_clock)
        Bind(self, wx.EVT_MENU, lambda event: self.Close())

    async def update_clock(self) -> NoReturn:
        while True:
            self.edit_timer.Label = time.strftime('%H:%M:%S')
            await wxt.sleep(0.5)


def main():
    app = wxt.App()
    frame = TestFrame()
    frame.Show()
    app.SetTopWindow(frame)
    app.MainLoop()


if __name__ == '__main__':
    main()
