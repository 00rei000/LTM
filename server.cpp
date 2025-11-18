// Simple TCP server with user registration and login
// - uses Winsock2 for Windows socket programming
// - supports basic REGISTER and LOGIN commands
// - logs all messages to console and file

#include <iostream>
#include <fstream>
#include <string>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <atomic>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <sstream>
#include <csignal>

// small trim helper
static inline std::string trim(const std::string &s) {
    size_t start = 0;
    while (start < s.size() && isspace((unsigned char)s[start])) start++;
    size_t end = s.size();
    while (end > start && isspace((unsigned char)s[end-1])) end--;
    return s.substr(start, end - start);
}

using namespace std;

// Global variables
unordered_map<string, string> users; // username -> password
unordered_map<string, string> sessions; // session_id -> username
unordered_map<string, string> user_to_session; // username -> session_id (single-session map)
mutex log_mutex;
ofstream log_file; // opened in main after files are created
mutex users_mutex;
mutex sessions_mutex;
atomic<int> next_client_id{1};

// Graceful shutdown: save state and close log so Windows releases file lock
// forward declarations so graceful_shutdown can call them
void save_sessions();
void save_users();

void graceful_shutdown(int signum) {
    // avoid calling log_message if log_file may be closed; write to cout as well
    cout << "Shutting down (signal " << signum << ")...\n";
    // persist data
    save_sessions();
    save_users();
    if (log_file.is_open()) {
        log_file << "Server shutting down\n";
        log_file.flush();
        log_file.close();
    }
    // request process exit
    exit(0);
}

// forward declarations (needed because graceful_shutdown is above save_* implementations)
void save_sessions();
void save_users();

// Function to log messages
void log_message(const string &message) {
    lock_guard<mutex> lock(log_mutex);
    log_file << message << endl;
    cout << message << endl;
}

void load_users() {
    ifstream users_file("users.txt");
    if (!users_file.is_open()) return;
    string line;
    while (getline(users_file, line)) {
        // remove CR if present
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        size_t delimiter_pos = line.find(':');
        if (delimiter_pos != string::npos) {
            string username = trim(line.substr(0, delimiter_pos));
            string password = trim(line.substr(delimiter_pos + 1));
            if (!username.empty()) {
                users[username] = password;
            }
        }
    }
}

void save_users() {
    lock_guard<mutex> lock(users_mutex);
    ofstream users_file("users.txt", ios::trunc);
    if (!users_file.is_open()) return;
    for (const auto &pair : users) {
        users_file << pair.first << ":" << pair.second << "\n";
    }
    users_file.flush();
}

void save_sessions() {
    lock_guard<mutex> lock(sessions_mutex);
    ofstream sessions_file("sessions.txt", ios::trunc);
    if (!sessions_file.is_open()) return;
    for (const auto &pair : sessions) {
        sessions_file << pair.first << ":" << pair.second << "\n";
    }
    sessions_file.flush();
}

// Function to handle client communication
void handle_client(SOCKET client_socket, int client_id, const string &client_addr_str) {
    // prefix used for this connection's logs
    string prefix = "Client[" + to_string(client_id) + "] ";
    string current_session; // session id associated with this connection (if any)
    string current_user; // username bound to this connection (if any)
    char buffer[1024];

    log_message(prefix + "connected: " + client_addr_str);

    while (true) {
        memset(buffer, 0, sizeof(buffer));
        int bytes_received = recv(client_socket, buffer, sizeof(buffer) - 1, 0);
        if (bytes_received <= 0) {
            log_message(prefix + "disconnected.");
            break;
        }

        string received_message(buffer);
        // trim potential trailing CR/LF for nicer logs
        while (!received_message.empty() && (received_message.back() == '\n' || received_message.back() == '\r'))
            received_message.pop_back();
        log_message(prefix + "Received: " + received_message);

        // Process commands (robust parsing: split by whitespace)
        string response;
        {
            istringstream iss(received_message);
            string cmd;
            iss >> cmd;
            if (cmd == "REGISTER") {
                string username, password;
                iss >> username >> password;
                if (username.empty() || password.empty()) {
                    response = "FAIL 400 INVALID_FORMAT\n";
                } else {
                    bool added = false;
                    {
                        lock_guard<mutex> lock(users_mutex);
                        if (users.find(username) == users.end()) {
                            users[username] = password;
                            added = true;
                        }
                    }
                    if (added) {
                        // call save_users() without holding users_mutex to avoid deadlock
                        save_users();
                        response = "SUCCESS 201 REGISTERED " + username + "\n";
                    } else {
                        response = "FAIL 409 USER_EXISTS\n";
                    }
                }
            } else if (cmd == "LOGIN") {
                string username, password;
                iss >> username >> password;
                if (username.empty() || password.empty()) {
                    response = "FAIL 400 INVALID_FORMAT\n";
                } else {
                    lock_guard<mutex> lock(users_mutex);
                    if (users.find(username) != users.end() && users[username] == password) {
                        // enforce single active session per username: remove old session if exists
                        {
                            lock_guard<mutex> s_lock(sessions_mutex);
                            auto it_user = user_to_session.find(username);
                            if (it_user != user_to_session.end()) {
                                string old_sid = it_user->second;
                                sessions.erase(old_sid);
                                user_to_session.erase(it_user);
                                log_message(prefix + "Removed old session for user " + username + " (" + old_sid + ")");
                            }
                        }

                        // generate a new session id using time + rand
                        string session_id = to_string((unsigned)time(nullptr)) + "-" + to_string(rand() % 100000);
                        {
                            lock_guard<mutex> s_lock(sessions_mutex);
                            sessions[session_id] = username;
                            user_to_session[username] = session_id;
                        }
                        save_sessions();
                        // bind this session to the connection
                        current_session = session_id;
                        current_user = username;
                        response = "SUCCESS 200 SESSION " + session_id + "\n";
                    } else {
                        response = "FAIL 401 INVALID_LOGIN\n";
                    }
                }
            } else if (cmd == "LOGOUT") {
                // Logout the session associated with this connection
                if (current_session.empty()) {
                    response = "FAIL 400 NOT_LOGGED_IN\n";
                } else {
                    bool removed = false;
                    string removed_user;
                    {
                        lock_guard<mutex> s_lock(sessions_mutex);
                        auto it = sessions.find(current_session);
                        if (it != sessions.end()) {
                            removed_user = it->second;
                            sessions.erase(it);
                            user_to_session.erase(removed_user);
                            removed = true;
                        }
                    }
                    if (removed) {
                        // call save_sessions() outside of the lock to avoid deadlock
                        save_sessions();
                        log_message(prefix + "User " + removed_user + " logged out (session " + current_session + ")");
                    }
                    current_session.clear();
                    current_user.clear();
                    response = "SUCCESS 200 LOGOUT\n";
                }
            } else if (cmd == "AUTH") {
                // AUTH command may be used to bind an existing session token to this connection
                string session_id;
                iss >> session_id;
                if (session_id.empty()) {
                    response = "FAIL 400 INVALID_FORMAT\n";
                } else {
                    lock_guard<mutex> s_lock(sessions_mutex);
                    auto it = sessions.find(session_id);
                    if (it != sessions.end()) {
                        current_session = session_id;
                        current_user = it->second;
                        response = "SUCCESS 200 AUTH_OK\n";
                    } else {
                        response = "FAIL 401 SESSION_EXPIRED\n";
                    }
                }
            
            } else {
                response = "FAIL 400 UNKNOWN_COMMAND\n";
            }
        }

        send(client_socket, response.c_str(), response.size(), 0);
        // trim trailing newlines for log clarity
        string resp_trim = response;
        while (!resp_trim.empty() && (resp_trim.back() == '\n' || resp_trim.back() == '\r'))
            resp_trim.pop_back();
        log_message(prefix + "Sent: " + resp_trim);
    }

    closesocket(client_socket);
}

int main() {
    // ensure persistence files exist (create if missing)
    {
        ofstream f;
        f.open("users.txt", ios::app); if (f.is_open()) f.close();
        f.open("sessions.txt", ios::app); if (f.is_open()) f.close();
        f.open("server.log", ios::app); if (f.is_open()) f.close();
    }

    // now open log file
    log_file.open("server.log", ios::app);

    load_users();

    // Load sessions from file (ignore malformed lines)
    {
        ifstream sessions_file("sessions.txt");
        string line;
        if (sessions_file.is_open()) {
            while (getline(sessions_file, line)) {
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (line.empty()) continue;
                size_t delimiter_pos = line.find(':');
                if (delimiter_pos != string::npos) {
                    string session_id = trim(line.substr(0, delimiter_pos));
                    string username = trim(line.substr(delimiter_pos + 1));
                    if (!session_id.empty() && !username.empty()) {
                        sessions[session_id] = username;
                        // rebuild user->session mapping (single-session policy)
                        user_to_session[username] = session_id;
                    }
                }
            }
            sessions_file.close();
        }
    }

    // seed rng for session ids
    srand((unsigned)time(nullptr));

    // register signal handler for graceful shutdown (Ctrl+C)
    signal(SIGINT, graceful_shutdown);
    signal(SIGTERM, graceful_shutdown);

    // Initialize Winsock
    WSADATA wsa_data;
    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
        cerr << "WSAStartup failed." << endl;
        return 1;
    }

    // Create server socket
    SOCKET server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (server_socket == INVALID_SOCKET) {
        cerr << "Socket creation failed." << endl;
        WSACleanup();
        return 1;
    }

    // Bind socket
    sockaddr_in server_addr = {};
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(8888);

    if (bind(server_socket, (sockaddr*)&server_addr, sizeof(server_addr)) == SOCKET_ERROR) {
        cerr << "Bind failed." << endl;
        closesocket(server_socket);
        WSACleanup();
        return 1;
    }

    // Listen for connections
    if (listen(server_socket, SOMAXCONN) == SOCKET_ERROR) {
        cerr << "Listen failed." << endl;
        closesocket(server_socket);
        WSACleanup();
        return 1;
    }

    log_message("Server started on port 8888.");

    // Accept clients
    while (true) {
        sockaddr_in client_addr;
        int client_size = sizeof(client_addr);
        SOCKET client_socket = accept(server_socket, (sockaddr*)&client_addr, &client_size);
        if (client_socket == INVALID_SOCKET) {
            cerr << "Accept failed." << endl;
            continue;
        }

    char client_ip[INET_ADDRSTRLEN];
    inet_ntop(AF_INET, &client_addr.sin_addr, client_ip, INET_ADDRSTRLEN);
    int client_port = ntohs(client_addr.sin_port);
    string client_addr_str = string(client_ip) + ":" + to_string(client_port);
    int client_id = next_client_id.fetch_add(1);
    log_message(string("Accepted connection: Client[") + to_string(client_id) + "] " + client_addr_str);

    // pass client id and ip:port to handler for richer logs
    thread client_thread(handle_client, client_socket, client_id, client_addr_str);
    client_thread.detach();
    }

    // Cleanup
    closesocket(server_socket);
    WSACleanup();
    return 0;
}
