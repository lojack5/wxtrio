# wxtrio
Run wx with trio in guest mode.

Limitations of this approach:
 - At least on Windows, when a menu is open and the mouse is not withing the bounding box of the window, the GUI main event loop does not yield to trio, and so trio tasks do not run.
 - Trio is started in guest mode, so wxPython's main event loop must be started before trio.  As a result, all async tasks are cancelled when the wxPython application exits.
 