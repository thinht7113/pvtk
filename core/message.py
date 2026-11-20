"""
PVTK Binary Message Implementation
Accurately mirrors com.hz.net.Message from decompiled Smali code.
"""

import struct

class Message:
    def __init__(self, msg_type: int = 0, data: bytes = None):
        self.type = msg_type & 0xFFFF
        self.data = bytearray(data) if data is not None else bytearray()
        self.offset = 0

    @property
    def msg_type(self) -> int:
        return self.type

    # ==================== Write Methods ====================
    def putByte(self, val: int):
        self.data.append(val & 0xFF)

    def putBoolean(self, val: bool):
        self.putByte(1 if val else 0)

    def putShort(self, val: int):
        self.data.extend(struct.pack(">h", val))

    def putInt(self, val: int):
        self.data.extend(struct.pack(">i", val))

    def putLong(self, val: int):
        self.data.extend(struct.pack(">q", val))

    def putChar(self, char_val: int):
        self.data.extend(struct.pack(">H", char_val & 0xFFFF))

    def putLength(self, length: int):
        # 3-byte unsigned integer (24-bit Big-Endian)
        self.data.append((length >> 16) & 0xFF)
        self.data.append((length >> 8) & 0xFF)
        self.data.append(length & 0xFF)

    def putString(self, text: str):
        if text is None:
            text = ""
        length = len(text)
        self.putLength(length)
        for char in text:
            self.putChar(ord(char))

    def putBytes(self, val: bytes):
        """Appends raw bytes to the message payload."""
        if val:
            self.data.extend(val)

    def getDataBytes(self) -> bytes:
        """
        Packs the full packet ready to be sent over TCP socket.
        Format: [2 bytes Total Length] + [2 bytes Msg Type] + [Payload Data]
        """
        payload_len = len(self.data)
        total_len = 4 + payload_len
        header = struct.pack(">Hh", total_len, self.type)
        return header + bytes(self.data)

    def to_wire_packet(self) -> bytes:
        """Alias for getDataBytes() to ensure wire packet format compatibility."""
        return self.getDataBytes()

    # ==================== Read Methods ====================
    def getByte(self) -> int:
        if self.offset >= len(self.data):
            return 0
        val = struct.unpack(">b", self.data[self.offset:self.offset+1])[0]
        self.offset += 1
        return val

    def getUByte(self) -> int:
        """Reads 1 unsigned byte (0 to 255). Matches Java Tool.byte2short(msg.getByte())."""
        if self.offset >= len(self.data):
            return 0
        val = struct.unpack(">B", self.data[self.offset:self.offset+1])[0]
        self.offset += 1
        return val

    def getBoolean(self) -> bool:
        return self.getByte() == 1

    def getShort(self) -> int:
        if self.offset + 2 > len(self.data):
            return 0
        val = struct.unpack(">h", self.data[self.offset:self.offset+2])[0]
        self.offset += 2
        return val

    def getInt(self) -> int:
        if self.offset + 4 > len(self.data):
            return 0
        val = struct.unpack(">i", self.data[self.offset:self.offset+4])[0]
        self.offset += 4
        return val

    def getLong(self) -> int:
        if self.offset + 8 > len(self.data):
            return 0
        val = struct.unpack(">q", self.data[self.offset:self.offset+8])[0]
        self.offset += 8
        return val

    def getChar(self) -> str:
        if self.offset + 2 > len(self.data):
            return ""
        char_code = struct.unpack(">H", self.data[self.offset:self.offset+2])[0]
        self.offset += 2
        # Filter out Java UI formatting / surrogate code points (0xD800 - 0xDFFF)
        if 0xD800 <= char_code <= 0xDFFF:
            return ""
        try:
            return chr(char_code)
        except Exception:
            return ""

    def getLength(self) -> int:
        """Reads a 3-byte Big-Endian length integer."""
        if self.offset + 3 > len(self.data):
            return 0
        b1, b2, b3 = struct.unpack(">BBB", self.data[self.offset:self.offset+3])
        self.offset += 3
        return (b1 << 16) | (b2 << 8) | b3

    def getString(self) -> str:
        size = self.getLength()
        if size <= 0:
            return ""
        chars = []
        for _ in range(size):
            chars.append(self.getChar())
        return "".join(chars)

    def getBytes(self, length: int = -1) -> bytes:
        """Reads remaining bytes (or exactly `length` bytes) from the message payload."""
        if length < 0:
            # Read all remaining
            if self.offset >= len(self.data):
                return b""
            val = bytes(self.data[self.offset:])
            self.offset = len(self.data)
            return val
        else:
            if self.offset + length > len(self.data):
                val = bytes(self.data[self.offset:])
                self.offset = len(self.data)
                return val
            val = bytes(self.data[self.offset:self.offset + length])
            self.offset += length
            return val

    def hasRemaining(self) -> bool:
        return self.offset < len(self.data)

    def __repr__(self):
        return f"<Message type=0x{self.type:04X} ({self.type}) len={len(self.data)} offset={self.offset}>"
