"""Some helper utilities"""
from __future__ import annotations

__all__ = [
    'XRCCtrl',
    'XRCFrame',
    'XRCApp',
    'disable',
]

import os
import sys
import contextlib
from functools import cached_property
from typing import Any

import wx
import wxtrio as wxt
from wx import xrc


# Some XRC helpers
class _xrc_property(cached_property):
    __slots__ = ('__name', )

    def __init__(self):
        super().__init__(lambda instance: xrc.XRCCTRL(instance, self.__name))

    def __set_name__(self, owner, name) -> None:
        self.__name = name
        super().__set_name__(owner, name)


def XRCCtrl(xrc_name: str = '') -> Any:
    """Assign to a class attribute to create it as a property which loads
    the control from XRC. Best used alongside a typehint for the control type.
    If no name is specified, the name of the attribute is assumed to be the XRC
    resource name.
    """
    if not xrc_name:
        return _xrc_property()
    else:
        return cached_property(lambda self: xrc.XRCCTRL(self, xrc_name))


class XRCApp(wxt.App):
    def __init__(self, xrc_file: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure working dir is same as script location
        pathname = os.path.dirname(sys.argv[0])
        with contextlib.chdir(pathname):
            _XRC().Load(xrc_file)

class XRCFrame(wx.Frame):
    """wx.Frame that handles initialization from XRC."""

    def __init__(self, parent: wx.Window | None = None, xrc_name: str = ''):
        if not xrc_name:
            xrc_name = type(self).__name__
        super().__init__()
        _XRC().LoadFrame(self, parent, xrc_name)


def _XRC() -> xrc.XmlResource:
    """Wrapper around xrc.XmlResource.Get() for typing purposes."""
    return xrc.XmlResource.Get()


# other helpers
@contextlib.contextmanager
def disable(window: wx.Window):
    """Temporarily disable a wx widget, returning it to its original state afterwards.
    """
    state = window.Enabled
    window.Enabled = False
    try:
        yield
    finally:
        window.Enabled = state
