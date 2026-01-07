// Server TCP đơn giản với đăng ký và đăng nhập
// - sử dụng Winsock2 cho lập trình socket Windows
// - hỗ trợ lệnh REGISTER và LOGIN cơ bản
// - ghi log tin nhắn ra console và file

#include <iostream>
#include <fstream>
#include <string>
#include <thread>
#include <mutex>
#include <unordered_map>
#include <atomic>
#include <cstdlib>
#include <ctime>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <sstream>
#include <csignal>
#include <vector>
#include <iomanip>
#include <algorithm>
#include <filesystem>
#include <cstring>
#include <sys/stat.h>
#include <sstream>

namespace fs = std::filesystem;

// hàm hỗ trợ xóa khoảng trắng
static inline std::string trim(const std::string &s) {
    size_t start = 0;
    while (start < s.size() && isspace((unsigned char)s[start])) start++;
    size_t end = s.size();
    while (end > start && isspace((unsigned char)s[end-1])) end--;
    return s.substr(start, end - start);
}

using namespace std;

// Biến toàn cục
unordered_map<string, string> users; // tên_đăng_nhập -> mật_khẩu
unordered_map<string, string> sessions; // id_phiên -> tên_đăng_nhập
unordered_map<string, string> user_to_session; // tên_đăng_nhập -> id_phiên (mỗi user 1 phiên)
mutex log_mutex;
ofstream log_file; // mở trong main sau khi tạo files
mutex users_mutex;
mutex sessions_mutex;
// bạn bè và lời mời kết bạn chờ duyệt
unordered_map<string, vector<string>> pending_requests; // người_nhận -> danh_sách_người_gửi
// Entry bạn bè lưu tên, trạng thái và ID cuộc trò chuyện
struct FriendEntry {
    string name;
    string status; // "online" hoặc "offline"
    string conv;   // ID cuộc trò chuyện cho cặp bạn này
};
// friends_map: tên_đăng_nhập -> danh_sách_FriendEntry
unordered_map<string, vector<FriendEntry>> friends_map; // tên_đăng_nhập -> danh_sách bạn bè với trạng thái + conv
mutex pending_mutex;
mutex friends_mutex;

// Cấu trúc nhóm chat
struct GroupInfo {
    string name;     // tên nhóm (định danh duy nhất)
    string creator;  // tên người tạo (admin)
    int max_members; // số thành viên tối đa
    vector<string> members; // danh sách tên thành viên
};
// groups_map: tên_nhóm -> GroupInfo
unordered_map<string, GroupInfo> groups_map;
// user_groups: tên_đăng_nhập -> danh_sách_nhóm mà user tham gia
unordered_map<string, vector<string>> user_groups;
// group_invites: tên_nhóm -> danh_sách người được mời
unordered_map<string, vector<string>> group_invites;
mutex groups_mutex;

// ============================================================================
// Cấu trúc truyền file (kiến trúc Lưu-và-Chuyển)
// ============================================================================

// Header khối nhị phân: 8 bytes
// [Vị trí File: 4 bytes][Độ dài Data: 4 bytes][Payload: biến đổi]
#define CHUNK_HEADER_SIZE 8
#define CHUNK_SIZE 65536  // 64KB payload mỗi khối

struct FileMetadata {
    string unique_id;        // ID file duy nhất do server tạo
    string original_filename;
    string sender_username;
    string target_type;      // "U" hoặc "G"
    string target_name;      // tên người dùng hoặc tên nhóm
    size_t filesize;
    size_t bytes_received;   // Tiến trình hiện tại
    string filepath;         // Đường dẫn trên server (uploads/unique_id)
    bool upload_complete;
    time_t upload_time;
};

// File đang truyền: unique_id -> FileMetadata
unordered_map<string, FileMetadata> active_uploads;
unordered_map<string, FileMetadata> completed_files;
mutex files_mutex;

// Hàm hỗ trợ: Tạo ID file duy nhất
string generate_file_id() {
    static int counter = 0;
    time_t now = time(nullptr);
    return to_string(now) + "_" + to_string(++counter);
}

// ============================================================================

// ánh xạ online để gửi thông báo: tên_đăng_nhập -> socket
unordered_map<string, SOCKET> online_sockets;
mutex online_mutex;
atomic<int> next_client_id{1};

// Tắt an toàn: lưu trạng thái và đóng log để Windows giải phóng file lock
// khai báo trước để graceful_shutdown có thể gọi
void save_sessions();
void save_users();
void save_groups();
void save_group_invites();

void graceful_shutdown(int signum) {
    // tránh gọi log_message nếu log_file có thể đã đóng; ghi ra cout
    // lưu dữ liệu
    save_sessions();
    save_users();
    save_groups();
    save_group_invites();
    if (log_file.is_open()) {
        log_file << "Server shutting down\n";
        log_file.flush();
        log_file.close();
    }
    // yêu cầu thoát tiến trình
    exit(0);
}

// khai báo trước (cần vì graceful_shutdown ở trên các hàm save_*)
void save_sessions();
void save_users();

// Hàm ghi log tin nhắn
void log_message(const string &message) {
    lock_guard<mutex> lock(log_mutex);
    // tạo timestamp YYYY-MM-DD HH:MM:SS
    std::time_t t = std::time(nullptr);
    std::tm tm;
#if defined(_WIN32)
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    std::ostringstream tsoss;
    tsoss << std::put_time(&tm, "%Y-%m-%d %H:%M:%S");
    string out = string("[") + tsoss.str() + "] " + message;
    log_file << out << endl;
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

void load_pending() {
    ifstream f("pending_requests.txt");
    if (!f.is_open()) return;
    string line;
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        size_t p = line.find(':');
        if (p == string::npos) continue;
        string target = trim(line.substr(0, p));
        string rest = trim(line.substr(p+1));
        vector<string> senders;
        if (!rest.empty()) {
            istringstream iss(rest);
            string tok;
            while (getline(iss, tok, ',')) {
                tok = trim(tok);
                if (!tok.empty()) senders.push_back(tok);
            }
        }
        if (!target.empty()) pending_requests[target] = senders;
    }
}

void save_pending() {
    lock_guard<mutex> lock(pending_mutex);
    ofstream f("pending_requests.txt", ios::trunc);
    if (!f.is_open()) return;
    for (const auto &p : pending_requests) {
        f << p.first << ":";
        for (size_t i=0;i<p.second.size();++i) {
            if (i) f << ",";
            f << p.second[i];
        }
        f << "\n";
    }
    f.flush();
}

void load_friends() {
    ifstream f("friends.txt");
    if (!f.is_open()) return;
    string line;
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        size_t p = line.find(':');
        if (p == string::npos) continue;
        string user = trim(line.substr(0, p));
        string rest = trim(line.substr(p+1));
            vector<FriendEntry> list;
        if (!rest.empty()) {
            istringstream iss(rest);
            string tok;
            while (getline(iss, tok, ',')) {
                tok = trim(tok);
                if (tok.empty()) continue;
                    // token format: friend|status|conv  (status and conv optional; defaults applied)
                    vector<string> parts;
                    {
                        istringstream ps(tok);
                        string p;
                        while (getline(ps, p, '|')) { parts.push_back(trim(p)); }
                    }
                    string fname = parts.size() > 0 ? parts[0] : string();
                    string status = parts.size() > 1 ? parts[1] : string("offline");
                    string conv = parts.size() > 2 ? parts[2] : string();
                    if (!fname.empty()) list.push_back(FriendEntry{fname, status, conv});
            }
        }
        if (!user.empty()) friends_map[user] = list;
    }
}

void save_friends() {
    lock_guard<mutex> lock(friends_mutex);
    ofstream f("friends.txt", ios::trunc);
    if (!f.is_open()) return;
    for (const auto &p : friends_map) {
        f << p.first << ":";
        for (size_t i=0;i<p.second.size();++i) {
            if (i) f << ",";
            // write as friend|status|conv
            const FriendEntry &e = p.second[i];
            f << e.name << "|" << e.status << "|" << e.conv;
        }
        f << "\n";
    }
    f.flush();
}

// Groups persistence
void load_groups() {
    ifstream f("groups.txt");
    if (!f.is_open()) return;
    string line;
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        // Format: group_name:creator:max_members:member1,member2,member3
        vector<string> parts;
        istringstream iss(line);
        string part;
        while (getline(iss, part, ':')) {
            parts.push_back(trim(part));
        }
        if (parts.size() < 3) continue;
        
        string gname = parts[0];
        string creator = parts[1];
        int max_members = atoi(parts[2].c_str());
        vector<string> members;
        if (parts.size() > 3) {
            istringstream mss(parts[3]);
            string mem;
            while (getline(mss, mem, ',')) {
                mem = trim(mem);
                if (!mem.empty()) members.push_back(mem);
            }
        }
        
        if (!gname.empty()) {
            groups_map[gname] = GroupInfo{gname, creator, max_members, members};
            // Build user_groups mapping
            for (const auto &mem : members) {
                user_groups[mem].push_back(gname);
            }
        }
    }
}

// Helper: save groups without acquiring lock (caller must hold groups_mutex)
void save_groups_unlocked() {
    ofstream f("groups.txt", ios::trunc);
    if (!f.is_open()) return;
    for (const auto &p : groups_map) {
        const GroupInfo &g = p.second;
        f << g.name << ":" << g.creator << ":" << g.max_members << ":";
        for (size_t i = 0; i < g.members.size(); ++i) {
            if (i) f << ",";
            f << g.members[i];
        }
        f << "\n";
    }
    f.flush();
}

void save_groups() {
    lock_guard<mutex> lock(groups_mutex);
    save_groups_unlocked();
}

void load_group_invites() {
    ifstream f("group_invites.txt");
    if (!f.is_open()) return;
    string line;
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        // Format: group_name:user1,user2,user3
        size_t p = line.find(':');
        if (p == string::npos) continue;
        string gname = trim(line.substr(0, p));
        string rest = trim(line.substr(p+1));
        vector<string> invites;
        if (!rest.empty()) {
            istringstream iss(rest);
            string tok;
            while (getline(iss, tok, ',')) {
                tok = trim(tok);
                if (!tok.empty()) invites.push_back(tok);
            }
        }
        if (!gname.empty()) group_invites[gname] = invites;
    }
}

// Helper: save group invites without acquiring lock (caller must hold groups_mutex)
void save_group_invites_unlocked() {
    ofstream f("group_invites.txt", ios::trunc);
    if (!f.is_open()) return;
    for (const auto &p : group_invites) {
        f << p.first << ":";
        for (size_t i = 0; i < p.second.size(); ++i) {
            if (i) f << ",";
            f << p.second[i];
        }
        f << "\n";
    }
    f.flush();
}

void save_group_invites() {
    lock_guard<mutex> lock(groups_mutex);
    save_group_invites_unlocked();
}

// check online map safely
bool is_user_online(const string &username) {
    lock_guard<mutex> ol(online_mutex);
    return online_sockets.find(username) != online_sockets.end();
}

// update stored friend-status entries: set any occurrences of 'username' to given status
void set_online_status_for_user(const string &username, const string &status) {
    {
        lock_guard<mutex> lock(friends_mutex);
        for (auto &p : friends_map) {
                for (auto &entry : p.second) {
                    if (entry.name == username) entry.status = status;
                }
        }
    }
    // persist change
    save_friends();
}

// send notification to user if online
void notify_user(const string &username, const string &message) {
    lock_guard<mutex> lock(online_mutex);
    auto it = online_sockets.find(username);
    if (it == online_sockets.end()) {
        // Ghi log khi user offline
        log_message("NOTIFY to " + username + " (offline): " + message);
        return;
    }
    SOCKET s = it->second;
    string msg = message + "\n";
    send(s, msg.c_str(), (int)msg.size(), 0);
    // Ghi log khi gửi thành công
    log_message("NOTIFY to " + username + ": " + message);
}

// Helper: get conversation ID between two users
string get_conversation_id(const string &user1, const string &user2) {
    lock_guard<mutex> lock(friends_mutex);
    auto it = friends_map.find(user1);
    if (it != friends_map.end()) {
        for (const auto &entry : it->second) {
            if (entry.name == user2) {
                return entry.conv;
            }
        }
    }
    return "";
}

// Helper: save message to file
bool save_message(const string &filename, const string &sender, const string &type, const string &content) {
    time_t now = time(nullptr);
    ofstream f(filename, ios::app);
    if (!f.is_open()) return false;
    f << now << "|" << sender << "|" << type << "|" << content << "\n";
    f.flush();
    return true;
}

// Helper: save message with explicit timestamp (used by backfill)
bool save_message_with_ts(const string &filename, const string &sender, const string &type, const string &content, time_t ts) {
    ofstream f(filename, ios::app);
    if (!f.is_open()) return false;
    f << ts << "|" << sender << "|" << type << "|" << content << "\n";
    f.flush();
    return true;
}

// Helper: read messages with time range filter
string read_messages(const string &filename, int limit, time_t start_ts, time_t end_ts) {
    ifstream f(filename);
    if (!f.is_open()) return "";
    
    vector<string> messages;
    string line;
    time_t now = time(nullptr);
    
    // Read all messages
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        
        // Parse timestamp
        size_t p = line.find('|');
        if (p == string::npos) continue;
        time_t ts = atoll(line.substr(0, p).c_str());
        
        // Filter by time range
        time_t filter_start = (start_ts == 0) ? 0 : start_ts;
        time_t filter_end = (end_ts == 0) ? now : end_ts;
        
        if (ts >= filter_start && ts <= filter_end) {
            messages.push_back(line);
        }
    }
    
    // Sort by timestamp (newest first for default behavior)
    sort(messages.begin(), messages.end(), [](const string &a, const string &b) {
        time_t ts_a = atoll(a.substr(0, a.find('|')).c_str());
        time_t ts_b = atoll(b.substr(0, b.find('|')).c_str());
        return ts_a > ts_b; // descending order
    });
    
    // Apply limit
    if (limit > 0 && messages.size() > (size_t)limit) {
        messages.resize(limit);
    }
    
    // Build response
    string result = "";
    for (size_t i = 0; i < messages.size(); ++i) {
        if (i > 0) result += " ";
        result += messages[i];
    }
    
    return result;
}

// Helper: create directory recursively (Windows)
bool create_directory_recursive(const string &path) {
    string cmd = "mkdir \"" + path + "\" 2>nul";
    system(cmd.c_str());
    return true;
}

// Helper: check if file exists
bool file_exists(const string &path) {
    ifstream f(path);
    return f.good();
}

// ============================================================================
// File Transfer Helper Functions
// ============================================================================
// Helper: Parse flexible time input to Unix timestamp
// Accepts:
// - pure integer seconds since epoch
// - ISO8601 like YYYY-MM-DDTHH:MM[:SS]
// - "YYYY-MM-DD HH:MM[:SS]"
long long parse_time_to_unix(const string &s) {
    string str = trim(s);
    if (str.empty()) return 0;
    // numeric
    if (all_of(str.begin(), str.end(), [](char c){ return isdigit((unsigned char)c); })) {
        return atoll(str.c_str());
    }
    // replace 'T' with space for easier parsing
    for (char &c : str) { if (c == 'T' || c=='t') c = ' '; }
    // Try with seconds
    tm tmv{};
    istringstream iss1(str);
    iss1 >> get_time(&tmv, "%Y-%m-%d %H:%M:%S");
    if (!iss1.fail()) {
        tmv.tm_isdst = -1;
        return (long long)mktime(&tmv);
    }
    // Try without seconds
    tm tmv2{};
    istringstream iss2(str);
    iss2 >> get_time(&tmv2, "%Y-%m-%d %H:%M");
    if (!iss2.fail()) {
        tmv2.tm_isdst = -1;
        return (long long)mktime(&tmv2);
    }
    // Fallback: now
    return (long long)time(nullptr);
}

// Helper: Ensure uploads directory exists
void ensure_uploads_directory() {
    fs::create_directories("uploads");
}

// Helper: Ensure files directory exists (per-conversation file index)
void ensure_files_directory() {
    fs::create_directories("files");
}

// Helper: Get file size safely
size_t get_file_size(const string &filepath) {
    ifstream file(filepath, ios::binary | ios::ate);
    if (!file.is_open()) return 0;
    return file.tellg();
}

// Helper: Send binary chunk header + payload
bool send_binary_chunk(SOCKET sock, uint32_t offset, uint32_t length, const char* data) {
    // Header: [offset:4][length:4]
    uint32_t net_offset = htonl(offset);
    uint32_t net_length = htonl(length);
    
    // Send header
    if (send(sock, (char*)&net_offset, 4, 0) != 4) return false;
    if (send(sock, (char*)&net_length, 4, 0) != 4) return false;
    
    // Send payload
    if (length > 0) {
        int sent = 0;
        while (sent < (int)length) {
            int n = send(sock, data + sent, length - sent, 0);
            if (n <= 0) return false;
            sent += n;
        }
    }
    return true;
}

// Helper: Receive exact N bytes
bool recv_exact(SOCKET sock, char* buffer, int length) {
    int received = 0;
    while (received < length) {
        int n = recv(sock, buffer + received, length - received, 0);
        if (n <= 0) return false;
        received += n;
    }
    return true;
}

// Helper: Save file metadata to disk
void save_file_metadata(const FileMetadata &meta) {
    lock_guard<mutex> lock(files_mutex);
    ofstream f("file_metadata.txt", ios::app);
    if (!f.is_open()) return;
    f << meta.unique_id << "|" 
      << meta.original_filename << "|"
      << meta.sender_username << "|"
      << meta.target_type << "|"
      << meta.target_name << "|"
      << meta.filesize << "|"
      << meta.filepath << "|"
      << meta.upload_time << "\n";
    f.flush();
}

// Helper: Load file metadata from disk
void load_file_metadata() {
    ifstream f("file_metadata.txt");
    if (!f.is_open()) return;
    string line;
    while (getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        
        vector<string> parts;
        istringstream iss(line);
        string part;
        while (getline(iss, part, '|')) {
            parts.push_back(part);
        }
        
        if (parts.size() >= 8) {
            FileMetadata meta;
            meta.unique_id = parts[0];
            meta.original_filename = parts[1];
            meta.sender_username = parts[2];
            meta.target_type = parts[3];
            meta.target_name = parts[4];
            meta.filesize = stoull(parts[5]);
            meta.filepath = parts[6];
            meta.upload_time = stoll(parts[7]);
            meta.upload_complete = true;
            meta.bytes_received = meta.filesize;
            
            completed_files[meta.unique_id] = meta;
        }
    }
}

// ============================================================================

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
            // if user was authenticated on this connection, mark offline
            if (!current_user.empty()) {
                {
                    lock_guard<mutex> ol(online_mutex);
                    auto it = online_sockets.find(current_user);
                    if (it != online_sockets.end()) online_sockets.erase(it);
                }
                set_online_status_for_user(current_user, "offline");
            }
            break;
        }

        string received_message(buffer);
        // trim potential trailing CR/LF for nicer logs
        while (!received_message.empty() && (received_message.back() == '\n' || received_message.back() == '\r'))
            received_message.pop_back();
        // also trim leading whitespace/newlines
        size_t start_ws = 0;
        while (start_ws < received_message.size() &&
               (received_message[start_ws] == '\n' || received_message[start_ws] == '\r' ||
                received_message[start_ws] == ' '  || received_message[start_ws] == '\t')) {
            start_ws++;
        }
        if (start_ws > 0) received_message.erase(0, start_ws);

        // Ignore empty/whitespace-only lines to avoid spurious UNKNOWN_COMMANDs
        if (received_message.empty()) {
            continue;
        }

        log_message(prefix + "Received: " + received_message);

        // Process commands (robust parsing: split by whitespace)
        string response;
        try {
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
                        string old_sid;
                        {
                            lock_guard<mutex> s_lock(sessions_mutex);
                            auto it_user = user_to_session.find(username);
                            if (it_user != user_to_session.end()) {
                                old_sid = it_user->second;
                                sessions.erase(old_sid);
                                user_to_session.erase(it_user);
                                log_message(prefix + "Removed old session for user " + username + " (" + old_sid + ")");
                            }
                        }
                        // if an old connection exists for this user, notify it that its session expired and force logout
                        if (!old_sid.empty()) {
                            SOCKET old_sock = INVALID_SOCKET;
                            {
                                lock_guard<mutex> ol(online_mutex);
                                auto it_sock = online_sockets.find(username);
                                if (it_sock != online_sockets.end()) {
                                    old_sock = it_sock->second;
                                    // remove old mapping so new login will replace it
                                    online_sockets.erase(it_sock);
                                }
                            }
                            if (old_sock != INVALID_SOCKET && old_sock != client_socket) {
                                // notify old client and close its socket to force logout
                                string notify = string("NOTIFY SESSION_EXPIRED ") + old_sid + "\n";
                                send(old_sock, notify.c_str(), (int)notify.size(), 0);
                                // close old socket so its thread will detect disconnection
                                closesocket(old_sock);
                            }
                            // mark friend statuses offline for that user (will be set online again for the new connection)
                            set_online_status_for_user(username, "offline");
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
                        // mark user as online
                        {
                            lock_guard<mutex> ol(online_mutex);
                            online_sockets[current_user] = client_socket;
                        }
                        response = "SUCCESS 200 SESSION " + session_id + "\n";
                            // update friend-status entries and persist
                            set_online_status_for_user(current_user, "online");
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
                        // remove from online map
                        lock_guard<mutex> ol(online_mutex);
                        auto oit = online_sockets.find(removed_user);
                        if (oit != online_sockets.end()) online_sockets.erase(oit);
                    }
                    current_session.clear();
                    current_user.clear();
                            set_online_status_for_user(removed_user, "offline");
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
                        // mark user as online for this connection
                        {
                            lock_guard<mutex> ol(online_mutex);
                            online_sockets[current_user] = client_socket;
                        }
                        response = "SUCCESS 200 AUTH_OK\n";
                    } else {
                        response = "FAIL 401 SESSION_EXPIRED\n";
                            set_online_status_for_user(current_user, "online");
                    }
                }
            } else if (cmd == "ADD_FRIEND") {
                // ADD_FRIEND <target>
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string target; iss >> target;
                    if (target.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        // check user exists
                        lock_guard<mutex> lock(users_mutex);
                        if (users.find(target) == users.end()) {
                            response = string("FAIL 404 USER_NOT_FOUND ") + target + "\n";
                        } else {
                            // add pending request for target
                            {
                                lock_guard<mutex> pl(pending_mutex);
                                auto &vec = pending_requests[target];
                                // avoid duplicates
                                bool exists = false;
                                for (auto &s: vec) if (s == current_user) { exists = true; break; }
                                if (!exists) vec.push_back(current_user);
                            }
                            save_pending();
                            response = string("SUCCESS 200 REQUEST_SENT ") + target + "\n";
                            // notify target if online
                            notify_user(target, string("NOTIFY_FRIEND_REQUEST ") + current_user);
                        }
                    }
                }
            } else if (cmd == "CONFIRM_FRIEND") {
                // CONFIRM_FRIEND <sender>
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string sender; iss >> sender;
                    if (sender.empty()) { response = "FAIL 400 INVALID_FORMAT\n"; }
                    else {
                        bool found = false;
                        {
                            lock_guard<mutex> pl(pending_mutex);
                            auto it = pending_requests.find(current_user);
                            if (it != pending_requests.end()) {
                                auto &vec = it->second;
                                for (auto itv = vec.begin(); itv != vec.end(); ++itv) {
                                    if (*itv == sender) { vec.erase(itv); found = true; break; }
                                }
                                if (vec.empty()) pending_requests.erase(current_user);
                            }
                        }
                        // persist pending changes (ensure the removed request is saved)
                        save_pending();
                        if (!found) {
                            response = string("FAIL 404 REQUEST_NOT_FOUND\n");
                        } else {
                            // add to friends both ways and assign/obtain a conversation id (conv)
                            {
                                lock_guard<mutex> fl(friends_mutex);
                                // try to find existing conv id for the pair (in either user's list)
                                auto find_conv = [&](const string &x, const string &y)->string {
                                    auto itx = friends_map.find(x);
                                    if (itx != friends_map.end()) {
                                        for (auto &ent : itx->second) {
                                            if (ent.name == y && !ent.conv.empty()) return ent.conv;
                                        }
                                    }
                                    return string();
                                };
                                string conv_id = find_conv(current_user, sender);
                                if (conv_id.empty()) conv_id = find_conv(sender, current_user);
                                if (conv_id.empty()) {
                                    conv_id = string("U") + to_string((unsigned)time(nullptr)) + "-" + to_string(rand() % 100000);
                                }
                                auto &a = friends_map[current_user];
                                auto &b = friends_map[sender];
                                auto add_if_missing_entry = [&](vector<FriendEntry>& v, const string& name, const string& st, const string& c){
                                    for (auto &pr : v) if (pr.name == name) { pr.status = st; pr.conv = c; return; }
                                    v.push_back(FriendEntry{name, st, c});
                                };
                                string status_sender = is_user_online(sender) ? "online" : "offline";
                                string status_current = is_user_online(current_user) ? "online" : "offline";
                                add_if_missing_entry(a, sender, status_sender, conv_id);
                                add_if_missing_entry(b, current_user, status_current, conv_id);
                            }
                            save_friends();
                            response = string("SUCCESS 201 FRIEND_ADDED ") + sender + "\n";
                            // notify sender if online
                            notify_user(sender, string("NOTIFY_FRIEND_ACCEPTED ") + current_user);
                        }
                    }
                }
            } else if (cmd == "REJECT_FRIEND") {
                // REJECT_FRIEND <sender>
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string sender; iss >> sender;
                    if (sender.empty()) { response = "FAIL 400 INVALID_FORMAT\n"; }
                    else {
                        bool found = false;
                        {
                            lock_guard<mutex> pl(pending_mutex);
                            auto it = pending_requests.find(current_user);
                            if (it != pending_requests.end()) {
                                auto &vec = it->second;
                                for (auto itv = vec.begin(); itv != vec.end(); ++itv) {
                                    if (*itv == sender) { vec.erase(itv); found = true; break; }
                                }
                                if (vec.empty()) pending_requests.erase(current_user);
                            }
                        }
                        if (!found) response = "FAIL 404 REQUEST_NOT_FOUND\n";
                        else {
                            save_pending();
                            response = string("SUCCESS 200 REJECTED_FRIEND ") + sender + "\n";
                            notify_user(sender, string("NOTIFY_FRIEND_REJECTED ") + current_user);
                        }
                    }
                }
            } else if (cmd == "INIT_GROUP") {
                if (current_session.empty()) { 
                    response = "FAIL 401 UNAUTHORIZED\n"; 
                }
                else {
                    string group_name;
                    int max_members = 20; // default
                    iss >> group_name >> max_members;
                    group_name = trim(group_name);
                    if (group_name.empty()) {
                        response = "FAIL 400 INVALID_LIMIT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        if (groups_map.find(group_name) != groups_map.end()) {
                            response = "FAIL 409 GROUP_EXISTS\n";
                        } else {
                            groups_map[group_name] = GroupInfo{group_name, current_user, max_members, {current_user}};
                            user_groups[current_user].push_back(group_name);
                            save_groups_unlocked();
                            response = string("SUCCESS 201 GROUP_CREATED ") + group_name + "\n";
                            log_message(prefix + "Created group: " + group_name + " (max: " + to_string(max_members) + ")");
                        }
                    }
                }
            } else if (cmd == "SEND_INVITE") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string group_name, target;
                    iss >> group_name >> target;
                    if (group_name.empty() || target.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(group_name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else {
                            // Check if current_user is admin
                            if (git->second.creator != current_user) {
                                response = "FAIL 403 NO_PERMISSION\n";
                            } else if (find(git->second.members.begin(), git->second.members.end(), target) != git->second.members.end()) {
                                response = "FAIL 409 ALREADY_MEMBER\n";
                            } else {
                                // Add to invites
                                group_invites[group_name].push_back(target);
                                save_group_invites_unlocked();
                                response = string("SUCCESS 200 INVITE_SENT ") + target + "\n";
                                // Notify target if online
                                notify_user(target, string("NOTIFY_GROUP_INVITE ") + group_name + " " + current_user + "\n");
                                log_message(prefix + "Invited " + target + " to group " + group_name);
                            }
                        }
                    }
                }
            } else if (cmd == "CONFIRM_JOIN") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string group_name;
                    iss >> group_name;
                    if (group_name.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(group_name);
                        auto iit = group_invites.find(group_name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else if (iit == group_invites.end()) {
                            response = "FAIL 404 INVITE_NOT_FOUND\n";
                        } else {
                            auto &invites = iit->second;
                            auto pos = find(invites.begin(), invites.end(), current_user);
                            if (pos == invites.end()) {
                                response = "FAIL 404 INVITE_NOT_FOUND\n";
                            } else {
                                // Remove from invites
                                invites.erase(pos);
                                // Add to members
                                git->second.members.push_back(current_user);
                                user_groups[current_user].push_back(group_name);
                                save_groups_unlocked();
                                save_group_invites_unlocked();
                                response = string("SUCCESS 201 JOINED ") + group_name + "\n";
                                log_message(prefix + current_user + " joined group " + group_name);
                                // Notify all members
                                for (const auto &mem : git->second.members) {
                                    if (mem != current_user) {
                                        notify_user(mem, string("NOTIFY_MEMBER_JOIN ") + group_name + " " + current_user + "\n");
                                    }
                                }
                            }
                        }
                    }
                }
            } else if (cmd == "REJECT_JOIN") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string group_name;
                    iss >> group_name;
                    if (group_name.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        auto iit = group_invites.find(group_name);
                        auto git = groups_map.find(group_name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else if (iit == group_invites.end()) {
                            response = "FAIL 404 INVITE_NOT_FOUND\n";
                        } else {
                            auto &invites = iit->second;
                            auto pos = find(invites.begin(), invites.end(), current_user);
                            if (pos == invites.end()) {
                                response = "FAIL 404 INVITE_NOT_FOUND\n";
                            } else {
                                invites.erase(pos);
                                save_group_invites_unlocked();
                                response = "SUCCESS 200 REJECTED_JOIN\n";
                                log_message(prefix + current_user + " rejected invite to group " + group_name);
                                // Notify admin
                                notify_user(git->second.creator, string("NOTIFY_INVITE_REJECTED ") + group_name + " " + current_user + "\n");
                            }
                        }
                    }
                }
            } else if (cmd == "EJECT_USER") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string group_name, target;
                    iss >> group_name >> target;
                    if (group_name.empty() || target.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(group_name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else if (git->second.creator != current_user) {
                            response = "FAIL 403 NO_PERMISSION\n";
                        } else {
                            auto &members = git->second.members;
                            auto pos = find(members.begin(), members.end(), target);
                            if (pos == members.end()) {
                                response = "FAIL 404 USER_NOT_FOUND\n";
                            } else {
                                members.erase(pos);
                                // Remove from user_groups
                                auto &ugroups = user_groups[target];
                                auto upos = find(ugroups.begin(), ugroups.end(), group_name);
                                if (upos != ugroups.end()) ugroups.erase(upos);
                                save_groups_unlocked();
                                response = "SUCCESS 200 EJECTED " + target + "\n";
                                log_message(prefix + current_user + " ejected " + target + " from group " + group_name);
                                // Notify ejected user
                                notify_user(target, string("NOTIFY_EJECTED ") + group_name + " " + current_user + "\n");
                                // Notify remaining members
                                for (const auto &mem : members) {
                                    notify_user(mem, string("NOTIFY_MEMBER_LEFT ") + group_name + " " + target + "\n");
                                }
                            }
                        }
                    }
                }
            } else if (cmd == "GET_MEMBERS") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string group_name;
                    iss >> group_name;
                    if (group_name.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(group_name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else {
                            // Check if current_user is member
                            if (find(git->second.members.begin(), git->second.members.end(), current_user) == git->second.members.end()) {
                                response = "FAIL 403 NOT_A_MEMBER\n";
                            } else {
                                string out = "";
                                for (size_t i = 0; i < git->second.members.size(); ++i) {
                                    const string &mem = git->second.members[i];
                                    string role = (mem == git->second.creator) ? "admin" : "member";
                                    string status = is_user_online(mem) ? "online" : "offline";
                                    out += mem + ":" + role + ":" + status;
                                    if (i + 1 < git->second.members.size()) out += " ";
                                }
                                response = string("SUCCESS 200 MEMBERS ") + out + "\n";
                            }
                        }
                    }
                }
            } else if (cmd == "GET_GROUPS") {
                // Return all groups that current user is member of
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    lock_guard<mutex> lock(groups_mutex);
                    string group_list = "";
                    auto it = user_groups.find(current_user);
                    if (it != user_groups.end()) {
                        for (size_t i = 0; i < it->second.size(); ++i) {
                            const string &gname = it->second[i];
                            auto git = groups_map.find(gname);
                            if (git != groups_map.end()) {
                                if (i > 0) group_list += " ";
                                group_list += gname + ":" + to_string((int)git->second.members.size());
                            }
                        }
                    }
                    response = string("SUCCESS 200 GROUPS ") + group_list + "\n";
                }
            } else if (cmd == "TEXT") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string type, name;
                    iss >> type >> name;
                    string content;
                    getline(iss, content);
                    content = trim(content);
                    
                    if (type.empty() || name.empty() || content.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else if (type == "U") {
                        // User to user message
                        // Check if users are friends
                        string conv_id = get_conversation_id(current_user, name);
                        if (conv_id.empty()) {
                            response = "FAIL 404 USER_NOT_FOUND\n";
                        } else {
                            string msg_file = "messages/U_" + conv_id + ".txt";
                            time_t ts = time(nullptr);
                            if (save_message(msg_file, current_user, "TEXT", content)) {
                                response = "SUCCESS 201 SENT\n";
                                // Chỉ notify người nhận
                                notify_user(name, string("NOTIFY_TEXT U ") + current_user + " " + to_string(ts) + " " + content);
                                log_message(prefix + "Sent TEXT to " + name + ": " + content);
                            } else {
                                response = "FAIL 500 SAVE_FAILED\n";
                            }
                        }
                    } else if (type == "G") {
                        // Group message
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else {
                            // Check if current_user is member
                            auto &members = git->second.members;
                            if (find(members.begin(), members.end(), current_user) == members.end()) {
                                response = "FAIL 403 NOT_A_MEMBER\n";
                            } else {
                                string msg_file = "messages/G_" + name + ".txt";
                                time_t ts = time(nullptr);
                                if (save_message(msg_file, current_user, "TEXT", content)) {
                                    response = "SUCCESS 201 SENT\n";
                                    // Notify all online members except sender
                                    for (const auto &mem : members) {
                                        if (mem != current_user) {
                                            notify_user(mem, string("NOTIFY_TEXT G ") + name + " " + current_user + " " + to_string(ts) + " " + content);
                                        }
                                    }
                                    log_message(prefix + "Sent TEXT to group " + name + ": " + content);
                                } else {
                                    response = "FAIL 500 SAVE_FAILED\n";
                                }
                            }
                        }
                    } else {
                        response = "FAIL 400 INVALID_TYPE\n";
                    }
                }
            } else if (cmd == "HISTORY") {
                // New HISTORY protocol: HISTORY <type> <target_name> <time_begin> <time_end>
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string type, target_name; string tbegin_s, tend_s;
                    iss >> type >> target_name >> tbegin_s >> tend_s;
                    type = trim(type);
                    target_name = trim(target_name);
                    if (type.empty() || target_name.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else if (type != "U" && type != "G") {
                        response = "FAIL 400 INVALID_TYPE\n";
                    } else {
                        long long tbegin = parse_time_to_unix(tbegin_s);
                        long long tend = parse_time_to_unix(tend_s);
                        bool allowed = false; string msg_file;
                        if (type == "U") {
                            string conv_id = get_conversation_id(current_user, target_name);
                            if (conv_id.empty()) conv_id = get_conversation_id(target_name, current_user);
                            if (conv_id.empty()) {
                                response = "FAIL 404 CONVERSATION_NOT_FOUND\n";
                            } else {
                                allowed = true;
                                msg_file = "messages/U_" + conv_id + ".txt";
                            }
                        } else {
                            lock_guard<mutex> lock(groups_mutex);
                            auto git = groups_map.find(target_name);
                            if (git == groups_map.end()) {
                                response = "FAIL 404 GROUP_NOT_FOUND\n";
                            } else {
                                auto &members = git->second.members;
                                if (find(members.begin(), members.end(), current_user) == members.end()) {
                                    response = "FAIL 403 ACCESS_DENIED\n";
                                } else {
                                    allowed = true;
                                    msg_file = "messages/G_" + target_name + ".txt";
                                }
                            }
                        }

                        if (allowed) {
                            vector<string> result_lines;
                            ifstream f(msg_file);
                            if (!f.is_open()) {
                                // Không có file lịch sử -> không có tin nhắn
                                response = "FAIL 404 NO_MESSAGES\n";
                            } else {
                                string line; long long msgId = 0;
                                while (getline(f, line)) {
                                    if (!line.empty() && line.back() == '\r') line.pop_back();
                                    if (line.empty()) continue;
                                    size_t p1 = line.find('|'); if (p1 == string::npos) continue;
                                    size_t p2 = line.find('|', p1+1); if (p2 == string::npos) continue;
                                    size_t p3 = line.find('|', p2+1); if (p3 == string::npos) continue;
                                    long long ts = atoll(line.substr(0, p1).c_str());
                                    string sender = line.substr(p1+1, p2 - (p1+1));
                                    string mtype = line.substr(p2+1, p3 - (p2+1));
                                    string content = line.substr(p3+1);
                                    if ((tbegin == 0 || ts >= tbegin) && (tend == 0 || ts <= tend)) {
                                        msgId++;
                                        size_t len = content.size();
                                        ostringstream oss;
                                        oss << msgId << "|" << sender << "|" << ts << "|" << mtype << "|" << len << "|" << content;
                                        result_lines.push_back(oss.str());
                                    }
                                }
                                
                                // Kiểm tra nếu không có tin nhắn trong khoảng thời gian
                                if (result_lines.empty()) {
                                    response = "FAIL 404 NO_MESSAGES\n";
                                } else {
                                    ostringstream hdr; hdr << "SUCCESS 200 " << result_lines.size() << "\n";
                                    string header = hdr.str();
                                    send(client_socket, header.c_str(), (int)header.size(), 0);
                                    string resp_trim = header; while (!resp_trim.empty() && (resp_trim.back()=='\n'||resp_trim.back()=='\r')) resp_trim.pop_back();
                                    log_message(prefix + string("Sent: ") + resp_trim);
                                    for (auto &l : result_lines) {
                                        string out = l + "\n";
                                        send(client_socket, out.c_str(), (int)out.size(), 0);
                                    }
                                    // Skip default response send
                                    continue;
                                }
                            }
                        }
                    }
                }
            } else if (cmd == "INIT_UPLOAD") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string type, name, filepath;
                    long long size = 0;
                    string checksum;
                    iss >> type >> name >> filepath >> size >> checksum;
                    
                    if (type.empty() || name.empty() || filepath.empty() || size <= 0 || checksum.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else if (size > 100 * 1024 * 1024) { // 100MB limit
                        response = "FAIL 400 FILE_TOO_LARGE\n";
                    } else if (type == "U") {
                        // Check if users are friends
                        string conv_id = get_conversation_id(current_user, name);
                        if (conv_id.empty()) {
                            response = "FAIL 404 USER_NOT_FOUND\n";
                        } else {
                            // For now, send success - binary transfer will be implemented later
                            response = "SUCCESS 200 UPLOAD_START\n";
                            log_message(prefix + "INIT_UPLOAD U " + name + " " + filepath + " (binary transfer not yet implemented)");
                            // TODO: Receive binary data, verify checksum, save file
                        }
                    } else if (type == "G") {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else if (find(git->second.members.begin(), git->second.members.end(), current_user) == git->second.members.end()) {
                            response = "FAIL 403 NOT_A_MEMBER\n";
                        } else {
                            response = "SUCCESS 200 UPLOAD_START\n";
                            log_message(prefix + "INIT_UPLOAD G " + name + " " + filepath + " (binary transfer not yet implemented)");
                            // TODO: Receive binary data, verify checksum, save file
                        }
                    } else {
                        response = "FAIL 400 INVALID_TYPE\n";
                    }
                }
            } else if (cmd == "DOWNLOAD") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    string type, name, filename;
                    iss >> type >> name >> filename;
                    
                    if (type.empty() || name.empty() || filename.empty()) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                    } else if (type == "U") {
                        string conv_id = get_conversation_id(current_user, name);
                        if (conv_id.empty()) {
                            response = "FAIL 404 USER_NOT_FOUND\n";
                        } else {
                            string filepath = "uploads/U_" + conv_id + "/" + filename;
                            if (!file_exists(filepath)) {
                                response = "FAIL 404 FILE_NOT_FOUND\n";
                            } else {
                                // For now, just acknowledge - binary transfer will be implemented later
                                response = "SUCCESS 200 DOWNLOAD_START 0 CHECKSUM_PLACEHOLDER\n";
                                log_message(prefix + "DOWNLOAD U " + name + " " + filename + " (binary transfer not yet implemented)");
                                // Persist a DOWNLOAD event (legacy filename-based)
                                string conv_id2 = get_conversation_id(current_user, name);
                                if (conv_id2.empty()) conv_id2 = get_conversation_id(name, current_user);
                                if (!conv_id2.empty()) {
                                    string msg_file = string("messages/U_") + conv_id2 + ".txt";
                                    save_message(msg_file, current_user, "DOWNLOAD", filename);
                                    string file_index = string("files/U_") + conv_id2 + ".txt";
                                    save_message(file_index, current_user, "DOWNLOAD", filename);
                                }
                                // TODO: Send binary data with checksum
                            }
                        }
                    } else if (type == "G") {
                        lock_guard<mutex> lock(groups_mutex);
                        auto git = groups_map.find(name);
                        if (git == groups_map.end()) {
                            response = "FAIL 404 GROUP_NOT_FOUND\n";
                        } else if (find(git->second.members.begin(), git->second.members.end(), current_user) == git->second.members.end()) {
                            response = "FAIL 403 NO_PERMISSION\n";
                        } else {
                            string filepath = "uploads/G_" + name + "/" + filename;
                            if (!file_exists(filepath)) {
                                response = "FAIL 404 FILE_NOT_FOUND\n";
                            } else {
                                response = "SUCCESS 200 DOWNLOAD_START 0 CHECKSUM_PLACEHOLDER\n";
                                log_message(prefix + "DOWNLOAD G " + name + " " + filename + " (binary transfer not yet implemented)");
                                // Persist a DOWNLOAD event (legacy filename-based)
                                string msg_file = string("messages/G_") + name + ".txt";
                                save_message(msg_file, current_user, "DOWNLOAD", filename);
                                string file_index = string("files/G_") + name + ".txt";
                                save_message(file_index, current_user, "DOWNLOAD", filename);
                                // TODO: Send binary data with checksum
                            }
                        }
                    } else {
                        response = "FAIL 400 INVALID_TYPE\n";
                    }
                }
            } else if (cmd == "GET_FRIENDS") {
                if (current_session.empty()) { response = "FAIL 401 UNAUTHORIZED\n"; }
                else {
                    // ensure session still present
                    lock_guard<mutex> s_lock(sessions_mutex);
                        if (sessions.find(current_session) == sessions.end()) { current_session.clear(); current_user.clear(); response = "FAIL 401 SESSION_EXPIRED\n"; }
                    else {
                        string out = "";
                        lock_guard<mutex> fl(friends_mutex);
                        auto it = friends_map.find(current_user);
                        if (it != friends_map.end()) {
                            for (size_t i=0;i<it->second.size();++i) {
                                auto &entry = it->second[i];
                                string fname = entry.name;
                                bool online = is_user_online(fname);
                                out += fname + ":" + (online?"online":"offline");
                                if (i+1<it->second.size()) out += " ";
                            }
                        }
                        response = string("SUCCESS 200 FRIENDS ") + out + "\n";
                    }
                }
            } else if (cmd == "REQ_UPLOAD") {
                // REQ_UPLOAD <Type> <Target> <Filename> <Filesize>
                // Response: SUCCESS 200 READY_UPLOAD <Unique_ID>
                // Note: Filename can contain spaces, so we parse manually
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string type, target;
                    iss >> type >> target;
                    
                    // Get remaining string: "filename filesize"
                    string rest;
                    getline(iss, rest);
                    rest = trim(rest);
                    
                    // Find last space to separate filename from filesize
                    size_t last_space = rest.rfind(' ');
                    string filename;
                    size_t filesize = 0;
                    
                    if (last_space != string::npos) {
                        filename = trim(rest.substr(0, last_space));
                        string filesize_str = trim(rest.substr(last_space + 1));
                        try {
                            filesize = stoull(filesize_str);
                        } catch (...) {
                            filesize = 0;
                        }
                    }
                    
                    if (type.empty() || target.empty() || filename.empty() || filesize == 0) {
                        response = "FAIL 400 INVALID_FORMAT\n";
                        log_message(prefix + "Invalid upload format - type:" + type + " target:" + target + 
                                  " filename:" + filename + " filesize:" + to_string(filesize));
                    } else {
                        // Validate target exists
                        bool valid_target = false;
                        if (type == "U") {
                            lock_guard<mutex> lock(users_mutex);
                            valid_target = (users.find(target) != users.end());
                        } else if (type == "G") {
                            lock_guard<mutex> lock(groups_mutex);
                            auto git = groups_map.find(target);
                            if (git != groups_map.end()) {
                                // Check if sender is member
                                auto &members = git->second.members;
                                valid_target = (find(members.begin(), members.end(), current_user) != members.end());
                            }
                        }
                        
                        if (!valid_target) {
                            response = "FAIL 404 TARGET_NOT_FOUND\n";
                        } else {
                            // Generate unique ID
                            string file_id = generate_file_id();
                            string filepath = string("uploads/") + file_id;
                            
                            // Create metadata
                            FileMetadata meta;
                            meta.unique_id = file_id;
                            meta.original_filename = filename;
                            meta.sender_username = current_user;
                            meta.target_type = type;
                            meta.target_name = target;
                            meta.filesize = filesize;
                            meta.bytes_received = 0;
                            meta.filepath = filepath;
                            meta.upload_complete = false;
                            meta.upload_time = time(nullptr);
                            
                            {
                                lock_guard<mutex> lock(files_mutex);
                                active_uploads[file_id] = meta;
                            }
                            
                            response = string("SUCCESS 200 READY_UPLOAD ") + file_id + "\n";
                            log_message(prefix + "Upload request: " + filename + " -> " + file_id);
                        }
                    }
                }
            } else if (cmd == "UPLOAD_DATA") {
                // Client will send: "UPLOAD_DATA <file_id>\n" followed by binary chunks
                // This command switches to binary mode
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    iss >> file_id;
                    
                    FileMetadata meta;
                    bool found = false;
                    {
                        lock_guard<mutex> lock(files_mutex);
                        auto it = active_uploads.find(file_id);
                        if (it != active_uploads.end()) {
                            meta = it->second;
                            found = true;
                        }
                    }
                    
                    if (!found) {
                        response = "FAIL 404 FILE_ID_NOT_FOUND\n";
                    } else {
                        // Send ready signal
                        string ready_msg = string("SUCCESS 200 START_UPLOAD ") + to_string(meta.bytes_received) + "\n";
                        send(client_socket, ready_msg.c_str(), ready_msg.size(), 0);
                        log_message(prefix + "Start receiving binary chunks for " + file_id);
                        
                        // Open file for writing (append mode for resume)
                        ofstream outfile(meta.filepath, ios::binary | ios::app);
                        if (!outfile.is_open()) {
                            response = "FAIL 500 FILE_OPEN_ERROR\n";
                            send(client_socket, response.c_str(), response.size(), 0);
                            continue;
                        }
                        
                        // Receive binary chunks
                        bool upload_success = true;
                        // Precompute total chunks for logging
                        uint32_t total_chunks = (uint32_t)((meta.filesize + CHUNK_SIZE - 1) / CHUNK_SIZE);
                        while (meta.bytes_received < meta.filesize) {
                            // Read chunk header: [offset:4][length:4]
                            uint32_t net_offset, net_length;
                            if (!recv_exact(client_socket, (char*)&net_offset, 4)) {
                                upload_success = false;
                                break;
                            }
                            if (!recv_exact(client_socket, (char*)&net_length, 4)) {
                                upload_success = false;
                                break;
                            }
                            
                            uint32_t offset = ntohl(net_offset);
                            uint32_t length = ntohl(net_length);
                            
                            // EOF marker
                            if (length == 0) {
                                log_message(prefix + "Received EOF marker for " + file_id);
                                break;
                            }
                            
                            // Read payload
                            vector<char> buffer(length);
                            if (!recv_exact(client_socket, buffer.data(), length)) {
                                upload_success = false;
                                break;
                            }
                            
                            // Log current/total chunk number (1-based)
                            uint32_t current_chunk = (offset / CHUNK_SIZE) + 1;
                            ostringstream up_oss;
                            up_oss << "Receiving UPLOAD chunk " << current_chunk << "/" << total_chunks
                                   << " for " << file_id << " (" << length << " bytes, offset=" << offset << ")";
                            log_message(prefix + up_oss.str());
                            
                            // Write to file at offset
                            outfile.seekp(offset);
                            outfile.write(buffer.data(), length);
                            
                            meta.bytes_received += length;
                            
                            // Update metadata
                            {
                                lock_guard<mutex> lock(files_mutex);
                                active_uploads[file_id].bytes_received = meta.bytes_received;
                            }
                        }
                        
                        outfile.close();
                        
                        if (upload_success && meta.bytes_received >= meta.filesize) {
                            // Upload complete
                            meta.upload_complete = true;
                            {
                                lock_guard<mutex> lock(files_mutex);
                                active_uploads.erase(file_id);
                                completed_files[file_id] = meta;
                            }
                            save_file_metadata(meta);
                            
                            response = "SUCCESS 200 UPLOAD_COMPLETE\n";
                            log_message(prefix + "Upload complete: " + file_id);
                            
                            // Persist FILE message to history and broadcast notification
                            if (meta.target_type == "G") {
                                lock_guard<mutex> lock(groups_mutex);
                                auto git = groups_map.find(meta.target_name);
                                if (git != groups_map.end()) {
                                    // Save to group message history as FILE: content = file_id:filename
                                    string msg_file = string("messages/G_") + meta.target_name + ".txt";
                                    save_message(msg_file, current_user, "FILE", file_id + ":" + meta.original_filename);
                                    // Also save to per-conversation file index with metadata
                                    string file_index = string("files/G_") + meta.target_name + ".txt";
                                    save_message(file_index, current_user, "FILEMETA", file_id + ":" + meta.original_filename + ":" + to_string(meta.filesize));
                                    for (const auto &member : git->second.members) {
                                        if (member != current_user) {
                                            notify_user(member, string("NOTIFY_FILE G ") + meta.target_name + " " + 
                                                      current_user + " " + file_id + " " + meta.original_filename);
                                        }
                                    }
                                }
                            } else if (meta.target_type == "U") {
                                // Save to 1-1 conversation history
                                string conv_id = get_conversation_id(current_user, meta.target_name);
                                if (conv_id.empty()) conv_id = get_conversation_id(meta.target_name, current_user);
                                if (!conv_id.empty()) {
                                    string msg_file = string("messages/U_") + conv_id + ".txt";
                                    save_message(msg_file, current_user, "FILE", file_id + ":" + meta.original_filename);
                                    // Also save to per-conversation file index with metadata
                                    string file_index = string("files/U_") + conv_id + ".txt";
                                    save_message(file_index, current_user, "FILEMETA", file_id + ":" + meta.original_filename + ":" + to_string(meta.filesize));
                                }
                                notify_user(meta.target_name, string("NOTIFY_FILE U ") + " " + 
                                          current_user + " " + file_id + " " + meta.original_filename);
                            }
                        } else {
                            response = "FAIL 500 UPLOAD_INTERRUPTED\n";
                            log_message(prefix + "Upload interrupted: " + file_id);
                        }
                        // Explicitly send the final status for this upload (complete or interrupted)
                        // We skip the generic send() at the end of the loop, so send here before continuing.
                        send(client_socket, response.c_str(), response.size(), 0);
                        // Skip normal response send - already sent ready signal and final status above
                        continue;
                    }
                }
            } else if (cmd == "REQ_RESUME_UPLOAD") {
                // REQ_RESUME_UPLOAD <file_id>
                // Server Authority: Server decides offset
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    iss >> file_id;
                    
                    lock_guard<mutex> lock(files_mutex);
                    auto it = active_uploads.find(file_id);
                    if (it == active_uploads.end()) {
                        response = "FAIL 404 FILE_ID_NOT_FOUND\n";
                    } else {
                        size_t current_size = get_file_size(it->second.filepath);
                        it->second.bytes_received = current_size;
                        response = string("SUCCESS 200 READY_UPLOAD ") + to_string(current_size) + "\n";
                        log_message(prefix + "Resume upload: " + file_id + " from byte " + to_string(current_size));
                    }
                }
            } else if (cmd == "REQ_CANCEL_UPLOAD") {
                // REQ_CANCEL_UPLOAD <file_id>
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    iss >> file_id;
                    
                    lock_guard<mutex> lock(files_mutex);
                    auto it = active_uploads.find(file_id);
                    if (it == active_uploads.end()) {
                        response = "FAIL 404 FILE_ID_NOT_FOUND\n";
                    } else {
                        // Delete temporary file
                        fs::remove(it->second.filepath);
                        active_uploads.erase(it);
                        response = "SUCCESS 200 UPLOAD_CANCELLED\n";
                        log_message(prefix + "Upload cancelled: " + file_id);
                    }
                }
            } else if (cmd == "REQ_DOWNLOAD") {
                // REQ_DOWNLOAD <file_id>
                // Response: SUCCESS 200 READY_DOWNLOAD <file_id> <filename> <filesize>
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    iss >> file_id;
                    
                    FileMetadata meta;
                    bool found = false;
                    {
                        lock_guard<mutex> lock(files_mutex);
                        auto it = completed_files.find(file_id);
                        if (it != completed_files.end()) {
                            meta = it->second;
                            found = true;
                        }
                    }
                    
                    if (!found) {
                        response = "FAIL 404 FILE_NOT_FOUND\n";
                    } else {
                        // Send ready signal with file_id
                        string ready_msg = string("SUCCESS 200 READY_DOWNLOAD ") + file_id + " " + meta.original_filename + " " + to_string(meta.filesize) + "\n";
                        send(client_socket, ready_msg.c_str(), ready_msg.size(), 0);
                        log_message(prefix + "Start sending file: " + file_id);
                        
                        // Open file for reading
                        ifstream infile(meta.filepath, ios::binary);
                        if (!infile.is_open()) {
                            response = "FAIL 500 FILE_OPEN_ERROR\n";
                            send(client_socket, response.c_str(), response.size(), 0);
                            continue;
                        }
                        
                        // Send binary chunks
                        uint32_t offset = 0;
                        uint32_t total_chunks = (uint32_t)((meta.filesize + CHUNK_SIZE - 1) / CHUNK_SIZE);
                        char buffer[CHUNK_SIZE];
                        while (offset < meta.filesize) {
                            uint32_t to_read = min((size_t)CHUNK_SIZE, meta.filesize - offset);
                            infile.read(buffer, to_read);
                            uint32_t actually_read = infile.gcount();
                            
                            if (actually_read == 0) break;
                            
                            if (!send_binary_chunk(client_socket, offset, actually_read, buffer)) {
                                log_message(prefix + "Download interrupted: " + file_id);
                                infile.close();
                                continue;
                            }
                            
                            // Log current/total chunk number (1-based)
                            uint32_t current_chunk = (offset / CHUNK_SIZE) + 1;
                            ostringstream dl_oss;
                            dl_oss << "Sending DOWNLOAD chunk " << current_chunk << "/" << total_chunks
                                   << " for " << file_id << " (" << actually_read << " bytes, offset=" << offset << ")";
                            log_message(prefix + dl_oss.str());
                            
                            offset += actually_read;
                        }
                        
                        // Send EOF marker
                        send_binary_chunk(client_socket, offset, 0, nullptr);
                        infile.close();
                        
                        log_message(prefix + "Download complete: " + file_id);
                        // Inform client (text) that download completed successfully, similar to upload
                        {
                            string completed_msg = string("SUCCESS 200 DOWNLOAD_COMPLETE\n");
                            send(client_socket, completed_msg.c_str(), (int)completed_msg.size(), 0);
                            log_message(prefix + "Sent: SUCCESS 200 DOWNLOAD_COMPLETE");
                        }

                        // Persist DOWNLOAD event to message history
                        // Actor is current_user; content uses file_id:filename
                        {
                            lock_guard<mutex> lock(files_mutex);
                            auto itc = completed_files.find(file_id);
                            if (itc != completed_files.end()) {
                                const FileMetadata &m = itc->second;
                                string content = file_id + ":" + m.original_filename;
                                if (m.target_type == "G") {
                                    string msg_file = string("messages/G_") + m.target_name + ".txt";
                                    save_message(msg_file, current_user, "DOWNLOAD", content);
                                    string file_index = string("files/G_") + m.target_name + ".txt";
                                    save_message(file_index, current_user, "DOWNLOAD", content);
                                } else if (m.target_type == "U") {
                                    string conv_id = get_conversation_id(m.sender_username, m.target_name);
                                    if (conv_id.empty()) conv_id = get_conversation_id(m.target_name, m.sender_username);
                                    if (!conv_id.empty()) {
                                        string msg_file = string("messages/U_") + conv_id + ".txt";
                                        save_message(msg_file, current_user, "DOWNLOAD", content);
                                        string file_index = string("files/U_") + conv_id + ".txt";
                                        save_message(file_index, current_user, "DOWNLOAD", content);
                                    }
                                }
                            }
                        }
                        
                        // Skip normal response send
                        continue;
                    }
                }
            } else if (cmd == "REQ_RESUME_DOWNLOAD") {
                // REQ_RESUME_DOWNLOAD <file_id> <offset>
                // Client Authority: Client tells server where to start
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    uint32_t resume_offset;
                    iss >> file_id >> resume_offset;
                    
                    FileMetadata meta;
                    bool found = false;
                    {
                        lock_guard<mutex> lock(files_mutex);
                        auto it = completed_files.find(file_id);
                        if (it != completed_files.end()) {
                            meta = it->second;
                            found = true;
                        }
                    }
                    
                    if (!found) {
                        response = "FAIL 404 FILE_NOT_FOUND\n";
                    } else if (resume_offset >= meta.filesize) {
                        response = "FAIL 400 INVALID_OFFSET\n";
                    } else {
                        // Send ready signal
                        string ready_msg = string("SUCCESS 200 RESUME_DOWNLOAD ") + to_string(resume_offset) + "\n";
                        send(client_socket, ready_msg.c_str(), ready_msg.size(), 0);
                        log_message(prefix + "Resume download: " + file_id + " from byte " + to_string(resume_offset));
                        
                        // Open file and seek
                        ifstream infile(meta.filepath, ios::binary);
                        if (!infile.is_open()) {
                            response = "FAIL 500 FILE_OPEN_ERROR\n";
                            send(client_socket, response.c_str(), response.size(), 0);
                            continue;
                        }
                        
                        infile.seekg(resume_offset);
                        
                        // Send remaining chunks
                        uint32_t offset = resume_offset;
                        uint32_t total_chunks = (uint32_t)((meta.filesize + CHUNK_SIZE - 1) / CHUNK_SIZE);
                        char buffer[CHUNK_SIZE];
                        while (offset < meta.filesize) {
                            uint32_t to_read = min((size_t)CHUNK_SIZE, meta.filesize - offset);
                            infile.read(buffer, to_read);
                            uint32_t actually_read = infile.gcount();
                            
                            if (actually_read == 0) break;
                            
                            if (!send_binary_chunk(client_socket, offset, actually_read, buffer)) {
                                log_message(prefix + "Resume download interrupted: " + file_id);
                                infile.close();
                                continue;
                            }
                            
                            // Log current/total chunk number (1-based) for resume
                            uint32_t current_chunk = (offset / CHUNK_SIZE) + 1;
                            ostringstream dlr_oss;
                            dlr_oss << "Sending DOWNLOAD chunk " << current_chunk << "/" << total_chunks
                                    << " for " << file_id << " (" << actually_read << " bytes, offset=" << offset << ")";
                            log_message(prefix + dlr_oss.str());
                            
                            offset += actually_read;
                        }
                        
                        // Send EOF marker
                        send_binary_chunk(client_socket, offset, 0, nullptr);
                        infile.close();
                        
                        log_message(prefix + "Resume download complete: " + file_id);
                        // Inform client (text) that download completed successfully, similar to upload
                        {
                            string completed_msg = string("SUCCESS 200 DOWNLOAD_COMPLETE\n");
                            send(client_socket, completed_msg.c_str(), (int)completed_msg.size(), 0);
                            log_message(prefix + "Sent: SUCCESS 200 DOWNLOAD_COMPLETE");
                        }

                        // Persist DOWNLOAD event after resumed transfer completes
                        {
                            lock_guard<mutex> lock(files_mutex);
                            auto itc = completed_files.find(file_id);
                            if (itc != completed_files.end()) {
                                const FileMetadata &m = itc->second;
                                string content = file_id + ":" + m.original_filename;
                                if (m.target_type == "G") {
                                    string msg_file = string("messages/G_") + m.target_name + ".txt";
                                    save_message(msg_file, current_user, "DOWNLOAD", content);
                                    string file_index = string("files/G_") + m.target_name + ".txt";
                                    save_message(file_index, current_user, "DOWNLOAD", content);
                                } else if (m.target_type == "U") {
                                    string conv_id = get_conversation_id(m.sender_username, m.target_name);
                                    if (conv_id.empty()) conv_id = get_conversation_id(m.target_name, m.sender_username);
                                    if (!conv_id.empty()) {
                                        string msg_file = string("messages/U_") + conv_id + ".txt";
                                        save_message(msg_file, current_user, "DOWNLOAD", content);
                                        string file_index = string("files/U_") + conv_id + ".txt";
                                        save_message(file_index, current_user, "DOWNLOAD", content);
                                    }
                                }
                            }
                        }
                        
                        // Skip normal response send
                        continue;
                    }
                }
            } else if (cmd == "REQ_CANCEL_DOWNLOAD") {
                // REQ_CANCEL_DOWNLOAD <file_id>
                // Just acknowledge - client will delete partial file
                if (current_session.empty()) {
                    response = "FAIL 401 NOT_AUTHENTICATED\n";
                } else {
                    string file_id;
                    iss >> file_id;
                    response = "SUCCESS 200 DOWNLOAD_CANCELLED\n";
                    log_message(prefix + "Download cancelled by client: " + file_id);
                }
            } else {
                response = "FAIL 400 UNKNOWN_COMMAND\n";
            }
        } catch (const exception &e) {
            // Catch any parsing or processing errors
            response = string("FAIL 500 SERVER_ERROR ") + e.what() + "\n";
            log_message(prefix + "Exception during command processing: " + string(e.what()));
        } catch (...) {
            response = "FAIL 500 SERVER_ERROR\n";
            log_message(prefix + "Unknown exception during command processing");
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
        f.open("pending_requests.txt", ios::app); if (f.is_open()) f.close();
        f.open("friends.txt", ios::app); if (f.is_open()) f.close();
        f.open("groups.txt", ios::app); if (f.is_open()) f.close();
        f.open("group_invites.txt", ios::app); if (f.is_open()) f.close();
        f.open("file_metadata.txt", ios::app); if (f.is_open()) f.close();
        f.open("server.log", ios::app); if (f.is_open()) f.close();
    }

    // Ensure persistence directories exist
    ensure_uploads_directory();
    ensure_files_directory();

    // now open log file
    log_file.open("server.log", ios::app);

    load_users();
    load_pending();
    load_friends();
    load_groups();
    load_group_invites();
    load_file_metadata();

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
