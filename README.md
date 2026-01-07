ChatApp (PyQt5 client + C++ Winsock2 server)# LTM


Overview

This repository contains a small chat application: a PyQt5-based GUI client (Python) and a C++ TCP server using Winsock2. The protocol supports REGISTER, LOGIN, TEXT messages, HISTORY queries, and file upload/download. This README explains how to build, run, test, and a simple resume strategy for uploads.

Contents

- gui_client.py  — PyQt5 GUI client (Python 3)
- server.cpp     — C++ TCP server (Winsock2)
- Makefile       — convenience targets (mingw32-make on Windows)
- messages/      — server message storage (runtime)

Requirements

- Windows 10/11
- Python 3.8+ with PyQt5
  - Install: pip install pyqt5
- MinGW (g++/mingw32-make) for building the server
- (Optional) Git for pushing to GitHub

Build and run

1) Server (Windows, MinGW)
- Open a MinGW shell or PowerShell with MinGW in PATH.
- From the project root, build with:
  mingw32-make server
- Run server:
  mingw32-make run-server

The server listens on port 8888 by default.

2) Client (Python + PyQt5)
- Create a virtualenv (recommended):
  python -m venv .venv; .\.venv\Scripts\Activate.ps1
- Install PyQt5:
  pip install pyqt5
- Run the client:
  python gui_client.py

Notes on protocol behavior

- HISTORY responses: server sends a header like "SUCCESS 200 <N>\n" followed by N newline-delimited history lines in the format:
  msgId|sender|timestamp|TYPE|len|content

  The client expects the header first and then collects exactly N lines as the history block.

- The server's current HISTORY handler returns all matching history rows it finds and reports N accordingly. The client's HISTORY request may include a `limit` parameter, but at present the server does not enforce a client-provided limit in the reviewed code. If you want the server to respect `limit`, add enforcement in the HISTORY handler before composing result lines.

Upload resume strategy (simple, robust)

A minimal resume strategy that is easy to implement on both client and server:

- Use a deterministic temporary filename for in-progress uploads:
  <original_filename>_<filesize>.part

- Flow:
  1. Client sends an upload-init request supplying the original filename and the expected final file size.
  2. Server checks for `uploads/<filename>_<filesize>.part`:
     - If present, server returns the current byte-offset (file length) so the client can resume from that offset.
     - If absent, server creates the `.part` file and returns offset 0.
  3. Client uploads file chunks with offsets; server writes them at the corresponding offset into the `.part` file.
  4. When received size == expected filesize, server renames `*.part` to the final filename and marks upload complete.

- Advantages:
  - No extra database mapping needed.
  - Resume works across client restarts and server restarts (as long as uploads directory persists).
  - The key (filename + filesize) is deterministic and easy to compute on the client.

- Caveats:
  - This assumes the uploader and the final filename + filesize are stable and unique enough to avoid collisions. If multiple clients may upload files with identical name and size concurrently, add a short-lived upload token or session id.

How to test the recent client fixes

The client has received fixes for three issues:
1) Auto-login after REGISTER — the client will now automatically proceed to LOGIN when registration succeeds.
2) Recipients are not forced to download files — file downloads are now user-initiated in the GUI.
3) TEXT messages with spaces — both realtime NOTIFY_TEXT and HISTORY messages preserve spaces and show correctly in the GUI.

Quick manual test (recommended by the developer):
- Start the server.
- Run the client and register/login two accounts.
- From account A, send a text message containing spaces, e.g. `hello world`.
- Switch to another conversation and switch back (force a history refresh).
- Verify both realtime and historical messages display correctly.

Contributing & pushing to GitHub

- Initialize a git repo (if not already):
  git init
  git add .
  git commit -m "Initial import"
- Create a new GitHub repository and push:
  git remote add origin <url>
  git branch -M main
  git push -u origin main

Follow-up work (suggested)

- Implement server-side enforcement of HISTORY `limit` parameter.
- Implement the `.part` resume logic in `server.cpp` and the matching client-side upload resume behavior.
- Add unit/integration tests for parsing HISTORY blocks and NOTIFY_TEXT handling.

License

Add a LICENSE file if you plan to publish this repo; otherwise, assume private use.

