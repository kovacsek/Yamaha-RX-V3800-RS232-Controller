import serial
import time
import socket
import threading
import queue
import sys
import yaml
import os
import re

# ANSI Color Codes (256-color support)
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # 256-color codes (foreground)
    # Tags
    REMOTE_TAG = '\033[38;5;30m'      # Dark Teal
    FRONT_PANEL_TAG = '\033[38;5;226m'  # Bright Yellow
    VOLUME_KNOB_TAG = '\033[38;5;200m'  # Hot Magenta
    SERIAL_TAG = '\033[38;5;46m'      # Bright Green
    SYSTEM_TAG = '\033[38;5;39m'      # Bright Blue
    TCP_TAG = '\033[38;5;208m'        # Orange
    TX_TAG = '\033[38;5;87m'          # Light Cyan
    ERROR_TAG = '\033[38;5;196m'      # Bright Red
    
    # Message content colors
    SUCCESS = '\033[38;5;82m'         # Bright Green
    WARNING = '\033[38;5;178m'        # Orange-Yellow
    HEX_DATA = '\033[1;38;5;231m'     # Bold White
    INFO = '\033[38;5;117m'           # Light Blue

class YamahaAVRDaemon:
    def __init__(self, config_file='config.yaml'):
        # 1. Load Config and Build Unified Tables
        self.config = self.load_config(config_file)
        self.serial_port = self.config['connection']['serial_port']
        self.tcp_port = self.config['connection']['tcp_port']
        self.tcp_host = self.config['connection']['host']

        # Load command functions, system commands, and report mappings
        self.commands = self.config.get('functions', {})
        self.system = self.config.get('system', {})
        # Reverse reports map: convert from NAME: "HEX" to HEX: NAME for lookup
        reports_config = self.config.get('reports', {})
        self.reports = {v: k for k, v in reports_config.items()}

        # 2. State Resources
        self.ser = None
        self.server_socket = None
        self.command_queue = queue.Queue()
        self.running = True
        self.is_ready = False
        self.incoming_buffer = b''
        self.clients = []
        self.clients_lock = threading.Lock()

        # 3. Protocol Constants
        self.STX, self.ETX = b'\x02', b'\x03'
        self.DC1, self.DC2 = b'\x11', b'\x12'

    def load_config(self, filepath):
        if not os.path.exists(filepath):
            print(f"[ERROR] '{filepath}' missing."); sys.exit(1)
        with open(filepath, 'r') as f: return yaml.safe_load(f)

    def broadcast(self, msg):
        """Sends updates to TCP clients. Heartbeats are hidden from local console."""
        # Print colorized message to console only
        if not "Heartbeat" in msg:
            colored_msg = self._colorize_message(msg)
            print(colored_msg)
        
        # Send plain message (no color codes) to TCP clients
        with self.clients_lock:
            for c in self.clients[:]:
                try:
                    c.sendall((msg + "\n").encode('utf-8'))
                except:
                    self.clients.remove(c)

    def _print_colored(self, msg):
        """Print colorized message to console directly (not broadcast)."""
        colored_msg = self._colorize_message(msg)
        print(colored_msg)

    def _colorize_message(self, msg):
        """Add ANSI color codes to message for console output with advanced 256-color support."""
        # Extract tag and content
        import re
        tag_match = re.match(r'^(\[.*?\])\s(.*)$', msg)
        
        if tag_match:
            tag = tag_match.group(1)
            content = tag_match.group(2)
            
            # Determine colors based on tag
            if "[REMOTE" in tag:
                tag_color = Colors.REMOTE_TAG
                content_color = '\033[38;5;123m'  # Light Teal for REMOTE content
            elif "[FRONT PANEL" in tag:
                tag_color = Colors.FRONT_PANEL_TAG
                content_color = '\033[38;5;229m'  # Light Yellow for FRONT PANEL content
            elif "[VOLUME KNOB" in tag:
                tag_color = Colors.VOLUME_KNOB_TAG
                content_color = '\033[38;5;213m'  # Light Magenta for VOLUME KNOB content
            elif "[SERIAL" in tag:
                tag_color = Colors.SERIAL_TAG
                content_color = '\033[38;5;120m'  # Light Green for SERIAL content
            elif "[SYSTEM" in tag:
                tag_color = Colors.SYSTEM_TAG
                content_color = '\033[38;5;117m'  # Light Blue for SYSTEM content
            elif "[TCP]" in tag:
                tag_color = Colors.TCP_TAG
                content_color = '\033[38;5;215m'  # Light Orange for TCP content
            elif "[TX]" in tag:
                tag_color = Colors.TX_TAG
                content_color = '\033[38;5;188m'  # Light Cyan-White for TX content
            elif "[ERROR]" in tag:
                tag_color = Colors.ERROR_TAG
                content_color = '\033[38;5;210m'  # Light Red for ERROR content
            else:
                tag_color = Colors.RESET
                content_color = Colors.SUCCESS
            
            # Colorize tag
            colored_tag = f"{tag_color}{Colors.BOLD}{tag}{Colors.RESET}"
            
            # Colorize content and hex data
            # Find hex code patterns like (Hex: ...) at the end
            hex_match = re.search(r'\s\(Hex:\s([^)]+)\)$', content)
            if hex_match:
                hex_code = hex_match.group(1)
                content_part = content[:hex_match.start()]
                colored_content = f"{content_color}{content_part}{Colors.RESET} {content_color}(Hex: {Colors.HEX_DATA}{hex_code}{content_color}){Colors.RESET}"
            else:
                colored_content = f"{content_color}{content}{Colors.RESET}"
            
            return f"{colored_tag} {colored_content}"
        else:
            return msg

    def open_serial(self):
        try:
            self.ser = serial.Serial(port=self.serial_port, baudrate=9600, rtscts=True, timeout=0)
            return True
        except: return False

    def perform_handshake(self):
        if not self.ser: return False
        try:
            self.ser.write(self.DC1 + b'000' + self.ETX)
            self.ser.timeout = 0.5
            resp = self.ser.read_until(self.ETX)
            if self.DC2 in resp:
                if not self.is_ready:
                    print("[HANDSHAKE] OK. System Ready.")
                    self.is_ready = True
                return True
        except: pass
        return False

    def send_raw_packet(self, sw_byte, data_str):
        if not self.ser: return
        # Packet Assembly: STX + Switch + Data + ETX
        packet = self.STX + sw_byte + data_str.encode('ascii') + self.ETX
        self.ser.write(packet)
        self._print_colored(f"[TX] Sent: {packet}")

    def run_serial_worker(self):
        last_heartbeat = time.time()
        print("[WORKER] Serial thread started.")
        while self.running:
            if self.ser is None:
                if self.open_serial(): self.perform_handshake()
                else: time.sleep(5); continue

            # --- HEARTBEAT (60s) ---
            if time.time() - last_heartbeat > 60:
                self.broadcast("[SYSTEM     ] PONG (Heartbeat)")
                last_heartbeat = time.time()

            # --- READ LOOP ---
            if self.ser:
                try:
                    if self.ser.in_waiting > 0:
                        data = self.ser.read(self.ser.in_waiting)
                        self.incoming_buffer += data
                        while self.ETX in self.incoming_buffer:
                            end = self.incoming_buffer.find(self.ETX) + 1
                            raw = self.incoming_buffer[:end]
                            self.incoming_buffer = self.incoming_buffer[end:]

                            if self.DC2 in raw:
                                if not self.is_ready:
                                    print("[HANDSHAKE] OK. DC2 Received.")
                                    self.is_ready = True
                                continue

                            stx_idx = raw.find(self.STX)
                            if stx_idx != -1:
                                payload = raw[stx_idx+1 : -1].decode('ascii', errors='ignore')
                                if not payload: continue
                                if not self.is_ready and payload[0] in ['0', '1', '2', '4']:
                                    self.is_ready = True

                                # Extract source (first digit: 0-4) and report code (next 5 digits)
                                src = payload[0]
                                if not re.match(r'^[0-4]', src):
                                    continue
                                
                                tag_map = {
                                    "0": "[SERIAL]",
                                    "1": "[REMOTE]",
                                    "2": "[FRONT PANEL]",
                                    "3": "[SYSTEM]",
                                    "4": "[FRONT KNOB]"
                                    
                                }
                                tag = tag_map.get(src, f"[{src}]")
                                
                                # Extract 4-digit report code for lookup (skip first digit which is source)
                                report_code = payload[2:6] if len(payload) >= 6 else payload[2:]
                                
                                if report_code in self.reports:
                                    self.broadcast(f"{tag} {self.reports[report_code]} (Hex: {payload})")
                                elif payload[1:4] == "026": # Volume Report ID
                                    try:
                                        db = (int(payload[4:], 16) * 0.5) - 99.5
                                        self.broadcast(f"{tag} VOLUME {db:.1f} dB (Hex: {payload})")
                                    except: pass
                                else:
                                    self.broadcast(f"{tag} RAW: {payload}")
                except:
                    self.is_ready = False
                    self.ser = None

            # --- WRITE LOOP ---
            try:
                line = self.command_queue.get(timeout=0.05).strip().upper()

                if line == "POWER_ON":
                    self.ser.write(self.DC1 + b'000' + self.ETX)
                    time.sleep(0.2)
                    self.send_raw_packet(b'0', self.commands['POWER_ON'])

                elif line.startswith("MAIN_VOL_SET_"):
                    try:
                        target_db = float(line.replace("MAIN_VOL_SET_", ""))

                        # 1. Calculate Hex: (dB + 99.5) * 2
                        # 2. Format as 2-digit Hex (e.g., 77)
                        hex_val = format(int((target_db + 99.5) * 2), '02X')

                        # 3. Build Packet: Switch '2' + Command '30' + Hex Data
                        # This results in \x02 + 2 + 30 + 77 + \x03
                        self.send_raw_packet(b'2', f"30{hex_val}")
                    except Exception as e:
                        print(f"[ERROR] Volume calculation failed: {e}")

                elif line in self.config.get('requests', {}):
                    self.send_raw_packet(b'2', self.config['requests'][line])

                elif line in self.system:
                    # System commands use switch byte '2'
                    self.send_raw_packet(b'2', self.system[line])

                elif self.is_ready and line in self.commands:
                    # Function commands use switch byte '0'
                    self.send_raw_packet(b'0', self.commands[line])

                elif not self.is_ready and line != "":
                    if self.perform_handshake():
                        self.command_queue.put(line)

            except queue.Empty: pass

    def handle_client(self, client, addr):
        self._print_colored(f"[TCP] New connection from {addr}")
        with self.clients_lock: self.clients.append(client)
        try:
            client.settimeout(1.0)
            while self.running:
                try:
                    data = client.recv(1024)
                    if not data: break
                    for c in data.decode('utf-8').strip().split('\n'):
                        if c: self.command_queue.put(c)
                except socket.timeout: continue
                except: break
        finally:
            with self.clients_lock:
                if client in self.clients: self.clients.remove(client)
            client.close()

    def start_tcp_listener(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: # Safe Keep-Alive
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt in ['TCP_KEEPIDLE', 'TCP_KEEPALIVE']:
                if hasattr(socket, opt):
                    self.server_socket.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), 60)
        except: pass
        self.server_socket.bind((self.tcp_host, self.tcp_port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)
        self._print_colored(f"[TCP] Listening on {self.tcp_port} (Heartbeat Every 60s)...")
        while self.running:
            try:
                client, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(client, addr), daemon=True).start()
            except socket.timeout: continue

    def close_all(self):
        print("\n[SYSTEM] Shutting down..."); self.running = False
        if self.ser: self.ser.close()
        if self.server_socket: self.server_socket.close()
        with self.clients_lock:
            for c in self.clients: c.close()

if __name__ == "__main__":
    daemon = YamahaAVRDaemon()
    worker = threading.Thread(target=daemon.run_serial_worker, daemon=True)
    worker.start()
    try: daemon.start_tcp_listener()
    except KeyboardInterrupt: pass
    finally: daemon.close_all(); worker.join(timeout=2.0); print("[SYSTEM] Exited cleanly.")