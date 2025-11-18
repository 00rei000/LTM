## Makefile for Windows (MinGW/mingw32-make) - builds server and client
# Usage:
#   mingw32-make init        # create persistence text files
#   mingw32-make build       # compile both server and client
#   mingw32-make server      # compile server only
#   mingw32-make client      # compile client only
#   mingw32-make run-server  # run server.exe (from bin) in current shell
#   mingw32-make run-client  # run client.exe (from bin)
#   mingw32-make clean       # remove build artifacts and txt persistence files

CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall
LDFLAGS ?= -lws2_32 -pthread

BIN_DIR ?= bin
SRCS_SERVER := server.cpp
SRCS_CLIENT := client.cpp

.PHONY: all init build server client run-server run-client clean

all: init build

# Build both
build: server client

# Server binary

server: $(SRCS_SERVER)
	@rem create bin dir if missing (works in cmd/powershell)
	@if not exist "$(BIN_DIR)" mkdir "$(BIN_DIR)"
	$(CXX) $(CXXFLAGS) $(SRCS_SERVER) -o "$(BIN_DIR)/server.exe" $(LDFLAGS)
	@echo Built $(BIN_DIR)/server.exe

# Client binary

client: $(SRCS_CLIENT)
	@rem create bin dir if missing (works in cmd/powershell)
	@if not exist "$(BIN_DIR)" mkdir "$(BIN_DIR)"
	$(CXX) $(CXXFLAGS) $(SRCS_CLIENT) -o "$(BIN_DIR)/client.exe" $(LDFLAGS)
	@echo Built $(BIN_DIR)/client.exe

# Initialize legacy text files (safe to run even if using SQLite)
init:
	@echo Initializing legacy text persistence files if missing...
	@if not exist users.txt (echo.>users.txt)
	@if not exist sessions.txt (echo.>sessions.txt)
	@if not exist pending_requests.txt (echo.>pending_requests.txt)
	@if not exist friends.txt (echo.>friends.txt)
	@if not exist online.txt (echo.>online.txt)
	@if not exist server.log (echo.>server.log)
	@echo init complete.

# Run commands (runs binaries from bin/)
run-server:
	@echo Running server (foreground)...
	@"$(BIN_DIR)/server.exe"

run-client:
	@echo Running client (foreground)...
	@"$(BIN_DIR)/client.exe"

# Clean build artifacts and legacy persistence files
clean:
	@echo Cleaning build artifacts and legacy persistence files...
	@if exist "$(BIN_DIR)\server.exe" del "$(BIN_DIR)\server.exe"
	@if exist "$(BIN_DIR)\client.exe" del "$(BIN_DIR)\client.exe"
	@if exist server.log del server.log
	@if exist users.txt del users.txt
	@if exist sessions.txt del sessions.txt
	@if exist pending_requests.txt del pending_requests.txt
	@if exist friends.txt del friends.txt
	@if exist online.txt del online.txt
	@if exist "$(BIN_DIR)" rmdir "$(BIN_DIR)"
	@echo Clean complete.
