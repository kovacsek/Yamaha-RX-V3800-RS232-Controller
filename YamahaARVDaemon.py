import serial
import time
import socket
import threading
import queue
import sys
import yaml
import os

class YamahaAVRDaemon:
    def __init__(self, config_file='config.yaml'):
        # 1. Load Config and Build Unified Tables
        self.config = self.load_config(config_file)
        self.serial_port = self.config['connection']['serial_port']
        self.tcp_port = self.config['connection']['tcp_port']
        self.tcp_host = self.config['connection']['host']

        self.commands = {}
        self.reports = {}
        # Map friendly names to Hex and Hex suffixes to names
        for name, codes in self.config.get('functions', {}).items():
            if isinstance(codes, list) and len(codes) == 2:
                cmd_hex, report_hex = codes
                if cmd_hex: self.commands[name] = cmd_hex
                if report_hex: self.reports[report_hex] = name

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
        """Sends status updates to all connected TCP clients (Node-RED)"""
        print(msg)
        with self.clients_lock:
            for c in self.clients[:]:
                try:
                    c.sendall((msg + "\n").encode('utf-8'))
                except:
                    self.clients.remove(c)

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
        packet = self.STX + sw_byte + data_str.encode('ascii') + self.ETX
        self.ser.write(packet)
        print(f"[TX] Sent: {packet}")

    def run_serial_worker(self):
        # Initialize heartbeat timer
        last_heartbeat = time.time()
        print("[WORKER] Serial thread started.")
        
        while self.running:
            if self.ser is None:
                if self.open_serial(): self.perform_handshake()
                else: time.sleep(5); continue

            # --- HEARTBEAT LOGIC (Every 60s) ---
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
                                
                                # Set ready if any valid response is seen
                                if not self.is_ready and payload[0] in ['0', '1', '2', '4']:
                                    self.is_ready = True

                                src = payload[0]
                                tag_map = {
                                    "1": "[REMOTE     ]",
                                    "2": "[FRONT PANEL]",
                                    "4": "[VOLUME KNOB]",
                                    "0": "[SERIAL     ]"
                                }
                                tag = tag_map.get(src, f"[{src}]")
                                suffix = payload[1:]

                                if suffix in self.reports:
                                    self.broadcast(f"{tag} {self.reports[suffix]} (Hex: {payload})")
                                elif payload[1:4] == "026":
                                    db = (int(payload[4:], 16) * 0.5) - 99.5
                                    self.broadcast(f"{tag} VOLUME: {db:.1f} dB (Hex: {payload})")
                                elif not payload.startswith('3'):
                                    self.broadcast(f"{tag} RAW: {payload}")
                except:
                    self.is_ready = False
                    self.ser = None

            # --- WRITE LOOP ---
            try:
                cmd = self.command_queue.get(timeout=0.05).strip().upper()
                if cmd == "POWER_ON":
                    # Special wake-up sequence for standby mode
                    self.ser.write(self.DC1 + b'000' + self.ETX)
                    time.sleep(0.2)
                    self.send_raw_packet(b'0', self.commands['POWER_ON'])
                elif cmd in self.config.get('requests', {}):
                    self.send_raw_packet(b'2', self.config['requests'][cmd])
                elif self.is_ready and cmd in self.commands:
                    self.send_raw_packet(b'0', self.commands[cmd])
                elif not self.is_ready and cmd in self.commands:
                    if self.perform_handshake():
                        self.send_raw_packet(b'0', self.commands[cmd])
                    else:
                        self.command_queue.put(cmd)
            except queue.Empty: pass

    def handle_client(self, client, addr):
        print(f"[TCP] New connection from {addr}")
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
        
        # --- SAFE KEEP-ALIVE CONFIGURATION ---
        try:
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # Linux specific idle timer tuning (60 seconds)
            for opt in ['TCP_KEEPIDLE', 'TCP_KEEPALIVE']:
                if hasattr(socket, opt):
                    self.server_socket.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), 60)
        except: pass

        self.server_socket.bind((self.tcp_host, self.tcp_port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)
        print(f"[TCP] Listening on {self.tcp_port} (Heartbeat Every 60s)...")
        
        while self.running:
            try:
                client, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(client, addr), daemon=True).start()
            except socket.timeout: continue

    def close_all(self):
        print("\n[SYSTEM] Shutting down...")
        self.running = False
        if self.ser: self.ser.close()
        if self.server_socket: self.server_socket.close()
        with self.clients_lock:
            for c in self.clients:
                try: c.close()
                except: pass

if __name__ == "__main__":
    daemon = YamahaAVRDaemon()
    worker = threading.Thread(target=daemon.run_serial_worker, daemon=True)
    worker.start()
    try:
        daemon.start_tcp_listener()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.close_all()
        worker.join(timeout=2.0)
        print("[SYSTEM] Exited cleanly.")