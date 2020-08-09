"""Implementation allowing wxPython to use async functions via trio.
   This is done by running trio in guest mode under wxPython.  The
   details are handled by wxtrio.App, a repleacement for wx.App
   
   Additionally, async window even handlers can now be bound to window
   events.  All of this is handled automatically via monkey patching the
   wx.Window.Bind method.  Finally, helper async functions are provided:
    - AsyncBind: a version of wx.Bind that accepts async functions for callbacks
    - DynamicBind: a version of wx.Bind that automatically determines whether to use the
          syncronous or asyncronous version.  This is what wx.Window.Bind has been replace with.
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

# Commonly used trio core functionality
from trio import sleep


__all__ = [
    'sleep',
    'App', 'Bind', 'AsyncBind', 'StartCoroutine', 'AsyncShowDialog',
]


# Save original Bind method
_BindSync = wx.Window.Bind


SPAWN_TIMEOUT = 1   # Timeout to wait for a nursery when spawning a child task


@attr.s(slots=True)
class WindowBindingData:
    event_handlers = attr.ib(default=defaultdict(list)) # List of event handlers this window is async subscribed to
    nursery = attr.ib(default=None) # nursury for launching tasks/event handler instances for this window
    nursery_queued = attr.ib(default=False) # `True` when this window's nursury has be queued for creation

    async def wait_nursery(self):
        """Wait until this windows's nursury object is created."""
        while self.nursery is None:
            await trio.sleep(0.1)


class App(wx.App):
    def __init__(self, *args, done_callback=None, **kwargs):
        # TODO: implement non-guest mode option?
        self.nursery = None
        self.done_callback = done_callback
        self.window_bindings = defaultdict(WindowBindingData)
        self.pending_tasks = []
        self._patch_window()
        super().__init__(*args, **kwargs)

    def _patch_window(self):
        """Monkey patch wx.Window to have our desired version of methods.
               wx.Window.Bind -> wxtrio.App.DynamicBind
               wx.Window.StartCoroutine -> added method
        """
        if wx.Window.Bind == _BindSync:
            def _bind(wx_window, wx_event_binder, handler, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
                self.DynamicBind(wx_window, wx_event_binder, handler, source, id, id2)
            wx.Window.Bind = _bind
            def _start(wx_window, coroutine):
                self.StartCoroutine(wx_window, coroutine)
            wx.Window.StartCoroutine = _start
        else:
            raise Warning('Attempt to patch wx.Window.Bind multiple times.')

    def ExitMainLoop(self):
        """Called internally by wxPython to signal that the main application should exit."""
        self.nursery.cancel_scope.cancel()
        super().ExitMainLoop()

    def OnInit(self):
        """Called by wxPython once it is in a state ready to begin the main loop."""
        # Start trio in guest mode
        if self.done_callback:
            done_callback = self.done_callback
        else:
            def done_callback(trio_outcome):
                pass
        trio.lowlevel.start_guest_run(
            self._trio_main,
            run_sync_soon_threadsafe=wx.CallAfter,
            done_callback=done_callback,
        )
        return True

    async def _trio_main(self):
        """Main async function to run under trio.  Simply creates
           needed nursuries to house upcoming async tasks, then
           waits forever, until cancelled when the wx.App gets
           shutdown.
        """
        async with trio.open_nursery() as nursery:
            # 1) Create the main trio nursery
            self.nursery = nursery
            # 2) Create nurseries for each window for long running tasks
            for wx_window in self.window_bindings:
                self.nursery.start_soon(self._build_nursery, wx_window)
            # 3) Start any pending tasks
            for wx_window, coroutine in self.pending_tasks:
                self.nursery.start_soon(self._spawn_into, wx_window, coroutine)
            self.pending_tasks = None
            # Wait until the application exits
            await trio.sleep_forever()

    def DynamicBind(self, wx_window, wx_event_binder, handler, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
        """Binds an event handler to a wx Event callback.  Automatically detects if the handler
           is async or sync, and bind appropriately.
        """
        if inspect.iscoroutinefunction(handler):
            # 'async def' function that has not been called
            self._queue_cleanup(wx_window)
            self.window_bindings[wx_window].event_handlers[wx_event_binder.typeId].append(handler)
            _BindSync(wx_window, wx_event_binder, lambda event: self.OnEvent(event, wx_window, wx_event_binder.typeId), source=source, id=id, id2=id2)
        elif inspect.iscoroutine(handler):
            # 'async def' function that has been called and returned an awaitable object
            raise TypeError('Event handler is an awaitable object returned from an async function.  Do not call the async function.')
        elif inspect.isawaitable(handler):
            # Some other awaitable object
            raise TypeError('Event handler is an awaitable object.  Pass either a function or an async function.')
        elif not callable(handler):
            raise TypeError('Event handler is not callable.')
        else:
            # Sync event handler
            _BindSync(wx_window, wx_event_binder, handler, source=source, id=id, id2=id2)

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
        _BindSync(wx_window, wx_event_binder, lambda event: self.OnEvent(event, wx_window, wx_event_binder.typeId), source=source, id=id, id2=id2)

    def _queue_cleanup(self, wx_window):
        """By 'cleanup' we mean: establish a nursery for this window that can then be
           cancelled when the window gets destroyed, cleaning up any associated tasks.
        """
        bindings = self.window_bindings[wx_window]
        # If a nursery does not yet exist for this window:
        #  - If there is a main nursery, launch a task to create a sub-nursery for our window
        #  - If not, wait until the main loops starts, nurseries will be created then
        if self.nursery is not None and bindings.nursery is None and not bindings.nursery_queued:
            bindings.nursery_queued = True
            self.nursery.start_soon(self._build_nursery, wx_window)

    async def _build_nursery(self, wx_window):
        """Create a nursery for managing tasks associated with the wx_window object."""
        if self.window_bindings[wx_window].nursery is not None:
            # Trying to create a nursery twice is a code smell
            raise Warning(f'Attempted to create nursery for {wx_window} multiple times.')
        async with trio.open_nursery() as nursery:
            bindings = self.window_bindings[wx_window]
            bindings.nursery = nursery
            _BindSync(wx_window, wx.EVT_WINDOW_DESTROY, lambda event: self.OnDestroy(event, wx_window), wx_window)
            await trio.sleep_forever()
        # We reach here when the nursery has been cancelled, ie: when OnDestroy is
        # triggered for this window.
        del self.window_bindings[wx_window]

    async def _spawn_into(self, wx_window, coroutine):
        """Spawn the child coroutine into the nursery associated with the wx_window."""
        with trio.move_on_after(SPAWN_TIMEOUT) as cancel_scope:
            # The wx_window's nursery may not be set up fully yet,
            # _build_nursery was queued in trio, but might not have begun to run,
            # so wait up to 10 seconds.
            await self.window_bindings[wx_window].wait_nursery()
        if cancel_scope.cancelled_caught:
            raise RuntimeError(f'Failed to nursery for {wx_window}, timed out.')
        self.window_bindings[wx_window].nursery.start_soon(coroutine)


    def StartCoroutine(self, wx_window, coroutine):
        """Start a coroutine, and attach its lifetime to the wx_window."""
        self._queue_cleanup(wx_window)
        if self.nursery is None:
            # No nursery yet, stash this task for later
            self.pending_tasks.append((wx_window, coroutine))
        else:
            # Spawn it
            self.nursery.start_soon(self._spawn_into, wx_window, coroutine)

    def OnEvent(self, wx_event, wx_window, wx_event_id):
        """An event occurred for something we've bound to an async event
           handler.
        """
        bindings = self.window_bindings[wx_window]
        for async_handler in bindings.event_handlers[wx_event_id]:
            bindings.nursery.start_soon(async_handler, wx_event.Clone())

    def OnDestroy(self, wx_event, wx_window):
        """Clean up any running tasks associated with the window
           that is being destroyed.
        """
        if wx_window in self.window_bindings:
            self.window_bindings[wx_window].nursery.cancel_scope.cancel()
        wx_event.Skip()


def Bind(wx_window, wx_event, handler, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
    '''Initial Bind call.  Get the wx.App instance and verify it is a wxt.App
       instance.
    '''
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    app.DynamicBind(wx_window, wx_event, handler, source, id, id2)


def AsyncBind(wx_event, async_callback, wx_window, source=None, id=wx.ID_ANY, id2=wx.ID_ANY):
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    app.AsyncBind(wx_event, async_callback, wx_window, source, id, id2)


def StartCoroutine(wx_window, coroutine):
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    app.StartCoroutine(wx_window, coroutine)


async def AsyncShowDialog(dialog):
    """Shows a non-modal wx Dialog asyncronously"""
    closed = trio.Event()
    def end_dialog(return_code):
        dialog.SetReturnCode(return_code)
        dialog.Hide()
        closed.set()
    def on_button(event):
        # Logic copied from src/common/dlgcmn.cpp:
        # wxDialogBase::OnButton, EndDialog, and AcceptAndClose
        event_id = event.GetId()
        if event_id == dialog.GetAffirmativeId():
            if dialog.Validate() and dialog.TransferDataFromWindow():
                end_dialog(event_id)
        elif event_id == wx.ID_APPLY:
            if dialog.Validate():
                dialog.TransferDataFromWindow()
        elif event_id == dialog.GetEscapeId() or (event_id == wx.ID_CANCEL and dialog.GetEscapeId() == wx.ID_ANY):
            end_dialog(wx.ID_CANCEL)
        else:
            event.Skip()
    def on_close(event):
        closed.set()
        dialog.Hide()
    dialog.Bind(wx.EVT_CLOSE, on_close)
    dialog.Bind(wx.EVT_BUTTON, on_button)
    dialog.Show()
    await closed.wait()
    return dialog.GetReturnCode()
