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
from functools import partial
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, ParamSpec, Generic, Awaitable, cast, TYPE_CHECKING

import wx
import trio

# Commonly used trio core functionality
from trio import sleep

P = ParamSpec('P')
T = TypeVar('T')
SyncHandler = Callable[[wx.Event], None]
AsyncHandler = Callable[[wx.Event], Awaitable[None]]
EventHandler = SyncHandler | AsyncHandler
AsyncMethod = Callable[P, Awaitable[T]]


__all__ = [
    'sleep',
    'App', 'Bind', 'AsyncBind', 'StartCoroutine', 'AsyncShowDialog',
]


class MethodPatch(Generic[P, T]):
    __slots__ = ('getter', 'setter', 'original_method', 'installed_method', )

    def __init__(self, cls: type, method_name: str, *, replacement_method: Callable[P, T] | None = None):
        self.getter = partial(getattr, cls, method_name, None)
        self.setter = partial(setattr, cls, method_name)
        self.original_method = self.getter()
        self.installed_method = None
        if replacement_method is not None:
            self.install(replacement_method)

    def install(self, replacement_method):
        current_method = self.getter()
        if current_method == self.original_method:
            # Original method currently in place, good to patch
            self.setter(replacement_method)
            self.installed_method = replacement_method
        elif current_method == self.installed_method:
            # Already installed, good to go
            pass
        else:
            raise Warning(f'Unknown replacement method for {self.original_method.__name__} in place, skipping install.')

    def uninstall(self):
        current_method = self.getter()
        if current_method == self.installed_method:
            self.setter(self.original_method)
            self.installed_method = None
        elif self.installed_method is not None:
            raise Warning(f'Unknown replacement method for {self.original_method.__name__} in place, cannot uninstall.')

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        """Call the original method."""
        return self.original_method(*args, **kwargs)


SPAWN_TIMEOUT = 1   # Timeout to wait for a nursery when spawning a child task


@dataclass(slots=True)
class WindowBindingData:
    event_handlers: defaultdict[int, list] = field(default_factory=lambda : defaultdict(list)) # List of event handlers this window is async subscribed to
    nursery: trio.Nursery | None = None # nursury for launching tasks/event handler instances for this window
    nursery_queued: bool = False # `True` when this window's nursury has be queued for creation

    async def wait_nursery(self) -> trio.Nursery:
        """Wait until this windows's nursury object is created."""
        while self.nursery is None:
            await trio.sleep(0.1)
        return self.nursery


class App(wx.App):
    def __init__(self, *args, **kwargs) -> None:
        # TODO: implement non-guest mode option?
        self.nursery: trio.Nursery | None = None
        self.window_bindings = defaultdict(WindowBindingData)
        self.pending_tasks: list = []
        self._patch_window_methods()
        super().__init__(*args, **kwargs)

    def _patch_window_methods(self) -> None:
        """Patch the wx.Window methods with our replacements."""
        self._BindSync = MethodPatch(wx.Window, 'Bind', replacement_method=Bind)
        self._StartCoroutinePatch = MethodPatch(wx.Window, 'StartCoroutine', replacement_method=StartCoroutine)
        self._CancelCoroutinePatch = MethodPatch(wx.Window, 'CancelCoroutines', replacement_method=self.CancelCoroutines)

    def _unpatch_window_methods(self) -> None:
        """Uninstall replacement methods from wx.Window."""
        self._BindSync.uninstall()
        self._StartCoroutinePatch.uninstall()
        self._CancelCoroutinePatch.uninstall()

    def ExitMainLoop(self) -> None:
        """Called internally by wxPython to signal that the main application should exit."""
        if self.nursery:
            self.nursery.cancel_scope.cancel()
        self._unpatch_window_methods()
        super().ExitMainLoop()

    def OnInit(self) -> bool:
        """Called by wxPython once it is in a state ready to begin the main loop."""
        # Start trio in guest mode
        trio.lowlevel.start_guest_run(
            self._trio_main,
            run_sync_soon_threadsafe=wx.CallAfter,
            done_callback=self._trio_done_callback,
        )
        return True

    def _trio_done_callback(self, trio_outcome) -> None:
        # Make sure wxPython shuts down,
        # As it may rely on some trio tasks that
        # have stopped
        wx.Exit()

    async def _trio_main(self) -> None:
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
            # Set `pending_tasks` to None to cause an exception if used later
            self.pending_tasks = None # type: ignore
            # Wait until the application exits
            await trio.sleep_forever()

    def DynamicBind(self, window: wx.Window, event_binder: wx.PyEventBinder, handler: EventHandler, source=None, id: int = wx.ID_ANY, id2: int = wx.ID_ANY) -> None:
        """Binds an event handler to a wx Event callback.  Automatically detects if the handler
           is async or sync, and bind appropriately.
        """
        if inspect.iscoroutinefunction(handler):
            # 'async def' function that has not been called
            self._queue_cleanup(window)
            self.window_bindings[window].event_handlers[event_binder.typeId].append(handler)
            self._BindSync(window, event_binder, lambda event: self.OnEvent(event, window, event_binder.typeId), source=source, id=id, id2=id2)
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
            handler = cast(SyncHandler, handler)
            self._BindSync(window, event_binder, handler, source=source, id=id, id2=id2)

    def AsyncBind(self, event_binder: wx.PyEventBinder, handler: AsyncHandler, window: wx.Window, source=None, id: int = wx.ID_ANY, id2: int = wx.ID_ANY) -> None:
        """Bind a coroutine as a wx Event callback on the given wx_window.
           Note: when wx_window is destroyed, any running corouting bount to it will be cancelled.
           wx_event: One of the wx.EVT_* event binding objects
           async_handler: Your async function that will handle the event
           source: Part of the wx event handling system (for distinguising between events of the same
                   type, but originating from different source child windows
           id: Used to specify the source of the event by ID, instead of instance
           id2: Used (when needed) to specify a range of IDs, for example with EVT_MENU_RANGE.
        """
        if not inspect.iscoroutinefunction(handler):
            raise TypeError('async_handler must be a async function or coroutine.')
        self._queue_cleanup(window)
        self.window_bindings[window].event_handlers[event_binder.typeId].append(handler)
        self._BindSync(window, event_binder, lambda event: self.OnEvent(event, window, event_binder.typeId), source=source, id=id, id2=id2)

    def _queue_cleanup(self, window: wx.Window) -> None:
        """By 'cleanup' we mean: establish a nursery for this window that can then be
           cancelled when the window gets destroyed, cleaning up any associated tasks.
        """
        bindings = self.window_bindings[window]
        # If a nursery does not yet exist for this window:
        #  - If there is a main nursery, launch a task to create a sub-nursery for our window
        #  - If not, wait until the main loops starts, nurseries will be created then
        if self.nursery is not None and bindings.nursery is None and not bindings.nursery_queued:
            bindings.nursery_queued = True
            self.nursery.start_soon(self._build_nursery, window)

    async def _build_nursery(self, window: wx.Window) -> None:
        """Create a nursery for managing tasks associated with the wx_window object."""
        if self.window_bindings[window].nursery is not None:
            # Trying to create a nursery twice is a code smell
            raise Warning(f'Attempted to create nursery for {window} multiple times.')
        async with trio.open_nursery() as nursery:
            bindings = self.window_bindings[window]
            bindings.nursery = nursery
            self._BindSync(window, wx.EVT_WINDOW_DESTROY, lambda event: self.OnDestroy(event, window), window)
            await trio.sleep_forever()
        # We reach here when the nursery has been cancelled, ie: when OnDestroy is
        # triggered for this window.
        del self.window_bindings[window]

    async def _spawn_into(self, window: wx.Window, coroutine: AsyncMethod) -> None:
        """Spawn the child coroutine into the nursery associated with the wx_window."""
        with trio.move_on_after(SPAWN_TIMEOUT) as cancel_scope:
            # The wx_window's nursery may not be set up fully yet,
            # _build_nursery was queued in trio, but might not have begun to run,
            # so wait up to 10 seconds.
            nursery = await self.window_bindings[window].wait_nursery()
        if cancel_scope.cancelled_caught:
            raise RuntimeError(f'Failed to nursery for {window}, timed out.')
        nursery.start_soon(coroutine)


    def StartCoroutine(self, window: wx.Window, coroutine: AsyncMethod, bind_onclose: bool = True) -> None:
        """Start a coroutine, and attach its lifetime to the `wx_window`. If
        `bind_onclose`, then also bind a custom OnClose event handler to cancel all
        associated coroutines.
        """
        self._queue_cleanup(window)
        if self.nursery is None:
            # No nursery yet, stash this task for later
            self.pending_tasks.append((window, coroutine))
        else:
            # Spawn it
            self.nursery.start_soon(self._spawn_into, window, coroutine)
        if bind_onclose:
            self._BindSync(window, wx.EVT_CLOSE, lambda event: self.OnDestroy(event, window), window)

    def OnEvent(self, event: wx.Event, window: wx.Window, event_id: int) -> None:
        """An event occurred for something we've bound to an async event handler."""
        bindings = self.window_bindings[window]
        for async_handler in bindings.event_handlers[event_id]:
            # if this even handler is bound already, the nurser is in fact created
            bindings.nursery.start_soon(async_handler, event.Clone()) # type: ignore

    def OnDestroy(self, event: wx.Event, window: wx.Window) -> None:
        """Clean up any running tasks associated with the window that is being
        destroyed.
        """
        self.CancelCoroutines(window)
        event.Skip()

    def CancelCoroutines(self, window: wx.Window) -> None:
        """Cancel all trio coroutines associated with a window."""
        if window in self.window_bindings:
            if (nursery := self.window_bindings[window].nursery):
                nursery.cancel_scope.cancel()


def get_app() -> App:
    """Return the wx.App instance, or raise an exception if it is not a wxtrio.App."""
    app = wx.App.Get()
    if not isinstance(app, App):
        raise RuntimeError('wx App must be a wxtrio.App object')
    return app

def Bind(window: wx.Window, event: wx.PyEventBinder, handler: EventHandler, source=None, id: int = wx.ID_ANY, id2: int = wx.ID_ANY) -> None:
    """Initial Bind call.  Get the wx.App instance and verify it is a wxt.App instance.
    """
    get_app().DynamicBind(window, event, handler, source, id, id2)


def AsyncBind(window: wx.Window, event: wx.PyEventBinder, handler: AsyncHandler, source=None, id: int = wx.ID_ANY, id2: int = wx.ID_ANY) -> None:
    get_app().AsyncBind(event, handler, window, source, id, id2)


def StartCoroutine(window: wx.Window, coroutine: Callable[..., Awaitable[Any]], bind_onclose: bool = True) -> None:
    """Start a coroutine opened by `wx_window`.  If `bind_onclose` then also binds a
    custom OnClose event handler to the window which will cancel all coroutines.
    """
    get_app().StartCoroutine(window, coroutine, bind_onclose)


async def AsyncShowDialog(dialog: wx.Dialog) -> int:
    """Shows a non-modal wx Dialog asyncronously"""
    closed = trio.Event()
    def end_dialog(return_code: int) -> None:
        dialog.SetReturnCode(return_code)
        dialog.Hide()
        closed.set()
    def on_button(event: wx.Event) -> None:
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
    def on_close(event: wx.Event) -> None:
        closed.set()
        dialog.Hide()
    dialog.Bind(wx.EVT_CLOSE, on_close)
    dialog.Bind(wx.EVT_BUTTON, on_button)
    dialog.Show()
    await closed.wait()
    return dialog.GetReturnCode()
