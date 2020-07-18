# wxtrio
Run wx with trio in guest mode.

Limitations of this approach:
 - The GUI hogs processing (and hence, trio tasks do not run) when the mouse is not moving while doing any of the following:
    - Resizing windows
    - Moving windows
    - Navigating menus
 - Trio is started in guest mode, so wxPython's main event loop must be started before trio.  As a result, all async tasks are cancelled when the wxPython application exits.
 
