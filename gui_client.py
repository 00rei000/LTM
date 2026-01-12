#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyQt5 GUI Client - Clean & Professional Design with File Transfer
"""

import sys
import socket
import threading
import json
import os
import struct
import time
from pathlib import Path
from datetime import datetime
from queue import Queue
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QListWidget, 
                             QTextEdit, QTextBrowser, QMessageBox, QListWidgetItem, QFrame, QTabWidget, 
                             QScrollArea, QSizePolicy, QFileDialog, QProgressBar, QDialog)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QThread, QTimer
from PyQt5.QtGui import QFont

# ============================================================================
# Lớp mạng
# ============================================================================

# Hằng số truyền file
CHUNK_HEADER_SIZE = 8
CHUNK_SIZE = 65536  # 64KB

class NetworkSignals(QObject):
    message_received = pyqtSignal(str)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    login_success = pyqtSignal(str, str)  # phiên, tên_đăng_nhập
    login_failed = pyqtSignal(str)
    registration_success = pyqtSignal(str)  # tên_đăng_nhập đã đăng ký
    friends_updated = pyqtSignal(list)
    notification = pyqtSignal(str, str)
    # New signals for groups and messages
    groups_updated = pyqtSignal(list)  # danh sách (tên_nhóm, số_thành_viên)
    group_invites_updated = pyqtSignal(list)  # danh sách (tên_nhóm, người_mời)
    pending_requests_updated = pyqtSignal(list)  # danh sách tên_người_gửi
    text_message = pyqtSignal(str, str, str, str)  # loại, tên, người_gửi, nội_dung
    history_received = pyqtSignal(str, str, list)  # loại, tên, tin_nhắn
    more_history_received = pyqtSignal(list)  # chỉ tin nhắn (để tải thêm)
    members_received = pyqtSignal(str, list)  # tên_nhóm, danh_sách (tên_đăng_nhập, vai_trò, trạng_thái)
    left_group = pyqtSignal(str)  # tên_nhóm (khi user tự rời hoặc bị kick)
    # Tín hiệu truyền file
    file_notification = pyqtSignal(str, str, str, str, str)  # loại, đích, người_gửi, id_file, tên_file
    upload_ready = pyqtSignal(str, str)  # id_file, vị_trí
    upload_progress = pyqtSignal(str, int, int)  # id_file, bytes_đã_gửi, tổng_bytes
    upload_complete = pyqtSignal(str)  # id_file
    upload_failed = pyqtSignal(str, str)  # id_file, error
    download_ready = pyqtSignal(str, str, int)  # id_file, filename, filesize
    download_progress = pyqtSignal(str, int, int)  # id_file, bytes_received, total_bytes
    download_complete = pyqtSignal(str)  # id_file
    download_failed = pyqtSignal(str, str)  # id_file, error

class NetworkThread(QThread):
    def __init__(self, host, port, signals):
        super().__init__()
        self.host = host
        self.port = port
        self.signals = signals
        self.sock = None
        self.running = False
        self.session = None
        self.username = None
        self.pending_downloads = {}  # id_file -> (filename, save_path, filesize)
        
    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.running = True
            self.signals.connected.emit()
            
            buffer = ""
            self.sock.settimeout(None)
            
            while self.running:
                try:
                    raw_data = self.sock.recv(1024)
                    if not raw_data:
                        print("[ERROR] Socket closed by server")
                        break
                    
                    # Try to decode as UTF-8 text
                    try:
                        data = raw_data.decode('utf-8')
                    except UnicodeDecodeError as e:
                        # Binary data in buffer (probably residual after file transfer)
                        print(f"[WARNING] Received non-UTF8 data (probably binary residual), skipping: {e}")
                        continue
                    
                    buffer += data
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            try:
                                self.handle_message(line)
                            except Exception as e:
                                print(f"[ERROR] Failed to handle message '{line}': {e}")
                                import traceback
                                traceback.print_exc()
                except Exception as e:
                    print(f"[ERROR] recv() failed: {e}")
                    if self.running:
                        self.signals.message_received.emit(f"Error: {e}")
                    break
                    
        except Exception as e:
            self.signals.message_received.emit(f"Connection failed: {e}")
        finally:
            self.running = False
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
            self.signals.disconnected.emit()
    
    def handle_message(self, msg):
        self.signals.message_received.emit(f"[Server] {msg}")

        # Kiểm tra xem có phải là history line không
        # History line format: msgId|sender|timestamp|TYPE|length|content
        # msgId và timestamp phải là số
        # Chỉ xử lý nếu đang chờ history lines (_history_expected > 0)
        if hasattr(self, '_history_expected') and self._history_expected > 0:
            # Kiểm tra format: phải có ít nhất 5 dấu | và part đầu tiên là số
            if msg.count('|') >= 5:
                first_part = msg.split('|')[0]
                if first_part.isdigit():
                    # Đây là history line
                    self._history_buffer.append(msg)
                    self._history_expected -= 1
                    if self._history_expected == 0:
                        self.signals.history_received.emit("", "", self._history_buffer)
                        self._history_buffer = []
                    return  # Đã xử lý xong, không cần parse tiếp
        
        # Parse các loại message khác
        parts = msg.split(' ', 2)
        if len(parts) < 2:
            return
            
        if parts[0] == "SUCCESS":
            code = parts[1]
            data = parts[2] if len(parts) > 2 else ""
            
            if code == "200":
                if data.startswith("SESSION "):
                    session = data.split(' ', 1)[1]
                    self.session = session
                    # Send AUTH command to authenticate this connection with the session
                    self.send(f"AUTH {session}")
                    self.signals.login_success.emit(session, self.username)
                elif data.startswith("FRIENDS "):
                    friends_str = data.split(' ', 1)[1] if ' ' in data else ""
                    friends = []
                    if friends_str:
                        for f in friends_str.split():
                            if ':' in f:
                                name, status = f.split(':', 1)
                                friends.append((name, status))
                    self.signals.friends_updated.emit(friends)
                elif data.startswith("PENDING_REQUESTS "):
                    # SUCCESS 200 PENDING_REQUESTS user1 user2 user3
                    requests_str = data.split(' ', 1)[1] if ' ' in data else ""
                    requests = []
                    if requests_str:
                        requests = [r.strip() for r in requests_str.split() if r.strip()]
                    self.signals.pending_requests_updated.emit(requests)
                elif data.startswith("GROUP_INVITES "):
                    # SUCCESS 200 GROUP_INVITES group1:inviter1 group2:inviter2
                    invites_str = data.split(' ', 1)[1] if ' ' in data else ""
                    invites = []
                    if invites_str:
                        for inv in invites_str.split():
                            if ':' in inv:
                                group_name, inviter = inv.split(':', 1)
                                invites.append((group_name, inviter))
                    self.signals.group_invites_updated.emit(invites)
                elif data.startswith("GROUPS "):
                    groups_str = data.split(' ', 1)[1] if ' ' in data else ""
                    groups = []
                    if groups_str:
                        for g in groups_str.split():
                            if ':' in g:
                                name, count = g.split(':', 1)
                                groups.append((name, count))
                    self.signals.groups_updated.emit(groups)
                elif data.startswith("MEMBERS "):
                    # SUCCESS 200 MEMBERS user1:role:status user2:role:status ...
                    members_str = data.split(' ', 1)[1] if ' ' in data else ""
                    members = []
                    if members_str:
                        for m in members_str.split():
                            if ':' in m:
                                parts = m.split(':', 2)
                                if len(parts) >= 3:
                                    username, role, status = parts[0], parts[1], parts[2]
                                    members.append((username, role, status))
                    # Emit with empty group name (will be determined by context)
                    self.signals.members_received.emit("", members)
                elif data.startswith("READY_UPLOAD "):
                    # Server ready to receive file
                    file_id = data.split(' ')[1]
                    self.signals.upload_ready.emit(file_id, "0")
                elif data.startswith("LEFT "):
                    # SUCCESS 200 LEFT <group_name>
                    group_name = data.split(' ', 1)[1]
                    self.signals.left_group.emit(group_name)
                    self.signals.notification.emit("Left Group", f"You have left {group_name}")
                    # Refresh groups list
                    self.send("GET_GROUPS\n")
                elif data.startswith("LEFT_AND_DELETED "):
                    # SUCCESS 200 LEFT_AND_DELETED <group_name>
                    group_name = data.split(' ', 1)[1]
                    self.signals.left_group.emit(group_name)
                    self.signals.notification.emit("Group Deleted", f"You left and {group_name} was deleted")
                    # Refresh groups list
                    self.send("GET_GROUPS\n")
                elif data.startswith("START_UPLOAD "):
                    # Server ready, provides offset for resume
                    offset = data.split(' ')[1]
                    # Start uploading file immediately in this thread
                    if hasattr(self, 'upload_manager') and self.upload_manager.active_upload:
                        file_id, filepath, filename, filesize, _, _ = self.upload_manager.active_upload
                        self.upload_file_sync(filepath, filesize, int(offset))
                elif data.startswith("UPLOAD_COMPLETE"):
                    if hasattr(self, 'upload_manager'):
                        self.upload_manager.on_upload_complete_from_server()
                elif data.startswith("READY_DOWNLOAD "):
                    # SUCCESS 200 READY_DOWNLOAD <file_id> <filename> <filesize>
                    download_parts = data.split(' ', 3)
                    if len(download_parts) >= 4:
                        file_id = download_parts[1]
                        filename = download_parts[2]
                        filesize = int(download_parts[3])
                        
                        
                        # Look up save path from pending_downloads
                        if file_id in self.pending_downloads:
                            _, save_path, _ = self.pending_downloads[file_id]
                            
                            # Start downloading (this will block the recv loop temporarily)
                            self.download_file_sync(file_id, save_path, filesize)
                        else:
                            print(f"[ERROR] No pending download for file_id={file_id}")
                elif data.startswith("RESUME_DOWNLOAD "):
                    offset = data.split(' ')[1]
                    # Continue download from offset
                    self.signals.download_ready.emit("", "", int(offset))
                else:
                    # Possible HISTORY header: server uses multi-line response
                    # Formats supported:
                    #   SUCCESS 200 <N>
                    #   SUCCESS 200 HISTORY <N>
                    hdr = msg.strip().split()
                    n = -1
                    if len(hdr) >= 3 and hdr[0] == "SUCCESS" and hdr[1] == "200":
                        if len(hdr) == 3 and hdr[2].isdigit():
                            n = int(hdr[2])
                        elif len(hdr) >= 4 and hdr[2] == "HISTORY" and hdr[3].isdigit():
                            n = int(hdr[3])
                    if n >= 0:
                        # Expect next n lines as history; buffer them using state
                        self._history_expected = n
                        self._history_buffer = []
                        if n == 0:
                            # Emit empty history immediately
                            self.signals.history_received.emit("", "", [])
                        return  # handled
                    
            elif code == "201":
                if data.startswith("REGISTERED "):
                    user = data.split(' ', 1)[1]
                    self.signals.notification.emit("Success", f"Account created: {user}")
                    # Tự động LOGIN sau khi REGISTER thành công
                    self.signals.registration_success.emit(user)
                elif data.startswith("FRIEND_ADDED "):
                    friend = data.split(' ', 1)[1]
                    self.signals.notification.emit("Friend Added", f"{friend} is now your friend")
                    
        elif parts[0] == "FAIL":
            # Only treat as login failure before session is established.
            code = parts[1]
            msg_text = parts[2] if len(parts) > 2 else "Unknown error"
            
            # Xử lý NO_MESSAGES - chỉ hiển thị popup nếu là fetch history có time range
            if code == "404" and msg_text == "NO_MESSAGES":
                # Emit với flag để biết là FAIL, không phải SUCCESS với 0 messages
                self.signals.history_received.emit("", "", ["__NO_MESSAGES__"])
            elif not self.session:
                self.signals.login_failed.emit(msg_text)
            else:
                # For post-login errors, show a notification and keep the connection.
                try:
                    self.signals.notification.emit("Error", msg_text)
                except Exception:
                    self.signals.message_received.emit(f"Error: {msg_text}")
            
        elif msg.startswith("NOTIFY_FRIEND_REQUEST "):
            sender = msg.split(' ', 1)[1]
            self.signals.notification.emit("Friend Request", f"{sender} sent you a friend request")
            
        elif msg.startswith("NOTIFY_FRIEND_ACCEPTED "):
            friend = msg.split(' ', 1)[1]
            self.signals.notification.emit("Request Accepted", f"{friend} accepted your friend request")
            
        elif msg.startswith("NOTIFY_SESSION_EXPIRED "):
            self.signals.notification.emit("Session Expired", "Logged out from another device")
            
        # Handle TEXT messages (NOTIFY_TEXT from server)
        elif msg.startswith("NOTIFY_TEXT "):
            # Server format:
            # NOTIFY_TEXT U <sender> <timestamp> <content> (for user messages)
            # NOTIFY_TEXT G <group_name> <sender> <timestamp> <content> (for group messages)
            # Split into 3 parts so the rest contains everything after the type
            parts = msg.split(' ', 2)  # NOTIFY_TEXT + type + rest
            if len(parts) >= 3:
                msg_type = parts[1]  # U or G
                rest = parts[2]  # remaining content (sender ...)

                if msg_type == "U":
                    # Format: <sender> <timestamp> <content>
                    rest_parts = rest.split(' ', 2)
                    if len(rest_parts) >= 3:
                        sender = rest_parts[0]
                        timestamp = rest_parts[1]
                        content = rest_parts[2]
                        # For U type, conversation name = sender name
                        self.signals.text_message.emit(msg_type, sender, sender, content)

                elif msg_type == "G":
                    # Format: <group_name> <sender> <timestamp> <content>
                    rest_parts = rest.split(' ', 3)
                    if len(rest_parts) >= 4:
                        group_name = rest_parts[0]
                        sender = rest_parts[1]
                        timestamp = rest_parts[2]
                        content = rest_parts[3]
                        self.signals.text_message.emit(msg_type, group_name, sender, content)
                
        # (moved) HISTORY header is handled inside SUCCESS 200 branch above
        
                
        # Xử lý lời mời nhóm
        elif msg.startswith("NOTIFY_GROUP_INVITE "):
            # NOTIFY_GROUP_INVITE <group_name> <inviter>
            parts = msg.split(' ', 3)
            if len(parts) >= 3:
                group_name = parts[1]
                inviter = parts[2]
                self.signals.notification.emit("Group Invite", f"{inviter} invited you to {group_name}")
        
        # Xử lý khi bị kick khỏi nhóm
        elif msg.startswith("NOTIFY_EJECTED "):
            # NOTIFY_EJECTED <group_name> <admin>
            parts = msg.split(' ', 3)
            if len(parts) >= 3:
                group_name = parts[1]
                admin = parts[2]
                self.signals.notification.emit("Removed from Group", f"You were removed from {group_name} by {admin}")
                # Emit signal to close chat window
                self.signals.left_group.emit(group_name)
                # Refresh groups list
                self.send("GET_GROUPS\n")
        
        # Xử lý khi có member rời nhóm
        elif msg.startswith("NOTIFY_MEMBER_LEFT "):
            # NOTIFY_MEMBER_LEFT <group_name> <username>
            parts = msg.split(' ', 3)
            if len(parts) >= 3:
                group_name = parts[1]
                username = parts[2]
                self.signals.notification.emit("Member Left", f"{username} left {group_name}")
        
        # Xử lý khi được chỉ định làm admin mới
        elif msg.startswith("NOTIFY_NEW_ADMIN "):
            # NOTIFY_NEW_ADMIN <group_name>
            parts = msg.split(' ', 2)
            if len(parts) >= 2:
                group_name = parts[1]
                self.signals.notification.emit("New Group Admin", f"You are now the admin of {group_name}")
        
        # Handle file notifications
        elif msg.startswith("NOTIFY_FILE "):
            # NOTIFY_FILE <type> <target> <sender> <file_id> <filename>
            parts = msg.split(' ', 5)
            if len(parts) >= 6:
                file_type = parts[1]  # U or G
                target = parts[2]  # username or group_name
                sender = parts[3]
                file_id = parts[4]
                filename = parts[5]
                self.signals.file_notification.emit(file_type, target, sender, file_id, filename)
    
    def send(self, cmd):
        if self.sock and self.running:
            try:
                # Don't add newline if cmd already ends with it
                if not cmd.endswith('\n'):
                    cmd = cmd + '\n'
                self.sock.sendall(cmd.encode('utf-8'))
                return True
            except Exception as e:
                print(f"[ERROR] Failed to send: {e}")
                return False
        else:
            print(f"[ERROR] Cannot send - sock or running is False")
        return False
    
    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
    
    def recv_exact(self, n):
        """Receive exactly n bytes from socket"""
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed while receiving data")
            data += chunk
        return data
    
    def upload_file_sync(self, filepath, filesize, offset):
        """Upload file synchronously (called from network thread)"""
        try:
            
            with open(filepath, 'rb') as f:
                f.seek(offset)
                bytes_sent = offset
                
                while bytes_sent < filesize:
                    # Read chunk
                    chunk_size = min(CHUNK_SIZE, filesize - bytes_sent)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    
                    # Send binary chunk: [offset:4][length:4][data]
                    header = struct.pack('!II', bytes_sent, len(data))
                    self.sock.sendall(header + data)
                    
                    bytes_sent += len(data)
                    
                    # Emit progress signal
                    if hasattr(self, 'upload_manager') and self.upload_manager.active_upload:
                        file_id = self.upload_manager.active_upload[0]
                        self.signals.upload_progress.emit(file_id, bytes_sent, filesize)
                    
                
                # Send EOF marker
                eof_header = struct.pack('!II', bytes_sent, 0)
                self.sock.sendall(eof_header)
                
        except Exception as e:
            print(f"[ERROR] Upload failed: {e}")
            if hasattr(self, 'upload_manager') and self.upload_manager.active_upload:
                file_id = self.upload_manager.active_upload[0]
                self.signals.upload_failed.emit(file_id, str(e))
    
    def download_file_sync(self, file_id, save_path, filesize):
        """Download file synchronously (called from network thread)"""
        try:
            
            with open(save_path, 'wb') as f:
                bytes_received = 0
                
                while bytes_received < filesize:
                    # Read chunk header: [offset:4][length:4]
                    header = self.recv_exact(8)
                    offset, length = struct.unpack('!II', header)
                    
                    
                    # Check for EOF marker
                    if length == 0:
                        break
                    
                    # Read chunk data
                    data = self.recv_exact(length)
                    
                    # Write to file at offset
                    f.seek(offset)
                    f.write(data)
                    
                    bytes_received += length
                    
                    # Emit progress signal
                    self.signals.download_progress.emit(file_id, bytes_received, filesize)
                    
                
                self.signals.download_complete.emit(file_id)
                # Cleanup pending mapping (if any)
                try:
                    if hasattr(self, 'pending_downloads') and file_id in self.pending_downloads:
                        self.pending_downloads.pop(file_id, None)
                except Exception:
                    pass
                
        except Exception as e:
            print(f"[ERROR] Download failed: {e}")
            import traceback
            traceback.print_exc()

# ============================================================================
# Cửa sổ đăng nhập
# ============================================================================

class LoginWindow(QWidget):
    login_success = pyqtSignal(str, str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chat Client")
        self.setFixedSize(450, 520)
        self.net_thread = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(40, 30, 40, 40)
        layout.setSpacing(15)

        # Title
        title = QLabel("Chat Client")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Segoe UI", 22, QFont.Bold))
        title.setStyleSheet("color: #1a1a1a;")
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("Connect to Server")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("Segoe UI", 10))
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        layout.addSpacing(15)

        # Server
        server_label = QLabel("Server Address")
        server_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        server_label.setStyleSheet("color: #333;")
        layout.addWidget(server_label)
        
        self.server_input = QLineEdit()
        self.server_input.setPlaceholderText("Enter server address (e.g., 100.78.191.22:8888)")
        self.server_input.setText(self.load_config().get("server", "100.78.191.22:8888"))
        self.server_input.setMinimumHeight(45)
        self.server_input.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.server_input)

        layout.addSpacing(5)

        # Username
        username_label = QLabel("Username")
        username_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        username_label.setStyleSheet("color: #333;")
        layout.addWidget(username_label)
        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter your username")
        self.username_input.setText(self.load_config().get("username", ""))
        self.username_input.setMinimumHeight(45)
        self.username_input.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.username_input)

        layout.addSpacing(5)

        # Password
        password_label = QLabel("Password")
        password_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        password_label.setStyleSheet("color: #333;")
        layout.addWidget(password_label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setMinimumHeight(45)
        self.password_input.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.password_input)

        layout.addSpacing(20)

        # Buttons
        self.login_btn = QPushButton("Login")
        self.login_btn.setMinimumHeight(50)
        self.login_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self.handle_login)
        layout.addWidget(self.login_btn)

        self.register_btn = QPushButton("Create Account")
        self.register_btn.setMinimumHeight(50)
        self.register_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.register_btn.setCursor(Qt.PointingHandCursor)
        self.register_btn.clicked.connect(self.handle_register)
        layout.addWidget(self.register_btn)

        layout.addSpacing(10)

        # Status
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.status_label)

        layout.addStretch()
        self.setLayout(layout)
        
        # Modern styling
        self.setStyleSheet("""
            QWidget {
                background: #f5f5f5;
                font-family: 'Segoe UI', 'San Francisco', 'Helvetica Neue', Arial, sans-serif;
            }
            QLabel {
                background: transparent;
            }
            QLineEdit {
                padding: 12px 15px;
                border: 2px solid #ddd;
                border-radius: 8px;
                background: white;
                font-size: 13px;
                color: #1a1a1a;
            }
            QLineEdit:focus {
                border: 2px solid #0066cc;
                background: #fff;
            }
            QLineEdit::placeholder {
                color: #999;
            }
            QPushButton {
                padding: 14px 20px;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                opacity: 0.9;
            }
        """)
        
        # Login button specific style
        self.login_btn.setStyleSheet(self.login_btn.styleSheet() + """
            QPushButton {
                background: #0066cc;
                color: white;
            }
            QPushButton:hover {
                background: #0052a3;
            }
        """)
        
        # Register button specific style
        self.register_btn.setStyleSheet(self.register_btn.styleSheet() + """
            QPushButton {
                background: white;
                color: #0066cc;
                border: 2px solid #0066cc;
            }
            QPushButton:hover {
                background: #f0f7ff;
            }
        """)

    def load_config(self):
        config_dir = Path.home() / "AppData" / "Roaming" / "LTM"
        config_file = config_dir / "config.json"
        try:
            if config_file.exists():
                with open(config_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}

    def save_config(self, server, username):
        config_dir = Path.home() / "AppData" / "Roaming" / "LTM"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        try:
            with open(config_file, 'w') as f:
                json.dump({"server": server, "username": username}, f)
        except:
            pass

    def handle_login(self):
        self.attempt_auth("LOGIN")

    def handle_register(self):
        self.attempt_auth("REGISTER")

    def attempt_auth(self, cmd_type):
        server = self.server_input.text().strip()
        username = self.username_input.text().strip()
        password = self.password_input.text().strip()

        if not server or not username or not password:
            self.status_label.setText("Please fill all fields")
            self.status_label.setStyleSheet("color: red;")
            return

        if ':' not in server:
            self.status_label.setText("Server format: host:port")
            self.status_label.setStyleSheet("color: red;")
            return

        try:
            host, port = server.rsplit(':', 1)
            port = int(port)
        except:
            self.status_label.setText("Invalid server format")
            self.status_label.setStyleSheet("color: red;")
            return

        self.status_label.setText("Connecting...")
        self.status_label.setStyleSheet("color: blue;")
        self.login_btn.setEnabled(False)
        self.register_btn.setEnabled(False)

        # Lưu server info để dùng sau khi register
        self.current_server = server
        self.current_username = username
        self.current_password = password

        signals = NetworkSignals()
        signals.connected.connect(lambda: self.on_connected(cmd_type, username, password))
        signals.login_success.connect(lambda s, u: self.on_login_success(server, u, s))
        signals.login_failed.connect(self.on_login_failed)
        signals.registration_success.connect(lambda u: self.on_registration_success(username, password))
        signals.message_received.connect(lambda msg: None)

        self.net_thread = NetworkThread(host, port, signals)
        self.net_thread.username = username
        self.net_thread.start()

    def on_connected(self, cmd_type, username, password):
        cmd = f"{cmd_type} {username} {password}"
        self.net_thread.send(cmd)

    def on_registration_success(self, username, password):
        """Sau khi REGISTER thành công, tự động LOGIN"""
        self.status_label.setText("Account created! Logging in...")
        self.status_label.setStyleSheet("color: green;")
        # Đợi một chút để server xử lý xong REGISTER
        QTimer.singleShot(300, lambda: self.send_login_after_register(username, password))
    
    def send_login_after_register(self, username, password):
        """Gửi lệnh LOGIN sau khi REGISTER"""
        cmd = f"LOGIN {username} {password}"
        if self.net_thread and self.net_thread.send(cmd):
            self.status_label.setText("Logging in...")
        else:
            self.status_label.setText("Login failed - please restart")
            self.status_label.setStyleSheet("color: red;")
            self.login_btn.setEnabled(True)
            self.register_btn.setEnabled(True)

    def on_login_success(self, server, username, session):
        self.save_config(server, username)
        self.status_label.setText("Success!")
        self.status_label.setStyleSheet("color: green;")
        QTimer.singleShot(500, lambda: self.open_main_window(server, username, session))

    def on_login_failed(self, error):
        self.status_label.setText(error)
        self.status_label.setStyleSheet("color: red;")
        self.login_btn.setEnabled(True)
        self.register_btn.setEnabled(True)
        if self.net_thread:
            self.net_thread.stop()

    def open_main_window(self, server, username, session):
        self.login_success.emit(server, username, session)
        self.close()

# ============================================================================
# Cửa sổ chính
# ============================================================================

class ChatWindow(QWidget):
    """Chat window for 1-1 or group messaging"""
    def __init__(self, network_thread, username, chat_type, chat_name, parent=None):
        super().__init__(parent)
        self.network = network_thread
        self.username = username
        self.chat_type = chat_type  # 'U' or 'G'
        self.chat_name = chat_name
        self.loading_more = False  # Cờ để tải thêm lịch sử
        self.oldest_message_ts = None  # Theo dõi tin nhắn cũ nhất để phân trang
        
        # Header hiện đại
        self.setWindowTitle("Messenger Chat")
        self.resize(600, 540)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QHBoxLayout()
        header_widget = QWidget()
        header_widget.setStyleSheet("background: #222; border-bottom: 1px solid #eee;")
        header_widget.setFixedHeight(64)
        avatar = QLabel()
        avatar.setFixedSize(40, 40)
        avatar.setStyleSheet("border-radius: 20px; background: #888;")
        header.addWidget(avatar)
        header.addSpacing(12)
        name_label = QLabel(f"{self.chat_name}")
        name_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        header.addWidget(name_label)
        header.addStretch(1)
        status_label = QLabel("● Online")
        status_label.setStyleSheet("color: #44ff44; font-size: 13px; font-weight: bold;")
        header.addWidget(status_label)
        header_widget.setLayout(header)
        main_layout.addWidget(header_widget)

        # Nút Tải thêm
        self.load_more_btn = QPushButton("Load More Messages")
        self.load_more_btn.setVisible(False)
        self.load_more_btn.clicked.connect(self.load_more_history)
        self.load_more_btn.setStyleSheet("""
            QPushButton {
                background: #f5f5f5;
                border: 1px solid #ddd;
                padding: 7px 18px;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #e0e0e0;
            }
        """)
        main_layout.addWidget(self.load_more_btn)

        # Khu vực hiển thị tin nhắn - panel bubble kiểu Messenger
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: #f7f8fa;
                border: none;
            }
        """)
        self.panel = QWidget()
        self.panel.setStyleSheet("background: #f7f8fa;")
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(12, 12, 12, 12)
        self.panel_layout.setSpacing(8)
        self.panel_layout.addStretch(1)
        self.scroll.setWidget(self.panel)
        main_layout.addWidget(self.scroll)

        # Khu vực nhập liệu
        input_widget = QWidget()
        input_widget.setStyleSheet("background: #fff; border-top: 1px solid #eee;")
        input_layout = QHBoxLayout(input_widget)
        input_layout.setContentsMargins(16, 10, 16, 10)
        input_layout.setSpacing(10)
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type a message...")
        self.message_input.setFont(QFont("Segoe UI", 12))
        self.message_input.setStyleSheet("""
            QLineEdit {
                background: #f5f5f5;
                border: 1.5px solid #ddd;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 15px;
                color: #222;
            }
            QLineEdit:focus {
                border: 1.5px solid #0066cc;
                background: #fff;
            }
        """)
        self.message_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.message_input)
        send_btn = QPushButton("Send")
        send_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0084ff, stop:1 #44aaff);
                color: white;
                border: none;
                padding: 10px 28px;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }
            QPushButton:hover {
                background: #0052a3;
            }
        """)
        send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(send_btn)
        main_layout.addWidget(input_widget)

        # Kết nối tín hiệu
        self.network.signals.text_message.connect(self.on_text_message)
        self.network.signals.history_received.connect(self.on_history_received)

        # Tải lịch sử khi mở
        self.load_history()
        self.setStyleSheet("background: #f7f8fa;")
    
    def load_history(self):
        """Request message history from server"""
        # HISTORY <loại> <tên> <giới_hạn> <ts_bắt_đầu> <ts_kết_thúc>
        # Tải 50 tin nhắn gần nhất
        # Luồng chính: luôn yêu cầu lịch sử để hiển thị hội thoại
        cmd = f"HISTORY {self.chat_type} {self.chat_name} 50 0 0\n"
        self.network.send(cmd)
    
    def send_message(self):
        """Send text message"""
        text = self.message_input.text().strip()
        if not text:
            return
        # TEXT <loại> <tên> <nội_dung>
        cmd = f"TEXT {self.chat_type} {self.chat_name} {text}\n"
        self.network.send(cmd)
        self.message_input.clear()
        
        # Ghi nhận tin nhắn local cuối để khử trùng và hiển thị cục bộ
        import time
        self.last_local_message = text
        self.last_local_msg_ts = time.time()
        self.append_message(self.username, text)
    
    def on_text_message(self, msg_type, name, sender, content):
        """Handle incoming text message with deduplication"""
        if msg_type == self.chat_type and name == self.chat_name:
            try:
                import time
                sender_norm = sender.strip().lower()
                my_norm = self.username.strip().lower()
                if sender_norm == my_norm and hasattr(self, 'last_local_message'):
                    if content == self.last_local_message and (time.time() - self.last_local_msg_ts) < 5:
                        pass  # Không hiển thị, đã có rồi
                    else:
                        self.append_message(sender, content)
                else:
                    self.append_message(sender, content)
            except Exception as e:
                print(f"[ERROR] on_text_message dedupe check failed: {e}")
                self.append_message(sender, content)
    
    def on_history_received(self, msg_type, name, messages):
        """Display history messages (support both 6-part new format and 4-part legacy)."""
        # Kiểm tra xem có phải yêu cầu tải thêm không
        if self.loading_more:
            self.loading_more = False
            self.on_more_history_received(messages)
            return
        
        # Kiểm tra nếu không có tin nhắn
        if not messages or (len(messages) == 1 and not messages[0].strip()):
            QMessageBox.information(self, "Lịch sử", "Không có tin nhắn hoặc file nào trong khoảng thời gian này.")
            return
        
        # Tải lịch sử ban đầu - xóa và hiển thị
        # Xóa tất cả widget từ panel trừ phần stretch
        while self.panel_layout.count() > 1:
            item = self.panel_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.oldest_message_ts = None
        
        for msg in messages:
            if not msg.strip():
                continue
            # New format: msgId|sender|timestamp|TYPE|length|content
            parts6 = msg.split('|', 5)
            sender = content = timestamp = None
            msg_type_txt = None
            if len(parts6) >= 6 and parts6[0].isdigit():
                _msg_id, sender, ts, msg_type_txt, _length, content = parts6
                timestamp = ts
            else:
                # Legacy: timestamp|sender|TYPE|content
                parts4 = msg.split('|', 3)
                if len(parts4) >= 4:
                    timestamp, sender, msg_type_txt, content = parts4
            if sender is None:
                continue
            # Track oldest message
            try:
                ts = int(timestamp)
                if self.oldest_message_ts is None or ts < self.oldest_message_ts:
                    self.oldest_message_ts = ts
            except:
                pass
            # Only render TEXT for now; file rendering can be added later
            if msg_type_txt == "TEXT":
                self.append_message(sender, content, timestamp)
        
        # Hiển thị nút tải thêm nếu có tin nhắn
        if self.oldest_message_ts:
            self.load_more_btn.setVisible(True)
    
    def on_more_history_received(self, messages):
        """Prepend older messages when loading more"""
        if not messages:
            return
        
        # Thêm tin nhắn cũ (chèn ở vị trí 0, trước tin nhắn hiện có)
        insert_position = 0
        for msg in messages:
            if not msg.strip():
                continue
            parts6 = msg.split('|', 5)
            sender = content = timestamp = None
            msg_type_txt = None
            if len(parts6) >= 6 and parts6[0].isdigit():
                _msg_id, sender, ts, msg_type_txt, _length, content = parts6
                timestamp = ts
            else:
                parts4 = msg.split('|', 3)
                if len(parts4) >= 4:
                    timestamp, sender, msg_type_txt, content = parts4
            if sender is None:
                continue
            try:
                ts = int(timestamp)
                if self.oldest_message_ts is None or ts < self.oldest_message_ts:
                    self.oldest_message_ts = ts
            except:
                pass
            if msg_type_txt == "TEXT":
                # For simplicity, append to bottom; advanced: insert at top
                self.append_message(sender, content, timestamp)
    
    def load_more_history(self):
        """Load older messages"""
        if not self.chat_type or not self.chat_name or not self.oldest_message_ts:
            return
        
        # Đặt cờ để biết đây là tải thêm
        self.loading_more = True
        # Yêu cầu tin nhắn trước oldest_message_ts
        cmd = f"HISTORY {self.chat_type} {self.chat_name} 20 0 {self.oldest_message_ts}\n"
        self.network.send(cmd)
    
    def append_message(self, sender, content, timestamp=None):
        """Add Messenger-style bubble to panel"""
        from datetime import datetime
        if timestamp:
            try:
                ts = int(timestamp)
                dt = datetime.fromtimestamp(ts)
                time_str = dt.strftime("%H:%M")
            except:
                time_str = ""
        else:
            time_str = datetime.now().strftime("%H:%M")
        
        sender_norm = sender.strip().lower()
        username_norm = self.username.strip().lower()
        is_self = (sender_norm == username_norm)
        
        # Tạo bubble tin nhắn
        bubble_widget = QWidget()
        bubble_layout = QVBoxLayout(bubble_widget)
        bubble_layout.setContentsMargins(0, 0, 0, 0)
        bubble_layout.setSpacing(2)
        
        # Tên người gửi (chỉ cho tin nhắn của người khác)
        if not is_self:
            sender_label = QLabel(sender)
            sender_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #555; padding-left: 12px;")
            bubble_layout.addWidget(sender_label)
        
        # Bubble nội dung tin nhắn
        msg_label = QLabel(content)
        msg_label.setWordWrap(True)
        msg_label.setTextFormat(Qt.PlainText)
        msg_label.setMaximumWidth(380)
        msg_label.setStyleSheet(f"""
            QLabel {{
                background-color: {'#0084ff' if is_self else '#e4e6eb'};
                color: {'white' if is_self else '#050505'};
                border-radius: 18px;
                padding: 10px 14px;
                font-size: 15px;
                font-family: 'Segoe UI', sans-serif;
            }}
        """)
        
        # Nhãn thời gian
        time_label = QLabel(time_str)
        time_label.setStyleSheet(f"""
            font-size: 11px; 
            color: #888; 
            padding-{'right' if is_self else 'left'}: 12px;
        """)
        
        # Layout cho bubble + thời gian
        content_layout = QHBoxLayout()
        content_layout.setSpacing(4)
        if is_self:
            content_layout.addStretch(1)
            bubble_layout.addWidget(msg_label, alignment=Qt.AlignRight)
            bubble_layout.addWidget(time_label, alignment=Qt.AlignRight)
        else:
            bubble_layout.addWidget(msg_label, alignment=Qt.AlignLeft)
            bubble_layout.addWidget(time_label, alignment=Qt.AlignLeft)
            content_layout.addStretch(1)
        
        # Chèn trước phần stretch ở cuối
        self.panel_layout.insertWidget(self.panel_layout.count()-1, bubble_widget)
        
        # Tự động cuộn xuống cuối
        QApplication.processEvents()
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())


# ============================================================================
# Trình quản lý truyền file
# ============================================================================

class FileTransferWorker(QThread):
    """Worker thread for uploading/downloading files"""
    progress = pyqtSignal(str, int, int)  # id_file/path, bytes_transferred, total
    completed = pyqtSignal(str)  # id_file/path
    failed = pyqtSignal(str, str)  # id_file/path, error_message
    
    def __init__(self, mode, sock, file_id, filepath, filesize, offset=0):
        super().__init__()
        self.mode = mode  # 'upload' hoặc 'download'
        self.sock = sock
        self.file_id = file_id
        self.filepath = filepath
        self.filesize = filesize
        self.offset = offset
        self.cancelled = False
        
    def run(self):
        try:
            if self.mode == 'upload':
                self.upload_file()
            elif self.mode == 'download':
                self.download_file()
        except Exception as e:
            self.failed.emit(self.file_id, str(e))
    
    def upload_file(self):
        """Upload file with binary chunks"""
        try:
            with open(self.filepath, 'rb') as f:
                f.seek(self.offset)
                bytes_sent = self.offset
                
                while bytes_sent < self.filesize:
                    if self.cancelled:
                        return
                    
                    # Read chunk
                    chunk_size = min(CHUNK_SIZE, self.filesize - bytes_sent)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    
                    # Send binary chunk: [offset:4][length:4][data]
                    header = struct.pack('!II', bytes_sent, len(data))
                    self.sock.sendall(header + data)
                    
                    bytes_sent += len(data)
                    self.progress.emit(self.file_id, bytes_sent, self.filesize)
                
                # Send EOF marker
                eof_header = struct.pack('!II', bytes_sent, 0)
                self.sock.sendall(eof_header)
                
                self.completed.emit(self.file_id)
        except Exception as e:
            self.failed.emit(self.file_id, f"Upload error: {str(e)}")
    
    def download_file(self):
        """Download file with binary chunks"""
        try:
            mode = 'ab' if self.offset > 0 else 'wb'
            with open(self.filepath, mode) as f:
                bytes_received = self.offset
                
                while bytes_received < self.filesize:
                    if self.cancelled:
                        return
                    
                    # Receive chunk header: [offset:4][length:4]
                    header = self.recv_exact(8)
                    if not header:
                        break
                    
                    offset, length = struct.unpack('!II', header)
                    
                    # EOF marker
                    if length == 0:
                        break
                    
                    # Receive payload
                    data = self.recv_exact(length)
                    if not data:
                        break
                    
                    f.write(data)
                    bytes_received += length
                    self.progress.emit(self.file_id, bytes_received, self.filesize)
                
                self.completed.emit(self.file_id)
        except Exception as e:
            self.failed.emit(self.file_id, f"Download error: {str(e)}")
    
    def recv_exact(self, n):
        """Receive exactly n bytes"""
        data = b''
        while len(data) < n:
            if self.cancelled:
                return None
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def cancel(self):
        self.cancelled = True


class UploadQueueManager(QObject):
    """Manages sequential upload queue"""
    queue_updated = pyqtSignal(list)  # Danh sách upload đang chờ
    upload_started = pyqtSignal(str, str)  # id_file, filename
    
    def __init__(self, network_thread):
        super().__init__()
        self.network = network_thread
        self.queue = Queue()
        self.current_worker = None
        self.pending_uploads = []  # [(đường_dẫn_file, loại_đích, tên_đích)]
        self.active_upload = None  # (id_file, đường_dẫn_file, tên_file, kích_thước)
        
    def add_files(self, filepaths, target_type, target_name):
        """Add files to upload queue"""
        for filepath in filepaths:
            if os.path.exists(filepath):
                self.pending_uploads.append((filepath, target_type, target_name))
        self.queue_updated.emit(self.pending_uploads)
        
        # Bắt đầu upload nếu chưa hoạt động
        if not self.current_worker or not self.current_worker.isRunning():
            self.process_next()
    
    def process_next(self):
        """Process next file in queue"""
        if not self.pending_uploads:
            return
        
        filepath, target_type, target_name = self.pending_uploads.pop(0)
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        
        # Gửi REQ_UPLOAD
        cmd = f"REQ_UPLOAD {target_type} {target_name} {filename} {filesize}\n"
        self.network.send(cmd)
        
        # Lưu lại để dùng khi nhận READY_UPLOAD
        self.active_upload = (None, filepath, filename, filesize, target_type, target_name)
        self.queue_updated.emit(self.pending_uploads)
    
    def on_ready_upload(self, file_id, offset):
        """Server is ready to receive file"""
        if not self.active_upload:
            return
        
        _, filepath, filename, filesize, target_type, target_name = self.active_upload
        self.active_upload = (file_id, filepath, filename, filesize, target_type, target_name)
        
        # Gửi lệnh UPLOAD_DATA
        cmd = f"UPLOAD_DATA {file_id}\n"
        result = self.network.send(cmd)
        
        # Đợi phản hồi START_UPLOAD, sau đó bắt đầu worker
        # (Sẽ được xử lý trong network thread)
        self.upload_started.emit(file_id, filename)
    
    def start_upload_worker(self, offset):
        """Start binary upload worker"""
        if not self.active_upload:
            return
        
        file_id, filepath, filename, filesize, _, _ = self.active_upload
        
        self.current_worker = FileTransferWorker('upload', self.network.sock, 
                                                 file_id, filepath, filesize, int(offset))
        self.current_worker.progress.connect(self.on_progress)
        self.current_worker.completed.connect(self.on_complete)
        self.current_worker.failed.connect(self.on_failed)
        self.current_worker.start()
    
    def on_progress(self, file_id, bytes_sent, total):
        """Upload progress"""
        self.network.signals.upload_progress.emit(file_id, bytes_sent, total)
    
    def on_complete(self, file_id):
        """Upload completed"""
        # Server sẽ gửi phản hồi UPLOAD_COMPLETE
        pass
    
    def on_failed(self, file_id, error):
        """Upload failed"""
        self.network.signals.upload_failed.emit(file_id, error)
        self.active_upload = None
        self.process_next()  # Tiếp tục với file tiếp theo
    
    def on_upload_complete_from_server(self):
        """Server confirmed upload complete"""
        if self.active_upload:
            file_id = self.active_upload[0]
            self.network.signals.upload_complete.emit(file_id)
            self.active_upload = None
        
        # Xử lý file tiếp theo trong hàng đợi
        self.process_next()
    
    def cancel_current(self):
        """Cancel current upload"""
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.current_worker.wait()
        
        if self.active_upload:
            file_id = self.active_upload[0]
            if file_id:
                cmd = f"REQ_CANCEL_UPLOAD {file_id}\n"
                self.network.send(cmd)
        
        self.active_upload = None
        self.process_next()


# ============================================================================
# Cửa sổ chính
# ============================================================================

class MainWindow(QWidget):
    def __init__(self, server, username, session, net_thread):
        super().__init__()
        self.server = server
        self.username = username
        self.session = session
        self.net_thread = net_thread
        # Track last locally-sent message to avoid duplicate when server echoes it back
        self.last_local_message = None
        self.last_local_msg_ts = 0
        self.pending_requests = []
        self.group_invites = []  # Store group invites: list of (group_name, inviter)
        self.chat_windows = {}  # Track open chat windows: name -> ChatWindow
        self.recent_chats = []  # List of recent conversations
        
        # File transfer management
        self.upload_manager = UploadQueueManager(net_thread)
        net_thread.upload_manager = self.upload_manager  # Link for callbacks
        self.file_notifications = []  # List of (type, target, sender, file_id, filename)
        self.active_downloads = {}  # id_file -> (worker, progress_bar, filepath)
        # Track local uploads (to show file bubble on sender side when complete)
        self.local_uploads = {}  # id_file -> (filename, target_type, target_name)
        
        self.setWindowTitle(f"Chat - {username}")
        self.setMinimumSize(1200, 800)
        
        self.net_thread.signals.friends_updated.connect(self.update_friends_list)
        self.net_thread.signals.groups_updated.connect(self.update_groups_list)
        self.net_thread.signals.pending_requests_updated.connect(self.update_pending_requests)
        self.net_thread.signals.group_invites_updated.connect(self.update_group_invites)
        self.net_thread.signals.notification.connect(self.show_notification)
        self.net_thread.signals.message_received.connect(self.log_message)
        self.net_thread.signals.disconnected.connect(self.on_disconnected)
        self.net_thread.signals.text_message.connect(self.on_new_message)
        self.net_thread.signals.history_received.connect(self.on_history_received)
        self.net_thread.signals.members_received.connect(self.show_members_dialog)
        self.net_thread.signals.left_group.connect(self.on_left_group)
        
        # Tín hiệu truyền file
        self.net_thread.signals.file_notification.connect(self.on_file_notification)
        self.net_thread.signals.upload_ready.connect(self.upload_manager.on_ready_upload)
        self.net_thread.signals.upload_progress.connect(self.on_upload_progress)
        self.net_thread.signals.upload_complete.connect(self.on_upload_complete)
        self.net_thread.signals.upload_failed.connect(self.on_upload_failed)
        self.upload_manager.upload_started.connect(self.on_upload_started)
        
        self.init_ui()
        self.refresh_friends()
        self.refresh_groups()
        # Load pending notifications on login
        QTimer.singleShot(500, self.load_pending_notifications)

    # Track last date shown in chat for inserting day separators
        self._last_date_shown = None
        
        # Auto-refresh friends status every 5 seconds
    # Remove auto-refresh friends

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(15, 10, 15, 10)
        header_widget = QWidget()
        header_widget.setStyleSheet("background-color: #555; color: white;")
        
        user_label = QLabel(f"Chat - {self.username}")
        user_label.setFont(QFont("Arial", 13, QFont.Bold))
        user_label.setStyleSheet("color: white;")
        header.addWidget(user_label)
        
        header.addStretch()
        
        # Notifications button with badge
        notif_btn = QPushButton("Notifications")
        notif_btn.setFixedSize(140, 30)
        notif_btn.setStyleSheet("""
            QPushButton {
                background-color: white;
                color: #333;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
            }
        """)
        notif_btn.clicked.connect(self.show_notifications_popup)
        header.addWidget(notif_btn)
        self.notif_btn = notif_btn
        
        logout_btn = QPushButton("Logout")
        logout_btn.setFixedSize(80, 30)
        logout_btn.setStyleSheet("""
            QPushButton {
                background-color: white;
                color: #333;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
            }
        """)
        logout_btn.clicked.connect(self.handle_logout)
        header.addWidget(logout_btn)
        
        header_widget.setLayout(header)
        main_layout.addWidget(header_widget)

        # Main content: 3 columns layout
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # Column 1: Recent Conversations (Left sidebar)
        conversations_panel = QVBoxLayout()
        conversations_panel.setContentsMargins(10, 10, 10, 10)
        
        conv_header = QHBoxLayout()
        conv_label = QLabel("Chats")
        conv_label.setFont(QFont("Arial", 14, QFont.Bold))
        conv_header.addWidget(conv_label)
        conv_header.addStretch()
        conversations_panel.addLayout(conv_header)
        
        self.conversations_list = QListWidget()
        self.conversations_list.itemClicked.connect(self.show_conversation_in_panel)
        self.conversations_list.setStyleSheet("""
            QListWidget {
                border: none;
                background-color: white;
                color: #000;
            }
            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #e0e0e0;
                color: #000;
            }
            QListWidget::item:hover {
                background-color: #f5f5f5;
            }
            QListWidget::item:selected {
                background-color: #e0e0e0;
                border-left: 3px solid #666;
            }
        """)
        conversations_panel.addWidget(self.conversations_list)
        
        conv_widget = QWidget()
        conv_widget.setLayout(conversations_panel)
        conv_widget.setStyleSheet("background-color: white;")
        conv_widget.setFixedWidth(280)
        content.addWidget(conv_widget)

        # Column 2: Chat Content (Center - Main area)
        chat_panel = QVBoxLayout()
        chat_panel.setContentsMargins(0, 0, 0, 0)
        chat_panel.setSpacing(0)
        
        # Chat header
        self.chat_header = QLabel("Select a conversation")
        self.chat_header.setFont(QFont("Arial", 14, QFont.Bold))
        self.chat_header.setStyleSheet("""
            QLabel {
                padding: 15px;
                background-color: #f8f9fa;
                border-bottom: 1px solid #ddd;
                color: #333;
            }
        """)
        chat_panel.addWidget(self.chat_header)
        
        # Header actions: History button + Load More (hidden)
        header_actions = QHBoxLayout()
        header_actions.setContentsMargins(10, 5, 10, 5)
        self.history_btn = QPushButton("History")
        self.history_btn.clicked.connect(self.open_history_dialog)
        self.history_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #333;
                border: 1px solid #ddd;
                padding: 8px 12px;
                margin: 5px;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        header_actions.addWidget(self.history_btn)
        self.load_more_btn = QPushButton("Load More Messages")
        self.load_more_btn.setVisible(False)
        self.load_more_btn.clicked.connect(self.load_more_history)
        self.load_more_btn.setStyleSheet("""
            QPushButton { background-color: #f0f0f0; color: #333; border: 1px solid #ddd; padding: 8px 12px; margin: 5px; }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        header_actions.addWidget(self.load_more_btn)
        chat_panel.addLayout(header_actions)
        
        # Messages area
        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        # Use a consistent font across clients (prefer Segoe UI on Windows, fallback to Arial)
        self.chat_display.setFont(QFont("Segoe UI", 12))
        # Prevent QTextBrowser from navigating (internal or external), we'll handle anchorClicked ourselves
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self.handle_download_link_click)  # Connect click event
        self.chat_display.setStyleSheet("""
            QTextBrowser {
                border: none;
                background-color: white;
                padding: 10px;
                color: #333;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
                line-height: 1.4;
            }
        """)
        chat_panel.addWidget(self.chat_display)
        
        # Track oldest message timestamp for pagination
        self.oldest_message_ts = None
        
        # Khu vực nhập liệu
        input_layout = QHBoxLayout()
        input_layout.setContentsMargins(15, 15, 15, 15)
        
        # Send File button
        self.send_file_btn = QPushButton("File")
        self.send_file_btn.setFont(QFont("Arial", 11, QFont.Bold))
        self.send_file_btn.setMinimumHeight(45)
        self.send_file_btn.setFixedWidth(90)
        self.send_file_btn.setEnabled(False)
        self.send_file_btn.clicked.connect(self.handle_send_file)
        self.send_file_btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #333;
                border: 1px solid #ccc;
                padding: 10px 15px;
                border-radius: 20px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:disabled {
                background-color: #f5f5f5;
                color: #999;
            }
        """)
        input_layout.addWidget(self.send_file_btn)
        
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Select a conversation to start chatting...")
        self.message_input.setFont(QFont("Arial", 11))
        self.message_input.setMinimumHeight(45)
        self.message_input.setEnabled(False)  # Disable until conversation selected
        self.message_input.returnPressed.connect(self.send_message_from_panel)
        self.message_input.setStyleSheet("""
            QLineEdit {
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 20px;
                color: #333;
            }
            QLineEdit:disabled {
                background-color: #f5f5f5;
                color: #999;
            }
        """)
        input_layout.addWidget(self.message_input)
        
        self.send_btn = QPushButton("Send")
        self.send_btn.setFont(QFont("Arial", 11, QFont.Bold))
        self.send_btn.setMinimumHeight(45)
        self.send_btn.setFixedWidth(100)
        self.send_btn.setEnabled(False)  # Disable until conversation selected
        self.send_btn.clicked.connect(self.send_message_from_panel)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 10px 25px;
                border-radius: 20px;
            }
            QPushButton:hover {
                background-color: #555;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #999;
            }
        """)
        input_layout.addWidget(self.send_btn)
        
        chat_panel.addLayout(input_layout)
        
        chat_widget = QWidget()
        chat_widget.setLayout(chat_panel)
        chat_widget.setStyleSheet("background-color: white;")
        content.addWidget(chat_widget, 3)  # Main area
        
        # Track current chat
        self.current_chat_type = None  # 'U' or 'G'
        self.current_chat_name = None

        # Column 3: Left Sidebar - Friends & Groups
        left_sidebar = QVBoxLayout()
        left_sidebar.setContentsMargins(0, 0, 0, 0)
        left_sidebar.setSpacing(0)
        
        left_tabs = QTabWidget()
        left_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #f8f9fa;
            }
            QTabBar::tab {
                padding: 10px 20px;
                background-color: #e0e0e0;
                border: none;
            }
            QTabBar::tab:selected {
                background-color: #f8f9fa;
                font-weight: bold;
            }
        """)
        
        # Tab 1: Friends
        friends_tab = QWidget()
        friends_layout = QVBoxLayout()
        friends_layout.setContentsMargins(10, 10, 10, 10)
        
        friends_header_layout = QHBoxLayout()
        friends_label = QLabel("Friends")
        friends_label.setFont(QFont("Arial", 12, QFont.Bold))
        friends_header_layout.addWidget(friends_label)
        friends_header_layout.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(80)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_friends)
        friends_header_layout.addWidget(refresh_btn)
        friends_layout.addLayout(friends_header_layout)
        
        self.friends_list = QListWidget()
        self.friends_list.itemClicked.connect(self.open_chat_with_friend)
        self.friends_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
            }
            QListWidget::item:hover {
                background-color: #e8f4f8;
            }
        """)
        friends_layout.addWidget(self.friends_list)
        
        add_friend_layout = QHBoxLayout()
        self.add_friend_input = QLineEdit()
        self.add_friend_input.setPlaceholderText("Username")
        self.add_friend_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)
        add_friend_layout.addWidget(self.add_friend_input)
        
        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(60)
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        add_btn.clicked.connect(self.handle_add_friend)
        add_friend_layout.addWidget(add_btn)
        friends_layout.addLayout(add_friend_layout)
        
        friends_tab.setLayout(friends_layout)
        
        # Tab 2: Groups
        groups_tab = QWidget()
        groups_layout = QVBoxLayout()
        groups_layout.setContentsMargins(10, 10, 10, 10)
        
        groups_header_layout = QHBoxLayout()
        groups_label = QLabel("Groups")
        groups_label.setFont(QFont("Arial", 12, QFont.Bold))
        groups_header_layout.addWidget(groups_label)
        groups_header_layout.addStretch()
        refresh_groups_btn = QPushButton("Refresh")
        refresh_groups_btn.setFixedWidth(80)
        refresh_groups_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        refresh_groups_btn.clicked.connect(self.refresh_groups)
        groups_header_layout.addWidget(refresh_groups_btn)
        groups_layout.addLayout(groups_header_layout)
        
        self.groups_list = QListWidget()
        self.groups_list.itemDoubleClicked.connect(self.open_group_chat)
        self.groups_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
            }
            QListWidget::item:hover {
                background-color: #e8f4f8;
            }
        """)
        groups_layout.addWidget(self.groups_list)
        
        # Group action buttons - Row 1: Open Chat
        open_chat_btn = QPushButton("Open Group Chat")
        open_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #0084ff;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0073e6;
            }
        """)
        open_chat_btn.clicked.connect(self.handle_open_group_chat)
        groups_layout.addWidget(open_chat_btn)
        
        # Row 2: View Members and Invite
        group_actions_layout = QHBoxLayout()
        view_members_btn = QPushButton("Members")
        view_members_btn.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
        """)
        view_members_btn.clicked.connect(self.handle_view_members)
        group_actions_layout.addWidget(view_members_btn)
        
        invite_btn = QPushButton("Invite")
        invite_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        invite_btn.clicked.connect(self.handle_invite_to_group)
        group_actions_layout.addWidget(invite_btn)
        groups_layout.addLayout(group_actions_layout)
        
        groups_layout.addSpacing(10)
        
        create_group_layout = QHBoxLayout()
        self.group_name_input = QLineEdit()
        self.group_name_input.setPlaceholderText("Group Name")
        self.group_name_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)
        create_group_layout.addWidget(self.group_name_input)
        
        self.max_members_input = QLineEdit()
        self.max_members_input.setPlaceholderText("Max")
        self.max_members_input.setFixedWidth(50)
        self.max_members_input.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
        """)
        create_group_layout.addWidget(self.max_members_input)
        
        create_btn = QPushButton("Create")
        create_btn.setFixedWidth(70)
        create_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        create_btn.clicked.connect(self.handle_create_group)
        create_group_layout.addWidget(create_btn)
        groups_layout.addLayout(create_group_layout)
        
        groups_tab.setLayout(groups_layout)
        
        # Tab 3: Notifications
        notif_tab = QWidget()
        notif_layout = QVBoxLayout()
        notif_layout.setContentsMargins(10, 10, 10, 10)
        
        notif_label = QLabel("Notifications")
        notif_label.setFont(QFont("Arial", 12, QFont.Bold))
        notif_layout.addWidget(notif_label)
        
        # Pending friend requests
        pending_label = QLabel("Friend Requests")
        pending_label.setFont(QFont("Arial", 10, QFont.Bold))
        notif_layout.addWidget(pending_label)
        
        self.pending_list = QListWidget()
        self.pending_list.setMaximumHeight(120)
        self.pending_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 6px;
            }
        """)
        notif_layout.addWidget(self.pending_list)
        
        pending_btns = QHBoxLayout()
        accept_btn = QPushButton("Accept")
        accept_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        accept_btn.clicked.connect(self.handle_accept)
        pending_btns.addWidget(accept_btn)
        
        reject_btn = QPushButton("Reject")
        reject_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        reject_btn.clicked.connect(self.handle_reject)
        pending_btns.addWidget(reject_btn)
        notif_layout.addLayout(pending_btns)
        
        notif_layout.addSpacing(10)
        
        # Group invites
        group_inv_label = QLabel("Group Invites")
        group_inv_label.setFont(QFont("Arial", 10, QFont.Bold))
        notif_layout.addWidget(group_inv_label)
        
        self.group_invites_list = QListWidget()
        self.group_invites_list.setMaximumHeight(120)
        self.group_invites_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 6px;
            }
        """)
        notif_layout.addWidget(self.group_invites_list)
        
        group_btns = QHBoxLayout()
        join_btn = QPushButton("Join")
        join_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        join_btn.clicked.connect(self.handle_join_group)
        group_btns.addWidget(join_btn)
        
        reject_group_btn = QPushButton("Reject")
        reject_group_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        reject_group_btn.clicked.connect(self.handle_reject_group)
        group_btns.addWidget(reject_group_btn)
        notif_layout.addLayout(group_btns)
        
        notif_layout.addStretch()
        
        # Activity log
        log_label = QLabel("Activity Log")
        log_label.setFont(QFont("Arial", 10, QFont.Bold))
        notif_layout.addWidget(log_label)
        
        self.activity_log = QTextEdit()
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(150)
        self.activity_log.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
                font-size: 10px;
            }
        """)
        notif_layout.addWidget(self.activity_log)
        
        notif_tab.setLayout(notif_layout)
        
        # Add tabs - Friends & Groups on left, Notifications on right
        left_tabs.addTab(friends_tab, "Friends")
        left_tabs.addTab(groups_tab, "Groups")
        
        left_sidebar.addWidget(left_tabs)
        
        left_sidebar_widget = QWidget()
        left_sidebar_widget.setLayout(left_sidebar)
        left_sidebar_widget.setStyleSheet("background-color: #f8f9fa;")
        left_sidebar_widget.setFixedWidth(250)
        content.addWidget(left_sidebar_widget)
        
        # Store notification tab for popup
        self.notif_tab = notif_tab
        self.notif_popup = None

        main_layout.addLayout(content)
        self.setLayout(main_layout)
        
        # Overall window style (unify fonts across all widgets)
        self.setStyleSheet("""
            QWidget {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
                color: #333;
            }
            QLabel {
                color: #333;
            }
            QListWidget {
                color: #333;
            }
            QLineEdit {
                color: #333;
            }
            QTextEdit {
                color: #333;
            }
        """)

    def refresh_friends(self):
        self.net_thread.send("GET_FRIENDS")
        self.log_message("Refreshing friends list...")

    def update_friends_list(self, friends):
        self.friends_list.clear()
        for name, status in friends:
            item = QListWidgetItem(f"{name} ({status})")
            self.friends_list.addItem(item)
        
        self.log_message(f"Friends updated: {len(friends)} friends")
    
    def load_pending_notifications(self):
        """Load pending friend requests and group invites from server"""
        self.net_thread.send("GET_PENDING_REQUESTS")
        self.net_thread.send("GET_GROUP_INVITES")
        self.log_message("Loading pending notifications...")
    
    def update_pending_requests(self, requests):
        """Update pending_requests list and show notifications"""
        self.pending_requests = requests
        
        # Update the pending_list widget
        self.pending_list.clear()
        for sender in requests:
            self.pending_list.addItem(sender)
        
        if requests:
            self.log_message(f"You have {len(requests)} pending friend request(s)")
            # Auto-show notifications popup if there are pending requests
            if not hasattr(self, '_popup_shown'):
                self._popup_shown = True
                QTimer.singleShot(1000, self.show_notifications_popup)
    
    def update_group_invites(self, invites):
        """Update group invites and show notifications"""
        # Store in a class variable for access in notifications popup
        self.group_invites = invites
        
        # Update the group_invites_list widget
        self.group_invites_list.clear()
        for group_name, inviter in invites:
            invite_text = f"{group_name} (invited by {inviter})"
            self.group_invites_list.addItem(invite_text)
        
        if invites:
            self.log_message(f"You have {len(invites)} group invite(s)")
            # Auto-show notifications popup if there are invites
            if not hasattr(self, '_popup_shown'):
                self._popup_shown = True
                QTimer.singleShot(1000, self.show_notifications_popup)
    
    def refresh_groups(self):
        self.net_thread.send("GET_GROUPS")
        self.log_message("Refreshing groups list...")
    
    def update_groups_list(self, groups):
        self.groups_list.clear()
        for name, count in groups:
            # Request group members to check if current user is admin
            item = QListWidgetItem(f"{name} ({count} members)")
            item.setData(Qt.UserRole, name)  # Store group name
            self.groups_list.addItem(item)
        
        self.log_message(f"Groups updated: {len(groups)} groups")

    def handle_add_friend(self):
        username = self.add_friend_input.text().strip()
        if not username:
            return
        
        self.net_thread.send(f"ADD_FRIEND {username}")
        self.log_message(f"Sent friend request to {username}")
        self.add_friend_input.clear()

    def handle_accept(self):
        current = self.pending_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a request first")
            return
        
        sender = current.text()
        self.net_thread.send(f"CONFIRM_FRIEND {sender}")
        self.log_message(f"Accepted friend request from {sender}")
        
        self.pending_list.takeItem(self.pending_list.currentRow())
        if sender in self.pending_requests:
            self.pending_requests.remove(sender)

    def handle_reject(self):
        current = self.pending_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a request first")
            return
        
        sender = current.text()
        self.net_thread.send(f"REJECT_FRIEND {sender}")
        self.log_message(f"Rejected friend request from {sender}")
        
        self.pending_list.takeItem(self.pending_list.currentRow())
        if sender in self.pending_requests:
            self.pending_requests.remove(sender)
    
    def show_notifications_popup(self):
        """Show notifications in a popup window"""
        if self.notif_popup is None or not self.notif_popup.isVisible():
            from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QPushButton, QHBoxLayout
            
            self.notif_popup = QDialog(self)
            self.notif_popup.setWindowTitle("Thông báo")
            self.notif_popup.setWindowFlags(self.notif_popup.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            self.notif_popup.setMinimumSize(450, 650)
            
            popup_layout = QVBoxLayout()
            popup_layout.setContentsMargins(20, 20, 20, 20)
            
            # Title
            title = QLabel("Notifications")
            title.setFont(QFont("Arial", 16, QFont.Bold))
            popup_layout.addWidget(title)
            
            # Friend Requests Section
            fr_label = QLabel("Friend Requests")
            fr_label.setFont(QFont("Arial", 12, QFont.Bold))
            fr_label.setStyleSheet("margin-top: 10px;")
            popup_layout.addWidget(fr_label)
            
            # Show pending list (interactive)
            pending_display = QListWidget()
            pending_display.setMaximumHeight(150)
            pending_display.setStyleSheet("""
                QListWidget {
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    background-color: #f9f9f9;
                }
                QListWidget::item {
                    padding: 8px;
                }
                QListWidget::item:selected {
                    background-color: #d0e8ff;
                }
            """)
            for i in range(self.pending_list.count()):
                pending_display.addItem(self.pending_list.item(i).text())
            if self.pending_list.count() == 0:
                pending_display.addItem("No pending requests")
            popup_layout.addWidget(pending_display)
            
            # Helper functions for friend requests from popup
            def handle_accept_from_popup():
                item = pending_display.currentItem()
                if not item or item.text() == "No pending requests":
                    QMessageBox.warning(self, "Error", "Please select a friend request")
                    return
                
                # Get sender name
                sender = item.text().strip()
                
                # Send command
                cmd = f"CONFIRM_FRIEND {sender}\n"
                self.net_thread.send(cmd)
                self.log_message(f"Accepted friend request from {sender}")
                
                # Remove from both lists
                row = pending_display.row(item)
                pending_display.takeItem(row)
                # Find and remove from original list
                for i in range(self.pending_list.count()):
                    if self.pending_list.item(i).text() == sender:
                        self.pending_list.takeItem(i)
                        if sender in self.pending_requests:
                            self.pending_requests.remove(sender)
                        break
                
                # Refresh friends
                QTimer.singleShot(500, self.refresh_friends)
                self.notif_popup.close()
            
            def handle_reject_from_popup():
                item = pending_display.currentItem()
                if not item or item.text() == "No pending requests":
                    QMessageBox.warning(self, "Error", "Please select a friend request")
                    return
                
                # Get sender name
                sender = item.text().strip()
                
                # Send command
                cmd = f"REJECT_FRIEND {sender}\n"
                self.net_thread.send(cmd)
                self.log_message(f"Rejected friend request from {sender}")
                
                # Remove from both lists
                row = pending_display.row(item)
                pending_display.takeItem(row)
                # Find and remove from original list
                for i in range(self.pending_list.count()):
                    if self.pending_list.item(i).text() == sender:
                        self.pending_list.takeItem(i)
                        if sender in self.pending_requests:
                            self.pending_requests.remove(sender)
                        break
                
                self.notif_popup.close()
            
            # Friend request buttons
            fr_btns = QHBoxLayout()
            accept_fr = QPushButton("Accept Selected")
            accept_fr.setStyleSheet("""
                QPushButton {
                    background-color: #28a745;
                    color: white;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #218838;
                }
            """)
            accept_fr.clicked.connect(handle_accept_from_popup)
            fr_btns.addWidget(accept_fr)
            
            reject_fr = QPushButton("Reject Selected")
            reject_fr.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545;
                    color: white;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #c82333;
                }
            """)
            reject_fr.clicked.connect(handle_reject_from_popup)
            fr_btns.addWidget(reject_fr)
            popup_layout.addLayout(fr_btns)
            
            # Group Invites Section
            gi_label = QLabel("Group Invites")
            gi_label.setFont(QFont("Arial", 12, QFont.Bold))
            gi_label.setStyleSheet("margin-top: 15px;")
            popup_layout.addWidget(gi_label)
            
            # Show group invites (interactive list)
            group_inv_display = QListWidget()
            group_inv_display.setMaximumHeight(150)
            group_inv_display.setStyleSheet("""
                QListWidget {
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    background-color: #f9f9f9;
                }
                QListWidget::item {
                    padding: 8px;
                }
                QListWidget::item:selected {
                    background-color: #d0e8ff;
                }
            """)
            for i in range(self.group_invites_list.count()):
                item_text = self.group_invites_list.item(i).text()
                group_inv_display.addItem(item_text)
            if self.group_invites_list.count() == 0:
                group_inv_display.addItem("No group invites")
            popup_layout.addWidget(group_inv_display)
            
            # Helper functions to handle group invites from popup
            def handle_join_from_popup():
                item = group_inv_display.currentItem()
                if not item or item.text() == "No group invites":
                    QMessageBox.warning(self, "Error", "Please select a group invite")
                    return
                
                # Get text and parse group name from format "group_name (from inviter)"
                text = item.text().strip()
                if " (from " in text:
                    group_name = text.split(" (from ")[0]
                else:
                    group_name = text
                
                # Send command
                cmd = f"CONFIRM_JOIN {group_name}\n"
                self.net_thread.send(cmd)
                self.log_message(f"Joining group: {group_name}")
                
                # Remove from both lists
                row = group_inv_display.row(item)
                group_inv_display.takeItem(row)
                # Find and remove from original list
                for i in range(self.group_invites_list.count()):
                    if group_name in self.group_invites_list.item(i).text():
                        self.group_invites_list.takeItem(i)
                        break
                
                # Refresh groups
                QTimer.singleShot(500, self.refresh_groups)
                self.notif_popup.close()
            
            def handle_reject_from_popup():
                item = group_inv_display.currentItem()
                if not item or item.text() == "No group invites":
                    QMessageBox.warning(self, "Error", "Please select a group invite")
                    return
                
                # Get text and parse group name from format "group_name (from inviter)"
                text = item.text().strip()
                if " (from " in text:
                    group_name = text.split(" (from ")[0]
                else:
                    group_name = text
                
                # Send command
                cmd = f"REJECT_JOIN {group_name}\n"
                self.net_thread.send(cmd)
                self.log_message(f"Rejected group: {group_name}")
                
                # Remove from both lists
                row = group_inv_display.row(item)
                group_inv_display.takeItem(row)
                # Find and remove from original list
                for i in range(self.group_invites_list.count()):
                    if group_name in self.group_invites_list.item(i).text():
                        self.group_invites_list.takeItem(i)
                        break
                
                self.notif_popup.close()
            
            # Group invite buttons
            gi_btns = QHBoxLayout()
            join_gr = QPushButton("Join Selected")
            join_gr.setStyleSheet("""
                QPushButton {
                    background-color: #28a745;
                    color: white;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #218838;
                }
            """)
            join_gr.clicked.connect(handle_join_from_popup)
            gi_btns.addWidget(join_gr)
            
            reject_gr = QPushButton("Reject Selected")
            reject_gr.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545;
                    color: white;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #c82333;
                }
            """)
            reject_gr.clicked.connect(handle_reject_from_popup)
            gi_btns.addWidget(reject_gr)
            popup_layout.addLayout(gi_btns)
            
            popup_layout.addStretch()
            
            # Close button
            close_btn = QPushButton("Close")
            close_btn.setStyleSheet("""
                QPushButton {
                    background-color: #666;
                    color: white;
                    border: none;
                    padding: 10px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #555;
                }
            """)
            close_btn.clicked.connect(self.notif_popup.close)
            popup_layout.addWidget(close_btn)
            
            self.notif_popup.setLayout(popup_layout)
            
            # Position near the notifications button
            button_pos = self.notif_btn.mapToGlobal(self.notif_btn.rect().bottomLeft())
            self.notif_popup.move(button_pos.x() - 300, button_pos.y() + 5)
            
            self.notif_popup.show()
        else:
            self.notif_popup.activateWindow()

    def show_notification(self, title, message):
        self.log_message(f"{title}: {message}")
        
        # Handle friend requests
        if "Friend Request" in title and "sent you" in message:
            sender = message.split()[0]
            if sender not in self.pending_requests:
                self.pending_requests.append(sender)
                self.pending_list.addItem(sender)
        
        # Handle group invites - parse message format: "inviter invited you to group_name"
        if "Group Invite" in title:
            # Extract group name and inviter from message
            # Format: "inviter invited you to group_name"
            parts = message.split(" invited you to ")
            if len(parts) == 2:
                inviter = parts[0]
                group_name = parts[1]
                # Add to group invites list if not already there
                invite_text = f"{group_name} (from {inviter})"
                # Check if already in list
                already_exists = False
                for i in range(self.group_invites_list.count()):
                    if self.group_invites_list.item(i).text() == invite_text:
                        already_exists = True
                        break
                if not already_exists:
                    self.group_invites_list.addItem(invite_text)
                    self.log_message(f"Added group invite to list: {invite_text}")
        
        if "accepted" in message.lower() or "added" in title.lower():
            QTimer.singleShot(500, self.refresh_friends)
        
        # Show popup notification
        QMessageBox.information(self, title, message)

    def log_message(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(f"[{timestamp}] {msg}")
    
    def open_conversation(self, item):
        """Open chat from conversation list"""
        # Item format: "Name\nLast message..."
        lines = item.text().split('\n')
        if len(lines) > 0:
            name = lines[0].strip()
            # Check if it's a group (starts with 👪) or user
        lines = item.text().split('\n')
        if len(lines) > 0:
            name = lines[0].strip()
            # Check if it's a group (starts with [Group]) or user
            if name.startswith('[Group]'):
                group_name = name[8:].strip()  # Remove prefix
    def open_chat_with_friend(self, item):
        """Show chat with friend in center panel"""
        text = item.text()
        # Extract friend name (format: "name (status)")
        # Remove status to get just the name
        if ' (' in text:
            friend_name = text.split(' (')[0].strip()
        else:
            friend_name = text.strip()
        
        self.show_chat_in_panel('U', friend_name)
        # Add to conversations if not exists
        self.add_to_conversations('U', friend_name, "", "")

    def open_group_chat(self, item):
        """Show group chat in center panel"""
        # Extract group name from item (format: "groupname (X members)")
        text = item.text()
        group_name = item.data(Qt.UserRole)  # Get stored group name
        if not group_name:
            # Fallback: parse from text
            group_name = text.split(' (')[0].strip() if ' (' in text else text.strip()
        # Add to conversations if not exists
        self.add_to_conversations('G', group_name, "", "")
    
    def show_conversation_in_panel(self, item):
        """Show conversation in center panel when clicked from conversations list"""
        text = item.text()
        lines = text.split('\n')
        if not lines:
            return
        
        # Parse conversation name
        conv_name = lines[0]
        if conv_name.startswith("[Group] "):
            # Group chat
            group_name = conv_name[8:].strip()
            self.show_chat_in_panel('G', group_name)
        else:
            # 1-1 chat
            self.show_chat_in_panel('U', conv_name.strip())
    
    def show_chat_in_panel(self, chat_type, chat_name):
        """Display chat in center panel"""
        self.current_chat_type = chat_type
        # Clean chat name: remove status like (online) or (offline)
        clean_name = chat_name.split(' (')[0] if ' (' in chat_name else chat_name
        self.current_chat_name = clean_name
        
        # Update header
        if chat_type == 'G':
            self.chat_header.setText(f"Group: {clean_name}")
        else:
            self.chat_header.setText(f"Chat with {clean_name}")
        
        # Clear current messages
        self.chat_display.clear()
        
        # Reset day separator tracking when switching chats
        self._last_date_shown = None
        
        # Set flag để biết đây là load lần đầu (không nên popup nếu không có tin nhắn)
        self._initial_load = True
        
        # Request message history for all time by default (begin=0, end=0 -> server interprets as open range)
        cmd = f"HISTORY {chat_type} {clean_name} 0 0\n"
        self.net_thread.send(cmd)
        self.log_message(f"Loading chat history with {clean_name}...")
            
            # Enable input, send button, and send file button
        self.message_input.setEnabled(True)
        self.message_input.setPlaceholderText("Type a message...")
        self.send_btn.setEnabled(True)
        self.send_file_btn.setEnabled(True)
        self.message_input.setFocus()
    
    def send_message_from_panel(self):
        """Send message from center panel input"""
        if not self.current_chat_type or not self.current_chat_name:
            QMessageBox.warning(self, "Error", "Please select a conversation first")
            return
        
        text = self.message_input.text().strip()
        if not text:
            return
        
        
        # Send TEXT command
        cmd = f"TEXT {self.current_chat_type} {self.current_chat_name} {text}\n"
        self.net_thread.send(cmd)
        self.message_input.clear()
        
        # Ghi nhận tin nhắn local cuối để khử trùng và hiển thị cục bộ
        import time
        self.last_local_message = text
        self.last_local_msg_ts = time.time()
        self.append_message_to_panel(self.username, text)
    
    def append_message_to_panel(self, sender, content, timestamp=None):
        """Add message to chat display"""
        # Format timestamp for display
        from datetime import datetime
        today_dt = datetime.now()
        if timestamp:
            try:
                ts = int(timestamp)
                dt = datetime.fromtimestamp(ts)
            except:
                dt = today_dt
        else:
            dt = today_dt
    # Display format: dd:mm:yy  hh:mm (as requested)
        time_str = dt.strftime("%d:%m:%y %H:%M")

        # Insert day separator if date changed since last message
        current_date = dt.strftime("%A, %d %B %Y")  # e.g., Monday, 16 December 2025
        if self._last_date_shown != current_date:
            sep_html = f"""
            <div style='display:block; width:100%; text-align:center; margin:12px 0;'>
                <span style='background:#eef3f8; color:#555; font-size:12px; padding:4px 10px; border-radius:12px; display:inline-block;'>
                    {current_date}
                </span>
            </div>
            """
            self.chat_display.append(sep_html)
            self._last_date_shown = current_date

        # Messenger style: your message always right, incoming always left
        sender_norm = sender.strip().lower()
        username_norm = self.username.strip().lower()
        
        # Escape HTML in content to avoid breaking
        from html import escape
        safe_content = escape(content)
        
        # Unified style for both text and file messages:
        # - Sender name blue (#1976D2)
        # - Light blue bubble (#e3f2fd) with subtle left border (#2196F3)
        # - Consistent font and time color
        if sender_norm == username_norm:
            msg_html = f"""
            <table width='100%' style='margin:6px 0; border-collapse:collapse;'><tr>
                <td width='35%'></td>
                <td width='65%' align='right'>
                    <div style='display:inline-block; max-width:85%; text-align:left;'>
                        <div style='font-size:11px; font-weight:bold; color:#1976D2; margin-bottom:4px; padding-left:10px;'>{escape(sender)}</div>
                        <div style='background-color:#e3f2fd; color:#333; padding:10px 12px; border-radius:10px; border-left:4px solid #2196F3; word-wrap:break-word;'>
                            {safe_content}
                            <div style='font-size:10px; color:#666; margin-top:6px; text-align:right;'>{time_str}</div>
                        </div>
                    </div>
                </td>
            </tr></table>
            """
        else:
            msg_html = f"""
            <table width='100%' style='margin:6px 0; border-collapse:collapse;'><tr>
                <td width='65%' align='left'>
                    <div style='display:inline-block; max-width:85%; text-align:left;'>
                        <div style='font-size:11px; font-weight:bold; color:#1976D2; margin-bottom:4px; padding-left:10px;'>{escape(sender)}</div>
                        <div style='background-color:#e3f2fd; color:#333; padding:10px 12px; border-radius:10px; border-left:4px solid #2196F3; word-wrap:break-word;'>
                            {safe_content}
                            <div style='font-size:10px; color:#666; margin-top:6px;'>{time_str}</div>
                        </div>
                    </div>
                </td>
                <td width='35%'></td>
            </tr></table>
            """

        self.chat_display.append(msg_html)
        self.chat_display.verticalScrollBar().setValue(self.chat_display.verticalScrollBar().maximum())
    
    def append_file_message_to_panel_html(self, sender, filename, file_id, timestamp=None):
        """Add file message to chat display (HTML version for MainWindow).
        File từ mình gửi nằm bên phải, file từ người khác nằm bên trái.
        """
        from datetime import datetime
        from html import escape
        
        # Format timestamp
        today_dt = datetime.now()
        if timestamp:
            try:
                ts = int(timestamp)
                dt = datetime.fromtimestamp(ts)
            except:
                dt = today_dt
        else:
            dt = today_dt
        time_str = dt.strftime("%d:%m:%y %H:%M")
        
        # Insert day separator if needed
        current_date = dt.strftime("%A, %d %B %Y")
        if self._last_date_shown != current_date:
            sep_html = f"""
            <div style='display:block; width:100%; text-align:center; margin:12px 0;'>
                <span style='background:#eef3f8; color:#555; font-size:12px; padding:4px 10px; border-radius:12px; display:inline-block;'>
                    {current_date}
                </span>
            </div>
            """
            self.chat_display.append(sep_html)
            self._last_date_shown = current_date
        
        # Check if file is from current user
        sender_norm = sender.strip().lower()
        username_norm = self.username.strip().lower()
        is_self = (sender_norm == username_norm)
        
        # File bubble HTML with download link
        safe_filename = escape(filename)
        safe_sender = escape(sender)
        
        # Create clickable download link
        download_link = f'<a href="#download|{file_id}|{filename}" style="color:#1976D2; text-decoration:none; font-weight:bold;">📎 {safe_filename}</a>'
        
        if is_self:
            # Right-aligned (your file)
            file_html = f"""
            <table width='100%' style='margin:6px 0; border-collapse:collapse;'><tr>
                <td width='35%'></td>
                <td width='65%' align='right'>
                    <div style='display:inline-block; max-width:85%; text-align:left;'>
                        <div style='font-size:11px; font-weight:bold; color:#1976D2; margin-bottom:4px; padding-left:10px;'>{safe_sender}</div>
                        <div style='background-color:#e3f2fd; color:#333; padding:10px 12px; border-radius:10px; border-left:4px solid #2196F3;'>
                            {download_link}
                            <div style='font-size:10px; color:#666; margin-top:6px; text-align:right;'>{time_str}</div>
                        </div>
                    </div>
                </td>
            </tr></table>
            """
        else:
            # Left-aligned (their file)
            file_html = f"""
            <table width='100%' style='margin:6px 0; border-collapse:collapse;'><tr>
                <td width='65%' align='left'>
                    <div style='display:inline-block; max-width:85%; text-align:left;'>
                        <div style='font-size:11px; font-weight:bold; color:#1976D2; margin-bottom:4px; padding-left:10px;'>{safe_sender}</div>
                        <div style='background-color:#e3f2fd; color:#333; padding:10px 12px; border-radius:10px; border-left:4px solid #2196F3;'>
                            {download_link}
                            <div style='font-size:10px; color:#666; margin-top:6px;'>{time_str}</div>
                        </div>
                    </div>
                </td>
                <td width='35%'></td>
            </tr></table>
            """
        
        self.chat_display.append(file_html)
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
    
    def on_history_received(self, msg_type, name, messages):
        """Render history lines from new protocol.
        messages: list of strings 'msgId|sender|timestamp|TYPE|length|content'
        """
        # If we're capturing history for a Files popup, don't modify the chat.
        if getattr(self, '_capture_history_mode', None) == 'files':
            try:
                self.show_files_popup_from_history(messages)
            finally:
                self._capture_history_mode = None
            return
        # Clear on first fetch batch
        if getattr(self, '_history_fetching', False):
            self.chat_display.clear()
            self._history_fetching = False
        
        # Kiểm tra xem có phải là FAIL 404 NO_MESSAGES không
        is_fail_no_messages = (len(messages) == 1 and messages[0] == "__NO_MESSAGES__")
        
        # Nếu là fetch history với time range (không phải load lần đầu), hiển thị popup
        # Load lần đầu có flag _initial_load = True
        if is_fail_no_messages:
            if not getattr(self, '_initial_load', False):
                # Chỉ hiển thị popup nếu KHÔNG phải load lần đầu
                QMessageBox.information(self, "Lịch sử", "Không có tin nhắn hoặc file nào trong khoảng thời gian này.")
            # Reset flag và return
            if hasattr(self, '_initial_load'):
                self._initial_load = False
            return
        
        # Show notification if no messages (SUCCESS 200 0 - có thể là lần đầu hoặc fetch)
        if not messages or (len(messages) == 1 and not messages[0].strip()):
            # Không popup nếu là load lần đầu
            if not getattr(self, '_initial_load', False):
                QMessageBox.information(self, "Lịch sử", "Không có tin nhắn hoặc file nào trong khoảng thời gian này.")
            if hasattr(self, '_initial_load'):
                self._initial_load = False
            return
        
        # Reset flag nếu có messages
        if hasattr(self, '_initial_load'):
            self._initial_load = False
        
        # Display both TEXT and FILE messages
        for line in messages:
            parts6 = line.split('|', 5)
            if len(parts6) >= 6:
                _msg_id, sender, ts, mtype, _length, content = parts6
            else:
                # Back-compat: timestamp|sender|TYPE|content
                parts4 = line.split('|', 3)
                if len(parts4) >= 4:
                    ts, sender, mtype, content = parts4
                else:
                    continue
            if mtype == "TEXT":
                self.append_message_to_panel(sender, content, ts)
            elif mtype == "FILE":
                file_id = None
                filename = content
                if ':' in content:
                    file_id, filename = content.split(':', 1)
                file_id = file_id or filename
                # Dùng method HTML để hiển thị file đúng thứ tự và căn đúng bên
                self.append_file_message_to_panel_html(sender, filename, file_id, ts)
    
    def on_more_history_received(self, messages):
        """Handle additional HISTORY response - prepend to existing messages"""
        if not messages:
            return
        
        # Collect older messages to prepend
        older_messages = []
        for msg in messages:
            if not msg.strip():
                continue
            parts = msg.split('|', 5)  # Tăng lên 5 để lấy đủ: msgId|sender|timestamp|TYPE|length|content
            if len(parts) >= 6:
                msg_id, sender, timestamp_str, msg_type_txt, length_str, content = parts
                # Track oldest message
                try:
                    ts = int(timestamp_str)
                    if self.oldest_message_ts is None or ts < self.oldest_message_ts:
                        self.oldest_message_ts = ts
                except:
                    pass
                older_messages.append((sender, content, timestamp_str, msg_type_txt))
        
        # Prepend to chat_display
        current_html = self.chat_display.toHtml()
        self.chat_display.clear()
        for sender, content, timestamp, msg_type_txt in older_messages:
            if msg_type_txt == "FILE":
                # FILE content format: "file_id:filename"
                if ':' in content:
                    file_id, filename = content.split(':', 1)
                    self.append_file_message_to_panel_html(sender, filename, file_id)
                else:
                    # Fallback nếu format sai
                    self.append_message_to_panel(sender, f"[File: {content}]", timestamp)
            else:
                # TEXT message
                self.append_message_to_panel(sender, content, timestamp)
        self.chat_display.insertHtml(current_html)
    
    def load_more_history(self):
        """Open History dialog (new protocol)."""
        self.open_history_dialog()

    def open_history_dialog(self):
        # Hộp thoại lịch sử với 2 tùy chọn: Custom và Show all
        from PyQt5.QtWidgets import QDateTimeEdit, QComboBox
        from PyQt5.QtCore import QDateTime
        dlg = QDialog(self)
        dlg.setWindowTitle("Lịch sử")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dlg)

        preset_box = QComboBox(dlg)
        preset_box.addItems(["Custom", "Show all"]) 
        layout.addWidget(QLabel("Preset"))
        layout.addWidget(preset_box)

        begin_dt = QDateTimeEdit(dlg)
        end_dt = QDateTimeEdit(dlg)
        begin_dt.setCalendarPopup(True)
        end_dt.setCalendarPopup(True)
        begin_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        end_dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        layout.addWidget(QLabel("Begin"))
        layout.addWidget(begin_dt)
        layout.addWidget(QLabel("End"))
        layout.addWidget(end_dt)

        # Mặc định: Custom với khoảng thời gian 1 giờ trước
        now = QDateTime.currentDateTime()
        begin_dt.setDateTime(now.addSecs(-3600))
        end_dt.setDateTime(now)

        def apply_preset(index):
            text = preset_box.currentText()
            if text == "Show all":
                begin_dt.setEnabled(False)
                end_dt.setEnabled(False)
            else:  # Custom
                begin_dt.setEnabled(True)
                end_dt.setEnabled(True)

        preset_box.currentIndexChanged.connect(apply_preset)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Fetch", dlg)
        cancel_btn = QPushButton("Cancel", dlg)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        def do_fetch():
            msg_type = getattr(self, 'current_chat_type', None)
            name = getattr(self, 'current_chat_name', None)
            if not msg_type or not name:
                QMessageBox.warning(self, "History", "No chat selected")
                return
            preset = preset_box.currentText()
            if preset == "Show all":
                cmd = f"HISTORY {msg_type} {name} 0 0"
            else:
                # Send timestamps with 'T' separator so each is a single token
                b = begin_dt.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
                e = end_dt.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
                cmd = f"HISTORY {msg_type} {name} {b} {e}"
            # Mark that we should clear the panel on first batch
            self._history_fetching = True
            self.net_thread.send(cmd)
            dlg.accept()

        # Wire buttons to inner functions and show the dialog
        ok_btn.clicked.connect(do_fetch)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec_()
    
    def add_to_conversations(self, msg_type, name, sender, content):
        """Add or update conversation in the list"""
        conv_prefix = "[Group] " if msg_type == 'G' else ""
        conv_name = f"{conv_prefix}{name}"
        
        if content:
            preview = f"{sender}: {content[:30]}..." if len(content) > 30 else f"{sender}: {content}"
        else:
            preview = "Click to start chatting"
        
        # Check if conversation exists
        found = False
        for i in range(self.conversations_list.count()):
            item = self.conversations_list.item(i)
            item_lines = item.text().split('\n')
            if item_lines and item_lines[0] == conv_name:
                # Update existing conversation
                if content:  # Only update if there's new content
                    item.setText(f"{conv_name}\n{preview}")
                # Move to top
                self.conversations_list.takeItem(i)
                self.conversations_list.insertItem(0, item)
                found = True
                break
        
        if not found:
            # Add new conversation at top
            item = QListWidgetItem(f"{conv_name}\n{preview}")
            self.conversations_list.insertItem(0, item)
    
    def on_new_message(self, msg_type, name, sender, content):
        """Handle new incoming message - update conversations list and chat panel"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M")
        
        
        # If this message is for the currently open chat, display it
        if self.current_chat_type == msg_type and self.current_chat_name == name:
            # Deduplicate server echo for messages we just sent locally
            try:
                import time
                sender_norm = sender.strip().lower()
                my_norm = self.username.strip().lower()
                if sender_norm == my_norm and self.last_local_message is not None:
                    # same sender as me - check if it's the echo of what we just sent
                    if content == self.last_local_message and (time.time() - self.last_local_msg_ts) < 5:
                        pass  # Don't display, it's already displayed locally
                    else:
                        # Different message or too old - display it
                        self.append_message_to_panel(sender, content)
                else:
                    # Message from someone else - always display
                    self.append_message_to_panel(sender, content)
            except Exception as e:
                print(f"[ERROR] on_new_message dedupe check failed: {e}")
                self.append_message_to_panel(sender, content)
        
        # Update or add to recent conversations
        self.add_to_conversations(msg_type, name, sender, content)
    
    def handle_create_group(self):
        """Create a new group"""
        group_name = self.group_name_input.text().strip()
        max_members = self.max_members_input.text().strip()
        
        
        if not group_name:
            QMessageBox.warning(self, "Error", "Please enter group name")
            return
        
        if not max_members or not max_members.isdigit():
            QMessageBox.warning(self, "Error", "Please enter valid max members number")
            return
        
        # INIT_GROUP <name> <max_members>
        cmd = f"INIT_GROUP {group_name} {max_members}\n"
        self.net_thread.send(cmd)
        self.log_message(f"Creating group: {group_name}")
        
        # Auto-refresh groups list after a short delay
        QTimer.singleShot(500, self.refresh_groups)
        
        self.group_name_input.clear()
        self.max_members_input.clear()
    
    def show_members_dialog(self, group_name, members):
        """Display group members in a dialog with kick/leave options"""
        # Use stored group name if parameter is empty
        if not group_name and hasattr(self, 'viewing_group'):
            group_name = self.viewing_group
        
        if not members:
            QMessageBox.information(self, "Group Members", "No members found")
            return
        
        # Create dialog
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPushButton
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Thành viên nhóm - {group_name if group_name else 'Nhóm'}")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumSize(450, 550)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel(f"Members of {group_name if group_name else 'Group'}")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title)
        
        # Members list
        members_list = QListWidget()
        members_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:hover {
                background-color: #f5f5f5;
            }
            QListWidget::item:selected {
                background-color: #d0e8ff;
            }
        """)
        
        # Track if current user is admin
        is_admin = False
        for username, role, status in members:
            # Add role indicator
            role_text = " (admin)" if role == "admin" else ""
            item_text = f"{username}{role_text} - {status}"
            item = QListWidgetItem(item_text)
            # Store username in item data
            item.setData(Qt.UserRole, username)
            members_list.addItem(item)
            
            # Check if current user is admin
            if username == self.username and role == "admin":
                is_admin = True
        
        layout.addWidget(members_list)
        
        # Action buttons
        buttons_layout = QHBoxLayout()
        
        # Leave group button (for all users)
        leave_btn = QPushButton("Leave Group")
        leave_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
        """)
        
        def handle_leave():
            reply = QMessageBox.question(dialog, "Leave Group", 
                                        f"Are you sure you want to leave {group_name}?",
                                        QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                # Use LEAVE_GROUP command for self-leave
                cmd = f"LEAVE_GROUP {group_name}\n"
                self.net_thread.send(cmd)
                self.log_message(f"Left group: {group_name}")
                QTimer.singleShot(500, self.refresh_groups)
                dialog.accept()
        
        leave_btn.clicked.connect(handle_leave)
        buttons_layout.addWidget(leave_btn)
        
        # Kick button (only for admin)
        if is_admin:
            kick_btn = QPushButton("Kick Selected User")
            kick_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ff6b6b;
                    color: white;
                    border: none;
                    padding: 10px;
                    border-radius: 4px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #ff5252;
                }
            """)
            
            def handle_kick():
                selected_item = members_list.currentItem()
                if not selected_item:
                    QMessageBox.warning(dialog, "Error", "Please select a user to kick")
                    return
                
                target_username = selected_item.data(Qt.UserRole)
                
                # Cannot kick yourself
                if target_username == self.username:
                    QMessageBox.warning(dialog, "Error", "You cannot kick yourself. Use 'Leave Group' instead.")
                    return
                
                # Confirm kick
                reply = QMessageBox.question(dialog, "Kick User", 
                                            f"Are you sure you want to kick {target_username} from {group_name}?",
                                            QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    # EJECT_USER <group_name> <username>
                    cmd = f"EJECT_USER {group_name} {target_username}\n"
                    self.net_thread.send(cmd)
                    self.log_message(f"Kicked {target_username} from group {group_name}")
                    # Remove from list
                    row = members_list.row(selected_item)
                    members_list.takeItem(row)
                    QMessageBox.information(dialog, "Success", f"{target_username} has been kicked from the group")
            
            kick_btn.clicked.connect(handle_kick)
            buttons_layout.addWidget(kick_btn)
        
        layout.addLayout(buttons_layout)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        dialog.exec_()
    
    def handle_open_group_chat(self):
        """Open group chat in center panel"""
        item = self.groups_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Error", "Please select a group first")
            return
        
        # Use the double-click handler
        self.open_group_chat(item)
    
    def handle_view_members(self):
        """View members of selected group"""
        item = self.groups_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Error", "Please select a group first")
            return
        
        group_name = item.data(Qt.UserRole)
        if not group_name:
            text = item.text()
            group_name = text.split(' (')[0].strip() if ' (' in text else text.strip()
        
        # Store group name for the callback
        self.viewing_group = group_name
        
        # GET_MEMBERS <group_name>
        cmd = f"GET_MEMBERS {group_name}\n"
        self.net_thread.send(cmd)
        self.log_message(f"Requesting members of group: {group_name}")
    
    def handle_invite_to_group(self):
        """Invite a friend to selected group"""
        from PyQt5.QtWidgets import QInputDialog
        
        # Check if group is selected
        group_item = self.groups_list.currentItem()
        if not group_item:
            QMessageBox.warning(self, "Error", "Please select a group first")
            return
        
        group_name = group_item.data(Qt.UserRole)
        if not group_name:
            text = group_item.text()
            group_name = text.split(' (')[0].strip() if ' (' in text else text.strip()
        
        # Get list of friends
        friends = []
        for i in range(self.friends_list.count()):
            friend_text = self.friends_list.item(i).text()
            # Extract friend name (format: "name (status)")
            if ' (' in friend_text:
                friend_name = friend_text.split(' (')[0].strip()
            else:
                friend_name = friend_text.strip()
            if friend_name:
                friends.append(friend_name)
        
        if not friends:
            QMessageBox.warning(self, "Error", "You have no friends to invite")
            return
        
        friend, ok = QInputDialog.getItem(self, "Invite to Group", 
                                          f"Select friend to invite to {group_name}:", 
                                          friends, 0, False)
        if ok and friend:
            # SEND_INVITE <group_name> <username>
            cmd = f"SEND_INVITE {group_name} {friend}\n"
            self.net_thread.send(cmd)
            self.log_message(f"Invited {friend} to group {group_name}")
            QMessageBox.information(self, "Success", f"Invited {friend} to {group_name}")
    
    def handle_join_group(self):
        """Accept group invite"""
        item = self.group_invites_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Error", "Please select a group invite")
            return
        
        # Parse group name from format "group_name (from inviter)"
        text = item.text()
        if " (from " in text:
            group_name = text.split(" (from ")[0]
        else:
            group_name = text
        
        # CONFIRM_JOIN <group_name>
        cmd = f"CONFIRM_JOIN {group_name}\n"
        self.net_thread.send(cmd)
        self.log_message(f"Joining group: {group_name}")
        
        # Auto-refresh groups list after joining
        QTimer.singleShot(500, self.refresh_groups)
        
        self.group_invites_list.takeItem(self.group_invites_list.row(item))
    
    def handle_reject_group(self):
        """Reject group invite"""
        item = self.group_invites_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Error", "Please select a group invite")
            return
        
        # Parse group name from format "group_name (from inviter)"
        text = item.text()
        if " (from " in text:
            group_name = text.split(" (from ")[0]
        else:
            group_name = text
        
        # REJECT_JOIN <group_name>
        cmd = f"REJECT_JOIN {group_name}\n"
        self.net_thread.send(cmd)
        self.log_message(f"Rejected group: {group_name}")
        
        self.group_invites_list.takeItem(self.group_invites_list.row(item))

    def handle_logout(self):
        reply = QMessageBox.question(self, "Logout", 
                                      "Are you sure?",
                                      QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.net_thread.send("LOGOUT")
            self.log_message("Logging out...")
            QTimer.singleShot(500, self.close)

    def on_disconnected(self):
        QMessageBox.warning(self, "Disconnected", "Connection lost")
        self.close()
    
    def on_left_group(self, group_name):
        """Handle when user leaves or is kicked from a group"""
        # Close chat window if open
        if group_name in self.chat_windows:
            chat_window = self.chat_windows[group_name]
            chat_window.close()
            del self.chat_windows[group_name]
        
        # Remove from conversations list if present
        for i in range(self.conversations_list.count()):
            item = self.conversations_list.item(i)
            if item.text().startswith(f"[Group] {group_name}"):
                self.conversations_list.takeItem(i)
                break
        
        # Remove from groups list immediately
        for i in range(self.groups_list.count()):
            item = self.groups_list.item(i)
            if item.data(Qt.UserRole) == group_name:
                self.groups_list.takeItem(i)
                self.log_message(f"Removed group '{group_name}' from groups list")
                break
        
        # Clear chat display if this group is currently displayed
        if hasattr(self, 'current_chat_type') and hasattr(self, 'current_chat_name'):
            if self.current_chat_type == 'G' and self.current_chat_name == group_name:
                # Clear the display
                self.chat_display.clear()
                
                # Show message
                self.chat_display.append(
                    '<div style="text-align: center; color: #999; font-size: 14px; margin-top: 50px;">'
                    'You have left this group'
                    '</div>'
                )
                
                # Disable input
                self.message_input.setEnabled(False)
                self.message_input.setPlaceholderText("You are no longer a member of this group")
                
                # Reset current chat
                self.current_chat_type = None
                self.current_chat_name = None
        
        # Refresh groups list from server
        self.refresh_groups()
    
    # ========================================================================
    # File Transfer Handlers
    # ========================================================================
    
    def handle_send_file(self):
        """Send file to current chat"""
        if not self.current_chat_type or not self.current_chat_name:
            QMessageBox.warning(self, "Error", "Please select a conversation first")
            return
        
        # File dialog to select files
        filepaths, _ = QFileDialog.getOpenFileNames(self, "Select Files to Send", 
                                                     "", "All Files (*.*)")
        if not filepaths:
            return
        
        # Add to upload queue
        self.upload_manager.add_files(filepaths, self.current_chat_type, self.current_chat_name)
        
        # Show upload progress dialog
        self.show_upload_progress_dialog()
    
    def show_upload_progress_dialog(self):
        """Show dialog with upload progress"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Uploading Files")
        dialog.setMinimumSize(500, 300)
        
        layout = QVBoxLayout()
        
        label = QLabel("Upload Queue:")
        layout.addWidget(label)
        
        # Current upload progress
        self.upload_progress_label = QLabel("Idle")
        layout.addWidget(self.upload_progress_label)
        
        self.upload_progress_bar = QProgressBar()
        self.upload_progress_bar.setRange(0, 100)
        layout.addWidget(self.upload_progress_bar)
        
        # Pending queue list
        self.upload_queue_list = QListWidget()
        layout.addWidget(self.upload_queue_list)
        
        # Buttons
        button_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel Current")
        cancel_btn.clicked.connect(lambda: self.upload_manager.cancel_current())
        button_layout.addWidget(cancel_btn)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        
        # Update queue display
        self.upload_manager.queue_updated.connect(self.update_upload_queue_display)
        
        dialog.exec_()
    
    def update_upload_queue_display(self, pending_uploads):
        """Update upload queue list"""
        if hasattr(self, 'upload_queue_list'):
            self.upload_queue_list.clear()
            for filepath, target_type, target_name in pending_uploads:
                filename = os.path.basename(filepath)
                self.upload_queue_list.addItem(f"{filename} -> {target_name}")
    
    def on_upload_started(self, file_id, filename):
        """Upload started"""
        if hasattr(self, 'upload_progress_label'):
            self.upload_progress_label.setText(f"Uploading: {filename}")
        self.log_message(f"Uploading {filename}...")
        # Capture target info for this upload so we can render a file bubble on sender side later
        try:
            au = getattr(self.upload_manager, 'active_upload', None)
            if au and au[0] == file_id:
                # (file_id, filepath, filename, filesize, target_type, target_name)
                self.local_uploads[file_id] = (filename, au[4], au[5])
        except Exception:
            pass
    
    def on_upload_progress(self, file_id, bytes_sent, total):
        """Update upload progress"""
        if hasattr(self, 'upload_progress_bar'):
            progress = int((bytes_sent / total) * 100)
            self.upload_progress_bar.setValue(progress)
    
    def on_upload_complete(self, file_id):
        """Upload completed"""
        if hasattr(self, 'upload_progress_label'):
            self.upload_progress_label.setText("Upload complete!")
        if hasattr(self, 'upload_progress_bar'):
            self.upload_progress_bar.setValue(100)
        
        self.show_notification("Upload Complete", "File uploaded successfully")
        self.log_message("File upload complete")
        # Also append the file message to the current chat on the sender side if it matches
        info = self.local_uploads.pop(file_id, None)
        if info:
            filename, tgt_type, tgt_name = info
            if (self.current_chat_type == tgt_type and
                self.current_chat_name == tgt_name):
                # Show as from self (sender side)
                self.append_file_message_to_panel(self.username, filename, file_id)
    
    def on_upload_failed(self, file_id, error):
        """Upload failed"""
        if hasattr(self, 'upload_progress_label'):
            self.upload_progress_label.setText(f"Upload failed: {error}")
        
        QMessageBox.warning(self, "Upload Failed", f"Failed to upload file:\n{error}")
    
    def on_file_notification(self, file_type, target, sender, file_id, filename):
        """Received file notification - hiển thị real-time ngay cả khi không mở chat"""
        # Store notification
        self.file_notifications.append((file_type, target, sender, file_id, filename))
        
        # Show notification
        if file_type == 'G':
            self.show_notification("File Received", 
                                  f"{sender} sent a file to {target}:\n{filename}")
        else:
            self.show_notification("File Received", 
                                  f"{sender} sent you a file:\n{filename}")
        
        # REAL-TIME: Hiển thị file trong panel nếu đang mở đúng chat
        chat_name = None
        if file_type == "U":
            # Private chat - chat name is sender
            chat_name = sender
        elif file_type == "G":
            # Group chat - chat name is target (group name)
            chat_name = target
        
        # Kiểm tra xem có đang mở chat này không
        if (hasattr(self, 'current_chat_type') and hasattr(self, 'current_chat_name') and
            self.current_chat_type == file_type and self.current_chat_name == chat_name):
            # Đang mở đúng chat → hiển thị ngay (dùng HTML version cho MainWindow)
            self.append_file_message_to_panel_html(sender, filename, file_id)
        
        # KHÔNG tự động hiển thị download dialog nữa
        # Người dùng có thể download bằng cách click vào file trong lịch sử chat
    
    def show_download_dialog(self, file_type, target, sender, file_id, filename):
        """Show dialog to download file"""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("File Received")
        msg.setText(f"{sender} sent you a file:\n{filename}")
        msg.setInformativeText("Do you want to download it?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        
        if msg.exec_() == QMessageBox.Yes:
            # Choose save location
            save_path, _ = QFileDialog.getSaveFileName(self, "Save File", filename, 
                                                        "All Files (*.*)")
            if save_path:
                self.start_download(file_id, filename, save_path)
    
    def start_download(self, file_id, original_filename, save_path):
        """Start downloading file"""
        # Send download request
        cmd = f"REQ_DOWNLOAD {file_id}\n"
        self.net_thread.send(cmd)
        
        # Store download info (will be used when READY_DOWNLOAD is received)
        self.active_downloads[file_id] = {
            'save_path': save_path,
            'original_filename': original_filename,
            'worker': None,
            'progress_dialog': None
        }
        
        self.log_message(f"Downloading {original_filename}...")
    
    def handle_send_file(self):
        """Handle send file button click"""
        if not hasattr(self, 'current_chat_type') or not hasattr(self, 'current_chat_name'):
            QMessageBox.warning(self, "No Chat Selected", "Please select a conversation first!")
            return
        
        # Open file dialog
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files to Send",
            "",
            "All Files (*.*)"
        )
        
        if not file_paths:
            return
        
        # Thêm file vào hàng đợi upload
        self.upload_manager.add_files(
            file_paths,
            self.current_chat_type,
            self.current_chat_name
        )
        
        # Show upload dialog
        self.show_upload_dialog()
    
    def show_upload_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar, QListWidget, QHBoxLayout, QPushButton
        # Build a simple upload queue/progress dialog
        self.upload_dialog = QDialog(self)
        self.upload_dialog.setWindowTitle("Upload Queue")
        layout = QVBoxLayout()

        self.current_upload_label = QLabel("No upload in progress")
        self.upload_progress_bar = QProgressBar()
        self.upload_progress_bar.setRange(0, 100)
        layout.addWidget(self.current_upload_label)
        layout.addWidget(self.upload_progress_bar)

        layout.addWidget(QLabel("Pending uploads:"))
        self.upload_queue_list = QListWidget()
        layout.addWidget(self.upload_queue_list)

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel current")
        cancel_btn.clicked.connect(self.upload_manager.cancel_current)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.upload_dialog.close)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.upload_dialog.setLayout(layout)

        # Update queue display
        self.upload_manager.queue_updated.connect(self.update_upload_queue_display)
        self.update_upload_queue_display(self.upload_manager.pending_uploads)

        self.upload_dialog.show()
    
    def update_upload_queue_display(self, pending_uploads):
        """Update upload queue list"""
        if not hasattr(self, 'upload_queue_list'):
            return
        
        self.upload_queue_list.clear()
        for filepath, target_type, target_name in pending_uploads:
            filename = os.path.basename(filepath)
            self.upload_queue_list.addItem(f"{filename} → {target_name}")
    
    def on_upload_started(self, file_id, filename):
        """Called when upload starts"""
        if hasattr(self, 'current_upload_label'):
            self.current_upload_label.setText(f"Uploading: {filename}")
            self.upload_progress_bar.setValue(0)
    
    def on_upload_progress(self, file_id, bytes_sent, total_bytes):
        """Update upload progress"""
        if hasattr(self, 'upload_progress_bar'):
            progress = int((bytes_sent / total_bytes) * 100)
            self.upload_progress_bar.setValue(progress)
    
    def on_upload_complete(self, file_id):
        """Upload completed"""
        if hasattr(self, 'current_upload_label'):
            self.current_upload_label.setText("Upload complete!")
            self.upload_progress_bar.setValue(100)
        
        QMessageBox.information(self, "Success", "File uploaded successfully!")
        self.log_message(f"File {file_id} uploaded successfully")
    
    def on_upload_failed(self, file_id, error):
        """Upload failed"""
        if hasattr(self, 'current_upload_label'):
            self.current_upload_label.setText(f"Upload failed: {error}")
        
        QMessageBox.critical(self, "Upload Failed", f"Failed to upload file:\n{error}")
        self.log_message(f"Upload failed: {error}")
    
    
    def append_file_message_to_panel(self, sender, filename, file_id, timestamp=None):
        """Append file message to chat panel with download button.
        If timestamp is provided (unix seconds), use it for display; otherwise use now.
        File từ mình gửi sẽ nằm bên phải, file từ người khác nằm bên trái.
        """
        if timestamp is not None:
            try:
                ts_int = int(timestamp)
                from datetime import datetime as _dt
                time_str = _dt.fromtimestamp(ts_int).strftime("%H:%M")
            except Exception:
                time_str = datetime.now().strftime("%H:%M")
        else:
            time_str = datetime.now().strftime("%H:%M")
        
        # Check if this is from current user (align right)
        sender_norm = sender.strip().lower()
        username_norm = self.username.strip().lower()
        is_self = (sender_norm == username_norm)
        
        # Create file bubble widget
        file_widget = QWidget()
        file_layout = QVBoxLayout(file_widget)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(2)
        
        # Tên người gửi (chỉ cho tin nhắn của người khác)
        if not is_self:
            sender_label = QLabel(sender)
            sender_label.setStyleSheet("font-size: 12px; color: #666; padding-left: 12px;")
            file_layout.addWidget(sender_label)
        
        # File bubble container
        bubble = QWidget()
        bubble.setMaximumWidth(380)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
        bubble_layout.setSpacing(6)
        
        # File icon + name
        file_info = QLabel(f"📎 {filename}")
        file_info.setWordWrap(True)
        file_info.setStyleSheet(f"""
            QLabel {{
                font-size: 14px;
                color: {'white' if is_self else '#050505'};
                font-weight: 500;
            }}
        """)
        bubble_layout.addWidget(file_info)
        
        # Download button
        download_btn = QPushButton("Download")
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background: {'#0066cc' if is_self else '#1976D2'};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {'#0052a3' if is_self else '#1565C0'};
            }}
        """)
        download_btn.clicked.connect(lambda: self.show_download_dialog('', '', sender, file_id, filename))
        bubble_layout.addWidget(download_btn)
        
        # Apply bubble style
        bubble.setStyleSheet(f"""
            QWidget {{
                background-color: {'#0084ff' if is_self else '#e4e6eb'};
                border-radius: 18px;
            }}
        """)
        
        # Nhãn thời gian
        time_label = QLabel(time_str)
        time_label.setStyleSheet(f"""
            font-size: 11px; 
            color: #888; 
            padding-{'right' if is_self else 'left'}: 12px;
        """)
        
        # Layout cho bubble + thời gian
        content_layout = QHBoxLayout()
        content_layout.setSpacing(4)
        if is_self:
            content_layout.addStretch(1)
            content_layout.addWidget(time_label, 0, Qt.AlignBottom)
            content_layout.addWidget(bubble, 0)
        else:
            content_layout.addWidget(bubble, 0)
            content_layout.addWidget(time_label, 0, Qt.AlignBottom)
            content_layout.addStretch(1)
        
        file_layout.addLayout(content_layout)
        
        # Chèn trước phần stretch ở cuối
        insert_pos = self.panel_layout.count() - 1
        self.panel_layout.insertWidget(insert_pos, file_widget)
        
        # Scroll to bottom
        QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()
        ))
    
    def show_files_popup_from_history(self, messages):
        """Build and show a popup dialog that lists all FILE entries parsed from history lines.
        Expected line format: 'msgId|sender|timestamp|TYPE|length|content' where for TYPE=FILE,
        content is usually 'file_id:filename'.
        """
        from datetime import datetime as _dt
        files = []  # list of dicts: {sender, ts, file_id, filename}
        for line in messages:
            parts6 = line.split('|', 5)
            if len(parts6) >= 6:
                _msg_id, sender, ts, mtype, _length, content = parts6
            else:
                parts4 = line.split('|', 3)
                if len(parts4) >= 4:
                    ts, sender, mtype, content = parts4
                else:
                    continue
            if mtype != 'FILE':
                continue
            file_id, filename = None, content
            if ':' in content:
                file_id, filename = content.split(':', 1)
            file_id = file_id or filename
            # Format ts
            try:
                ts_view = _dt.fromtimestamp(int(ts)).strftime('%d/%m/%y %H:%M')
            except Exception:
                ts_view = ''
            files.append({'sender': sender, 'ts': ts_view, 'file_id': file_id, 'filename': filename})

        # Build dialog UI
        dlg = QDialog(self)
        dlg.setWindowTitle('Danh sách File')
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(520, 480)
        vbox = QVBoxLayout(dlg)
        if not files:
            vbox.addWidget(QLabel('Không có file trong đoạn chat này.'))
        else:
            # Scroll area for long lists
            scroll = QScrollArea(dlg)
            scroll.setWidgetResizable(True)
            container = QWidget()
            list_layout = QVBoxLayout(container)
            for item in files:
                row = QHBoxLayout()
                info = QLabel(f"{item['filename']}\nNgười gửi: {item['sender']}    Thời gian: {item['ts']}")
                info.setWordWrap(True)
                btn = QPushButton('Download')
                # Capture values in default args
                btn.clicked.connect(lambda _, fid=item['file_id'], fn=item['filename']: self.show_download_dialog('', '', '', fid, fn))
                row.addWidget(info, 1)
                row.addWidget(btn, 0)
                list_layout.addLayout(row)
            list_layout.addStretch(1)
            scroll.setWidget(container)
            vbox.addWidget(scroll)
        close_btn = QPushButton('Close', dlg)
        close_btn.clicked.connect(dlg.accept)
        vbox.addWidget(close_btn)
        dlg.exec_()
    
    def handle_download_link_click(self, url):
        """Handle click on download link in chat"""
        # QTextBrowser may give a full URL like 'about:blank#download|id|filename'
        url_str = url.toString()
        frag = url.fragment()  # part after '#'

        # Preferred format: #download|<file_id>|<filename>
        if frag and frag.startswith("download|"):
            parts = frag.split("|", 2)
            if len(parts) >= 3:
                _, file_id, filename = parts[0], parts[1], parts[2]
                self.show_download_dialog("", "", "", file_id, filename)
                return

        # Back-compat: raw string starts with '#download|'
        if url_str.startswith("#download|"):
            payload = url_str[10:]
            parts = payload.split("|", 1)
            if len(parts) >= 2:
                file_id, filename = parts[0], parts[1]
                self.show_download_dialog("", "", "", file_id, filename)
                return

        # Back-compat: older scheme 'download:<file_id>:<filename>'
        if url_str.startswith("download:"):
            parts = url_str.split(":", 2)
            if len(parts) >= 3:
                file_id, filename = parts[1], parts[2]
                self.show_download_dialog("", "", "", file_id, filename)
                return

        print("[WARN] Unrecognized download link format; ignoring")
    
    def show_download_dialog(self, file_type, target, sender, file_id, filename):
        """Show dialog to download received file"""
        from PyQt5.QtWidgets import QFileDialog
        
        # Ask user where to save
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save file from {sender}",
            filename,
            "All Files (*.*)"
        )
        
        if not save_path:
            return
        
        
        # Send download request to server
        cmd = f"REQ_DOWNLOAD {file_id}\n"
        self.net_thread.send(cmd)
        
        # Store download info in network thread for when server responds
        self.net_thread.pending_downloads[file_id] = (filename, save_path, 0)  # (filename, save_path, filesize - will be updated)

    def closeEvent(self, event):
        if self.net_thread:
            self.net_thread.stop()
            self.net_thread.wait(2000)
        event.accept()

# ============================================================================
# Application
# ============================================================================

class ChatApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName("Chat Client")
        self.main_window = None
        self.net_thread = None

    def run(self):
        login = LoginWindow()
        login.login_success.connect(self.on_login_success)
        login.show()
        return self.exec_()

    def on_login_success(self, server, username, session):
        sender = self.sender()
        if hasattr(sender, 'net_thread'):
            self.net_thread = sender.net_thread
            self.main_window = MainWindow(server, username, session, self.net_thread)
            self.main_window.show()

if __name__ == '__main__':
    # Enable high DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = ChatApp(sys.argv)
    sys.exit(app.run())
