"""Very simply wxPython application, using wxtrio.  Features a static
   text control with the current time, demonstrating the coroutine running
   while other GUI and processing async functions are running.

   This also illustrates one limitation of this approach, click to resize
   the window and drag, but don't release.  Or, click to move the window
   and drag, but again don't release.  The GUI task holds control of the
   thread, preventing any other tasks from executing.
"""
import time

import wx
from wx import xrc
import wxtrio as wxt


class disable:
    """Temporarily disable a wxWidget."""
    def __init__(self, window):
        self._window = window

    def __enter__(self):
        self._window.Disable()

    def __exit__(self, *args, **kwargs):
        self._window.Enable()


class TestFrame(wx.Frame):
    def __init__(self, parent=None):
        # initialize via XRC
        super(TestFrame, self).__init__()
        xrc.XmlResource.Get().LoadFrame(self, parent, 'TestFrame')
        # Bind events, start long tasks
        xrc.XRCCTRL(self, 'button').Bind(wx.EVT_BUTTON, self.OnButton)
        wxt.StartCoroutine(self.update_clock, self)

    async def update_clock(self):
        label = xrc.XRCCTRL(self, 'edit_timer')
        while True:
            label.SetLabel(time.strftime('%H:%M:%S'))
            await wxt.sleep(0.5)

    async def OnButton(self, event):
        # Show a text entry dialog after 2 seconds, update
        # a text control with status
        edit = xrc.XRCCTRL(self, 'edit')
        with disable(xrc.XRCCTRL(self, 'button')):
            edit.SetLabel("Button clicked")
            await wxt.sleep(1)
            edit.SetLabel("Working")
            await wxt.sleep(1)
            with wx.TextEntryDialog(self, 'Please enter something: ') as dialog:
                result = await wxt.AsyncShowDialog(dialog)
                result = '%s - %s' % (result, dialog.GetValue())
            edit.SetLabel('Result: %s' % result)


def main():
    app = wxt.App()
    xrc.XmlResource.Get().Load('xrc_example.xrc')
    frame = TestFrame()
    frame.Show()
    app.SetTopWindow(frame)
    app.MainLoop()


if __name__=='__main__':
    main()
