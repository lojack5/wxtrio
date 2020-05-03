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
import wxtrio as wxt


class TestFrame(wx.Frame):
    def __init__(self, parent=None):
        super(TestFrame, self).__init__(parent)

        # Widgets
        panel = wx.Panel(self)
        self.button =  wx.Button(panel, label="Submit")
        self.edit =  wx.StaticText(panel, style=wx.ALIGN_CENTRE_HORIZONTAL|wx.ST_NO_AUTORESIZE)
        self.edit_timer =  wx.StaticText(panel, style=wx.ALIGN_CENTRE_HORIZONTAL|wx.ST_NO_AUTORESIZE)

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(self.button, 1, wx.ALL|wx.ALIGN_CENTER, 5)
        vbox.AddStretchSpacer(1)
        vbox.Add(self.edit, 1, wx.EXPAND|wx.ALL, 5)
        vbox.Add(self.edit_timer, 1, wx.EXPAND|wx.ALL, 5)
        panel.SetSizer(vbox)
        panel.Layout()

        sizer = wx.BoxSizer()
        sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sizer)
        self.CenterOnScreen(wx.BOTH)

        # Events and long running tasks
        wxt.AsyncBind(wx.EVT_BUTTON, self.OnButton, self.button)
        wxt.StartCoroutine(self.update_clock, self)

    async def OnButton(self, event):
        self.button.Disable()
        self.edit.SetLabel("Button clicked")
        await wxt.sleep(1)
        self.edit.SetLabel("Working")
        await wxt.sleep(1)
        with wx.TextEntryDialog(self, 'Please enter something: ') as dialog:
            result = await wxt.AsyncShowDialog(dialog)
            result = '%s - %s' % (result, dialog.GetValue())
        self.edit.SetLabel('Result: %s' % result)
        self.button.Enable()

    async def update_clock(self):
        while True:
            self.edit_timer.SetLabel(time.strftime('%H:%M:%S'))
            await wxt.sleep(0.5)


def main():
    app = wxt.App()
    frame = TestFrame()
    frame.Show()
    app.SetTopWindow(frame)
    app.MainLoop()


if __name__=='__main__':
    main()
