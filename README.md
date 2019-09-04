# wxtrio
Implement the wx event loop on top of trio.

Limitations of this approach all involve the GUI task hogging control and not yielding to trio:
 - Resizing windows
 - Moving windows
 - Navigating menus
