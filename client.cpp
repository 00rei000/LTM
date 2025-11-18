#include <winsock2.h>
#include <ws2tcpip.h>
#include <iostream>
#include <thread>
#include <string>
#include <mutex>

#ifdef _MSC_VER
#pragma comment(lib, "Ws2_32.lib")
#endif

using namespace std;

string server_host = "127.0.0.1";
int server_port = 8888;

bool send_line(SOCKET s, const string &line){
    string out = line + "\n";
    int total = 0; int len = (int)out.size();
    const char *ptr = out.c_str();
    while(total < len){
        int sent = send(s, ptr+total, len-total, 0);
        if(sent == SOCKET_ERROR) return false;
        total += sent;
    }
    return true;
}

bool recv_line(SOCKET s, string &out){
    out.clear(); char buf[1];
    while(true){
        int n = recv(s, buf, 1, 0);
        if(n <= 0) return false;
        if(buf[0] == '\n') break;
        if(buf[0] == '\r') continue;
        out.push_back(buf[0]);
    }
    return true;
}

int main(){
    WSADATA wsData;
    if(WSAStartup(MAKEWORD(2,2), &wsData) != 0){ cerr << "WSAStartup failed\n"; return 1; }
    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if(sock == INVALID_SOCKET){ cerr << "socket failed\n"; return 1; }

    sockaddr_in hint;
    hint.sin_family = AF_INET;
    hint.sin_port = htons(server_port);
    inet_pton(AF_INET, server_host.c_str(), &hint.sin_addr);

    cout << "Connecting to " << server_host << ":" << server_port << "...\n";
    if(connect(sock, (sockaddr*)&hint, sizeof(hint)) == SOCKET_ERROR){ cerr << "connect failed\n"; return 1; }
    cout << "Connected. Type commands (REGISTER, LOGIN, AUTH, LIST_FRIENDS, SEND_FRIEND_REQUEST, ACCEPT_FRIEND_REQUEST, LOGOUT, QUIT) or use friendly shortcuts below.\n";

    // receiver thread
    thread recv_thread([&](){
        string line;
        while(recv_line(sock, line)){
            cout << "[Server] " << line << "\n";
        }
        cout << "Connection closed by server\n";
        exit(0);
    });

    // main input loop
    string input;
    while(true){
        getline(cin, input);
        if(input.empty()) continue;
        // convenient friendly shortcuts
        // register: /register user pass
        if(input.rfind("/register ",0) == 0){
            string rest = input.substr(10);
            send_line(sock, string("REGISTER ")+rest);
            continue;
        }
        if(input.rfind("/login ",0) == 0){ send_line(sock, string("LOGIN ")+input.substr(7)); continue; }
        if(input.rfind("/auth ",0) == 0){ send_line(sock, string("AUTH ")+input.substr(6)); continue; }
        if(input == "/list") { send_line(sock, "LIST_FRIENDS"); continue; }
        if(input.rfind("/add ",0) == 0){ send_line(sock, string("SEND_FRIEND_REQUEST ")+input.substr(5)); continue; }
        if(input.rfind("/accept ",0) == 0){ send_line(sock, string("ACCEPT_FRIEND_REQUEST ")+input.substr(8)); continue; }
        if(input == "/quit" || input == "/logout") { send_line(sock, "QUIT"); break; }

        // otherwise send input as-is (for advanced commands)
        send_line(sock, input);
    }

    recv_thread.join();
    closesocket(sock);
    WSACleanup();
    return 0;
}
