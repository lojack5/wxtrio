"""Very simply wxPython application, using wxtrio.  Features a static
   text control with the current time, demonstrating the coroutine running
   while other GUI and processing async functions are running.
"""
import time

import wx
import wxtrio as wxt
from wxtrio import StartCoroutine, Bind

from utils import XRCCtrl, XRCFrame, XRCApp, disable


class TestFrame(XRCFrame):
    label: wx.StaticText = XRCCtrl('edit_timer')
    edit: wx.StaticText = XRCCtrl()
    button: wx.Button = XRCCtrl()

    def __init__(self, parent: wx.Window | None = None):
        super().__init__(parent)
        # Bind events, start long tasks
        Bind(self.button, wx.EVT_BUTTON, self.OnButton)
        StartCoroutine(self, self.update_clock)

    async def update_clock(self) -> None:
        while True:
            self.label.Label = time.strftime('%H:%M:%S')
            await wxt.sleep(0.5)

    async def OnButton(self, event: wx.Event) -> None:
        # Show a text entry dialog after 2 seconds, update
        # a text control with status
        with disable(self.button):
            self.edit.Label = 'Button clicked'
            await wxt.sleep(1)
            self.edit.Label = 'Working'
            await wxt.sleep(1)
            dialog = wx.TextEntryDialog(self, 'Please enter something:')
            result = await wxt.AsyncShowDialog(dialog)
            result = f'{result} - {dialog.GetValue()}'
            self.edit.Label = f'Result: {result}'


def main():
    app = XRCApp(xrc_file='xrc_example.xrc')
    frame = TestFrame()
    frame.Show()
    app.SetTopWindow(frame)
    app.MainLoop()


if __name__ == '__main__':
    main()
