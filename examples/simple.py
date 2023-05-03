"""Very simply wxPython application, using wxtrio.  Features a static
   text control with the current time, demonstrating the coroutine running
   while other GUI and processing async functions are running.
"""
import time
from typing import NoReturn

import wx
from utils import disable

import wxtrio as wxt
from wxtrio import Bind, StartCoroutine


class TestFrame(wx.Frame):
    def __init__(self, parent: wx.Window | None = None) -> None:
        super().__init__(parent)

        # Widgets
        panel = wx.Panel(self)
        self.button = wx.Button(panel, label='Submit')
        self.edit = wx.StaticText(
            panel, style=wx.ALIGN_CENTRE_HORIZONTAL | wx.ST_NO_AUTORESIZE
        )
        self.edit_timer = wx.StaticText(
            panel, style=wx.ALIGN_CENTRE_HORIZONTAL | wx.ST_NO_AUTORESIZE
        )

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(self.button, 1, wx.ALL | wx.ALIGN_CENTER, 5)
        vbox.AddStretchSpacer(1)
        vbox.Add(self.edit, 1, wx.EXPAND | wx.ALL, 5)
        vbox.Add(self.edit_timer, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(vbox)
        panel.Layout()

        sizer = wx.BoxSizer()
        sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.CenterOnScreen(wx.BOTH)

        # Events and long running tasks
        Bind(self, wx.EVT_BUTTON, self.OnButton, self.button)
        StartCoroutine(self, self.update_clock)

    async def OnButton(self, event: wx.Event) -> None:
        with disable(self.button):
            self.edit.Label = 'Button clicked'
            await wxt.sleep(1)  # or trio.sleep(1) or anyio.sleep(1)
            self.edit.Label = 'Working'
            await wxt.sleep(1)
            dialog = wx.TextEntryDialog(self, 'Please enter something: ')
            result = await wxt.AsyncShowDialog(dialog)
            result = f'{result} - {dialog.GetValue()}'
            self.edit.Label = f'Result: {result}'

    async def update_clock(self) -> NoReturn:
        while True:
            self.edit_timer.SetLabel(time.strftime('%H:%M:%S'))
            await wxt.sleep(0.5)


def main():
    app = wxt.App()
    frame = TestFrame()
    frame.Show()
    app.SetTopWindow(frame)
    app.MainLoop()


if __name__ == '__main__':
    main()
