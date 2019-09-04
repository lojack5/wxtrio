"""Initial implementation of wxPython's event loop on trio.
   This entry point is a drop in replacement for wx.App: wxtrio.App
   
   The constructor for App optionally accepts a nursery from trio,
   to allow for the user to start their own trio context, and run
   wxPython until a nursery they specify.  If no nursery is provided,
   then App will start trio upon execution of MainLoop.
   
   In either case, MainLoop is the main entry point into the wxPython
   application, just as in synchronous wxPython.
   
   Finally, helper async functions are provided:
    - AsyncBind: a version of wx.Bind that accepts async functions for callbacks
    - AsyncShowDialog: a version of wx.ShowDialog that processes a non-modal dialog asynchronously
    - StartCoroutine: launches a async function, tieing it to a wx window object.
    In the case of both AsyncBind and StartCoroutine, the async task will automatically be
    cancelled in the event that the window object is destroyed.
"""
import inspect
from collections import defaultdict

import wx
import trio
import attr


# TODO: deal with MacOS?
WX_TRIO_WAIT_INTERVAL = 0.005


# Commonly used trio core functionality
from trio import sleep


@attr.s(slots=True)
class WindowBindingData:
    # List of events this window is subscribed to asyncronously
    event_handlers = attr.ib(default=defaultdict(list))
    # Nursery for launching new tasks/event handlers for this window
    nursery = attr.ib(default=None)
    # Set to true when the nursery object creation has be queued
    nursery_queued = attr.ib(default=False)

    # Wait until the nursery object has been created
    async def wait_nursery(self):
        while self.nursery is None:
            await trio.sleep(0.1)


class App(wx.App):
    def __init__(self, nursery=None, *args, **kwargs):
        # nursery the App is running under.  Used to launch non-window specific tasks
        self.nursery = nursery
        # event handler and task data for each window object
        self.window_bindings = defaultdict(WindowBindingData)
        # tasks that are queued to be launched, but could not be because trio was
        # not yet started at the time
        self.pending_tasks = []
        super(App, self).__init__(*args, **kwargs)

    def MainLoop(self, bootstrap_trio=True):
        """Start the main wx app event loop asyncronously.  If boostrap_trio
           is true and no nursery was provided in the constructor, this will
           start trio's main task as well.
        """
        if self.nursery is None and bootstrap_trio:
            trio.run(self._trio_main)
        else:
            self.nursery.start_soon(self._main_loop)

    def OnInit(self):
        # This is supposed to be defaulted to True already, but
        # for some reason is False without this call
        self.SetExitOnFrameDelete(True)
        return True

    async def _trio_main(self):
        # Start trio, then begin the app's main loop
        async with trio.open_nursery() as nursery:
            self.nursery = nursery
            nursery.start_soon(self._main_loop)

    async def _main_loop(self):
        """Asyncronous version of the wx event loop."""
        # 1) Create any nurseries needed to track tasks associated with wx_windows
        for wx_window in self.window_bindings:
            if self.window_bindings[wx_window].nursery is None:
                self.nursery.start_soon(self._build_nursery, wx_window)
        # 2) Start any pending coroutines
        for wx_window, coroutine in self.pending_tasks:
            self.nursery.start_soon(self._spawn_into, wx_window, coroutine)
        self.pending_tasks = None # Set to none, so any further appends will raise an exception
        # 3) Execute wx main event loop
        wx_loop = wx.GUIEventLoop()
        with wx.EventLoopActivator(wx_loop):
            while True:
                while wx_loop.Pending():
                    wx_loop.Dispatch()
                # yield to other trio tasks
                await trio.sleep(WX_TRIO_WAIT_INTERVAL)
                self.ProcessPendingEvents()
                wx_loop.ProcessIdle()

    def AsyncBind(self, wx_event_binder, async_handler, wx_window, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
        """Bind a coroutine as a wx Event callback on the given wx_window.
           Note: when wx_window is destroyed, any running corouting bount to it will be cancelled.
           wx_event: One of the wx.EVT_* event binding objects
           async_handler: Your async function that will handle the event
           source: Part of the wx event handling system (for distinguising between events of the same
                   type, but originating from different source child windows
           id: Used to specify the source of the event by ID, instead of instance
           id2: Used (when needed) to specify a range of IDs, for example with EVT_MENU_RANGE.
        """
        if not inspect.iscoroutinefunction(async_handler):
            raise TypeError('async_handler must be a async function or coroutine.')
        self._queue_cleanup(wx_window)
        self.window_bindings[wx_window].event_handlers[wx_event_binder.typeId].append(async_handler)
        wx_window.Bind(wx_event_binder, lambda event: self.OnEvent(event, wx_window, wx_event_binder.typeId), source=source, id=id, id2=id2)

    def _queue_cleanup(self, wx_window):
        bindings = self.window_bindings[wx_window]
        # If there is no main nursery yet, the remainder of this will be handled
        # in _main_loop upon startup
        if self.nursery is not None and bindings.nursery is None and not bindings.nursery_queued:
            # Need to create a nursery for this wx_window
            bindings.nursery_queued = True
            self.nursery.start_soon(self._build_nursery, wx_window)

    async def _build_nursery(self, wx_window):
        """Create a nursery for managing tasks associated with the wx_window object."""
        # Check to make sure this isnt already set up
        if self.window_bindings[wx_window].nursery is not None:
            print('Warning: attempt to recreate nursery for', wx_window)
            return
        async with trio.open_nursery() as nursery:
            bindings = self.window_bindings[wx_window]
            bindings.nursery = nursery
            wx_window.Bind(wx.EVT_WINDOW_DESTROY, lambda event: self.OnDestroy(event, wx_window), wx_window)
            await trio.sleep_forever()
        del self.window_bindings[wx_window]

    async def _spawn_into(self, wx_window, coroutine):
        """Spawn the child coroutine into the nursery associated with the wx_window."""
        with trio.move_on_after(10) as cancel_scope:
            # The wx_window's nursery may not be set up fully yet,
            # _build_nursery was queued in trio, but might not have begun to run,
            # so wait up to 10 seconds.
            await self.window_bindings[wx_window].wait_nursery()
        if cancel_scope.cancelled_caught:
            raise RuntimeError('Failed to create cancel scope, timed out.')
        self.window_bindings[wx_window].nursery.start_soon(coroutine)


    def StartCoroutine(self, coroutine, wx_window):
        """Start a coroutine, and attach its lifetime to the wx_window."""
        self._queue_cleanup(wx_window)
        if self.nursery is None:
            # No nursery yet, stash this task for later
            self.pending_tasks.append((wx_window, coroutine))
        else:
            # Spawn it
            self.nursery.start_soon(self._spawn_into, wx_window, coroutine)

    def OnEvent(self, wx_event, wx_window, wx_event_id):
        bindings = self.window_bindings[wx_window]
        for async_handler in bindings.event_handlers[wx_event_id]:
            bindings.nursery.start_soon(async_handler, wx_event.Clone())

    def OnDestroy(self, wx_event, wx_window):
        """Clean up any running tasks associated with the window
           that is being destroyed.
        """
        if wx_window in self.window_bindings:
            self.window_bindings[wx_window].nursery.cancel_scope.cancel()


def AsyncBind(wx_event, async_callback, wx_window, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    app.AsyncBind(wx_event, async_callback, wx_window, source=source, id=id, id2=id2)


def StartCoroutine(coroutine, wx_window):
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    app.StartCoroutine(coroutine, wx_window)


async def AsyncShowDialog(dialog):
    """Shows a non-modal wx Dialog asyncronously"""
    closed = trio.Event()
    def end_dialog(return_code):
        dialog.SetReturnCode(return_code)
        dialog.Hide()
        closed.set()
    async def on_button(event):
        # Logic copied from src/common/dlgcmn.cpp:
        # wxDialogBase::OnButton, EndDialog, and AcceptAndClose
        event_id = event.GetId()
        if event_id == dialog.GetAffirmativeId():
            if dialog.Validate() and dialog.TransferDataFromWindow():
                end_dialog(event_id)
        elif event_id == wx.ID_APPLY:
            if dialog.Validate():
                dialog.TransferDataFromWindow()
        elif event_id == dialog.GetEscapeId() or (id == wx.ID_CANCEL and dialog.GetEscapeId() == wx.ID_ANY):
            end_dialog(wx.ID_CANCEL)
        else:
            event.Skip()
    async def on_close(event):
        closed.set()
        dialog.Hide()
    AsyncBind(wx.EVT_CLOSE, on_close, dialog)
    AsyncBind(wx.EVT_BUTTON, on_button, dialog)
    dialog.Show()
    await closed.wait()
    return dialog.GetReturnCode()
