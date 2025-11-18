# Simple TCP Chat (C++ / Winsock)

This repository contains a minimal TCP-based client/server chat-like system implemented in C++ (Winsock). It implements the requested features without WebSocket:

- TCP client-server connection using Winsock (no WebSocket)
- Server-side logging of received and sent messages to `server.log`
- User registration and login persisted to plain text files
- View friends and their online/offline status
- Send friend requests and accept friend requests with notifications

Files
- `server.cpp` - the server. Run on Windows; listens on port 54000 by default.
- `client.cpp` - a simple interactive client.
- `Makefile` - convenience for MinGW/g++ builds.
- `users.txt`, `sessions.txt`, `pending_requests.txt`, `friends.txt`, `online.txt` - plain-text persistence files used by the server to store users, sessions (tokens), pending friend requests, friend lists, and current online users.

Build (MinGW / g++)

Open PowerShell in this folder and run (assuming `g++` from MinGW is in PATH):

```powershell
mingw32-make
```

or run the compile lines directly:

```powershell
g++ -std=c++17 server.cpp -o server.exe -lws2_32 -pthread
g++ -std=c++17 client.cpp -o client.exe -lws2_32 -pthread
```

Run

1. Start the server:

```powershell
.\server.exe
```

2. Start one or more clients:

```powershell
.\client.exe
```

Client commands
- Use server protocol commands directly (one per line):
  - REGISTER username password
  - LOGIN username password (server replies with a numeric status and a token, e.g.: `200 OK Logged in TOKEN <token>`)
  - AUTH token
  - LIST_FRIENDS
  - SEND_FRIEND_REQUEST target
  - ACCEPT_FRIEND_REQUEST fromUser
  - LOGOUT or QUIT

- Friendly client shortcuts (client CLI):
  - /register alice secret
  - /login alice secret
  - /auth <token>          # restore session using token returned at login
  - /list
  - /add bob
  - /accept alice
  - /quit

Notes, limitations and next steps
- The server stores state in the simple plain-text files listed above. Passwords and tokens are stored in plaintext for simplicity.
- Tokens persist across server restarts (stored in `sessions.txt`). Online users are saved to `online.txt` when they login/logout but are runtime-only — the server starts with no users online.
- No encryption or authentication beyond this simple protocol — do not use on untrusted networks.
- Concurrency is protected by simple mutexes; for large-scale use you'd want a proper database and better error handling.

If you want, I can:
- Add message sending between friends.
- Improve the persistence to use JSON or SQLite.
- Add unit tests or an automated script to drive multiple clients.

Response codes
The server uses HTTP-like numeric response codes with a reason phrase. Examples you may see:

- 200 OK
- 201 Created
- 400 Bad Request
- 401 Unauthorized
- 403 Forbidden
- 404 Not Found
- 409 Conflict

Responses are single-line messages prefixed by the numeric code and reason, optional extra text follows (for example `200 OK Logged in TOKEN <token>`).
